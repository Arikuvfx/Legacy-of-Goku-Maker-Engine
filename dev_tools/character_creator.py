"""
character_creator.py  –  Dev-menu character editor
====================================================
Scans assets/sprites/player/ to discover player characters, then lets you
create or edit per-character JSON configs saved to assets/characters/{id}.json.

Only the player/ sub-folder is scanned — enemies, NPCs, and other sprites
that live elsewhere inside assets/sprites/ are intentionally ignored.

Wire-up (dev_menu.py)
---------------------
    import character_creator
    # inside your menu-option handler:
    character_creator.run(screen, clock)

Expected folder conventions
----------------------------
assets/
  sprites/
    player/
      {char_id}/                ← one folder per player character
        {costume}/              ← costume sub-folder (e.g. "default", "ssj")
          walk/
            0.png  1.png  ...  ← walk-cycle frames
        walk/                   ← flat layout (no costume sub-dirs) also ok
          0.png  1.png  ...
  characters/
    {char_id}.json              ← written here on Save
"""

from __future__ import annotations

import json
import os
import sys
import copy
import math
from pathlib import Path
from typing import Optional

import pygame

# ──────────────────────────────────────────────────────────────────────
#  Paths
# ──────────────────────────────────────────────────────────────────────
#
# IMPORTANT: these must NOT be resolved against the current working
# directory. A relative Path("assets/characters") only happens to work in
# PyCharm because the project root is the CWD there. Once this is packaged
# into a .exe (PyInstaller etc.), the CWD when double-clicked isn't
# guaranteed to be the folder the .exe lives in — so a relative path can
# silently miss the real assets/characters folder, load_config() then
# falls back to DEFAULT_CONFIG, and edits made in the character creator
# appear to do nothing in the shipped build.
#
# Instead, anchor everything to the folder the running program actually
# lives in: the .exe's own folder when frozen (PyInstaller), or this
# project's root folder (one level up from dev_tools/) when run from source.
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent.parent

SPRITES_DIR    = BASE_DIR / "assets/sprites/player"
CHARACTERS_DIR = BASE_DIR / "assets/characters"
ATTACKS_DIR    = BASE_DIR / "assets/sprites/attacks"   # global roster, not per-character
HUD_ICONS_DIR  = BASE_DIR / "assets/ui/hud"            # named icon PNGs used in the HUD picker
PORTRAITS_DIR  = BASE_DIR / "assets/portraits"         # see CharacterEditor._load_portrait()
UNIVERSAL_DIR  = BASE_DIR / "assets/sprites/universal" # shadow.png / shadowbig.png — see LayerManager._load_shadow()


def resolve_portrait_path(char_id: str, costume: str = "", form: str = "") -> Optional[Path]:
    """
    Resolve the portrait image file for a character/costume/transformation
    combo, with graceful fallback so portrait art can be added incrementally
    (per-costume) instead of needing every costume x transformation combo
    filled in up front.

    `costume` is a bare costume folder name (e.g. "base", "gi_alt") — NOT a
    transformation path. `form` is a bare transformation name (e.g. "ssj"),
    or "" for that costume's base look.

    Naming convention, flat folder assets/portraits/:
      {char_id}_{costume}_{form}.png   — costume + transformation specific
      {char_id}_{costume}.png          — costume-specific base look
      {char_id}_{form}.png             — legacy, costume-agnostic transformation
      {char_id}.png                    — legacy, costume-agnostic base look

    The last two exist so characters that only ever had flat, costume-less
    portraits (the old convention) keep working untouched; once a
    costume-specific portrait is added for a given costume, it takes over
    for that costume only.
    """
    candidates = []
    if costume and form:
        candidates.append(f"{char_id}_{costume}_{form}")
    if costume:
        candidates.append(f"{char_id}_{costume}")
    if form:
        candidates.append(f"{char_id}_{form}")
    candidates.append(char_id)

    seen = set()
    for name in candidates:
        if name in seen:
            continue
        seen.add(name)
        path = PORTRAITS_DIR / f"{name}.png"
        if path.exists():
            return path
    return None

# ──────────────────────────────────────────────────────────────────────
#  Palette  (dark dev-tool)
# ──────────────────────────────────────────────────────────────────────
C_BG          = (14,  14,  20)
C_PANEL       = (24,  24,  34)
C_PANEL_DARK  = (18,  18,  26)
C_BORDER      = (50,  50,  72)
C_ACCENT      = (80, 160, 255)
C_ACCENT2     = (255, 200,  55)    # unsaved / warning gold
C_TEXT        = (215, 215, 228)
C_TEXT_DIM    = (110, 110, 138)
C_RED         = (220,  70,  70)
C_GREEN       = (70,  200, 100)
C_BAR_BG      = (38,  38,  55)
C_BAR_FILL    = (80, 160, 255)
C_HOVER       = (40,  40,  62)
C_SELECTED    = (30,  75, 140)
C_TAB_ACT     = (44,  44,  68)
C_TAB_INACT   = (24,  24,  34)
C_DIALOG_BG   = (22,  22,  32)

# ──────────────────────────────────────────────────────────────────────
#  Default character config skeleton
# ──────────────────────────────────────────────────────────────────────
DEFAULT_CONFIG: dict = {
    "id":           "",
    "display_name": "",
    # Freeform prose shown in the Scouter Data description panel (see
    # ui/scouter_menu.py's _get_entity_description / _draw_data_description).
    "description":  "",
    "costume":      "default",
    "shadow_size":  32,
    "stats": {
        "max_hp":   100,
        "max_ki":   100,
        "power":     50,   # STR — melee damage
        "ki_power":  50,   # POW — ki blast damage
        "defense":   50,   # legacy/unused, kept for save-file compatibility
        "vitality":  50,   # END — incoming melee mitigation
        "speed":     50,   # SPD
        "ki_regen":  30,
    },
    "attacks": {
        "ki_attack_mode":  "blast",   # "blast" | "beam" | "both"
        "blast_cost":      20,
        "beam_cost":       50,
        "melee_duration":  0.5,       # seconds
        # Whether holding the melee button (rather than tapping it) lunges
        # forward or spins in place once fully charged — see
        # Player.release_charged_melee() / Game._reload_attack_config().
        "charged_melee_style": "lunge",   # "lunge" | "spin"
        "walk_speed":      150,
        "run_speed":       300,
        "fly_speed":       450,
        # Attack ids (sub-folder names under assets/sprites/attacks/) this
        # character can use in-game. Populated via the icon picker on the
        # Attacks tab — see discover_attacks() / load_attack_icon().
        "equipped_attacks": [],
    },
    # Each entry: {id, display_name, costume, power_mult, defense_mult,
    #              speed_mult, ki_drain}. "costume" points at one of the
    # folders discover_costumes() finds for this character — it's what gets
    # shown in the preview when a transformation is selected/stepped through.
    "transformations": [],
    # "costume" paths (e.g. "base/transformations/ssj") the user explicitly
    # removed via the editor. sync_transformations() re-registers any
    # on-disk transformation folder that doesn't already have a config
    # entry — without this list it can't tell "never added yet" apart from
    # "deliberately deleted", so a removed transformation would silently
    # reappear the next time the character is loaded as long as its sprite
    # folder still exists on disk.
    "removed_transformations": [],
}

# ══════════════════════════════════════════════════════════════════════
#  Helper: filesystem scanning
# ══════════════════════════════════════════════════════════════════════

def discover_characters() -> list[str]:
    """Return character IDs (sub-folder names in assets/sprites/player/).

    If a custom menu order has been saved (see save_character_order(),
    set via the ▲/▼ controls in the character list), characters are
    returned in that order. Any character not yet placed in the saved
    order — newly added, or created outside this tool — is appended
    afterward, sorted alphabetically. Falls back to plain alphabetical
    if no custom order has been saved.
    """
    if not SPRITES_DIR.exists():
        return []
    order, removed = load_character_menu()
    found = sorted(
        d.name for d in SPRITES_DIR.iterdir()
        if d.is_dir() and not d.name.startswith(".") and d.name not in removed
    )
    if not order:
        return found
    found_set = set(found)
    ordered  = [cid for cid in order if cid in found_set]
    leftover = sorted(found_set - set(ordered))
    return ordered + leftover


def discover_costumes(char_id: str) -> list[str]:
    """
    Return form names for a character (e.g. ["base", "ssj", "ssj2"]).
    A form is any sub-directory of assets/sprites/player/{char_id}/ that
    contains at least one *.png sprite sheet.
    Falls back to ["base"] if nothing is found.
    """
    base = SPRITES_DIR / char_id
    if not base.exists():
        return ["base"]

    forms = [
        d.name
        for d in sorted(base.iterdir())
        if d.is_dir()
        and not d.name.startswith(".")
        and any(d.glob("*.png"))
    ]
    return forms if forms else ["base"]


def discover_transformations(char_id: str, costume: str = "base") -> list[str]:
    """
    Return transformation form names for a character/costume pair
    (e.g. ["ssj", "ssj2"]).
    Scans assets/sprites/player/{char_id}/{costume}/transformations/ for
    sub-folders that contain at least one *.png sprite sheet.
    Returns just the folder name (e.g. "ssj"), not the full path.
    The runtime resolves these to "{costume}/transformations/ssj" internally.
    """
    transforms_dir = SPRITES_DIR / char_id / costume / "transformations"
    if not transforms_dir.exists():
        return []
    return [
        d.name
        for d in sorted(transforms_dir.iterdir())
        if d.is_dir()
        and not d.name.startswith(".")
        and any(d.glob("*.png"))
    ]


def discover_animation_ids(char_id: str) -> list[str]:
    """
    Return base animation names for a character (e.g. ["idle", "walk",
    "run", "attack"]) — the *.png stems directly under each of the
    character's form folders (assets/sprites/player/{char_id}/{form}/),
    NOT the per-direction keys discover_animations() produces (it suffixes
    each with "_down"/"_left"/etc. after slicing the sheet). This is the
    lighter-weight, direction-agnostic id play_character_animation actions
    reference (direction is resolved separately, from the character's
    current facing, at runtime).

    Unioned across every form/costume (via discover_costumes()) rather
    than scoped to just "base", since which costume is active when the
    action fires isn't known at edit time and animation sets are expected
    to be consistent across a character's forms. Returns [] if char_id is
    falsy or nothing can be loaded.
    """
    if not char_id:
        return []
    base = SPRITES_DIR / char_id
    if not base.exists():
        return []

    names: set[str] = set()
    for form in discover_costumes(char_id):
        folder = base / form
        if not folder.exists():
            continue
        names.update(png.stem for png in folder.glob("*.png"))
    return sorted(names)


def discover_portraits() -> list[str]:
    """Return every portrait id — the filename stem of each *.png directly
    in assets/portraits/ (e.g. "Goku", "Goku_base_ssj", "Vegeta_gi_alt").

    These are exactly the ids resolve_portrait_path() matches against
    ({char_id}_{costume}_{form}, {char_id}_{costume}, {char_id}_{form},
    {char_id}) — that function resolves ONE portrait for a specific
    char/costume/form combo with fallback; this instead lists every id
    that actually exists on disk, for pickers (e.g. the event editor's
    dialogue_box/set_portrait action fields) that need the full roster
    up front rather than resolving on demand.
    """
    if not PORTRAITS_DIR.exists():
        return []
    return sorted(
        p.stem for p in PORTRAITS_DIR.iterdir()
        if p.is_file() and p.suffix.lower() == ".png" and not p.name.startswith(".")
    )


def discover_attacks() -> list[str]:
    """
    Return attack ids — every sub-folder of assets/sprites/attacks/ that
    contains at least one *.png.

    Unlike discover_costumes()/discover_transformations(), this is a GLOBAL
    roster shared by every character (e.g. "ki_blast", "kamehameha"), not
    something scoped to char_id. The Attacks tab lets the user pick which of
    these a given character is allowed to use; that selection is saved to
    cfg["attacks"]["equipped_attacks"] for the game to read.
    """
    if not ATTACKS_DIR.exists():
        return []
    _EXCLUDED_ATTACKS = {"bullet", "rocket"}
    return sorted(
        d.name for d in ATTACKS_DIR.iterdir()
        if d.is_dir()
        and not d.name.startswith(".")
        and d.name not in _EXCLUDED_ATTACKS
        and any(d.glob("*.png"))
    )


def load_walk_frames(char_id: str, form: str) -> list[pygame.Surface]:
    """
    Load walk-cycle frames for the sidebar preview (down direction only).
    Reads walk.png (or idle/run fallback) from assets/sprites/player/{char_id}/{form}/.
    """
    folder = SPRITES_DIR / char_id / form
    if not folder.exists():
        return []

    frame_w, frame_h = _read_sprite_size(folder)

    for stem in ("walk", "idle", "run"):
        png = folder / f"{stem}.png"
        if not png.exists():
            continue
        try:
            sheet = pygame.image.load(str(png)).convert_alpha()
        except Exception:
            continue
        dirs = _slice_sheet(sheet, frame_w, frame_h)
        if "down" in dirs:
            return dirs["down"]

    return []


def _read_sprite_size(folder: Path, default: int = 32) -> tuple[int, int]:
    """Read frame dimensions from sprite_size.txt (format: '48x48'). Falls back to default x default."""
    p = folder / "sprite_size.txt"
    if p.exists():
        try:
            w, h = p.read_text().strip().lower().split("x")
            return int(w), int(h)
        except Exception:
            pass
    return default, default


# ── Attack icons (Attacks-tab picker) ───────────────────────────────────
# Existing attack assets (ki_blast.png, begin_kamehameha.png, ...) all use
# 16x16 frames — see projectile.py / beam.py — so that's the fallback frame
# size here too. A folder can override it with its own sprite_size.txt,
# same convention as player sprites.
ATTACK_ICON_SIZE = 48          # fallback size when no HUD icon exists
_ATTACK_ICON_CACHE: dict[str, pygame.Surface] = {}


def load_attack_icon(attack_id: str, size: int = ATTACK_ICON_SIZE) -> pygame.Surface:
    """
    Return (and cache) an icon surface for an attack, used by the Attacks-tab
    picker.

    Priority:
      1. assets/sprites/attacks/{attack_id}/icon.png — per-attack icon at
         native pixel size; takes precedence over everything else.
      2. assets/ui/hud/{attack_id}.png  — dedicated HUD icon, loaded at its
         *native* pixel size (no scaling).
      3. assets/sprites/attacks/{attack_id}/ — sprite-sheet fallback: grab the
         top-left frame and scale it to `size` (same as before).
      4. Placeholder tile when nothing is found.
    """
    if attack_id in _ATTACK_ICON_CACHE:
        return _ATTACK_ICON_CACHE[attack_id]

    icon: Optional[pygame.Surface] = None

    # ── 1. Per-attack icon.png (native resolution) ─────────────────────
    # assets/sprites/attacks/{attack_id}/icon.png takes top priority so
    # each attack can ship its own dedicated display icon.
    sprite_icon_path = ATTACKS_DIR / attack_id / "icon.png"
    if sprite_icon_path.exists():
        try:
            icon = pygame.image.load(str(sprite_icon_path)).convert_alpha()
        except Exception:
            icon = None

    # ── 2. HUD icon (native resolution) ───────────────────────────────
    if icon is None:
        hud_path = HUD_ICONS_DIR / f"{attack_id}.png"
        if hud_path.exists():
            try:
                icon = pygame.image.load(str(hud_path)).convert_alpha()
            except Exception:
                icon = None

    # ── 3. Sprite-sheet fallback (scaled thumbnail) ────────────────────
    if icon is None:
        folder = ATTACKS_DIR / attack_id
        if folder.exists():
            png_files = sorted(folder.glob("*.png"))
            # Preference: <attack_id>.png > begin_* > anything
            candidates: list[Path] = []
            candidates += [p for p in png_files if p.stem == attack_id]
            candidates += [p for p in png_files if p.stem.startswith("begin")]
            candidates += png_files

            frame_w, frame_h = _read_sprite_size(folder, default=16)

            for path in candidates:
                try:
                    sheet = pygame.image.load(str(path)).convert_alpha()
                    fw = min(frame_w, sheet.get_width())
                    fh = min(frame_h, sheet.get_height())
                    if fw <= 0 or fh <= 0:
                        continue
                    frame = sheet.subsurface(pygame.Rect(0, 0, fw, fh))
                    icon = pygame.transform.smoothscale(frame, (size, size))
                    break
                except Exception:
                    continue

    # ── 4. Placeholder tile ────────────────────────────────────────────
    if icon is None:
        icon = pygame.Surface((size, size), pygame.SRCALPHA)
        pygame.draw.rect(icon, C_PANEL_DARK, icon.get_rect(), border_radius=6)
        pygame.draw.rect(icon, C_BORDER, icon.get_rect(), 1, border_radius=6)
        ph_font = pygame.font.Font(None, size)
        label = (attack_id[:1] or "?").upper()
        txt = ph_font.render(label, True, C_TEXT_DIM)
        icon.blit(txt, txt.get_rect(center=icon.get_rect().center))

    _ATTACK_ICON_CACHE[attack_id] = icon
    return icon


# Row-index to direction name for 4-dir and 8-dir sheets
_DIRS_4 = ["down", "left", "right", "up"]
_DIRS_8 = ["down", "down_left", "left", "up_left", "up", "up_right", "right", "down_right"]


def _slice_sheet(sheet: pygame.Surface, frame_w: int, frame_h: int) -> dict[str, list[pygame.Surface]]:
    """
    Cut every direction row out of a sprite sheet.
    Returns {"down": [...frames], "left": [...frames], ...}.
    Row count >= 8 uses the 8-direction map, otherwise 4-direction.
    """
    if frame_w <= 0 or frame_h <= 0:
        return {}

    num_frames = max(1, sheet.get_width()  // frame_w)
    num_rows   = max(1, sheet.get_height() // frame_h)
    directions = (_DIRS_8 if num_rows >= 8 else _DIRS_4)[:num_rows]
    result: dict[str, list[pygame.Surface]] = {}

    for row_idx, direction in enumerate(directions):
        frames: list[pygame.Surface] = []
        for col in range(num_frames):
            frame = pygame.Surface((frame_w, frame_h), pygame.SRCALPHA)
            frame.blit(sheet, (0, 0),
                       (col * frame_w, row_idx * frame_h, frame_w, frame_h))
            frames.append(frame)
        if frames:
            result[direction] = frames

    return result


def discover_animations(char_id: str, form: str) -> dict[str, list[pygame.Surface]]:
    """
    Load every directional animation for the given character form.

    Folder: assets/sprites/player/{char_id}/{form}/
    Each *.png is a sprite sheet: rows = directions, columns = frames.
    Frame size comes from sprite_size.txt in the same folder (defaults to 32x32).
    Returns {"walk_down": [frames], "walk_left": [frames], ...}.
    """
    folder = SPRITES_DIR / char_id / form
    if not folder.exists():
        return {}

    frame_w, frame_h = _read_sprite_size(folder)
    result: dict[str, list[pygame.Surface]] = {}

    for png in sorted(folder.glob("*.png")):
        anim_name = png.stem
        try:
            sheet = pygame.image.load(str(png)).convert_alpha()
        except Exception:
            continue
        for direction, frames in _slice_sheet(sheet, frame_w, frame_h).items():
            result[f"{anim_name}_{direction}"] = frames

    return result


def load_config(char_id: str) -> dict:
    path = CHARACTERS_DIR / f"{char_id}.json"
    if path.exists():
        try:
            with open(path) as f:
                data = json.load(f)
            # Merge missing keys from default. "stats"/"attacks" are excluded
            # from the top-level update and merged separately below, so that
            # new default keys (e.g. a stat added after this character was
            # last saved) survive instead of being wiped out by the saved
            # file's older, incomplete sub-dict.
            merged = copy.deepcopy(DEFAULT_CONFIG)
            merged.update({k: v for k, v in data.items()
                           if k not in ("stats", "attacks")})
            for sub in ("stats", "attacks"):
                merged[sub].update(data.get(sub, {}))
            merged["id"] = char_id
            return merged
        except Exception:
            pass
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["id"] = char_id
    cfg["display_name"] = char_id.replace("_", " ").title()
    return cfg


def save_config(cfg: dict) -> None:
    CHARACTERS_DIR.mkdir(parents=True, exist_ok=True)
    path = CHARACTERS_DIR / f"{cfg['id']}.json"
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2)


def delete_config(char_id: str) -> None:
    path = CHARACTERS_DIR / f"{char_id}.json"
    if path.exists():
        path.unlink()


# ── Character menu state (order + deletions) ────────────────────────
# One small file holds everything the roster needs beyond the sprite
# folders and per-character configs themselves:
#   - "order":   hand-picked ordering from the ▲/▼ controls
#   - "removed": IDs explicitly deleted via the character creator
#
# It lives next to assets/characters/, NOT inside it — anything that
# scans assets/characters/*.json and treats each file as a character
# config (e.g. an in-game character-select menu calling cfg.get('id'))
# would choke on this file otherwise.
#
# discover_characters() lists sprite sub-folders, not config files, so
# deleting a character's config alone doesn't make it disappear from
# the roster (its sprite folder is still on disk) — "removed" tracks
# IDs the user explicitly deleted so they stay hidden even after the
# tool is reopened, without touching any sprite assets. Re-creating a
# character with the same ID (via "New") clears it from "removed" again.
MENU_FILE           = CHARACTERS_DIR.parent / "character_menu.json"
_LEGACY_ORDER_FILE   = CHARACTERS_DIR / "_order.json"                    # oldest, broken location
_LEGACY_ORDER_FILE_2 = CHARACTERS_DIR.parent / "character_menu_order.json"    # pre-consolidation
_LEGACY_REMOVED_FILE = CHARACTERS_DIR.parent / "character_menu_removed.json"  # pre-consolidation


def _migrate_legacy_menu_files() -> None:
    """One-time cleanup: earlier versions of this tool stored order (and
    later, deletions) in one or two separate files. Pull whatever's found
    into MENU_FILE, then remove the old files so they aren't mistaken for
    something else and don't linger as clutter."""
    order:   list[str] = []
    removed: list[str] = []

    if _LEGACY_ORDER_FILE.exists():
        try:
            data = json.loads(_LEGACY_ORDER_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                order = [str(cid) for cid in data]
        except Exception:
            pass
    if _LEGACY_ORDER_FILE_2.exists():
        try:
            data = json.loads(_LEGACY_ORDER_FILE_2.read_text(encoding="utf-8"))
            if isinstance(data, list):
                order = [str(cid) for cid in data]
        except Exception:
            pass
    if _LEGACY_REMOVED_FILE.exists():
        try:
            data = json.loads(_LEGACY_REMOVED_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                removed = [str(cid) for cid in data]
        except Exception:
            pass

    if order or removed:
        save_character_menu(order, removed)

    for legacy in (_LEGACY_ORDER_FILE, _LEGACY_ORDER_FILE_2, _LEGACY_REMOVED_FILE):
        try:
            legacy.unlink()
        except Exception:
            pass


def load_character_menu() -> tuple[list[str], set[str]]:
    """Return (order, removed) from MENU_FILE. order may be stale
    (reference deleted characters, omit new ones) — callers should
    reconcile against the real folder list. Returns ([], set()) if
    nothing has been saved yet."""
    if not MENU_FILE.exists() and (
        _LEGACY_ORDER_FILE.exists() or _LEGACY_ORDER_FILE_2.exists() or _LEGACY_REMOVED_FILE.exists()
    ):
        _migrate_legacy_menu_files()
    if not MENU_FILE.exists():
        return [], set()
    try:
        data = json.loads(MENU_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            order   = [str(cid) for cid in data.get("order", [])]
            removed = {str(cid) for cid in data.get("removed", [])}
            return order, removed
    except Exception:
        pass
    return [], set()


def save_character_menu(order: list[str], removed: list[str] | set[str]) -> None:
    """Persist both the menu ordering and the deleted-character list together."""
    MENU_FILE.parent.mkdir(parents=True, exist_ok=True)
    MENU_FILE.write_text(
        json.dumps({"order": order, "removed": sorted(removed)}, indent=2),
        encoding="utf-8",
    )


def load_character_order() -> list[str]:
    """Return just the saved custom menu ordering of character IDs."""
    order, _removed = load_character_menu()
    return order


def load_removed_characters() -> set[str]:
    """Return just the set of explicitly-deleted character IDs."""
    _order, removed = load_character_menu()
    return removed


# ── Global game settings (not per-character) ────────────────────────
# Things like max_level apply to the whole game/GameConfig, not to any
# one character, so they don't belong in a character's own JSON (which
# load_config()/save_config() manage above). They get their own small
# sibling file instead, same rationale as MENU_FILE above.
GLOBAL_SETTINGS_FILE = CHARACTERS_DIR.parent / "game_settings.json"

DEFAULT_GLOBAL_SETTINGS = {
    "max_level": 99,   # mirrors GameConfig.max_level's own default
}


def load_global_settings() -> dict:
    """Return saved global settings, merged over the defaults so new keys
    added later don't break existing save files. Returns the defaults
    untouched if nothing has been saved yet."""
    settings = copy.deepcopy(DEFAULT_GLOBAL_SETTINGS)
    if GLOBAL_SETTINGS_FILE.exists():
        try:
            data = json.loads(GLOBAL_SETTINGS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                settings.update(data)
        except Exception:
            pass
    return settings


def save_global_settings(settings: dict) -> None:
    GLOBAL_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    GLOBAL_SETTINGS_FILE.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def save_character_order(order: list[str]) -> None:
    """Persist the menu ordering, keeping the removed list untouched."""
    save_character_menu(order, load_removed_characters())


def save_removed_characters(removed: set[str]) -> None:
    """Persist the removed-character list, keeping the order untouched."""
    save_character_menu(load_character_order(), removed)



def sync_transformations(cfg: dict, costumes: list[str],
                         transform_forms: list[str] | None = None) -> bool:
    """
    Auto-register transformation entries for any discovered forms that don't
    already have one.

    transform_forms: form names from discover_transformations() (e.g. ["ssj"]).
      These are stored with costume = "transformations/ssj" to match the path
      that _resolve_transform_costume() returns at runtime.

    costumes: base costume list, used only to resolve which costume is
      "current" for the nested-layout registration below. A plain costume is
      NOT a transformation — an outfit swap and a power-up form are different
      things — so alternate costume folders are never auto-registered as
      transformations here. Only genuine transformation sub-folders
      (assets/sprites/player/{char}/{costume}/transformations/{form}/) get
      auto-registered, and each stays scoped to the costume it was found under.

    Matching is by the "costume" field. Returns True if any entries were added.
    """
    cfg.setdefault("transformations", [])
    cfg.setdefault("removed_transformations", [])
    transformations = cfg["transformations"]
    used    = {t.get("costume") for t in transformations}
    removed = set(cfg["removed_transformations"])

    added = False

    # Resolve the base costume up front — needed for both the nested and legacy paths.
    base = cfg.get("costume", "")
    base = base if base in costumes else (costumes[0] if costumes else base)

    # New nested layout: assets/sprites/player/{char_id}/{base_costume}/transformations/{form}/
    for form in (transform_forms or []):
        costume_path = f"{base}/transformations/{form}"
        if costume_path in used:
            continue
        # The folder is still on disk, but the user explicitly deleted this
        # transformation via the editor before — respect that instead of
        # silently re-adding it every time the character is loaded.
        if costume_path in removed:
            continue
        transformations.append({
            "id":            form,
            "display_name":  form.replace("_", " ").upper(),
            "costume":       costume_path,
            "power_mult":    1.0,
            "defense_mult":  1.0,
            "speed_mult":    1.0,
            "ki_drain":      0.0,
        })
        used.add(costume_path)
        added = True

    return added


# ══════════════════════════════════════════════════════════════════════
#  Tiny widget helpers (all draw onto a given surface)
# ══════════════════════════════════════════════════════════════════════

# ── Cached text rendering (perf) ────────────────────────────────────────
# pygame's font.render() rasterizes glyphs from scratch on every call, and
# this editor calls it dozens of times per frame (every button, label,
# header, and list row re-renders its text every draw() even though most
# of that text — button captions, section headers, tab names, hint
# strings, character-list rows — is identical to what was drawn a frame
# ago). That's wasted CPU for a picture that hasn't changed.
#
# render_text_cached() memoizes by (font, exact text, colour): the first
# time a given combo is drawn it renders and stores the Surface; every
# repeat afterwards is a dict lookup instead of a re-rasterize. The cache
# is safe to use anywhere the *set* of distinct text+colour combinations
# is small and stable (button labels, headers, list entries, tab names,
# character/attack IDs, status messages, ...).
#
# Deliberately NOT routed through this cache: text that changes on every
# frame or keystroke — slider values while dragging, the text-input's
# live contents, per-frame animation-frame counters. Those have
# effectively unlimited distinct values, so caching them would just leak
# Surfaces into this dict forever for no benefit.
_TEXT_RENDER_CACHE: dict[tuple[int, str, tuple], pygame.Surface] = {}


def render_text_cached(font: pygame.font.Font, text: str, color) -> pygame.Surface:
    """Cached equivalent of font.render(text, True, color) for text that
    repeats identically across frames. See _TEXT_RENDER_CACHE note above —
    don't use this for text with high/unbounded variety."""
    key = (id(font), text, tuple(color))
    surface = _TEXT_RENDER_CACHE.get(key)
    if surface is None:
        surface = font.render(text, True, color)
        _TEXT_RENDER_CACHE[key] = surface
    return surface


def draw_rect_outline(surf: pygame.Surface, rect: pygame.Rect,
                      color=C_BORDER, radius=4, width=1) -> None:
    """Draw a rounded outline only (no fill) — used for panel/box borders."""
    pygame.draw.rect(surf, color, rect, width, border_radius=radius)


def draw_label(surf: pygame.Surface, font: pygame.font.Font,
               text: str, x: int, y: int, color=C_TEXT_DIM) -> None:
    """Blit a plain text label at (x, y). Text is cached (see above) since
    field labels ("Power", "Speed", ...) are the same string every frame."""
    surf.blit(render_text_cached(font, text, color), (x, y))


def draw_section_header(surf: pygame.Surface, font: pygame.font.Font,
                         text: str, rect: pygame.Rect) -> None:
    """Draw a horizontal divider line with a small caption label on top of
    it, used to separate groups of widgets within a tab (e.g. 'Equipped
    Attacks', 'Edit Selected')."""
    pygame.draw.line(surf, C_BORDER,
                     (rect.x, rect.y + 8), (rect.right, rect.y + 8))
    lbl = render_text_cached(font, f"  {text}  ", C_TEXT_DIM)
    surf.blit(lbl, (rect.x + 12, rect.y))


class TextInput:
    """Single-line text field."""
    H = 28

    def __init__(self, rect: pygame.Rect, value: str = ""):
        self.rect    = rect
        self.value   = value
        self.active  = False
        self.cursor  = len(value)
        self._blink  = 0.0

    def handle_event(self, event: pygame.event.Event) -> bool:
        """Returns True if value changed."""
        if event.type == pygame.MOUSEBUTTONDOWN:
            self.active = self.rect.collidepoint(event.pos)
            if self.active:
                self.cursor = len(self.value)
            return False
        if not self.active:
            return False
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_BACKSPACE:
                if self.cursor > 0:
                    self.value  = self.value[:self.cursor-1] + self.value[self.cursor:]
                    self.cursor -= 1
                    return True
            elif event.key == pygame.K_DELETE:
                self.value = self.value[:self.cursor] + self.value[self.cursor+1:]
                return True
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
                self.value  = self.value[:self.cursor] + event.unicode + self.value[self.cursor:]
                self.cursor += 1
                return True
        return False

    def draw(self, surf: pygame.Surface, font: pygame.font.Font,
             dt: float) -> None:
        self._blink = (self._blink + dt) % 1.2
        border_col = C_ACCENT if self.active else C_BORDER
        pygame.draw.rect(surf, C_PANEL_DARK, self.rect, border_radius=4)
        pygame.draw.rect(surf, border_col, self.rect, 1, border_radius=4)
        clip = self.rect.inflate(-8, -4)
        txt  = font.render(self.value, True, C_TEXT)
        surf.blit(txt, (clip.x, self.rect.y + (self.rect.h - txt.get_height()) // 2),
                  area=pygame.Rect(0, 0, clip.w, txt.get_height()))
        if self.active and self._blink < 0.6:
            cx = clip.x + font.size(self.value[:self.cursor])[0]
            cy = self.rect.y + 4
            pygame.draw.line(surf, C_TEXT, (cx, cy), (cx, self.rect.bottom - 4))


class TextArea:
    """Multi-line text field with soft word-wrap, for freeform prose fields
    like the character/entity Description — TextInput above is single-line
    only, so this is a separate widget rather than an extension of it.

    self.value is always the raw, un-wrapped string (the only thing that
    ever gets written back into cfg["description"]); wrapping is purely a
    draw-time concern recomputed from self.rect.w every frame via _wrap(),
    so resizing the panel (or just re-editing) never desyncs the two.
    Enter inserts a literal '\\n' (hard break) so an author's intentional
    paragraph breaks survive re-wrapping instead of being swallowed into
    one run-on paragraph. Ctrl+Enter (or Tab/Escape) defocuses instead,
    since plain Enter is needed for line breaks.
    """
    H = 100   # default box height if the caller doesn't override rect.h

    def __init__(self, rect: pygame.Rect, value: str = "", max_len: int = 600):
        self.rect     = rect
        self.value    = value
        self.max_len  = max_len
        self.active   = False
        self.cursor   = len(value)
        self._blink   = 0.0
        self._scroll  = 0   # index of the first visible wrapped line

    def handle_event(self, event: pygame.event.Event) -> bool:
        """Returns True if value changed."""
        if event.type == pygame.MOUSEBUTTONDOWN:
            self.active = self.rect.collidepoint(event.pos)
            if self.active:
                self.cursor = len(self.value)
            return False
        if not self.active:
            return False
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_BACKSPACE:
                if self.cursor > 0:
                    self.value  = self.value[:self.cursor-1] + self.value[self.cursor:]
                    self.cursor -= 1
                    return True
            elif event.key == pygame.K_DELETE:
                self.value = self.value[:self.cursor] + self.value[self.cursor+1:]
                return True
            elif event.key == pygame.K_LEFT:
                self.cursor = max(0, self.cursor - 1)
            elif event.key == pygame.K_RIGHT:
                self.cursor = min(len(self.value), self.cursor + 1)
            elif event.key == pygame.K_HOME:
                self.cursor = 0
            elif event.key == pygame.K_END:
                self.cursor = len(self.value)
            elif event.key == pygame.K_RETURN:
                if (pygame.key.get_mods() & pygame.KMOD_CTRL):
                    self.active = False
                elif len(self.value) < self.max_len:
                    self.value  = self.value[:self.cursor] + "\n" + self.value[self.cursor:]
                    self.cursor += 1
                    return True
            elif event.key in (pygame.K_TAB, pygame.K_ESCAPE):
                self.active = False
            elif event.unicode and event.unicode.isprintable():
                if len(self.value) < self.max_len:
                    self.value  = self.value[:self.cursor] + event.unicode + self.value[self.cursor:]
                    self.cursor += 1
                    return True
        return False

    def _wrap(self, font: pygame.font.Font, width: int) -> list[tuple[str, int]]:
        """Word-wrap self.value to `width` px, returning [(line_text,
        start_offset_into_value), ...] so the cursor's absolute position
        can be mapped back to a (line, x) on screen in _cursor_xy()."""
        lines: list[tuple[str, int]] = []
        para_start = 0
        for para in self.value.split("\n"):
            if para == "":
                lines.append(("", para_start))
            else:
                words = para.split(" ")
                cur, cur_start, pos = "", para_start, para_start
                for w in words:
                    trial = f"{cur} {w}" if cur else w
                    if cur and font.size(trial)[0] > width:
                        lines.append((cur, cur_start))
                        cur, cur_start = w, pos
                    else:
                        cur = trial
                    pos += len(w) + 1   # word + the space that followed it
                lines.append((cur, cur_start))
            para_start += len(para) + 1   # +1 for the '\n' that was split on
        return lines or [("", 0)]

    def _line_of_cursor(self, lines: list[tuple[str, int]]) -> int:
        for i, (text, start) in enumerate(lines):
            if start <= self.cursor <= start + len(text):
                return i
        return len(lines) - 1

    def draw(self, surf: pygame.Surface, font: pygame.font.Font,
             dt: float, placeholder: str = "Click to add a description...") -> None:
        self._blink = (self._blink + dt) % 1.2
        border_col = C_ACCENT if self.active else C_BORDER
        pygame.draw.rect(surf, C_PANEL_DARK, self.rect, border_radius=4)
        pygame.draw.rect(surf, border_col, self.rect, 1, border_radius=4)

        pad   = 8
        inner = self.rect.inflate(-pad * 2, -pad * 2)
        line_h = font.get_height() + 2
        rows_visible = max(1, inner.h // line_h)

        if not self.value and not self.active:
            ph = font.render(placeholder, True, C_TEXT_DIM)
            surf.blit(ph, (inner.x, inner.y))
            return

        lines = self._wrap(font, inner.w)
        cur_line = self._line_of_cursor(lines)
        if cur_line < self._scroll:
            self._scroll = cur_line
        elif cur_line >= self._scroll + rows_visible:
            self._scroll = cur_line - rows_visible + 1
        self._scroll = max(0, min(self._scroll, max(0, len(lines) - 1)))

        old_clip = surf.get_clip()
        surf.set_clip(self.rect)
        y = inner.y
        for text, _ in lines[self._scroll:self._scroll + rows_visible + 1]:
            txt = font.render(text, True, C_TEXT)
            surf.blit(txt, (inner.x, y))
            y += line_h

        if self.active and self._blink < 0.6 and self._scroll <= cur_line < self._scroll + rows_visible:
            text, start = lines[cur_line]
            rel = max(0, min(self.cursor - start, len(text)))
            cx = inner.x + font.size(text[:rel])[0]
            cy = inner.y + (cur_line - self._scroll) * line_h
            pygame.draw.line(surf, C_TEXT, (cx, cy), (cx, cy + line_h - 2))
        surf.set_clip(old_clip)

        # Scroll hint so it's obvious there's more text than fits.
        if len(lines) > rows_visible:
            more = render_text_cached(
                font, f"{self._scroll+1}-{min(len(lines), self._scroll+rows_visible)}/{len(lines)}", C_TEXT_DIM
            )
            surf.blit(more, (self.rect.right - more.get_width() - 4, self.rect.y - more.get_height() - 2))


class Slider:
    """Integer or float slider with optional step."""
    H = 20

    def __init__(self, rect: pygame.Rect, min_val: float, max_val: float,
                 value: float, step: float = 1.0, fmt: str = "{:.0f}"):
        self.rect    = rect
        self.min     = min_val
        self.max     = max_val
        self.value   = float(value)
        self.step    = step
        self.fmt     = fmt
        self._drag   = False

    def _val_from_x(self, x: int) -> float:
        t = (x - self.rect.x) / self.rect.w
        raw = self.min + t * (self.max - self.min)
        if self.step:
            raw = round(raw / self.step) * self.step
        return max(self.min, min(self.max, raw))

    def handle_event(self, event: pygame.event.Event) -> bool:
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.rect.collidepoint(event.pos):
                self._drag = True
                self.value = self._val_from_x(event.pos[0])
                return True
        if event.type == pygame.MOUSEBUTTONUP:
            self._drag = False
        if event.type == pygame.MOUSEMOTION and self._drag:
            self.value = self._val_from_x(event.pos[0])
            return True
        return False

    def draw(self, surf: pygame.Surface, font: pygame.font.Font) -> None:
        track = pygame.Rect(self.rect.x, self.rect.centery - 3,
                            self.rect.w, 6)
        pygame.draw.rect(surf, C_BAR_BG,  track, border_radius=3)
        t = (self.value - self.min) / (self.max - self.min)
        fill_w = int(track.w * t)
        if fill_w > 0:
            pygame.draw.rect(surf, C_BAR_FILL,
                             pygame.Rect(track.x, track.y, fill_w, 6),
                             border_radius=3)
        kx = self.rect.x + int(self.rect.w * t)
        ky = self.rect.centery
        pygame.draw.circle(surf, C_ACCENT, (kx, ky), 8)
        pygame.draw.circle(surf, C_BG,     (kx, ky), 5)
        val_txt = font.render(self.fmt.format(self.value), True, C_TEXT)
        surf.blit(val_txt, (self.rect.right + 8, self.rect.centery - val_txt.get_height() // 2))


class StatBar:
    """Read-only coloured bar for stat display.

    Note: not currently wired into any tab (the Stats tab uses plain
    Sliders instead) — kept as a ready-made widget for a future
    read-only stat summary view. `value` changes constantly wherever a
    live stat is shown, so its text is intentionally NOT cached (see
    render_text_cached notes above).
    """
    H = 18

    @staticmethod
    def draw(surf: pygame.Surface, font: pygame.font.Font,
             rect: pygame.Rect, value: int, max_val: int = 255) -> None:
        pygame.draw.rect(surf, C_BAR_BG,   rect, border_radius=3)
        t = max(0, min(1, value / max_val))
        # Colour gradient: blue → green → yellow → red
        r = int(min(255, 2 * 255 * t))
        g = int(min(255, 2 * 255 * (1 - t)))
        col = (max(30, 255 - int(200 * t)), max(80, int(160 * t)), C_BAR_FILL[2])
        fill = rect.copy(); fill.w = int(rect.w * t)
        if fill.w > 0:
            pygame.draw.rect(surf, C_BAR_FILL, fill, border_radius=3)
        txt = font.render(str(value), True, C_TEXT)
        surf.blit(txt, (rect.right + 6, rect.y + (rect.h - txt.get_height()) // 2))


def draw_button(surf: pygame.Surface, font: pygame.font.Font,
                rect: pygame.Rect, label: str,
                color=C_ACCENT, hover: bool = False,
                danger: bool = False) -> None:
    """Draw a rounded, optionally-hover-highlighted button with a caption.
    Every button on screen (Save, Delete, tab arrows, dialog Confirm/
    Cancel, ...) goes through here, so the caption text is cached — the
    same handful of labels get redrawn every single frame."""
    base = (200, 60, 60) if danger else color
    bg   = tuple(min(255, c + 30) for c in base) if hover else C_PANEL
    pygame.draw.rect(surf, bg, rect, border_radius=5)
    pygame.draw.rect(surf, base, rect, 1, border_radius=5)
    txt = render_text_cached(font, label, base if not hover else C_TEXT)
    surf.blit(txt, txt.get_rect(center=rect.center))


# ══════════════════════════════════════════════════════════════════════
#  Confirm dialog
# ══════════════════════════════════════════════════════════════════════

# Non-blocking modal dialogs are implemented as state held on CharacterCreator
# (self.dialog) and drawn/handled inline — see _handle_dialog_event /
# _draw_dialog below. This lets the editor live inside the host game's main
# loop like every other dev tool instead of running its own blocking loop.


# ══════════════════════════════════════════════════════════════════════
#  Sprite Preview Panel
# ══════════════════════════════════════════════════════════════════════

PREVIEW_SCALE = 2      # px scale for sprite display
ANIM_FPS      = 8.0    # walk-cycle playback speed

# ── Ground shadow (mirrors LayerManager._load_shadow / _get_scaled_shadow
# in draw_layers.py) ─────────────────────────────────────────────────────
# The real game shadow is a loaded sprite asset — assets/sprites/universal/
# shadow.png (or shadowbig.png for the legacy Player.shadow_size == 'big'
# case) — scaled to ~32% of the entity's shadow width, NOT a drawn ellipse
# or a tinted copy of the character sprite. The character creator's Shadow
# Size slider only ever sets shadow_width (cfg["shadow_size"] -> the numeric
# override), never the legacy 'small'/'big' string, so the preview always
# uses the small variant — same as every character actually does unless
# something else in-game explicitly flips Player.shadow_size to 'big'.
_SHADOW_SPRITE_CACHE: dict[str, pygame.Surface] = {}         # 'small'/'big' -> raw source
_SHADOW_SCALED_CACHE: dict[tuple[str, int], pygame.Surface] = {}  # (variant, target_w) -> scaled


def _load_shadow_sprite(big: bool = False) -> pygame.Surface:
    """Load (and cache) the universal shadow sprite, falling back to the
    same drawn ellipse LayerManager._load_shadow() falls back to if the
    asset is missing — so the preview matches the real renderer exactly
    instead of approximating its look."""
    key = "big" if big else "small"
    cached = _SHADOW_SPRITE_CACHE.get(key)
    if cached is not None:
        return cached
    path = UNIVERSAL_DIR / ("shadowbig.png" if big else "shadow.png")
    surf: Optional[pygame.Surface] = None
    if path.exists():
        try:
            surf = pygame.image.load(str(path)).convert_alpha()
        except Exception:
            surf = None
    if surf is None:
        w, h = (64, 20) if big else (32, 12)
        surf = pygame.Surface((w, h), pygame.SRCALPHA)
        pygame.draw.ellipse(surf, (0, 0, 0, 80), surf.get_rect())
    _SHADOW_SPRITE_CACHE[key] = surf
    return surf


def get_preview_shadow(shadow_width: float, big: bool = False) -> pygame.Surface:
    """Ground shadow scaled for the preview panel, using the exact same
    ~32%-of-width / aspect-locked-to-source-sprite math as
    LayerManager._get_scaled_shadow(), with PREVIEW_SCALE standing in for
    RENDER_SCALE (the preview's walk frames are scaled the same way).
    Cached per (variant, rounded target width) since shadow_width only
    actually changes while the Shadow Size slider is being dragged."""
    variant = "big" if big else "small"
    source  = _load_shadow_sprite(big)
    target_w = max(8, int(max(0, shadow_width) * PREVIEW_SCALE * 0.32))
    key = (variant, target_w)
    cached = _SHADOW_SCALED_CACHE.get(key)
    if cached is not None:
        return cached
    orig_w = max(1, source.get_width())
    orig_h = max(1, source.get_height())
    target_h = max(4, int(orig_h * target_w / orig_w))
    scaled = pygame.transform.scale(source, (target_w, target_h))
    _SHADOW_SCALED_CACHE[key] = scaled
    return scaled


class SpritePreview:
    def __init__(self, rect: pygame.Rect):
        self.rect    = rect
        self.frames: list[pygame.Surface] = []
        self.frame_i = 0.0
        self._char   = ""
        self._costume= ""
        # In-game px width of the ground shadow (character_creator.py's
        # cfg["shadow_size"] / the Shadow Size slider on the Identity tab —
        # this is Player.shadow_width, NOT the legacy 'small'/'big'
        # Player.shadow_size string, which this tool never sets). Kept as a
        # live-updatable field so dragging the slider updates the preview
        # immediately without needing a full sprite reload.
        self.shadow_width: float = 32
        # Entity height in game px, for the feet_y offset — see draw().
        # Falls back to the walk frame's own pixel height if unknown.
        self.entity_height: Optional[int] = None

    def load(self, char_id: str, costume: str) -> None:
        if char_id == self._char and costume == self._costume:
            return
        self._char   = char_id
        self._costume= costume
        raw = load_walk_frames(char_id, costume)
        if raw:
            self.frames = [
                pygame.transform.scale(
                    f,
                    (f.get_width() * PREVIEW_SCALE,
                     f.get_height() * PREVIEW_SCALE)
                )
                for f in raw
            ]
        else:
            self.frames = []
        self.frame_i = 0.0

    def update(self, dt: float) -> None:
        if self.frames:
            self.frame_i = (self.frame_i + dt * ANIM_FPS) % len(self.frames)

    def _blit_shadow(self, surf: pygame.Surface, cx: float, feet_y: float) -> None:
        """Blit the real shadow.png asset, scaled/positioned exactly like
        LayerManager._draw_shadow(): centred under the given feet point."""
        shadow = get_preview_shadow(self.shadow_width)
        sx = round(cx - shadow.get_width()  / 2)
        sy = round(feet_y - shadow.get_height() / 2)
        surf.blit(shadow, (sx, sy))

    def draw(self, surf: pygame.Surface, font_sm: pygame.font.Font) -> None:
        pygame.draw.rect(surf, C_PANEL_DARK, self.rect, border_radius=6)
        pygame.draw.rect(surf, C_BORDER,     self.rect, 1, border_radius=6)

        if self.frames:
            frame = self.frames[int(self.frame_i)]
            fx = self.rect.centerx - frame.get_width() // 2
            fy = self.rect.centery - frame.get_height() // 2

            # feet_y mirrors LayerManager._draw_shadow(): obj's vertical
            # anchor (here, the frame's vertical centre, matching how the
            # sprite is centred on obj.x/obj.y in-game) plus
            # entity_height * RENDER_SCALE / 2.25 (PREVIEW_SCALE standing in
            # for RENDER_SCALE here). entity_height defaults to the walk
            # frame's own raw pixel height when the real hitbox height
            # (Player.height) isn't known to this preview.
            raw_h = self.entity_height if self.entity_height is not None \
                else frame.get_height() / PREVIEW_SCALE
            feet_y = self.rect.centery + (raw_h * PREVIEW_SCALE) / 2.25

            # Shadow first (below the sprite), same draw order as
            # LayerManager.draw_all(): "_draw_shadow just before the entity".
            self._blit_shadow(surf, self.rect.centerx, feet_y)
            surf.blit(frame, (fx, fy))
            info = font_sm.render(
                f"frame {int(self.frame_i)+1}/{len(self.frames)}",
                True, C_TEXT_DIM
            )
            surf.blit(info, (self.rect.x + 6, self.rect.bottom - 20))
        else:
            # Placeholder silhouette
            ph_w, ph_h = 48 * PREVIEW_SCALE, 64 * PREVIEW_SCALE
            ph = pygame.Rect(
                self.rect.centerx - ph_w // 2,
                self.rect.centery - ph_h // 2,
                ph_w, ph_h
            )
            feet_y = self.rect.centery + (ph_h / 2.25)
            self._blit_shadow(surf, self.rect.centerx, feet_y)
            pygame.draw.rect(surf, C_BORDER, ph, border_radius=6)
            lbl = render_text_cached(font_sm, "no sprites", C_TEXT_DIM)
            surf.blit(lbl, lbl.get_rect(centerx=self.rect.centerx,
                                         top=ph.bottom + 6))


# ══════════════════════════════════════════════════════════════════════
#  Animation Grid Panel  (Preview tab)
# ══════════════════════════════════════════════════════════════════════

CELL_PAD        = 12   # gap between cells and panel edges
TARGET_CELL_W   = 200  # ideal cell width; actual is computed to fill the panel


class AnimationGridPanel:
    """
    Scrollable grid that shows every discovered animation (state × direction)
    for the current character / costume, each playing back independently.

    Layout is computed dynamically from self.rect so it always fills the full
    panel width regardless of resolution.
    """

    def __init__(self, rect: pygame.Rect):
        self.rect        = rect
        self.animations: dict[str, list[pygame.Surface]] = {}
        self._scaled:    dict[str, list[pygame.Surface]] = {}
        self._timers:    dict[str, float]                = {}
        self.scroll      = 0
        self._max_scroll = 0
        # computed layout (updated in _compute_layout)
        self._cols   = 4
        self._cell_w = TARGET_CELL_W
        self._cell_h = TARGET_CELL_W + 30

    # ── Layout ────────────────────────────────────────────────────────
    def set_rect(self, rect: pygame.Rect) -> None:
        """Update the panel rect, recomputing layout only if size changed."""
        size_changed = (rect.w != self.rect.w or rect.h != self.rect.h)
        self.rect = rect
        if size_changed and self.animations:
            self._compute_layout()
            self._rebuild_scaled()
            self._recalc_scroll()
            self.scroll = min(self.scroll, self._max_scroll)

    def _compute_layout(self) -> None:
        """Derive columns and cell dimensions from the current panel width."""
        avail_w = self.rect.w - CELL_PAD          # space for cells + gaps
        # how many cols fit at the target width?
        self._cols   = max(2, avail_w // (TARGET_CELL_W + CELL_PAD))
        # stretch cells to fill the full width evenly
        self._cell_w = (avail_w - self._cols * CELL_PAD) // self._cols
        self._cell_h = self._cell_w + 30          # a bit taller than wide

    # ── Data ──────────────────────────────────────────────────────────
    def load(self, animations: dict[str, list[pygame.Surface]]) -> None:
        self.animations = animations
        self._timers    = {name: 0.0 for name in animations}
        self.scroll     = 0
        self._compute_layout()
        self._rebuild_scaled()
        self._recalc_scroll()

    def _rebuild_scaled(self) -> None:
        """Pre-scale every frame to fit inside a cell (done once on load)."""
        self._scaled = {}
        label_h      = 22
        pad          = 10
        area_w = self._cell_w - pad * 2
        area_h = self._cell_h - label_h - pad * 2
        for name, frames in self.animations.items():
            scaled_frames: list[pygame.Surface] = []
            for f in frames:
                s  = min(area_w / max(f.get_width(), 1),
                         area_h / max(f.get_height(), 1))
                sw = max(1, int(f.get_width()  * s))
                sh = max(1, int(f.get_height() * s))
                scaled_frames.append(pygame.transform.scale(f, (sw, sh)))
            self._scaled[name] = scaled_frames

    def _recalc_scroll(self) -> None:
        n       = max(1, len(self.animations))
        rows    = math.ceil(n / self._cols)
        total_h = rows * (self._cell_h + CELL_PAD) + CELL_PAD
        self._max_scroll = max(0, total_h - self.rect.h)

    # ── Events ────────────────────────────────────────────────────────
    def handle_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.MOUSEWHEEL:
            if self.rect.collidepoint(pygame.mouse.get_pos()):
                self.scroll = max(0, min(self._max_scroll,
                                         self.scroll - event.y * 40))

    # ── Update ────────────────────────────────────────────────────────
    def update(self, dt: float) -> None:
        for name, frames in self.animations.items():
            self._timers[name] = (self._timers[name] + dt * ANIM_FPS) % len(frames)

    # ── Draw ──────────────────────────────────────────────────────────
    def draw(self, surf: pygame.Surface, font_sm: pygame.font.Font) -> None:
        old_clip = surf.get_clip()
        surf.set_clip(self.rect)

        if not self.animations:
            msg = render_text_cached(font_sm, "No animations found for this character / form.",
                                     C_TEXT_DIM)
            surf.blit(msg, msg.get_rect(center=self.rect.center))
            surf.set_clip(old_clip)
            return

        cw   = self._cell_w
        ch   = self._cell_h
        cols = self._cols
        x0   = self.rect.x + CELL_PAD
        y0   = self.rect.y + CELL_PAD - int(self.scroll)

        label_h = 22
        pad     = 10

        for i, name in enumerate(self.animations):
            col = i % cols
            row = i // cols
            cx  = x0 + col * (cw + CELL_PAD)
            cy  = y0 + row * (ch + CELL_PAD)

            if cy + ch < self.rect.top or cy > self.rect.bottom:
                continue

            cell = pygame.Rect(cx, cy, cw, ch)
            pygame.draw.rect(surf, C_PANEL_DARK, cell, border_radius=8)
            pygame.draw.rect(surf, C_BORDER,     cell, 1, border_radius=8)

            # Label (cached — the animation name itself never changes)
            lbl_text = name if len(name) <= 20 else name[:18] + "…"
            lbl = render_text_cached(font_sm, lbl_text, C_TEXT_DIM)
            surf.blit(lbl, (cell.x + (cw - lbl.get_width()) // 2, cell.y + 5))

            # Animated sprite — centred in the area below the label
            scaled_frames = self._scaled.get(name, [])
            if scaled_frames:
                frame      = scaled_frames[int(self._timers[name])]
                area_y     = cell.y + label_h
                area_h     = ch - label_h - pad
                fx = cell.x + (cw - frame.get_width())  // 2
                fy = area_y  + (area_h - frame.get_height()) // 2
                surf.blit(frame, (fx, fy))

            # Frame counter
            raw_frames = self.animations[name]
            fi  = int(self._timers[name]) + 1
            ctr = font_sm.render(f"{fi}/{len(raw_frames)}", True, C_BORDER)
            surf.blit(ctr, (cell.right - ctr.get_width() - 5,
                            cell.bottom - ctr.get_height() - 3))

        # Scrollbar
        if self._max_scroll > 0:
            total_h = self.rect.h + self._max_scroll
            bar_h   = max(24, int(self.rect.h * self.rect.h / total_h))
            bar_y   = self.rect.y + int(self.scroll / self._max_scroll
                                        * (self.rect.h - bar_h))
            bar = pygame.Rect(self.rect.right - 8, bar_y, 5, bar_h)
            pygame.draw.rect(surf, C_BORDER, bar, border_radius=3)

        surf.set_clip(old_clip)


# ══════════════════════════════════════════════════════════════════════
#  Tab renderers
# ══════════════════════════════════════════════════════════════════════

TAB_IDENTITY  = 0
TAB_STATS     = 1
TAB_ATTACKS   = 2
TAB_TRANSFORM = 3
TAB_PREVIEW   = 4
TAB_SETTINGS  = 5
TAB_NAMES     = ["Identity", "Stats", "Attacks", "Transformations", "Preview", "Settings"]


class CharacterEditor:
    """
    Manages widgets for all three tabs.
    Rebuilt whenever the selected character changes.
    """

    def __init__(self, panel: pygame.Rect, char_id: str, cfg: dict,
                 costumes: list[str], transform_forms: list[str] | None = None,
                 available_attacks: list[str] | None = None):
        self.panel           = panel
        self.char_id         = char_id
        self.cfg             = cfg
        self.costumes        = costumes
        # Form names shown in the Transformations-tab costume picker (e.g. ["ssj", "ssj2"]).
        # Stored separately from base costumes — these live under transformations/.
        self.transform_forms = transform_forms or []
        self.dirty           = False          # unsaved changes flag

        # Layout constants
        lx = panel.x + 20             # label column x
        fx = panel.x + 140            # field column x
        fw = panel.w - 160            # field width (leave room for val label)
        row_h = 42

        # ── Identity tab ────────────────────────────────────────────
        y = panel.y + 60
        self.name_input    = TextInput(pygame.Rect(fx, y, fw, 28), cfg["display_name"])

        y += row_h
        self.costume_idx   = costumes.index(cfg["costume"]) if cfg["costume"] in costumes else 0

        # Description box — rect is a placeholder here; _draw_identity()
        # repositions it every frame based on where the portrait preview
        # ends up, same pattern as name_input/shadow_slider above.
        self.desc_input = TextArea(pygame.Rect(fx, 0, fw, TextArea.H), cfg.get("description", ""))

        # Preview tab — must come after costume_idx is resolved.
        # Dropdown state for the form picker shown at the top of the Preview tab.
        self.preview_form_idx         = 0       # index into _all_preview_forms()
        self.preview_dropdown_open    = False
        self.preview_dropdown_scroll  = 0       # first visible row when list is open
        self._preview_dropdown_btn:   Optional[pygame.Rect]           = None
        self._preview_dropdown_rows:  list[tuple[str, pygame.Rect]]   = []
        self.preview_form_thumbnails: dict[str, list[pygame.Surface]] = {}
        self._preview_thumb_timers:   dict[str, float]                = {}
        self._preview_form_changed:   bool                            = False
        self.anim_grid = AnimationGridPanel(panel)
        self._reload_anim_grid(char_id, costumes[self.costume_idx] if costumes else "base")
        self._load_preview_thumbnails()

        y += row_h
        self.shadow_slider = Slider(
            pygame.Rect(fx, y + 6, fw - 50, 20),
            8, 96, cfg["shadow_size"], step=4
        )

        # ── Stats tab ───────────────────────────────────────────────
        stats = cfg["stats"]
        y0    = panel.y + 60
        self.stat_sliders: dict[str, Slider] = {}
        for i, key in enumerate(["max_hp", "max_ki", "power", "ki_power",
                                   "defense", "vitality", "speed", "ki_regen"]):
            sy = y0 + i * row_h
            self.stat_sliders[key] = Slider(
                pygame.Rect(fx, sy + 6, fw - 50, 20),
                1, 255, stats[key], step=1
            )

        # ── Attacks tab ─────────────────────────────────────────────
        atk = cfg["attacks"]

        y0 = panel.y + 60
        self.atk_sliders: dict[str, Slider] = {}
        specs = [
            ("blast_cost",       1, 100,   atk["blast_cost"],       1,    "{:.0f}"),
            ("beam_cost",        1, 200,   atk["beam_cost"],         1,    "{:.0f}"),
            ("melee_duration",   0.1, 3.0, atk["melee_duration"],   0.05, "{:.2f}s"),
            ("walk_speed",       50, 500,  atk["walk_speed"],        10,   "{:.0f}"),
            ("run_speed",        100, 800, atk["run_speed"],         10,   "{:.0f}"),
            ("fly_speed",        100,1200, atk["fly_speed"],         50,   "{:.0f}"),
        ]
        for i, (key, mn, mx, val, step, fmt) in enumerate(specs):
            sy = y0 + i * row_h
            self.atk_sliders[key] = Slider(
                pygame.Rect(fx, sy + 6, fw - 60, 20),
                mn, mx, val, step, fmt
            )

        # Equipped-attacks icon picker. available_attacks is the GLOBAL
        # roster from discover_attacks() (every folder under
        # assets/sprites/attacks/); equipped_attacks is this character's
        # subset of it, stored directly in cfg so toggling a button mutates
        # the save data in place — no separate flush() step needed.
        self.available_attacks = available_attacks or []
        atk.setdefault("equipped_attacks", [])
        atk.setdefault("charged_melee_style", "lunge")
        self._charged_melee_style_rect: Optional[pygame.Rect] = None
        self.equipped_attacks: list[str] = atk["equipped_attacks"]
        self.attack_btn_rects: dict[str, pygame.Rect] = {}   # rebuilt on demand, see _build_attack_grid
        self._attack_grid_y0 = 0
        self._attack_grid_cache_key = None   # see _build_attack_grid's memoization

        # ── Transformations tab ────────────────────────────────────
        # self.transformations is the FULL list for every costume this
        # character has; a given costume's transformations are the entries
        # whose "costume" field is "{that costume}/transformations/{form}".
        # Always go through visible_transformations() (scoped to whichever
        # costume is selected on the Identity tab) rather than indexing
        # this list directly — a costume's transformation should only ever
        # be visible/navigable while that costume itself is selected.
        self.cfg.setdefault("transformations", [])
        self.transformations: list[dict] = self.cfg["transformations"]
        self.transform_idx          = 0 if self.visible_transformations() else -1
        self.transform_costume_idx  = 0
        self.transform_name_input: Optional[TextInput] = None
        self.transform_sliders: dict[str, Slider] = {}
        self._load_transform_widgets()

        # ── Identity tab: portrait cycle ─────────────────────────────
        # Slowly cycles the Identity-tab portrait through the base look
        # plus every registered transformation — see _portrait_cycle_forms()
        # / _load_portrait() / _draw_identity_portrait().
        self.portrait_cache: dict[tuple[str, str], Optional[pygame.Surface]] = {}
        self.portrait_cycle_timer = 0.0

    # ── Transformation scoping ───────────────────────────────────────
    def _current_costume(self) -> str:
        """The base costume currently selected on the Identity tab."""
        return self.costumes[self.costume_idx] if self.costumes else "base"

    def _transforms_for_costume(self, costume: str) -> list[dict]:
        """Transformations that belong to `costume` — i.e. entries stored as
        '{costume}/transformations/{form}'. A costume's transformation is its
        own thing, distinct from the costume itself, and should never show up
        while a *different* costume is selected."""
        prefix = f"{costume}/transformations/"
        return [t for t in self.transformations if t.get("costume", "").startswith(prefix)]

    def visible_transformations(self) -> list[dict]:
        """Transformations to display/navigate on the Transformations tab:
        only those owned by the costume currently selected on the Identity tab."""
        return self._transforms_for_costume(self._current_costume())

    # ── Transformation widget (re)build ─────────────────────────────
    def _load_transform_widgets(self) -> None:
        """(Re)build the edit-form widgets for the currently selected
        transformation. Called whenever the selection, or the list itself,
        changes (add / remove / step)."""
        fx = self.panel.x + 140
        fw = self.panel.w - 160
        visible = self.visible_transformations()
        if not (0 <= self.transform_idx < len(visible)):
            self.transform_name_input = None
            self.transform_sliders    = {}
            return

        tf = visible[self.transform_idx]
        self.transform_name_input = TextInput(
            pygame.Rect(fx, 0, fw, 28), tf.get("display_name", "")
        )
        # The costume field is stored as e.g. "base/transformations/ssj".
        # Extract just the form name ("ssj") for the transform_forms picker index.
        saved_costume = tf.get("costume", "")
        if "/transformations/" in saved_costume:
            form_name = saved_costume.split("/transformations/")[-1]
        else:
            form_name = saved_costume
        picker_list = self.transform_forms
        self.transform_costume_idx = (
            picker_list.index(form_name)
            if form_name in picker_list else 0
        )
        self.transform_sliders = {
            "power_mult":   Slider(pygame.Rect(fx, 0, fw - 50, 20),
                                    0.5, 5.0, tf.get("power_mult", 1.0), 0.05, "{:.2f}x"),
            "defense_mult": Slider(pygame.Rect(fx, 0, fw - 50, 20),
                                    0.5, 5.0, tf.get("defense_mult", 1.0), 0.05, "{:.2f}x"),
            "speed_mult":   Slider(pygame.Rect(fx, 0, fw - 50, 20),
                                    0.5, 5.0, tf.get("speed_mult", 1.0), 0.05, "{:.2f}x"),
            "ki_drain":     Slider(pygame.Rect(fx, 0, fw - 50, 20),
                                    0.0, 50.0, tf.get("ki_drain", 0.0), 0.5, "{:.1f}/s"),
        }

    # ── Animation grid reload ──────────────────────────────────────
    # ── Preview helpers ────────────────────────────────────────────
    def _all_preview_forms(self) -> list[str]:
        """Flat list of every browsable form for the currently selected costume:
        the base costume itself first, then its transformation sub-folders as
        '{costume}/transformations/{name}'.
        This is the list the Preview-tab dropdown cycles through."""
        base_costume = self.costumes[self.costume_idx] if self.costumes else "base"
        forms: list[str] = [base_costume]
        for tf in self.transform_forms:
            forms.append(f"{base_costume}/transformations/{tf}")
        return forms

    THUMB_H       = 48    # thumbnail height inside the dropdown rows
    DROP_ROW_H    = 60    # total row height (thumbnail + padding)
    DROP_ROWS_VIS = 5     # max rows visible without scrolling

    def _load_preview_thumbnails(self) -> None:
        """Pre-load animated walk frames for every form so the dropdown
        can show a live sprite preview next to each entry."""
        self.preview_form_thumbnails = {}
        self._preview_thumb_timers   = {}
        for form in self._all_preview_forms():
            raw = load_walk_frames(self.char_id, form)
            if raw:
                scale = self.THUMB_H / max(raw[0].get_height(), 1)
                sw    = max(1, int(raw[0].get_width()  * scale))
                sh    = max(1, int(raw[0].get_height() * scale))
                self.preview_form_thumbnails[form] = [
                    pygame.transform.smoothscale(f, (sw, sh)) for f in raw
                ]
            else:
                ph = pygame.Surface((self.THUMB_H, self.THUMB_H), pygame.SRCALPHA)
                pygame.draw.rect(ph, C_PANEL_DARK, ph.get_rect(), border_radius=4)
                pygame.draw.rect(ph, C_BORDER,     ph.get_rect(), 1, border_radius=4)
                self.preview_form_thumbnails[form] = [ph]
            self._preview_thumb_timers[form] = 0.0

    def _reload_anim_grid(self, char_id: str, costume: str) -> None:
        anims = discover_animations(char_id, costume)
        self.anim_grid.set_rect(self.panel)   # keep rect in sync
        self.anim_grid.load(anims)

    # ── Sync widget values → cfg ───────────────────────────────────
    def flush(self) -> None:
        self.cfg["display_name"] = self.name_input.value.strip() or self.char_id
        self.cfg["description"]  = self.desc_input.value.strip()
        self.cfg["costume"]      = self.costumes[self.costume_idx]
        self.cfg["shadow_size"]  = int(self.shadow_slider.value)

        for key, sl in self.stat_sliders.items():
            self.cfg["stats"][key] = int(sl.value)

        self.cfg["attacks"]["ki_attack_mode"] = self.cfg["attacks"].get("ki_attack_mode", "blast")
        for key, sl in self.atk_sliders.items():
            if "duration" in key:
                self.cfg["attacks"][key] = round(sl.value, 3)
            else:
                self.cfg["attacks"][key] = int(sl.value)

        visible = self.visible_transformations()
        if 0 <= self.transform_idx < len(visible) and self.transform_name_input:
            tf = visible[self.transform_idx]
            tf["display_name"] = self.transform_name_input.value.strip() or tf.get("id", "")
            base_costume = self._current_costume()
            # A transformation's "costume" field always nests under the
            # costume that owns it — never a bare costume name, since a
            # costume is not itself a transformation.
            if self.transform_forms:
                form = self.transform_forms[self.transform_costume_idx]
                tf["costume"] = f"{base_costume}/transformations/{form}"
            else:
                tf.setdefault("costume", f"{base_costume}/transformations/{tf.get('id', '')}")
            for key, sl in self.transform_sliders.items():
                tf[key] = round(sl.value, 2) if key != "ki_drain" else round(sl.value, 1)

    # ── Event routing ──────────────────────────────────────────────
    def handle_event(self, event: pygame.event.Event, active_tab: int) -> None:
        changed = False
        if active_tab == TAB_IDENTITY:
            changed |= self.name_input.handle_event(event)
            changed |= self.desc_input.handle_event(event)
            changed |= self.shadow_slider.handle_event(event)
        elif active_tab == TAB_STATS:
            for sl in self.stat_sliders.values():
                changed |= sl.handle_event(event)
        elif active_tab == TAB_ATTACKS:
            for sl in self.atk_sliders.values():
                changed |= sl.handle_event(event)
        elif active_tab == TAB_TRANSFORM:
            if self.transform_name_input:
                changed |= self.transform_name_input.handle_event(event)
            for sl in self.transform_sliders.values():
                changed |= sl.handle_event(event)
        elif active_tab == TAB_PREVIEW:
            self.anim_grid.handle_event(event)
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                mx, my    = event.pos
                all_forms = self._all_preview_forms()
                if self._preview_dropdown_btn and self._preview_dropdown_btn.collidepoint(mx, my):
                    self.preview_dropdown_open = not self.preview_dropdown_open
                elif self.preview_dropdown_open:
                    hit = False
                    for form, rect in self._preview_dropdown_rows:
                        if rect.collidepoint(mx, my):
                            idx = all_forms.index(form) if form in all_forms else 0
                            if idx != self.preview_form_idx:
                                self.preview_form_idx = idx
                                self._reload_anim_grid(self.char_id, form)
                                self._preview_form_changed = True
                            self.preview_dropdown_open = False
                            hit = True
                            break
                    if not hit:
                        self.preview_dropdown_open = False
            elif event.type == pygame.MOUSEWHEEL and self.preview_dropdown_open:
                all_forms  = self._all_preview_forms()
                max_scroll = max(0, len(all_forms) - self.DROP_ROWS_VIS)
                self.preview_dropdown_scroll = max(
                    0, min(max_scroll, self.preview_dropdown_scroll - event.y)
                )
        if changed:
            self.dirty = True

    # ── Draw ───────────────────────────────────────────────────────
    def draw(self, surf: pygame.Surface,
             font: pygame.font.Font, font_sm: pygame.font.Font,
             active_tab: int, dt: float) -> None:
        lx = self.panel.x + 20
        fx = self.panel.x + 140
        fw = self.panel.w - 160
        row_h = 42

        if active_tab == TAB_IDENTITY:
            self._draw_identity(surf, font, font_sm, lx, fx, fw, row_h, dt)
        elif active_tab == TAB_STATS:
            self._draw_stats(surf, font, font_sm, lx, fx, fw, row_h)
        elif active_tab == TAB_ATTACKS:
            self._draw_attacks(surf, font, font_sm, lx, fx, fw, row_h)
        elif active_tab == TAB_TRANSFORM:
            self._draw_transformations(surf, font, font_sm, lx, fx, fw, row_h, dt)
        elif active_tab == TAB_PREVIEW:
            self._draw_preview(surf, font, font_sm, dt)

    def _draw_preview(self, surf: pygame.Surface,
                      font: pygame.font.Font, font_sm: pygame.font.Font,
                      dt: float) -> None:
        """Preview tab: animated-thumbnail dropdown + full animation grid."""
        lx        = self.panel.x + 20
        mx, my    = pygame.mouse.get_pos()
        all_forms = self._all_preview_forms()
        n         = len(all_forms)

        # Advance thumbnail timers so they animate while the dropdown is open
        for form, frames in self.preview_form_thumbnails.items():
            self._preview_thumb_timers[form] = (
                self._preview_thumb_timers.get(form, 0.0) + dt * ANIM_FPS
            ) % max(len(frames), 1)

        # ── Dropdown button ──────────────────────────────────────────
        BTN_H    = 32
        BTN_W    = min(320, self.panel.w - 40)
        btn_rect = pygame.Rect(lx, self.panel.y + 10, BTN_W, BTN_H)
        self._preview_dropdown_btn = btn_rect

        current = all_forms[self.preview_form_idx] if all_forms else ""
        display = current.split("/")[-1] if "/" in current else current or "—"
        counter = f"  {self.preview_form_idx + 1}/{n}" if n > 1 else ""

        hov_btn  = btn_rect.collidepoint(mx, my)
        btn_bg   = C_HOVER if (hov_btn or self.preview_dropdown_open) else C_PANEL_DARK
        btn_bord = C_ACCENT if self.preview_dropdown_open else C_BORDER
        pygame.draw.rect(surf, btn_bg,   btn_rect, border_radius=5)
        pygame.draw.rect(surf, btn_bord, btn_rect, 1, border_radius=5)
        lbl = render_text_cached(font, display + counter, C_TEXT)
        surf.blit(lbl, (btn_rect.x + 10,
                        btn_rect.y + (BTN_H - lbl.get_height()) // 2))
        arrow = render_text_cached(font_sm, "▴" if self.preview_dropdown_open else "▾", C_TEXT_DIM)
        surf.blit(arrow, (btn_rect.right - arrow.get_width() - 10,
                          btn_rect.y + (BTN_H - arrow.get_height()) // 2))

        # ── Animation grid (always drawn, behind the dropdown) ───────
        HEADER_H  = BTN_H + 18
        grid_rect = pygame.Rect(self.panel.x, self.panel.y + HEADER_H,
                                self.panel.w, self.panel.h - HEADER_H)
        self.anim_grid.set_rect(grid_rect)
        self.anim_grid.update(dt)
        self.anim_grid.draw(surf, font_sm)

        # ── Dropdown list (floats on top of grid when open) ──────────
        if not self.preview_dropdown_open or not all_forms:
            self._preview_dropdown_rows = []
            return

        ROW_H     = self.DROP_ROW_H
        VIS       = self.DROP_ROWS_VIS
        THUMB_W   = self.THUMB_H
        PAD       = 6
        DROP_W    = max(BTN_W, 280)
        vis_count = min(n, VIS)
        drop_h    = vis_count * ROW_H + PAD * 2
        drop_rect = pygame.Rect(lx, btn_rect.bottom + 2, DROP_W, drop_h)

        pygame.draw.rect(surf, C_PANEL,  drop_rect, border_radius=6)
        pygame.draw.rect(surf, C_ACCENT, drop_rect, 1, border_radius=6)

        self._preview_dropdown_rows = []
        for vis_i in range(vis_count):
            form_i = self.preview_dropdown_scroll + vis_i
            if form_i >= n:
                break
            form = all_forms[form_i]

            ry        = drop_rect.y + PAD + vis_i * ROW_H
            item_rect = pygame.Rect(drop_rect.x + PAD, ry,
                                    DROP_W - PAD * 2, ROW_H - PAD)
            self._preview_dropdown_rows.append((form, item_rect))

            is_sel    = (form_i == self.preview_form_idx)
            is_hov    = item_rect.collidepoint(mx, my)
            item_bg   = C_SELECTED if is_sel else (C_HOVER if is_hov else C_PANEL_DARK)
            item_bord = C_ACCENT   if is_sel else C_BORDER
            pygame.draw.rect(surf, item_bg,   item_rect, border_radius=5)
            pygame.draw.rect(surf, item_bord, item_rect, 1, border_radius=5)

            # Animated walk thumbnail
            frames = self.preview_form_thumbnails.get(form, [])
            if frames:
                frame     = frames[int(self._preview_thumb_timers.get(form, 0)) % len(frames)]
                thumb_box = pygame.Rect(item_rect.x + 4,
                                        item_rect.y + (item_rect.h - self.THUMB_H) // 2,
                                        THUMB_W, self.THUMB_H)
                tx = thumb_box.x + (THUMB_W - frame.get_width())  // 2
                ty = thumb_box.y + (self.THUMB_H - frame.get_height()) // 2
                surf.blit(frame, (tx, ty))
            else:
                thumb_box = pygame.Rect(item_rect.x + 4, item_rect.y + 4, THUMB_W, self.THUMB_H)

            form_display = form.split("/")[-1] if "/" in form else form
            name_lbl = render_text_cached(font, form_display,
                                          C_TEXT if is_sel else C_TEXT_DIM)
            surf.blit(name_lbl, (thumb_box.right + 10,
                                 item_rect.y + (item_rect.h - name_lbl.get_height()) // 2))

        # Scroll indicators
        if self.preview_dropdown_scroll > 0:
            up_txt = render_text_cached(font_sm, "▲", C_TEXT_DIM)
            surf.blit(up_txt, (drop_rect.right - up_txt.get_width() - 6,
                               drop_rect.y + 2))
        if self.preview_dropdown_scroll + VIS < n:
            dn_txt = render_text_cached(font_sm, "▼", C_TEXT_DIM)
            surf.blit(dn_txt, (drop_rect.right - dn_txt.get_width() - 6,
                               drop_rect.bottom - dn_txt.get_height() - 2))

    # ── Identity tab: portrait cycle ────────────────────────────────
    PORTRAIT_BOX          = 120   # px square the portrait preview is framed in
    PORTRAIT_HOLD_SECONDS = 2.5   # how long each form's portrait is shown

    def _portrait_cycle_forms(self) -> list[tuple[str, str]]:
        """(form_suffix, display_label) pairs to cycle through in the
        Identity-tab portrait preview: the currently selected costume's
        base look, followed only by *that costume's own* registered
        transformations, in order. A different costume's transformation
        never appears here — it isn't relevant until that costume is
        selected.

        form_suffix is "" for the base look (portrait file has no suffix,
        e.g. "goku.png") and the bare form name for a transformation (e.g.
        "ssj" → "goku_ssj.png"), matching assets/portraits/{char_id}[_{form}].png.
        """
        forms: list[tuple[str, str]] = [("", "Base")]
        for tf in self.visible_transformations():
            costume = tf.get("costume", "")
            form = costume.split("/")[-1] if costume else ""
            if form:
                forms.append((form, tf.get("display_name") or tf.get("id", "?")))
        return forms

    def _load_portrait(self, form: str) -> Optional[pygame.Surface]:
        """Load (and cache) the portrait for the currently selected costume
        + a given transformation form (see resolve_portrait_path())."""
        costume = self._current_costume()
        cache_key = (costume, form)
        if cache_key in self.portrait_cache:
            return self.portrait_cache[cache_key]
        surf = None
        path = resolve_portrait_path(self.char_id, costume, form)
        if path:
            try:
                surf = pygame.image.load(str(path)).convert_alpha()
            except Exception:
                surf = None
        self.portrait_cache[cache_key] = surf
        return surf

    def _draw_identity_portrait(self, surf: pygame.Surface,
                                font_sm: pygame.font.Font,
                                x: int, y: int, dt: float) -> None:
        forms = self._portrait_cycle_forms()
        n     = len(forms)
        box   = self.PORTRAIT_BOX

        self.portrait_cycle_timer = (
            (self.portrait_cycle_timer + dt) % (self.PORTRAIT_HOLD_SECONDS * n)
        )
        idx = int(self.portrait_cycle_timer // self.PORTRAIT_HOLD_SECONDS) % n
        form, label = forms[idx]

        rect = pygame.Rect(x, y, box, box)
        pygame.draw.rect(surf, C_PANEL_DARK, rect, border_radius=8)
        pygame.draw.rect(surf, C_BORDER,     rect, 1, border_radius=8)

        img = self._load_portrait(form)
        if img:
            s  = min((box - 12) / max(img.get_width(), 1),
                     (box - 12) / max(img.get_height(), 1))
            sw = max(1, int(img.get_width()  * s))
            sh = max(1, int(img.get_height() * s))
            scaled = pygame.transform.smoothscale(img, (sw, sh))
            surf.blit(scaled, scaled.get_rect(center=rect.center))
        else:
            ph = render_text_cached(font_sm, "no portrait", C_TEXT_DIM)
            surf.blit(ph, ph.get_rect(center=rect.center))

        cap_txt = f"{label}   ({idx + 1}/{n})" if n > 1 else label
        cap = render_text_cached(font_sm, cap_txt, C_TEXT_DIM)
        surf.blit(cap, (rect.x, rect.bottom + 8))

        # Progress dots — one per form, filled for whichever is on screen.
        if n > 1:
            dot_r   = 3
            gap     = 10
            total_w = (n - 1) * gap
            dx      = rect.centerx - total_w // 2
            dy      = rect.bottom + 28
            for i in range(n):
                col = C_ACCENT if i == idx else C_BORDER
                pygame.draw.circle(surf, col, (dx + i * gap, dy), dot_r)

    def _draw_identity(self, surf, font, font_sm, lx, fx, fw, row_h, dt):
        y = self.panel.y + 60

        draw_label(surf, font_sm, "ID (read-only)", lx, y + 6)
        id_txt = render_text_cached(font, self.char_id, C_ACCENT)
        surf.blit(id_txt, (fx, y + 4))
        y += row_h

        draw_label(surf, font_sm, "Display Name", lx, y + 6)
        self.name_input.rect.y = y
        self.name_input.draw(surf, font_sm, dt)
        y += row_h

        draw_label(surf, font_sm, "Costume", lx, y + 6)
        # Cycle arrows
        arr_l = pygame.Rect(fx,        y + 2, 26, 26)
        arr_r = pygame.Rect(fx + 160,  y + 2, 26, 26)
        mx, my = pygame.mouse.get_pos()
        draw_button(surf, font_sm, arr_l, "◄",
                    hover=arr_l.collidepoint(mx, my))
        draw_button(surf, font_sm, arr_r, "►",
                    hover=arr_r.collidepoint(mx, my))
        costume_lbl = render_text_cached(
            font, self.costumes[self.costume_idx] if self.costumes else "—", C_TEXT
        )
        surf.blit(costume_lbl, (fx + 34, y + 5))
        y += row_h

        draw_label(surf, font_sm, "Shadow Size", lx, y + 6)
        self.shadow_slider.rect.y = y + 6
        self.shadow_slider.draw(surf, font_sm)
        y += row_h

        # ── Description — freeform prose shown in the Scouter Data panel
        # (see ui/scouter_menu.py's _get_entity_description). Lives on the
        # Identity tab, next to the other "who is this character" fields,
        # rather than tucked into Stats/Attacks/Transformations. ─────────
        y += 14
        draw_label(surf, font_sm, "Description", lx, y + 6)
        self.desc_input.rect = pygame.Rect(fx, y, fw, TextArea.H)
        self.desc_input.draw(surf, font_sm, dt)
        y += TextArea.H + row_h - 28

        # ── Portrait preview — cycles slowly through the base look and
        # every registered transformation, so you can sanity-check each
        # form's portrait art without leaving the Identity tab. ─────────
        y += 14
        draw_section_header(surf, font_sm, "Portrait Preview",
                            pygame.Rect(lx, y + 4, fw + 80, 0))
        y += 30
        self._draw_identity_portrait(surf, font_sm, lx, y, dt)

    def _draw_stats(self, surf, font, font_sm, lx, fx, fw, row_h):
        LABELS = {
            "max_hp":   "Max HP",
            "max_ki":   "Max Ki",
            "power":    "STR (Melee)",
            "ki_power": "POW (Ki Blast)",
            "defense":  "Defense (legacy)",
            "vitality": "END (Defense)",
            "speed":    "SPD",
            "ki_regen": "Ki Regen",
        }
        y = self.panel.y + 60
        for key, sl in self.stat_sliders.items():
            draw_label(surf, font_sm, LABELS[key], lx, y + 6)
            sl.rect.y = y + 6
            sl.draw(surf, font_sm)
            y += row_h

    def _build_attack_grid(self) -> None:
        """
        (Re)compute icon-button rects for every discovered attack.

        Called from _draw_attacks every frame so it always matches the
        current panel size; the click handler in CharacterCreator.
        handle_input() reads the same self.attack_btn_rects, so drawing
        and hit-testing can never drift out of sync with each other.

        PERF: the layout only actually depends on the attack roster, the
        panel width, and the grid's top y-offset — none of which change
        from one frame to the next while the Attacks tab just sits open.
        We memoize on those three things and skip the recompute (which
        touches every attack, doing a dict rebuild + N Rect allocations)
        when nothing has actually moved.

        Icon size is derived from the actual pixel dimensions of the first
        available icon (HUD PNGs are already sized for display and must not
        be scaled), falling back to ATTACK_ICON_SIZE if nothing is loaded yet.
        """
        cache_key = (tuple(self.available_attacks), self.panel.w, self._attack_grid_y0)
        if getattr(self, "_attack_grid_cache_key", None) == cache_key:
            return  # layout unchanged since last frame — nothing to do
        self._attack_grid_cache_key = cache_key

        self.attack_btn_rects = {}
        if not self.available_attacks:
            return

        # Determine cell size from the real icon surface dimensions so that
        # high-res HUD PNGs are shown at their native resolution.
        first_icon = load_attack_icon(self.available_attacks[0])
        icon_w     = first_icon.get_width()
        icon_h     = first_icon.get_height()

        lx      = self.panel.x + 20
        avail_w = max(icon_w, self.panel.w - 40)
        gap     = 16
        cell_w  = icon_w + gap
        cols    = max(1, (avail_w + gap) // cell_w)
        cell_h  = icon_h + 30   # icon + label row + gap to next row

        for i, aid in enumerate(self.available_attacks):
            col = i % cols
            row = i // cols
            x = lx + col * cell_w
            y = self._attack_grid_y0 + row * cell_h
            self.attack_btn_rects[aid] = pygame.Rect(x, y, icon_w, icon_h)

    def _draw_attacks(self, surf, font, font_sm, lx, fx, fw, row_h):
        y = self.panel.y + 60

        LABELS = {
            "blast_cost":      "Blast Ki Cost",
            "beam_cost":       "Beam Ki Cost",
            "melee_duration":  "Melee Duration",
            "walk_speed":      "Walk Speed",
            "run_speed":       "Run Speed",
            "fly_speed":       "Fly Speed",
        }
        for key, sl in self.atk_sliders.items():
            draw_label(surf, font_sm, LABELS[key], lx, y + 6)
            sl.rect.y = y + 6
            sl.draw(surf, font_sm)
            y += row_h

        # ── Charged Melee style ──────────────────────────────────────
        # Holding the melee attack button (see Player.start_charging_melee)
        # rolls into either a forward lunge or a rooted in-place spin once
        # fully charged — pick which one this character uses. Read by
        # Game._reload_attack_config() into player.charged_melee_style.
        y += 14
        mx, my = pygame.mouse.get_pos()
        draw_label(surf, font_sm, "Charged Melee Style", lx, y + 6)
        style = self.cfg["attacks"].get("charged_melee_style", "lunge")
        style_btn = pygame.Rect(fx, y + 2, 140, 28)
        self._charged_melee_style_rect = style_btn
        draw_button(surf, font_sm, style_btn, "Lunge" if style == "lunge" else "Spin",
                   hover=style_btn.collidepoint(mx, my))
        y += row_h

        # ── Equipped Attacks (icon picker) ──────────────────────────
        # Click an icon to toggle whether this character has that attack
        # equipped — see discover_attacks() for where the roster comes
        # from, and the TAB_ATTACKS block in CharacterCreator.handle_input
        # for the click handling that pairs with this layout.
        y += 14
        draw_section_header(surf, font_sm, "Equipped Attacks", pygame.Rect(lx, y + 4, fw + 80, 0))
        y += 30

        if not self.available_attacks:
            hint = render_text_cached(
                font_sm,
                "No attacks found in assets/sprites/attacks/ — add an attack "
                "folder (e.g. 'ki_blast') to populate this list.",
                C_TEXT_DIM,
            )
            surf.blit(hint, (lx, y + 4))
            return

        self._attack_grid_y0 = y
        self._build_attack_grid()

        mx, my = pygame.mouse.get_pos()
        for aid, icon_rect in self.attack_btn_rects.items():
            selected = aid in self.equipped_attacks
            hovered  = icon_rect.collidepoint(mx, my)

            # Load at native size — HUD icons are already display-ready.
            icon = load_attack_icon(aid)
            # Re-use the icon's actual dimensions for the drawn rect in case
            # icons have varying sizes (future-proofing).
            draw_rect = pygame.Rect(icon_rect.x, icon_rect.y,
                                    icon.get_width(), icon.get_height())
            frame      = draw_rect.inflate(8, 8)
            bg_col     = C_SELECTED if selected else (C_HOVER if hovered else C_PANEL_DARK)
            border_col = C_ACCENT   if selected else C_BORDER
            pygame.draw.rect(surf, bg_col, frame, border_radius=6)
            pygame.draw.rect(surf, border_col, frame, 2 if selected else 1, border_radius=6)
            surf.blit(icon, draw_rect.topleft)

            # Cached: attack id is fixed, "selected" only ever toggles
            # between two colours, so the label set is small and stable.
            label = render_text_cached(
                font_sm, aid.replace("_", " ").title(), C_TEXT if selected else C_TEXT_DIM
            )
            surf.blit(label, label.get_rect(centerx=draw_rect.centerx, top=draw_rect.bottom + 6))

        bottom = max(r.bottom for r in self.attack_btn_rects.values()) + 24
        hint = render_text_cached(
            font_sm, "Click an icon to equip / unequip that attack for this character.", C_TEXT_DIM
        )
        surf.blit(hint, (lx, bottom))

    def _draw_transformations(self, surf, font, font_sm, lx, fx, fw, row_h, dt):
        mx, my  = pygame.mouse.get_pos()
        visible = self.visible_transformations()
        has_tf  = bool(visible)
        y = self.panel.y + 60

        # ── Stepper: step through this costume's own transformations ──
        draw_label(surf, font_sm, f"Preview ({self._current_costume()})", lx, y + 6)
        arr_l = pygame.Rect(fx,       y + 2, 26, 26)
        arr_r = pygame.Rect(fx + 160, y + 2, 26, 26)
        draw_button(surf, font_sm, arr_l, "◄", hover=has_tf and arr_l.collidepoint(mx, my))
        draw_button(surf, font_sm, arr_r, "►", hover=has_tf and arr_r.collidepoint(mx, my))
        if has_tf:
            tf = visible[self.transform_idx]
            name_txt = tf.get("display_name") or tf.get("id", "—")
            counter  = f"({self.transform_idx + 1}/{len(visible)})"
        else:
            name_txt, counter = "— none —", ""
        surf.blit(render_text_cached(font, name_txt, C_TEXT), (fx + 34, y + 5))
        if counter:
            surf.blit(render_text_cached(font_sm, counter, C_TEXT_DIM), (fx + 200, y + 8))
        y += row_h

        # ── Add / Remove ─────────────────────────────────────────────
        btn_add    = pygame.Rect(fx,       y + 2, 150, 28)
        btn_remove = pygame.Rect(fx + 160, y + 2, 110, 28)
        draw_button(surf, font_sm, btn_add, "+ Add Transformation",
                    hover=btn_add.collidepoint(mx, my))
        if has_tf:
            draw_button(surf, font_sm, btn_remove, "Remove", danger=True,
                        hover=btn_remove.collidepoint(mx, my))
        y += row_h

        if not has_tf:
            hint = render_text_cached(
                font_sm,
                f"'{self._current_costume()}' has no transformations yet — click "
                "+ Add Transformation (e.g. 'ssj', 'ssj2', 'kaioken') to create one.",
                C_TEXT_DIM,
            )
            surf.blit(hint, (lx, y + 10))
            return

        draw_section_header(surf, font_sm, "Edit Selected",
                            pygame.Rect(lx, y + 14, fw + 80, 0))
        y += 30

        draw_label(surf, font_sm, "Display Name", lx, y + 6)
        if self.transform_name_input:
            self.transform_name_input.rect.y = y
            self.transform_name_input.draw(surf, font_sm, dt)
        y += row_h

        draw_label(surf, font_sm, "Form", lx, y + 6)
        c_arr_l = pygame.Rect(fx,       y + 2, 26, 26)
        c_arr_r = pygame.Rect(fx + 160, y + 2, 26, 26)
        draw_button(surf, font_sm, c_arr_l, "◄", hover=c_arr_l.collidepoint(mx, my))
        draw_button(surf, font_sm, c_arr_r, "►", hover=c_arr_r.collidepoint(mx, my))
        picker_list = self.transform_forms
        form_display = picker_list[self.transform_costume_idx] if picker_list else "—"
        costume_lbl = render_text_cached(font, form_display, C_TEXT)
        surf.blit(costume_lbl, (fx + 34, y + 5))
        y += row_h

        LABELS = {
            "power_mult":   "Power x",
            "defense_mult": "Defense x",
            "speed_mult":   "Speed x",
            "ki_drain":     "Ki Drain /s",
        }
        for key in ("power_mult", "defense_mult", "speed_mult", "ki_drain"):
            sl = self.transform_sliders.get(key)
            if not sl:
                continue
            draw_label(surf, font_sm, LABELS[key], lx, y + 6)
            sl.rect.y = y + 6
            sl.draw(surf, font_sm)
            y += row_h


# ══════════════════════════════════════════════════════════════════════
#  Character List Panel (left sidebar)
# ══════════════════════════════════════════════════════════════════════

class CharacterList:
    ITEM_H = 36

    def __init__(self, rect: pygame.Rect):
        self.rect        = rect
        self.chars: list[str] = []
        self.selected    = ""
        self.scroll      = 0
        self._hovered    = ""

        # Reorder controls (▲/▼ buttons in the reserved bottom strip).
        # order_changed is set True right after a successful move; the
        # owner (CharacterCreator) checks it each frame and is
        # responsible for persisting self.chars via save_character_order()
        # and clearing the flag.
        self.order_changed = False

    def set_chars(self, chars: list[str], selected: str = "") -> None:
        self.chars    = chars
        self.selected = selected or (chars[0] if chars else "")

    def _reorder_button_rects(self) -> tuple[pygame.Rect, pygame.Rect]:
        """Rects for the ▲ Up / ▼ Down buttons, in the strip reserved
        below the scrollable item list."""
        bar_y = self.rect.bottom - 38
        half  = (self.rect.w - 16) // 2
        btn_up   = pygame.Rect(self.rect.x + 6, bar_y, half, 28)
        btn_down = pygame.Rect(btn_up.right + 4, bar_y, half, 28)
        return btn_up, btn_down

    def move_selected(self, delta: int) -> bool:
        """Swap the selected character with its neighbor delta steps away
        (-1 = up, +1 = down). Returns True if a swap happened."""
        if not self.selected or self.selected not in self.chars:
            return False
        i = self.chars.index(self.selected)
        j = i + delta
        if not (0 <= j < len(self.chars)):
            return False
        self.chars[i], self.chars[j] = self.chars[j], self.chars[i]
        self._ensure_visible(j)
        return True

    def _ensure_visible(self, index: int) -> None:
        """Scroll just enough so the row at `index` is on-screen."""
        visible_count = max(1, (self.rect.h - 80) // self.ITEM_H)
        if index < self.scroll:
            self.scroll = index
        elif index >= self.scroll + visible_count:
            self.scroll = index - visible_count + 1
        self.scroll = max(0, min(self.scroll, max(0, len(self.chars) - 1)))

    def handle_event(self, event: pygame.event.Event) -> Optional[str]:
        """Returns char_id if selection changed, else None."""
        visible_rect = self.rect.inflate(0, -80)   # leave room for button
        if event.type == pygame.MOUSEWHEEL:
            if visible_rect.collidepoint(pygame.mouse.get_pos()):
                self.scroll = max(0, min(
                    len(self.chars) - 1,
                    self.scroll - event.y
                ))
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mx, my = event.pos

            btn_up, btn_down = self._reorder_button_rects()
            if btn_up.collidepoint(mx, my):
                if self.move_selected(-1):
                    self.order_changed = True
                return None
            if btn_down.collidepoint(mx, my):
                if self.move_selected(1):
                    self.order_changed = True
                return None

            for i, cid in enumerate(self.chars):
                item_y = visible_rect.y + (i - self.scroll) * self.ITEM_H
                item_r = pygame.Rect(visible_rect.x, item_y,
                                     visible_rect.w, self.ITEM_H)
                if item_r.collidepoint(mx, my) and visible_rect.collidepoint(mx, my):
                    if self.selected != cid:
                        self.selected = cid
                        return cid
        return None

    def draw(self, surf: pygame.Surface, font: pygame.font.Font,
             font_sm: pygame.font.Font, dirty_id: str) -> None:
        pygame.draw.rect(surf, C_PANEL_DARK, self.rect, border_radius=6)
        pygame.draw.rect(surf, C_BORDER,     self.rect, 1, border_radius=6)

        hdr = render_text_cached(font_sm, "CHARACTERS", C_TEXT_DIM)
        surf.blit(hdr, (self.rect.x + 12, self.rect.y + 10))

        mx, my = pygame.mouse.get_pos()
        visible_rect = pygame.Rect(self.rect.x, self.rect.y + 34,
                                   self.rect.w, self.rect.h - 80)

        old_clip = surf.get_clip()
        surf.set_clip(visible_rect)

        for i, cid in enumerate(self.chars):
            item_y = visible_rect.y + (i - self.scroll) * self.ITEM_H
            item_r = pygame.Rect(visible_rect.x + 2, item_y,
                                 visible_rect.w - 4, self.ITEM_H - 2)
            if item_y < visible_rect.top - self.ITEM_H:
                continue
            if item_y > visible_rect.bottom:
                break

            is_sel = (cid == self.selected)
            is_hov = item_r.collidepoint(mx, my)
            bg = C_SELECTED if is_sel else (C_HOVER if is_hov else C_PANEL_DARK)
            pygame.draw.rect(surf, bg, item_r, border_radius=4)

            dot_col = C_ACCENT2 if cid == dirty_id else C_TEXT_DIM
            pygame.draw.circle(surf, dot_col,
                               (item_r.x + 12, item_r.centery), 4)

            # Cached: character ids are a small, fixed set, and each one
            # only ever appears in the selected/unselected colour.
            lbl = render_text_cached(font, cid, C_TEXT if is_sel else C_TEXT_DIM)
            surf.blit(lbl, (item_r.x + 24, item_r.y + (item_r.h - lbl.get_height()) // 2))

        surf.set_clip(old_clip)

        # ── Reorder controls ──────────────────────────────────────
        btn_up, btn_down = self._reorder_button_rects()
        pygame.draw.line(surf, C_BORDER,
                         (self.rect.x + 4, btn_up.y - 6),
                         (self.rect.right - 4, btn_up.y - 6))

        idx      = self.chars.index(self.selected) if self.selected in self.chars else -1
        can_up   = idx > 0
        can_down = idx != -1 and idx < len(self.chars) - 1

        draw_button(surf, font_sm, btn_up, "▲ Up",
                   color=C_ACCENT if can_up else C_TEXT_DIM,
                   hover=can_up and btn_up.collidepoint(mx, my))
        draw_button(surf, font_sm, btn_down, "▼ Down",
                   color=C_ACCENT if can_down else C_TEXT_DIM,
                   hover=can_down and btn_down.collidepoint(mx, my))


# ══════════════════════════════════════════════════════════════════════
#  Main entry point — non-blocking overlay (matches SpriteEditor /
#  CutsceneEditor / WorldMapEditor pattern: toggle() / handle_input() /
#  update(dt) / draw(screen), driven each frame by the host game loop).
# ══════════════════════════════════════════════════════════════════════

HEADER_H  = 44
FOOTER_H  = 52
LIST_W    = 190
PREVIEW_H = 220
TAB_H     = 34
PAD       = 8


class CharacterCreator:
    """
    Dev-tool overlay for browsing / editing character configs and previewing
    every discovered animation. Lives inside the host game's main loop —
    it does not own the display, event polling, or the clock.
    """

    def __init__(self, screen_width: int, screen_height: int):
        self.screen_width  = screen_width
        self.screen_height = screen_height
        self.active = False

        pygame.font.init()
        try:
            self.font    = pygame.font.SysFont("segoeui,dejavusans,arial", 16, bold=False)
            self.font_sm = pygame.font.SysFont("segoeui,dejavusans,arial", 13, bold=False)
            self.font_hd = pygame.font.SysFont("segoeui,dejavusans,arial", 20, bold=True)
        except Exception:
            self.font = self.font_sm = self.font_hd = pygame.font.Font(None, 18)

        self._build_layout()

        # ── State ───────────────────────────────────────────────────
        self.active_tab = TAB_IDENTITY
        self.chars      = []
        self.char_list  = CharacterList(self.list_rect)
        self.selected_id = None
        self.costumes    = ["base"]
        self.available_attacks: list[str] = []   # global roster, see discover_attacks()
        self.cfg         = copy.deepcopy(DEFAULT_CONFIG)
        self.editor      = None
        self.preview     = SpritePreview(self.preview_rect)

        self.status_msg   = ""
        self.status_col   = C_TEXT_DIM
        self.status_timer = 0.0

        # ── Global (non-per-character) settings ──────────────────────
        self.global_settings = load_global_settings()
        self.max_level_slider = Slider(
            pygame.Rect(0, 0, 260, 20),   # rect.y positioned in _draw_settings
            1, 999, self.global_settings["max_level"], step=1
        )

        # Non-blocking modal dialog state. None when no dialog is open.
        # dict keys: kind ('confirm'/'input'), message, field (TextInput,
        # input-only), error (input-only), on_confirm (callable)
        self.dialog = None

    # ── Layout ──────────────────────────────────────────────────────
    def _build_layout(self) -> None:
        sw, sh = self.screen_width, self.screen_height

        self.list_rect = pygame.Rect(PAD, HEADER_H + PAD,
                                     LIST_W, sh - HEADER_H - FOOTER_H - PAD * 2)
        self.preview_rect = pygame.Rect(PAD, sh - FOOTER_H - PREVIEW_H - PAD,
                                        LIST_W, PREVIEW_H)
        self.list_rect.height -= PREVIEW_H + PAD

        editor_x = LIST_W + PAD * 2
        self.editor_rect = pygame.Rect(editor_x, HEADER_H + PAD + TAB_H,
                                       sw - editor_x - PAD,
                                       sh - HEADER_H - FOOTER_H - PAD * 2 - TAB_H)

        self.tab_rects: list[pygame.Rect] = []
        tab_w = self.editor_rect.w // len(TAB_NAMES)
        for i in range(len(TAB_NAMES)):
            self.tab_rects.append(pygame.Rect(
                editor_x + i * tab_w, HEADER_H + PAD, tab_w, TAB_H
            ))

        self.btn_save   = pygame.Rect(sw - 230, sh - FOOTER_H + 10, 100, 32)
        self.btn_delete = pygame.Rect(sw - 120, sh - FOOTER_H + 10, 100, 32)
        self.btn_new    = pygame.Rect(PAD + 4,  sh - FOOTER_H + 10, LIST_W - 8, 32)

    # ── Lifecycle ──────────────────────────────────────────────────
    def toggle(self) -> None:
        """Toggle overlay visibility. Refreshes the character list on open."""
        self.active = not self.active
        if self.active:
            self._refresh_char_list()

    def _refresh_char_list(self) -> None:
        self.chars = discover_characters()
        self.available_attacks = discover_attacks()
        self.char_list.set_chars(self.chars)
        self.selected_id = self.char_list.selected
        if self.selected_id:
            self._load_char(self.selected_id)
        else:
            self.editor = None

    # ── Character switching ───────────────────────────────────────
    def _load_char(self, cid: str) -> None:
        self.selected_id      = cid
        self.costumes         = discover_costumes(cid)
        self.cfg              = load_config(cid)
        # Discover transforms for whichever costume is set in the config (or the first one)
        cfg_costume = self.cfg.get("costume", "")
        base_costume = cfg_costume if cfg_costume in self.costumes else (self.costumes[0] if self.costumes else "base")
        self.transform_forms  = discover_transformations(cid, base_costume)
        added                 = sync_transformations(self.cfg, self.costumes, self.transform_forms)
        self.editor           = CharacterEditor(self.editor_rect, cid, self.cfg,
                                                self.costumes, self.transform_forms,
                                                self.available_attacks)
        self.preview.load(cid, base_costume)
        self.preview.shadow_width = self.cfg.get("shadow_size", 32)
        if added:
            self.editor.dirty = True
            self._set_status("Detected new transformation(s) — Save to keep them")

    def _switch_char(self, cid: str) -> None:
        if self.editor:
            self.editor.flush()
        self._load_char(cid)

    def _set_status(self, msg: str, ok: bool = True) -> None:
        self.status_msg   = msg
        self.status_col   = C_GREEN if ok else C_RED
        self.status_timer = 3.0

    # ── Dialog helpers (non-blocking) ────────────────────────────
    def _open_confirm(self, message: str, on_confirm) -> None:
        self.dialog = {"kind": "confirm", "message": message, "on_confirm": on_confirm}

    def _open_input(self, prompt: str, on_confirm, default: str = "") -> None:
        field = TextInput(pygame.Rect(0, 0, 392, 32), default)  # rect set in _draw_dialog
        field.active = True
        field.cursor = len(default)
        self.dialog = {"kind": "input", "message": prompt, "field": field,
                       "error": "", "on_confirm": on_confirm}

    def _close_dialog(self) -> None:
        self.dialog = None

    def _do_delete_selected(self) -> None:
        deleted_id = self.selected_id
        delete_config(deleted_id)

        # Remove from the roster itself, not just its config, and persist
        # that removal so it doesn't reappear next time the panel opens
        # (discover_characters() would otherwise keep finding its sprite
        # folder and re-adding it to the list).
        removed = load_removed_characters()
        removed.add(deleted_id)

        if deleted_id in self.chars:
            self.chars.remove(deleted_id)
        save_character_menu(self.chars, removed)
        self.char_list.set_chars(self.chars)

        self._set_status(f"Deleted {deleted_id}", ok=False)

        self.selected_id = self.char_list.selected
        if self.selected_id:
            self._load_char(self.selected_id)
        else:
            self.editor = None

    def _do_create_char(self, new_id: str) -> None:
        order, removed = load_character_menu()
        removed.discard(new_id)
        if new_id not in self.chars:
            self.chars.append(new_id)   # lands at the end of the menu order;
            self.char_list.set_chars(self.chars, new_id)   # move it with ▲/▼ if needed
        save_character_menu(self.chars, removed)
        self._switch_char(new_id)

    # ── Transformations ──────────────────────────────────────────
    def _reset_transform_scope(self) -> None:
        """Switching the Identity-tab costume changes which transformations
        are in scope, so re-point transform_idx/widgets at the new costume's
        own list instead of leaving them on the previous costume's entry."""
        ed = self.editor
        if not ed:
            return
        visible = ed.visible_transformations()
        ed.transform_idx = 0 if visible else -1
        ed._load_transform_widgets()

    def _set_transform_preview(self, costume: str) -> None:
        """Show the given costume's sprites in both the sidebar quick
        preview and the Preview-tab animation grid.

        Accepts base costume names and transformation paths like
        '{costume}/transformations/ssj' (which are not in self.costumes)."""
        if not self.editor or not self.selected_id:
            return
        is_transform = "/transformations/" in costume
        if not is_transform and costume not in self.costumes:
            costume = self.costumes[0] if self.costumes else "base"
        self.preview.load(self.selected_id, costume)
        self.editor._reload_anim_grid(self.selected_id, costume)
        # The Preview tab's dropdown label is driven by preview_form_idx,
        # not by whatever the grid happens to currently hold — without
        # this, switching forms here (sidebar/grid) leaves that index
        # pointing at the old form, so the Preview tab shows a label like
        # "base" next to sprites that are actually SSJ until the dropdown
        # is opened and a selection is made there.
        all_forms = self.editor._all_preview_forms()
        if costume in all_forms:
            self.editor.preview_form_idx = all_forms.index(costume)

    def _do_add_transformation(self, raw_id: str) -> None:
        if not self.editor:
            return
        ed = self.editor
        existing = {t.get("id") for t in ed.transformations}
        new_id, n = raw_id or "transformation", 2
        while new_id in existing:
            new_id = f"{raw_id}_{n}"
            n += 1
        base_costume = ed._current_costume()
        # A new transformation always belongs to the costume that's selected
        # right now — it's stored nested under that costume, never as a bare
        # costume name, since a costume is not itself a transformation.
        if ed.transform_forms:
            form = ed.transform_forms[ed.transform_costume_idx]
        else:
            form = new_id
        default_costume = f"{base_costume}/transformations/{form}"
        ed.transformations.append({
            "id":            new_id,
            "display_name":  new_id.replace("_", " ").title(),
            "costume":       default_costume,
            "power_mult":    1.0,
            "defense_mult":  1.0,
            "speed_mult":    1.0,
            "ki_drain":      0.0,
        })
        ed.transform_idx = len(ed.visible_transformations()) - 1
        ed._load_transform_widgets()
        ed.dirty = True
        self._set_transform_preview(default_costume)
        self._set_status(f"Added transformation '{new_id}' to '{base_costume}'")

    def _do_remove_transformation(self) -> None:
        ed = self.editor
        if not ed:
            return
        visible = ed.visible_transformations()
        if not (0 <= ed.transform_idx < len(visible)):
            return
        removed = visible[ed.transform_idx]
        ed.transformations.remove(removed)
        # Remember this costume path was deliberately deleted so
        # sync_transformations() doesn't silently re-add it next time this
        # character is loaded, as long as its sprite folder still exists.
        removed_costume = removed.get("costume", "")
        if removed_costume:
            ed.cfg.setdefault("removed_transformations", [])
            if removed_costume not in ed.cfg["removed_transformations"]:
                ed.cfg["removed_transformations"].append(removed_costume)
        new_visible = ed.visible_transformations()
        ed.transform_idx = min(ed.transform_idx, len(new_visible) - 1) if new_visible else -1
        ed._load_transform_widgets()
        ed.dirty = True
        if new_visible:
            self._set_transform_preview(new_visible[ed.transform_idx].get("costume", ""))
        else:
            self._set_transform_preview(self.cfg.get("costume", "base"))
        self._set_status(f"Removed transformation '{removed.get('id', '')}'", ok=False)

    def _handle_dialog_event(self, event: pygame.event.Event) -> None:
        d = self.dialog
        sw, sh = self.screen_width, self.screen_height

        if d["kind"] == "confirm":
            W, H = 420, 160
            dlg = pygame.Rect((sw - W) // 2, (sh - H) // 2, W, H)
            btn_ok = pygame.Rect(dlg.x + 30,      dlg.bottom - 52, 160, 36)
            btn_no = pygame.Rect(dlg.right - 190, dlg.bottom - 52, 160, 36)

            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_RETURN:
                    cb = d["on_confirm"]; self._close_dialog(); cb()
                elif event.key == pygame.K_ESCAPE:
                    self._close_dialog()
            elif event.type == pygame.MOUSEBUTTONDOWN:
                mx, my = event.pos
                if btn_ok.collidepoint(mx, my):
                    cb = d["on_confirm"]; self._close_dialog(); cb()
                elif btn_no.collidepoint(mx, my):
                    self._close_dialog()

        elif d["kind"] == "input":
            W, H = 440, 170
            dlg = pygame.Rect((sw - W) // 2, (sh - H) // 2, W, H)
            field_rect = pygame.Rect(dlg.x + 24, dlg.y + 80, W - 48, 32)
            d["field"].rect = field_rect
            btn_ok = pygame.Rect(dlg.x + 30,      dlg.bottom - 50, 160, 34)
            btn_no = pygame.Rect(dlg.right - 190, dlg.bottom - 50, 160, 34)

            def try_submit():
                v = d["field"].value.strip().lower().replace(" ", "_")
                if v:
                    cb = d["on_confirm"]; self._close_dialog(); cb(v)
                else:
                    d["error"] = "ID cannot be empty"

            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    self._close_dialog(); return
                if event.key == pygame.K_RETURN:
                    try_submit(); return
            d["field"].handle_event(event)
            if event.type == pygame.MOUSEBUTTONDOWN:
                mx, my = event.pos
                if btn_no.collidepoint(mx, my):
                    self._close_dialog()
                elif btn_ok.collidepoint(mx, my):
                    try_submit()

    def _draw_dialog(self, screen: pygame.Surface, dt: float) -> None:
        d = self.dialog
        sw, sh = self.screen_width, self.screen_height
        overlay = pygame.Surface((sw, sh), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 160))
        screen.blit(overlay, (0, 0))
        mx, my = pygame.mouse.get_pos()

        if d["kind"] == "confirm":
            W, H = 420, 160
            dlg = pygame.Rect((sw - W) // 2, (sh - H) // 2, W, H)
            pygame.draw.rect(screen, C_DIALOG_BG, dlg, border_radius=8)
            pygame.draw.rect(screen, C_BORDER,    dlg, 1, border_radius=8)

            msg = render_text_cached(self.font, d["message"], C_TEXT)
            screen.blit(msg, msg.get_rect(centerx=dlg.centerx, top=dlg.y + 28))

            btn_ok = pygame.Rect(dlg.x + 30,      dlg.bottom - 52, 160, 36)
            btn_no = pygame.Rect(dlg.right - 190, dlg.bottom - 52, 160, 36)
            for btn, lbl, danger in [(btn_ok, "Confirm", True), (btn_no, "Cancel", False)]:
                draw_button(screen, self.font_sm, btn, lbl, danger=danger,
                           hover=btn.collidepoint(mx, my))

        elif d["kind"] == "input":
            W, H = 440, 170
            dlg = pygame.Rect((sw - W) // 2, (sh - H) // 2, W, H)
            pygame.draw.rect(screen, C_DIALOG_BG, dlg, border_radius=8)
            pygame.draw.rect(screen, C_BORDER,    dlg, 1, border_radius=8)

            msg = render_text_cached(self.font, d["message"], C_TEXT)
            screen.blit(msg, (dlg.x + 24, dlg.y + 24))

            d["field"].rect = pygame.Rect(dlg.x + 24, dlg.y + 80, W - 48, 32)
            d["field"].draw(screen, self.font_sm, dt)

            hint = render_text_cached(self.font_sm, "lowercase, underscores only", C_TEXT_DIM)
            screen.blit(hint, (dlg.x + 24, d["field"].rect.bottom + 4))
            if d["error"]:
                err = render_text_cached(self.font_sm, d["error"], C_RED)
                screen.blit(err, (dlg.x + 24, d["field"].rect.bottom + 4))

            btn_ok = pygame.Rect(dlg.x + 30,      dlg.bottom - 50, 160, 34)
            btn_no = pygame.Rect(dlg.right - 190, dlg.bottom - 50, 160, 34)
            for btn, lbl, danger in [(btn_ok, "Create", False), (btn_no, "Cancel", True)]:
                draw_button(screen, self.font_sm, btn, lbl, danger=danger,
                           hover=btn.collidepoint(mx, my))

    # ── Input ──────────────────────────────────────────────────────
    def handle_input(self, event: pygame.event.Event):
        """Returns 'close' when the overlay was just closed, else None."""
        if not self.active:
            return None

        if self.dialog is not None:
            self._handle_dialog_event(event)
            return None

        mx, my = pygame.mouse.get_pos()

        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                if self.editor:
                    self.editor.flush()
                self.active = False
                return "close"
            if event.key == pygame.K_s and (event.mod & pygame.KMOD_CTRL):
                if self.editor:
                    self.editor.flush()
                    save_config(self.cfg)
                    self.editor.dirty = False
                    self._set_status(f"Saved  {self.cfg['id']}.json")

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            for i, tr in enumerate(self.tab_rects):
                if tr.collidepoint(mx, my):
                    prev_tab = self.active_tab
                    self.active_tab = i
                    # The sidebar preview otherwise still shows whatever
                    # costume the previous tab left it on, and only
                    # updates once the user clicks a form arrow inside
                    # this tab — so jump straight to the currently
                    # selected transformation's form as soon as the tab
                    # is opened.
                    if i == TAB_TRANSFORM and i != prev_tab and self.editor:
                        visible = self.editor.visible_transformations()
                        idx = self.editor.transform_idx
                        if 0 <= idx < len(visible):
                            self._set_transform_preview(visible[idx].get("costume", ""))

            if self.btn_save.collidepoint(mx, my) and self.editor:
                self.editor.flush()
                save_config(self.cfg)
                self.editor.dirty = False
                self._set_status(f"Saved  {self.cfg['id']}.json")

            if self.btn_delete.collidepoint(mx, my) and self.selected_id:
                sid = self.selected_id
                self._open_confirm(f"Delete config for  '{sid}'?", self._do_delete_selected)

            if self.btn_new.collidepoint(mx, my):
                self._open_input("New character ID:", self._do_create_char)

            if self.active_tab == TAB_IDENTITY and self.editor:
                arr_l = pygame.Rect(self.editor_rect.x + 140, 0, 26, 26)
                arr_r = pygame.Rect(self.editor_rect.x + 300, 0, 26, 26)
                y_costume = self.editor_rect.y + 60 + 2 * 42   # +60 matches _draw_identity's top margin
                arr_l.y = y_costume + 2
                arr_r.y = y_costume + 2
                if arr_l.collidepoint(mx, my):
                    self.editor.costume_idx = (self.editor.costume_idx - 1) % max(1, len(self.costumes))
                    self.editor.dirty = True
                    new_costume = self.costumes[self.editor.costume_idx]
                    self.transform_forms = discover_transformations(self.selected_id, new_costume)
                    self.editor.transform_forms = self.transform_forms
                    self.preview.load(self.selected_id, new_costume)
                    self.editor._reload_anim_grid(self.selected_id, new_costume)
                    self._reset_transform_scope()
                if arr_r.collidepoint(mx, my):
                    self.editor.costume_idx = (self.editor.costume_idx + 1) % max(1, len(self.costumes))
                    self.editor.dirty = True
                    new_costume = self.costumes[self.editor.costume_idx]
                    self.transform_forms = discover_transformations(self.selected_id, new_costume)
                    self.editor.transform_forms = self.transform_forms
                    self.preview.load(self.selected_id, new_costume)
                    self.editor._reload_anim_grid(self.selected_id, new_costume)
                    self._reset_transform_scope()

            if self.active_tab == TAB_ATTACKS and self.editor:
                # _charged_melee_style_rect is kept in sync with
                # _draw_attacks (set every frame it draws the button), same
                # pattern as attack_btn_rects below.
                style_rect = self.editor._charged_melee_style_rect
                if style_rect and style_rect.collidepoint(mx, my):
                    atk = self.editor.cfg["attacks"]
                    atk["charged_melee_style"] = (
                        "spin" if atk.get("charged_melee_style", "lunge") == "lunge" else "lunge"
                    )
                    self.editor.dirty = True

                # attack_btn_rects is kept in sync with _draw_attacks (see
                # CharacterEditor._build_attack_grid), so it always reflects
                # the icons currently on screen — no separate layout math
                # needed here, just hit-test against it directly.
                for aid, rect in self.editor.attack_btn_rects.items():
                    if rect.collidepoint(mx, my):
                        if aid in self.editor.equipped_attacks:
                            self.editor.equipped_attacks.remove(aid)
                        else:
                            self.editor.equipped_attacks.append(aid)
                        self.editor.dirty = True

            if self.active_tab == TAB_TRANSFORM and self.editor:
                ed      = self.editor
                fx_t    = self.editor_rect.x + 140
                y0      = self.editor_rect.y + 60   # matches _draw_transformations's top margin
                row_h_  = 42
                visible = ed.visible_transformations()
                n       = len(visible)

                arr_l = pygame.Rect(fx_t,       y0 + 2, 26, 26)
                arr_r = pygame.Rect(fx_t + 160, y0 + 2, 26, 26)
                if n and arr_l.collidepoint(mx, my):
                    ed.flush()
                    ed.transform_idx = (ed.transform_idx - 1) % n
                    ed._load_transform_widgets()
                    self._set_transform_preview(ed.visible_transformations()[ed.transform_idx].get("costume", ""))
                if n and arr_r.collidepoint(mx, my):
                    ed.flush()
                    ed.transform_idx = (ed.transform_idx + 1) % n
                    ed._load_transform_widgets()
                    self._set_transform_preview(ed.visible_transformations()[ed.transform_idx].get("costume", ""))

                btn_add    = pygame.Rect(fx_t,       y0 + row_h_ + 2, 150, 28)
                btn_remove = pygame.Rect(fx_t + 160, y0 + row_h_ + 2, 110, 28)
                if btn_add.collidepoint(mx, my):
                    self._open_input("New transformation ID:", self._do_add_transformation)
                if n and btn_remove.collidepoint(mx, my):
                    tf    = visible[ed.transform_idx]
                    label = tf.get("display_name") or tf.get("id", "")
                    self._open_confirm(f"Remove transformation '{label}'?", self._do_remove_transformation)

                picker_list = self.editor.transform_forms
                if n and picker_list:
                    y_costume = y0 + 3 * row_h_ + 30
                    c_arr_l = pygame.Rect(fx_t,       y_costume + 2, 26, 26)
                    c_arr_r = pygame.Rect(fx_t + 160, y_costume + 2, 26, 26)
                    if c_arr_l.collidepoint(mx, my):
                        ed.flush()
                        ed.transform_costume_idx = (ed.transform_costume_idx - 1) % len(picker_list)
                        ed.dirty = True
                        form = picker_list[ed.transform_costume_idx]
                        self._set_transform_preview(f"{ed._current_costume()}/transformations/{form}")
                    if c_arr_r.collidepoint(mx, my):
                        ed.flush()
                        ed.transform_costume_idx = (ed.transform_costume_idx + 1) % len(picker_list)
                        ed.dirty = True
                        form = picker_list[ed.transform_costume_idx]
                        self._set_transform_preview(f"{ed._current_costume()}/transformations/{form}")

        if self.active_tab == TAB_SETTINGS:
            if self.max_level_slider.handle_event(event):
                new_max = int(self.max_level_slider.value)
                if new_max != self.global_settings["max_level"]:
                    self.global_settings["max_level"] = new_max
                    save_global_settings(self.global_settings)
                    self._set_status(f"Max level set to {new_max}")

        new_sel = self.char_list.handle_event(event)
        if new_sel:
            self._switch_char(new_sel)
            self.active_tab = TAB_IDENTITY

        if self.char_list.order_changed:
            self.char_list.order_changed = False
            save_character_order(self.char_list.chars)
            self._set_status("Character order updated")

        if self.editor:
            self.editor.handle_event(event, self.active_tab)
            if self.editor._preview_form_changed:
                self.editor._preview_form_changed = False
                all_forms = self.editor._all_preview_forms()
                if all_forms:
                    self.preview.load(self.selected_id,
                                      all_forms[self.editor.preview_form_idx])

        return None

    # ── Update ─────────────────────────────────────────────────────
    def update(self, dt: float) -> None:
        if not self.active:
            return
        self.preview.update(dt)
        if self.status_timer > 0:
            self.status_timer -= dt

    # ── Draw ───────────────────────────────────────────────────────
    def draw(self, screen: pygame.Surface, dt: float = 0.0) -> None:
        if not self.active:
            return

        sw, sh = self.screen_width, self.screen_height
        font, font_sm, font_hd = self.font, self.font_sm, self.font_hd
        mx, my = pygame.mouse.get_pos()

        screen.fill(C_BG)

        hdr_rect = pygame.Rect(0, 0, sw, HEADER_H)
        pygame.draw.rect(screen, C_PANEL, hdr_rect)
        pygame.draw.line(screen, C_BORDER, (0, HEADER_H - 1), (sw, HEADER_H - 1))
        title = render_text_cached(font_hd, "CHARACTER CREATOR", C_TEXT)
        screen.blit(title, (16, (HEADER_H - title.get_height()) // 2))
        hint = render_text_cached(font_sm, "ESC to close  •  Ctrl+S to save", C_TEXT_DIM)
        screen.blit(hint, (sw - hint.get_width() - 16, (HEADER_H - hint.get_height()) // 2))

        footer_rect = pygame.Rect(0, sh - FOOTER_H, sw, FOOTER_H)
        pygame.draw.rect(screen, C_PANEL, footer_rect)
        pygame.draw.line(screen, C_BORDER, (0, sh - FOOTER_H), (sw, sh - FOOTER_H))

        if self.status_timer > 0:
            # Cached: a given status message ("Saved x.json", ...) is shown
            # unchanged for status_timer's whole countdown, so this would
            # otherwise re-rasterize the same string every frame for ~2s.
            sm = render_text_cached(font_sm, self.status_msg, self.status_col)
            screen.blit(sm, (LIST_W + PAD * 3, sh - FOOTER_H + 18))

        draw_button(screen, font_sm, self.btn_save, "Save  ✓",
                    color=C_ACCENT, hover=self.btn_save.collidepoint(mx, my))
        draw_button(screen, font_sm, self.btn_delete, "Delete",
                    color=C_RED, danger=True, hover=self.btn_delete.collidepoint(mx, my))
        draw_button(screen, font_sm, self.btn_new, "+ New Character",
                    hover=self.btn_new.collidepoint(mx, my))

        if self.editor and self.editor.dirty:
            dot_txt = render_text_cached(font_sm, "● unsaved", C_ACCENT2)
            screen.blit(dot_txt, (self.btn_save.x - dot_txt.get_width() - 12, self.btn_save.y + 8))

        dirty_id = (self.selected_id or "") if (self.editor and self.editor.dirty) else ""
        self.char_list.draw(screen, font, font_sm, dirty_id)

        preview_rect_adj = pygame.Rect(PAD, self.list_rect.bottom + PAD,
                                       LIST_W, sh - FOOTER_H - self.list_rect.bottom - PAD * 2)
        self.preview.rect = preview_rect_adj
        # Keep the preview's shadow in sync with the Identity tab's Shadow
        # Size slider live, frame-by-frame — not just on save/flush — so
        # dragging the slider is reflected immediately, the same way the
        # walk-cycle sprite itself updates as the costume/form changes.
        if self.editor:
            self.preview.shadow_width = self.editor.shadow_slider.value
        self.preview.draw(screen, font_sm)

        pygame.draw.rect(screen, C_PANEL, self.editor_rect.union(
            pygame.Rect(self.editor_rect.x, HEADER_H + PAD, self.editor_rect.w, TAB_H)
        ), border_radius=6)
        pygame.draw.rect(screen, C_BORDER, self.editor_rect.inflate(0, TAB_H), 1, border_radius=6)

        for i, (name, tr) in enumerate(zip(TAB_NAMES, self.tab_rects)):
            is_act = (i == self.active_tab)
            bg = C_TAB_ACT if is_act else C_TAB_INACT
            pygame.draw.rect(screen, bg, tr,
                             border_radius=6 if i == 0 else (6 if i == len(TAB_NAMES)-1 else 0))
            col = C_BORDER if not is_act else C_ACCENT
            pygame.draw.rect(screen, col, tr, 1)
            lbl = render_text_cached(font, name, C_TEXT if is_act else C_TEXT_DIM)
            screen.blit(lbl, lbl.get_rect(center=tr.center))
        tr = self.tab_rects[self.active_tab]
        pygame.draw.line(screen, C_TAB_ACT, (tr.x + 1, tr.bottom), (tr.right - 1, tr.bottom), 2)

        if self.editor:
            self.editor.panel = self.editor_rect
            old_clip = screen.get_clip()
            screen.set_clip(self.editor_rect)
            self.editor.draw(screen, font, font_sm, self.active_tab, dt)
            screen.set_clip(old_clip)
        elif self.chars == []:
            msg = render_text_cached(
                font, "No characters found in assets/sprites/player/ — create one with + New Character",
                C_TEXT_DIM,
            )
            screen.blit(msg, msg.get_rect(center=self.editor_rect.center))

        # Settings is global, not tied to whichever character is selected,
        # so it draws independently of self.editor (and even when no
        # character exists yet).
        if self.active_tab == TAB_SETTINGS:
            self._draw_settings(screen, font, font_sm)

        if self.dialog is not None:
            self._draw_dialog(screen, dt)

    def _draw_settings(self, screen: pygame.Surface,
                       font: pygame.font.Font, font_sm: pygame.font.Font) -> None:
        """Global (not per-character) game settings — currently just max
        level, persisted to GLOBAL_SETTINGS_FILE via save_global_settings()
        and picked up by GameConfig at game startup."""
        lx = self.editor_rect.x + 20
        fx = self.editor_rect.x + 180
        y  = self.editor_rect.y + 60

        draw_label(screen, font_sm, "Max Level", lx, y + 6)
        self.max_level_slider.rect.x = fx
        self.max_level_slider.rect.y = y + 6
        self.max_level_slider.draw(screen, font_sm)

        hint = render_text_cached(
            font_sm,
            "Applies game-wide (all characters share the same level cap). "
            "Takes effect next time the game starts.",
            C_TEXT_DIM,
        )
        screen.blit(hint, (lx, y + 42))