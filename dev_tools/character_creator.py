"""
character_creator.py  -  Dev-menu character editor
====================================================
Scans assets/sprites/player/ to discover player characters, then lets you
create or edit per-character JSON configs saved to assets/characters/{id}.json.

Only the player/ sub-folder is scanned - enemies, NPCs, and other sprites
that live elsewhere inside assets/sprites/ are intentionally ignored.

Wire-up (game loop)
-------------------
    from dev_tools.character_creator import CharacterCreator
    creator = CharacterCreator(screen_width, screen_height)

    creator.toggle()                      # open / close the overlay
    if creator.handle_input(event) == "back_to_dev_menu": ...   # per event
    creator.update(dt)                    # per frame
    creator.draw(screen)                  # per frame (GPUScreen or Surface)

Layout of this file
-------------------
  1. Data layer  - paths, discovery, config / menu / global-settings
                   persistence, transformation sync. No UI in here.
  2. UI layer    - the interface, built on dev_tools.ui_kit so it matches
                   the Dev Menu and Room Editor.

Expected folder conventions
----------------------------
assets/
  sprites/
    player/
      {char_id}/                <- one folder per player character
        {costume}/              <- costume sub-folder (e.g. "default", "ssj")
          walk/
            0.png  1.png  ...  <- walk-cycle frames
        walk/                   <- flat layout (no costume sub-dirs) also ok
          0.png  1.png  ...
  characters/
    {char_id}.json              <- written here on Save
"""

from __future__ import annotations

import json
import os
import sys
import copy
import math
import colorsys
from pathlib import Path
from typing import Optional

import numpy as np
import pygame
from types import SimpleNamespace

import dev_tools.ui_kit as uk

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
PORTRAITS_DIR  = BASE_DIR / "assets/portraits"         # see CharacterCreator._load_portrait()
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
#  Default character config skeleton
# ──────────────────────────────────────────────────────────────────────
DEFAULT_CONFIG: dict = {
    "id":           "",
    "display_name": "",
    # Freeform prose shown in the Scouter Data description panel (see
    # ui/scouter_menu.py's _get_entity_description / _draw_data_description).
    "description":  "",
    "costume":      "default",
    # Sprite-sheet frame dimensions for normal/base art.
    # Individual transformations can override these per form.
    "sprite_width":  32,
    "sprite_height": 32,
    "shadow_size":  32,
    # This character's assigned color — used to color-code anything tied
    # to a specific character in-game (e.g. a level gate's number, so the
    # player can tell at a glance which character it's locked to). Stored
    # as a "#RRGGBB" hex string, picked freely via a color wheel in the
    # Identity tab (see CharacterCreator's hue-strip/SV-square widget).
    "color":        "#FFD700",
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


def hex_to_rgb(hex_str: str, fallback: tuple = (255, 215, 0)) -> tuple:
    """'#RRGGBB' -> (r, g, b). Falls back to gold on anything malformed."""
    try:
        h = (hex_str or "").lstrip("#")
        if len(h) != 6:
            return fallback
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except (ValueError, TypeError):
        return fallback


def rgb_to_hex(rgb: tuple) -> str:
    """(r, g, b) -> '#RRGGBB', used to persist the color wheel's picked
    color back into cfg['color']."""
    r, g, b = rgb
    return "#{:02X}{:02X}{:02X}".format(int(r), int(g), int(b))


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

    frame_w, frame_h = get_form_sprite_size(char_id, form)

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
      1. assets/sprites/attacks/{attack_id}/icon.png — per-attack icon,
         enlarged up to `size` if the source art is smaller (see below).
      2. assets/ui/hud/{attack_id}.png  — dedicated HUD icon, same
         enlarge-if-small treatment.
      3. assets/sprites/attacks/{attack_id}/ — sprite-sheet fallback: grab the
         top-left frame and scale it to `size` (same as before).
      4. Placeholder tile when nothing is found.

    Icons from #1/#2 are loaded at native resolution first, then — if
    smaller than `size` in both dimensions — scaled UP (never down) to fit
    within a `size` x `size` box, aspect ratio preserved. Existing attack
    art (ki_blast.png, begin_kamehameha.png, ...) is 16x16, so without this
    the picker showed icons that were basically invisible. Scaling uses
    pygame.transform.scale (nearest-neighbor), not smoothscale, so the
    pixel art stays crisp instead of going blurry.
    """
    if attack_id in _ATTACK_ICON_CACHE:
        return _ATTACK_ICON_CACHE[attack_id]

    icon: Optional[pygame.Surface] = None
    native = False  # True if icon came from #1/#2 (needs the enlarge step below)

    # ── 1. Per-attack icon.png (native resolution) ─────────────────────
    # assets/sprites/attacks/{attack_id}/icon.png takes top priority so
    # each attack can ship its own dedicated display icon.
    sprite_icon_path = ATTACKS_DIR / attack_id / "icon.png"
    if sprite_icon_path.exists():
        try:
            icon = pygame.image.load(str(sprite_icon_path)).convert_alpha()
            native = True
        except Exception:
            icon = None

    # ── 2. HUD icon (native resolution) ───────────────────────────────
    if icon is None:
        hud_path = HUD_ICONS_DIR / f"{attack_id}.png"
        if hud_path.exists():
            try:
                icon = pygame.image.load(str(hud_path)).convert_alpha()
                native = True
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
        pygame.draw.rect(icon, uk.Theme.CARD_BG[:3], icon.get_rect(), border_radius=6)
        pygame.draw.rect(icon, uk.Theme.CARD_BORDER, icon.get_rect(), 1, border_radius=6)
        ph_font = pygame.font.Font(None, size)
        label = (attack_id[:1] or "?").upper()
        txt = ph_font.render(label, True, uk.Theme.TEXT_DIM)
        icon.blit(txt, txt.get_rect(center=icon.get_rect().center))

    # ── Enlarge small native icons ──────────────────────────────────────
    # #1/#2 above load at whatever resolution the source PNG happens to be.
    # If that's smaller than `size` in both dimensions (e.g. the game's
    # 16x16 attack frames reused as an icon), bump it up so it isn't shown
    # at a barely-visible size in the picker. Only ever scales UP — HUD art
    # already at or above `size` is left exactly as-is.
    if icon is not None and native:
        w, h = icon.get_width(), icon.get_height()
        if 0 < w < size and 0 < h < size:
            factor = size / max(w, h)
            icon = pygame.transform.scale(
                icon, (max(1, round(w * factor)), max(1, round(h * factor)))
            )

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

    frame_w, frame_h = get_form_sprite_size(char_id, form)
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
            if "sprite_width" not in data or "sprite_height" not in data:
                legacy_folder = SPRITES_DIR / char_id / merged.get("costume", "")
                if not legacy_folder.is_dir():
                    discovered = discover_costumes(char_id)
                    legacy_folder = SPRITES_DIR / char_id / (discovered[0] if discovered else "base")
                lw, lh = _read_sprite_size(legacy_folder)
                merged["sprite_width"] = lw
                merged["sprite_height"] = lh
            return merged
        except Exception:
            pass
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["id"] = char_id
    cfg["display_name"] = char_id.replace("_", " ").title()
    # DEFAULT_CONFIG["costume"] is just the literal placeholder string
    # "default" — it doesn't correspond to any real costume folder on disk.
    # The character-creator UI itself never leaks this (CharacterCreator
    # falls back to costumes[0] whenever cfg["costume"] isn't an actual
    # discovered costume), but callers that use load_config() directly at
    # runtime — Game._switch_character(), TransformationSystem.
    # _resolve_transform_costume(), _refresh_transformation_gate() — take
    # cfg["costume"] at face value. For a character that's never been
    # opened/saved in the character creator (e.g. just dropped in as a new
    # sprite folder), that left self.player.costume set to "default",
    # which doesn't exist as a folder — so any transformation registered
    # under the real costume (e.g. "base/transformations/ssj") could never
    # be found, and pressing the transform key silently did nothing.
    # Seed it with the character's actual first discovered costume instead,
    # so a never-saved character behaves the same as a saved one.
    real_costumes = discover_costumes(char_id)
    cfg["costume"] = real_costumes[0] if real_costumes else "base"
    return cfg


def get_form_sprite_size(char_id: str, form: str = "base") -> tuple[int, int]:
    """Return the frame size for a character form.

    Saved creator JSON is authoritative. For characters that have not been
    migrated/saved yet, the old sprite_size.txt is still honored so opening
    the creator does not change how legacy art is sliced.
    """
    config_path = CHARACTERS_DIR / f"{char_id}.json"
    raw = {}
    if config_path.exists():
        try:
            with open(config_path, encoding="utf-8") as f:
                loaded = json.load(f)
            raw = loaded if isinstance(loaded, dict) else {}
        except Exception:
            raw = {}

    base_w = raw.get("sprite_width")
    base_h = raw.get("sprite_height")
    try:
        base_size = (max(1, int(base_w)), max(1, int(base_h)))             if base_w is not None and base_h is not None else None
    except (TypeError, ValueError):
        base_size = None

    form = str(form or "")
    if "/transformations/" in form:
        for tf in raw.get("transformations", []) or []:
            if isinstance(tf, dict) and tf.get("costume") == form:
                try:
                    if tf.get("sprite_width") is not None and tf.get("sprite_height") is not None:
                        return max(1, int(tf["sprite_width"])), max(1, int(tf["sprite_height"]))
                except (TypeError, ValueError):
                    pass
                break

        if base_size:
            return base_size
        legacy_folder = SPRITES_DIR / char_id / form
        return _read_sprite_size(legacy_folder)

    for tf in raw.get("transformations", []) or []:
        if not isinstance(tf, dict):
            continue
        costume = str(tf.get("costume", ""))
        if costume.endswith(f"/transformations/{form}") or tf.get("id") == form:
            try:
                if tf.get("sprite_width") is not None and tf.get("sprite_height") is not None:
                    return max(1, int(tf["sprite_width"])), max(1, int(tf["sprite_height"]))
            except (TypeError, ValueError):
                pass
            break

    if base_size:
        return base_size

    return _read_sprite_size(SPRITES_DIR / char_id / form)

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
        transform_folder = SPRITES_DIR / cfg.get("id", "") / costume_path
        legacy_w, legacy_h = _read_sprite_size(
            transform_folder,
            int(cfg.get("sprite_width", 32)),
        )
        transformations.append({
            "id":            form,
            "display_name":  form.replace("_", " ").upper(),
            "costume":       costume_path,
            "sprite_width":  legacy_w,
            "sprite_height": legacy_h,
            "power_mult":    1.0,
            "defense_mult":  1.0,
            "speed_mult":    1.0,
            "ki_drain":      0.0,
            # Custom ki-bar color override (see CharacterCreator's Ki Bar
            # Color picker on the Transformations tab). None = use the
            # sprite's baked-in transformed_ki_bar.png colors as before.
            "ki_color":      None,
            # Whether the transformed-ki charge bar shows/fills while the
            # transform animation plays (see CharacterCreator's "Show Charge
            # Bar" checkbox). True = historical behavior. False = the
            # animation plays straight through at its own pace with no bar.
            "ki_bar_enabled":  True,
            # Seconds the charge bar takes to fill before the transform
            # completes (see CharacterCreator's "Charge Duration" slider).
            # None = use TransformationSystem's built-in default (~3.75s,
            # matched to the stock 'transform' sprite sheet's own length).
            # Ignored entirely when ki_bar_enabled is False.
            "charge_duration": None,
            # Prerequisite tier — the form-name (e.g. "ssj") of another
            # transformation on this SAME costume that the player must
            # already be transformed into before this one becomes
            # reachable (see CharacterCreator's "Requires" picker on the
            # Transformations tab). None = a base-level form, reachable
            # directly from the untransformed state like every
            # transformation before this feature existed.
            "requires": None,
        })
        used.add(costume_path)
        added = True

    return added


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


def get_preview_shadow(shadow_width: float, big: bool = False,
                       scale: float = PREVIEW_SCALE) -> pygame.Surface:
    """Ground shadow scaled for the preview panel, using the exact same
    ~32%-of-width / aspect-locked-to-source-sprite math as
    LayerManager._get_scaled_shadow(), with `scale` (default PREVIEW_SCALE) standing in for
    RENDER_SCALE (the preview's walk frames are scaled the same way).
    Cached per (variant, rounded target width) since shadow_width only
    actually changes while the Shadow Size slider is being dragged."""
    variant = "big" if big else "small"
    source  = _load_shadow_sprite(big)
    target_w = max(8, int(max(0, shadow_width) * scale * 0.32))
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

# ══════════════════════════════════════════════════════════════════════
#  UI layer
# ══════════════════════════════════════════════════════════════════════
#
# Everything below is the Character Creator's interface, rebuilt on
# dev_tools.ui_kit so it reads as part of the same family as the Dev Menu
# and the Room Editor: navy backdrop, flat cards with hairline borders
# that light up gold on hover, bitmap menu font, vector line icons.
#
# It draws ONLY through ui_kit's dispatch helpers (draw_rect_on /
# draw_circle_on / draw_line_on / blit_surface / draw_panel ...), so it
# works on the engine's GPUScreen as well as on a plain pygame.Surface.
#
# The data layer above (discovery, load/save, menu order, global
# settings, transformation sync) is untouched — this layer only edits the
# same cfg dict those functions read and write.

_T = uk.Theme

# Same flat two-tone backdrop / bar colours DevMenu and RoomEditor use.
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

TAB_IDENTITY  = 0
TAB_STATS     = 1
TAB_ATTACKS   = 2
TAB_TRANSFORM = 3
TAB_PREVIEW   = 4
TAB_SETTINGS  = 5
TAB_NAMES     = ["Identity", "Stats", "Attacks", "Transformations", "Preview", "Settings"]
# Settings isn't a peer of the per-character tabs above - it's global and
# lives in its own small icon button (see _settings_rect / config.png),
# not in the tab row. BAR_TAB_NAMES is what the tab row itself lays out.
BAR_TAB_NAMES = TAB_NAMES[:TAB_SETTINGS]


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


# ── Bitmap font wrapper ─────────────────────────────────────────────────

class _Font:
    """One BitmapFont pinned to one pixel height, plus the text metrics the
    layout code needs.

    BitmapFont only has glyphs for letters, digits and  . , ! ? : - + / _ ( ) '
    Anything else (e.g. %, #, quotes) would silently vanish from a text
    field while still being stored in the data, so unsupported characters
    are *displayed* as '?' — the stored value is never altered.

    Widths are computed arithmetically from per-glyph advances instead of
    rendering every prefix, so wrapping / caret placement in a text area
    doesn't rasterise (and cache) hundreds of throw-away strings.

    Text is positioned by BASELINE, not by its surface rect. BitmapFont
    sizes each string's canvas to its own tallest glyph (+ descender
    padding), so centring surfaces makes "no" sit lower than "go".
    """

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
            except AttributeError:      # kit without the glyph helper: assume drawable
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
        """-> (surface, descender_px). descender_px is how far below the
        baseline the surface extends (canvas bottom - baseline)."""
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
        """Ellipsise `text` to at most max_w pixels."""
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
        """Greedy word-wrap -> list of lines (honours explicit newlines)."""
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


# ── Wrapped-text spans (for the multi-line field) ───────────────────────

_SPAN_CACHE: dict = {}


def _split_hard(font, text, s, e, width):
    """Break one over-long run [s, e) on character boundaries."""
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
    """Word-wrap `text` into [(start, end), ...] index spans of the ORIGINAL
    string (newlines and the spaces at soft-wrap points sit between spans),
    so caret / selection maths can map straight back to string offsets."""
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
    """Index of the wrapped line that string offset `idx` belongs to."""
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
    by the New-ID dialogs. Pure state — the creator draws it and feeds it
    keys. key() returns None, 'changed', 'commit' or 'cancel'."""

    def __init__(self, value="", multiline=False, max_len=600, allowed=None):
        self.value = value
        self.cursor = len(value)
        self.anchor = None
        self.multiline = multiline
        self.max_len = max_len
        self.allowed = allowed
        self.blink = 0.0
        self.goal_x = None            # remembered column (px) for Up/Down
        self.view = None              # (font, wrap_width) set by the drawer

    # -- selection ------------------------------------------------------
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
        """Type/paste `text` at the caret. True if the value changed."""
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

    # -- caret movement -------------------------------------------------
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
    """String offset within `line_text` whose caret is nearest to pixel x."""
    if x <= 0 or not line_text:
        return 0
    shown = font.disp(line_text)          # 1:1 with line_text (unsupported -> '?')
    sp = font._sp()
    best_i, best_d = 0, x
    acc = 0
    for i, ch in enumerate(shown, 1):
        acc += font._advance(ch) + (sp if i > 1 else 0)
        d = abs(acc - x)
        if d < best_d:
            best_i, best_d = i, d
    return best_i


# ── Colour picker surfaces ──────────────────────────────────────────────

def _hex_to_hsv(hex_str):
    r, g, b = hex_to_rgb(hex_str)
    return colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)


def _hsv_to_hex(h, s, v):
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return rgb_to_hex((round(r * 255), round(g * 255), round(b * 255)))


def _round_bake(rgb_surface, radius):
    """Copy an opaque surface into an SRCALPHA one with anti-aliased rounded
    corners (multiplied by ui_kit's cached supersampled rounded-rect)."""
    w, h = rgb_surface.get_size()
    out = pygame.Surface((w, h), pygame.SRCALPHA)
    out.blit(rgb_surface, (0, 0))
    try:
        mask = uk._rounded_rect_surface(w, h, radius, (255, 255, 255, 255), None, 0)
        out.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
    except Exception:
        pass
    return out


_HUE_STRIP_CACHE: dict = {}
_SV_CACHE: dict = {}


def _hue_strip_surface(w, h, radius=6):
    key = (w, h, radius)
    surf = _HUE_STRIP_CACHE.get(key)
    if surf is None:
        col = np.zeros((h, 3), dtype=np.uint8)
        for i in range(h):
            r, g, b = colorsys.hsv_to_rgb(i / max(1, h - 1), 1.0, 1.0)
            col[i] = (round(r * 255), round(g * 255), round(b * 255))
        arr = np.tile(col[np.newaxis, :, :], (w, 1, 1))          # (w, h, 3)
        surf = _round_bake(pygame.surfarray.make_surface(arr), radius)
        _HUE_STRIP_CACHE[key] = surf
    return surf


def _sv_surface(hue, w, h, radius=6):
    """Saturation (x) / value (y, top = bright) square for one hue."""
    key = (round(hue, 3), w, h, radius)
    surf = _SV_CACHE.get(key)
    if surf is None:
        if len(_SV_CACHE) > 96:
            _SV_CACHE.clear()
        base = np.array(colorsys.hsv_to_rgb(hue, 1.0, 1.0), dtype=np.float32)
        s = np.linspace(0.0, 1.0, w, dtype=np.float32)[:, None, None]     # (w,1,1)
        v = np.linspace(1.0, 0.0, h, dtype=np.float32)[None, :, None]     # (1,h,1)
        rgb = v * ((1.0 - s) + s * base[None, None, :])                    # (w,h,3)
        arr = np.clip(rgb * 255, 0, 255).astype(np.uint8)
        surf = _round_bake(pygame.surfarray.make_surface(arr), radius)
        _SV_CACHE[key] = surf
    return surf


def _fit_surface(surf, max_w, max_h):
    """Scale to fit a box: whole-number nearest-neighbour when enlarging
    (keeps pixel art crisp), smooth when shrinking."""
    w, h = surf.get_size()
    if w <= 0 or h <= 0 or max_w <= 0 or max_h <= 0:
        return surf
    s = min(max_w / w, max_h / h)
    if s >= 1:
        k = int(s)
        return pygame.transform.scale(surf, (w * k, h * k)) if k > 1 else surf
    return pygame.transform.smoothscale(surf, (max(1, int(w * s)), max(1, int(h * s))))


# ── Vector icons (same fn(surface, rect, color[, width]) shape as ui_kit) ─

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
        if direction == "left":
            pts = [(cx + s * 0.6, cy - s), (cx - s * 0.6, cy), (cx + s * 0.6, cy + s)]
        elif direction == "right":
            pts = [(cx - s * 0.6, cy - s), (cx + s * 0.6, cy), (cx - s * 0.6, cy + s)]
        elif direction == "up":
            pts = [(cx - s, cy + s * 0.6), (cx, cy - s * 0.6), (cx + s, cy + s * 0.6)]
        else:
            pts = [(cx - s, cy - s * 0.6), (cx, cy + s * 0.6), (cx + s, cy - s * 0.6)]
        uk.draw_line_on(surface, color, pts[0], pts[1], width)
        uk.draw_line_on(surface, color, pts[1], pts[2], width)
    return draw


_ic_left = _make_chevron("left")
_ic_right = _make_chevron("right")
_ic_up = _make_chevron("up")
_ic_down = _make_chevron("down")


def _ic_bars(surface, rect, color, width=2):
    """Three vertical bars of different heights — Stats tab."""
    cx, cy = rect.center
    s = min(rect.w, rect.h)
    for i, hgt in enumerate((0.34, 0.56, 0.44)):
        x = cx + (i - 1) * s * 0.26
        uk.draw_line_on(surface, color, (x, cy + s * 0.28), (x, cy + s * 0.28 - s * hgt), width + 1)


def _tab_icon(fn, width=2):
    def draw(surface, rect, color):
        try:
            fn(surface, rect, color, width)
        except TypeError:
            fn(surface, rect, color)
    return draw


# Fallback for the Settings icon button when config.png isn't on disk yet.
_gear_icon = _tab_icon(uk.draw_gear_icon)


# ── Sidebar sprite preview (state only; drawn by CharacterCreator) ──────

class SpritePreview:
    """Down-facing walk cycle of the selected character/form, plus the
    shadow width, for the sidebar preview panel."""

    def __init__(self):
        self.frames: list[pygame.Surface] = []
        self._scaled: dict[int, list[pygame.Surface]] = {}
        self._char = ""
        self._form = ""
        self.frame_i = 0.0
        # In-game px width of the ground shadow (cfg["shadow_size"]).
        self.shadow_width: float = 32
        # Entity height in game px for the feet offset; falls back to the
        # walk frame's own pixel height when unknown.
        self.entity_height: Optional[int] = None

    def invalidate(self) -> None:
        self._char = ""
        self._form = ""

    def load(self, char_id: str, form: str) -> None:
        if char_id == self._char and form == self._form:
            return
        self._char, self._form = char_id, form
        self.frames = load_walk_frames(char_id, form) if char_id else []
        self._scaled = {}
        self.frame_i = 0.0

    def update(self, dt: float) -> None:
        if self.frames:
            self.frame_i = (self.frame_i + dt * ANIM_FPS) % len(self.frames)

    def frame(self, scale: int) -> Optional[pygame.Surface]:
        if not self.frames:
            return None
        lst = self._scaled.get(scale)
        if lst is None:
            if scale > 1:
                lst = [pygame.transform.scale(f, (f.get_width() * scale, f.get_height() * scale))
                       for f in self.frames]
            else:
                lst = list(self.frames)
            self._scaled[scale] = lst
        return lst[int(self.frame_i) % len(lst)]


# ══════════════════════════════════════════════════════════════════════
#  CharacterCreator — non-blocking overlay
#  (host loop drives toggle() / handle_input(event) / update(dt) /
#   draw(screen), same as SpriteEditor / RoomEditor / DevMenu)
# ══════════════════════════════════════════════════════════════════════

ROSTER_ROW_H   = 46
ROSTER_ROW_GAP = 6


def _id_char_ok(ch: str) -> bool:
    """Characters allowed in a new character / transformation ID."""
    return ch.isascii() and (ch.isalnum() or ch in "_- ")


def _hex_char_ok(ch: str) -> bool:
    """Characters allowed while typing a hex color code."""
    return ch in "0123456789ABCDEFabcdef#"



class CharacterCreator:
    """Dev-tool overlay for browsing / editing character configs and
    previewing every discovered animation. Lives inside the host game's
    main loop — it does not own the display, event polling, or the clock."""

    def __init__(self, screen_width: int, screen_height: int):
        self.screen_width = int(screen_width)
        self.screen_height = int(screen_height)
        self.active = False

        pygame.font.init()
        self._init_fonts()

        # ── model ──────────────────────────────────────────────────
        self.active_tab = TAB_IDENTITY
        self.chars: list[str] = []
        self.selected_id: Optional[str] = None
        self.costumes: list[str] = ["base"]
        self.costume_idx = 0
        self.transform_forms: list[str] = []
        self.available_attacks: list[str] = []      # global roster, see discover_attacks()
        self.cfg: dict = copy.deepcopy(DEFAULT_CONFIG)
        self.dirty = False
        self.tf_idx = -1            # index into visible_transformations()
        self.tf_form_idx = 0        # index into transform_forms (the "Form" stepper)
        self.preview_form = "base"  # form shown in sidebar preview + Preview tab
        self.preview = SpritePreview()
        self.global_settings = load_global_settings()
        self._settings_pending = False
        self._char_colors: dict[str, tuple] = {}
        self._tf_color_memory: dict[str, str] = {}

        # ── status / dialog ────────────────────────────────────────
        self.status_msg = ""
        self.status_ok = True
        self.status_timer = 0.0
        # None, or {'kind': 'confirm'|'input', 'title', 'message', 'on_confirm',
        #           'edit', 'error', 'confirm_label', 'danger', 'scroll'}
        self.dialog: Optional[dict] = None

        # ── per-frame UI plumbing ──────────────────────────────────
        self._mouse = (screen_width // 2, screen_height // 2)
        self._hm = self._mouse           # mouse used for hover (offscreen under a dialog)
        self._dt = 1 / 60
        self._clock = 0.0                # animation clock, in frames (ANIM_FPS)
        self._pulse = 0.0
        self._updated = False
        self._hits: list[dict] = []
        self._vp: Optional[pygame.Rect] = None
        self._drag: Optional[dict] = None
        self._focus = None               # SimpleNamespace(key, edit, set)
        self._repeat_on = False
        self._hv: dict = {}
        self._tscroll: dict = {}
        self._text_rects: list[pygame.Rect] = []
        self._text_rects_new: list[pygame.Rect] = []
        self._ml_info: dict = {}
        self._ml_info_new: dict = {}
        self._tip: Optional[str] = None
        self._scroll = [0.0] * len(TAB_NAMES)
        self._content_h = [0] * len(TAB_NAMES)
        self._roster_scroll = 0.0
        self._hsv: dict = {}
        self._thumbs: dict = {}
        self._portraits: dict = {}
        self._portrait_t = 0.0
        self._grid_key = None
        self._grid_anims: dict = {}
        self._grid_scaled_cache: dict = {}
        self._icon_cache: dict = {}
        self._close_requested = False    # set by the header Back button

        self._layout()

    # ── setup ──────────────────────────────────────────────────────
    @staticmethod
    def _font_root() -> str:
        anchored = BASE_DIR / "assets" / "ui" / "fonts"
        return str(anchored) if anchored.exists() else os.path.join("assets", "ui", "fonts")

    def _init_fonts(self) -> None:
        """Same bitmap family as DevMenu / RoomEditor: title text uses the
        plain uppercase/lowercase glyph folders, everything else the menu
        glyph set."""
        root = self._font_root()
        menu = uk.BitmapFont(root, letter_spacing=1)
        title = uk.BitmapFont(root, letter_spacing=1)
        title.uppercase_dir = os.path.join(root, "uppercase")
        title.lowercase_dir = os.path.join(root, "lowercase")
        self.f_title = _Font(title, 32)
        self.f_lg = _Font(menu, 20)
        self.f_md = _Font(menu, 16)
        self.f_sm = _Font(menu, 12)

        icon_path = os.path.join(str(BASE_DIR), "assets", "ui", "dev_menu", "icons", "back.png")
        if not os.path.exists(icon_path):
            icon_path = os.path.join("assets", "ui", "dev_menu", "icons", "back.png")
        self._back_icon = self._load_png_icon(icon_path, 34)

        # Small standalone Settings button (global game settings, not
        # per-character) - lives next to the tab row rather than as a tab
        # of its own. Falls back to the drawn gear icon if config.png is
        # missing so a bare asset folder doesn't leave the button blank.
        settings_icon_path = os.path.join(str(BASE_DIR), "assets", "ui", "dev_menu", "icons", "config.png")
        if not os.path.exists(settings_icon_path):
            settings_icon_path = os.path.join("assets", "ui", "dev_menu", "icons", "config.png")
        self._settings_icon = self._load_png_icon(settings_icon_path, 24)

        # Delete-character button (roster panel) - uses the real trash.png
        # art instead of the drawn vector icon. Falls back to the vector
        # icon if the asset isn't there.
        trash_icon_path = os.path.join(str(BASE_DIR), "assets", "ui", "dev_menu", "icons", "trash.png")
        if not os.path.exists(trash_icon_path):
            trash_icon_path = os.path.join("assets", "ui", "dev_menu", "icons", "trash.png")
        self._trash_icon = self._load_png_icon(trash_icon_path, 24)

        # Add-transformation button (Transformations tab) - uses plus.png,
        # same fallback convention as the icons above.
        plus_icon_path = os.path.join(str(BASE_DIR), "assets", "ui", "dev_menu", "icons", "plus.png")
        if not os.path.exists(plus_icon_path):
            plus_icon_path = os.path.join("assets", "ui", "dev_menu", "icons", "plus.png")
        self._plus_icon = self._load_png_icon(plus_icon_path, 24)

        # Header save button - icon-only, uses save.png (same as the Entity
        # Creator), with the same fallback convention as the icons above.
        save_icon_path = os.path.join(str(BASE_DIR), "assets", "ui", "dev_menu", "icons", "save.png")
        if not os.path.exists(save_icon_path):
            save_icon_path = os.path.join("assets", "ui", "dev_menu", "icons", "save.png")
        self._save_icon = self._load_png_icon(save_icon_path, 26)

    @staticmethod
    def _load_png_icon(path: str, box: int) -> Optional[pygame.Surface]:
        """Same crop + integer-blow-up + point-sample path DevMenu uses for
        its header icons, so the back arrow is pixel-identical. Returns
        None when the file is missing (caller falls back to a vector icon)."""
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
        # Same proportions DevMenu / RoomEditor use for their bars.
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
        prev_h = _clamp(round(area_h * 0.40), 180, 280)
        self.list_rect = pygame.Rect(m, top, side_w, max(160, area_h - prev_h - 16))
        self.prev_rect = pygame.Rect(m, self.list_rect.bottom + 16, side_w, prev_h)
        self.roster_view = pygame.Rect(self.list_rect.x + 8, self.list_rect.y + 46,
                                       side_w - 16, max(40, self.list_rect.h - 46 - 62))
        self.roster_btn_y = self.list_rect.bottom - 52

        main_x = m + side_w + 24
        main_w = w - m - main_x
        self.tab_h = 46
        gap = 8

        # Small square Settings button, right end of the row - reserve its
        # space first, then lay the real per-character tabs out in what's
        # left of the row.
        self.settings_rect = pygame.Rect(0, top, self.tab_h, self.tab_h)
        self.settings_rect.right = main_x + main_w
        tabs_w = main_w - self.tab_h - gap

        n = len(BAR_TAB_NAMES)
        avail = tabs_w - gap * (n - 1)
        # One consistent tab style for every tab: the roomiest of medium or
        # small labels that all fit (no icons - text-only tabs).
        pad = 14
        widths = None
        for font in (md, sm):
            need = [font.width(nm) + 2 * pad for nm in BAR_TAB_NAMES]
            if sum(need) <= avail:
                widths, self._tab_font = need, font
                break
        if widths is None:                      # nothing fits: even split, labels get ellipsised
            widths, self._tab_font = [avail // n] * n, sm
        else:                                   # share the spare room out evenly
            extra = avail - sum(widths)
            widths = [wd + extra // n for wd in widths]
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
        """Toggle overlay visibility. Refreshes the roster on open."""
        if self.active:
            self._shutdown()
        else:
            self.active = True
            self._mouse = tuple(pygame.mouse.get_pos())
            self.dialog = None
            self._drag = None
            self._blur()
            self._refresh_char_list()

    def _shutdown(self) -> None:
        self._blur()
        self._close_dialog()
        self._flush_settings()
        self._drag = None
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

    def _refresh_char_list(self) -> None:
        self.chars = discover_characters()
        self.available_attacks = discover_attacks()
        self.global_settings = load_global_settings()
        self._char_colors = {cid: self._peek_color(cid) for cid in self.chars}
        self.preview.invalidate()
        self._thumbs.clear()
        self._portraits.clear()
        keep = self.selected_id if self.selected_id in self.chars else (self.chars[0] if self.chars else None)
        if keep:
            self._load_char(keep)
        else:
            self._clear_selection()

    @staticmethod
    def _peek_color(cid: str) -> tuple:
        try:
            with open(CHARACTERS_DIR / f"{cid}.json", encoding="utf-8") as f:
                return hex_to_rgb(json.load(f).get("color"), _T.GOLD)
        except Exception:
            return _T.GOLD

    def _clear_selection(self) -> None:
        self.selected_id = None
        self.cfg = copy.deepcopy(DEFAULT_CONFIG)
        self.costumes, self.costume_idx = ["base"], 0
        self.transform_forms = []
        self.tf_idx, self.tf_form_idx = -1, 0
        self.dirty = False
        self.preview.load("", "base")
        self._grid_key = None

    # ── character switching ────────────────────────────────────────
    def _load_char(self, cid: str) -> None:
        self.selected_id = cid
        self.costumes = discover_costumes(cid)
        self.cfg = load_config(cid)
        cfg_costume = self.cfg.get("costume", "")
        base = cfg_costume if cfg_costume in self.costumes else self.costumes[0]
        self.costume_idx = self.costumes.index(base)
        self.transform_forms = discover_transformations(cid, base)
        added = sync_transformations(self.cfg, self.costumes, self.transform_forms)
        self.cfg.setdefault("transformations", [])
        self.cfg["attacks"].setdefault("equipped_attacks", [])
        self.cfg["attacks"].setdefault("charged_melee_style", "lunge")
        self.dirty = bool(added)
        self.tf_idx = 0 if self.visible_transformations() else -1
        self._sync_tf_form_idx()
        self._blur()
        self._scroll = [0.0] * len(TAB_NAMES)
        self._portrait_t = 0.0
        self._set_preview_form(base)
        self._char_colors[cid] = hex_to_rgb(self.cfg.get("color"), _T.GOLD)
        if added:
            self._set_status("Detected new transformation(s) - Save to keep them")

    def _switch_char(self, cid: str) -> None:
        self._load_char(cid)

    def _set_status(self, msg: str, ok: bool = True) -> None:
        self.status_msg = msg
        self.status_ok = ok
        self.status_timer = 3.0

    def _mark_dirty(self) -> None:
        self.dirty = True

    # ── model helpers (ported from the old editor) ─────────────────
    def _current_costume(self) -> str:
        return self.costumes[self.costume_idx] if self.costumes else "base"

    @staticmethod
    def _form_name_of(tf: dict) -> str:
        """Folder form-name of a transformation ('base/transformations/ssj'
        -> 'ssj'). Same identifier game.py's unlocked_transformations /
        TransformationSystem's 'requires' gating use."""
        costume = tf.get("costume", "")
        if "/transformations/" in costume:
            return costume.split("/transformations/")[-1]
        return tf.get("id", "")

    def _transforms_for_costume(self, costume: str) -> list[dict]:
        prefix = f"{costume}/transformations/"
        return [t for t in self.cfg.get("transformations", []) if t.get("costume", "").startswith(prefix)]

    def visible_transformations(self) -> list[dict]:
        """Only the transformations owned by the currently selected costume."""
        return self._transforms_for_costume(self._current_costume())

    def _tf(self) -> Optional[dict]:
        vis = self.visible_transformations()
        return vis[self.tf_idx] if 0 <= self.tf_idx < len(vis) else None

    def _requires_candidates(self) -> list[str]:
        """Form-names eligible as the selected transformation's prerequisite:
        every OTHER form on this costume that doesn't (transitively) already
        require it — that would be a cycle TransformationSystem can never
        satisfy."""
        visible = self.visible_transformations()
        if not (0 <= self.tf_idx < len(visible)):
            return []
        current = self._form_name_of(visible[self.tf_idx])
        by_form = {self._form_name_of(t): t for t in visible}

        def depends_on_current(name: str, seen: set) -> bool:
            if name in seen:
                return False
            seen.add(name)
            tf = by_form.get(name)
            req = tf.get("requires") if tf else None
            if not req:
                return False
            return True if req == current else depends_on_current(req, seen)

        return [f for f in by_form if f != current and not depends_on_current(f, set())]

    def _all_preview_forms(self) -> list[str]:
        base = self._current_costume()
        return [base] + [f"{base}/transformations/{tf}" for tf in self.transform_forms]

    def _sync_tf_form_idx(self) -> None:
        tf = self._tf()
        if not tf:
            self.tf_form_idx = 0
            return
        costume = tf.get("costume", "")
        name = costume.split("/transformations/")[-1] if "/transformations/" in costume else costume
        self.tf_form_idx = self.transform_forms.index(name) if name in self.transform_forms else 0

    def _reset_transform_scope(self) -> None:
        """Switching costume changes which transformations are in scope."""
        self.tf_idx = 0 if self.visible_transformations() else -1
        self._sync_tf_form_idx()

    def _set_preview_form(self, form: str) -> None:
        """Point the sidebar preview and the Preview tab at `form` (a costume
        name or '{costume}/transformations/{form}')."""
        if "/transformations/" not in form and form not in self.costumes:
            form = self.costumes[0] if self.costumes else "base"
        self.preview_form = form
        if self.selected_id:
            self.preview.load(self.selected_id, form)
        self._grid_key = None

    def _select_costume(self, delta: int) -> None:
        if not self.selected_id:
            return
        self.costume_idx = (self.costume_idx + delta) % max(1, len(self.costumes))
        costume = self.costumes[self.costume_idx]
        self.cfg["costume"] = costume
        self.transform_forms = discover_transformations(self.selected_id, costume)
        self._reset_transform_scope()
        self._set_preview_form(costume)
        self._mark_dirty()

    def _tf_step(self, delta: int) -> None:
        vis = self.visible_transformations()
        if not vis:
            return
        self._blur()
        self.tf_idx = (self.tf_idx + delta) % len(vis)
        self._sync_tf_form_idx()
        self._set_preview_form(vis[self.tf_idx].get("costume", ""))

    def _tf_form_step(self, delta: int) -> None:
        tf = self._tf()
        if not tf or not self.transform_forms:
            return
        self._blur()
        self.tf_form_idx = (self.tf_form_idx + delta) % len(self.transform_forms)
        tf["costume"] = f"{self._current_costume()}/transformations/{self.transform_forms[self.tf_form_idx]}"
        self._mark_dirty()
        self._set_preview_form(tf["costume"])

    def _tf_requires_step(self, delta: int) -> None:
        tf = self._tf()
        if not tf:
            return
        options = [None] + self._requires_candidates()
        if len(options) < 2:
            return
        cur = tf.get("requires")
        idx = options.index(cur) if cur in options else 0
        tf["requires"] = options[(idx + delta) % len(options)]
        self._mark_dirty()

    # ── roster actions ─────────────────────────────────────────────
    def _move_selected(self, delta: int) -> None:
        if not self.selected_id or self.selected_id not in self.chars:
            return
        i = self.chars.index(self.selected_id)
        j = i + delta
        if not (0 <= j < len(self.chars)):
            return
        self.chars[i], self.chars[j] = self.chars[j], self.chars[i]
        save_character_order(self.chars)
        self._ensure_roster_visible(j)
        self._set_status("Character order updated")

    def _ensure_roster_visible(self, index: int) -> None:
        pitch = ROSTER_ROW_H + ROSTER_ROW_GAP
        top, bottom = index * pitch, index * pitch + ROSTER_ROW_H
        if top < self._roster_scroll:
            self._roster_scroll = top
        elif bottom > self._roster_scroll + self.roster_view.h:
            self._roster_scroll = bottom - self.roster_view.h

    def _do_create_char(self, new_id: str) -> None:
        order, removed = load_character_menu()
        removed.discard(new_id)
        if new_id not in self.chars:
            self.chars.append(new_id)   # lands at the end; move it with the arrows
        save_character_menu(self.chars, removed)
        self._char_colors.setdefault(new_id, _T.GOLD)
        self._switch_char(new_id)
        self.active_tab = TAB_IDENTITY
        self._ensure_roster_visible(self.chars.index(new_id))

    def _do_delete_selected(self) -> None:
        deleted_id = self.selected_id
        if not deleted_id:
            return
        delete_config(deleted_id)
        # Remove from the roster itself (not just the config) and persist
        # it, or discover_characters() would find the sprite folder again.
        removed = load_removed_characters()
        removed.add(deleted_id)
        idx = self.chars.index(deleted_id) if deleted_id in self.chars else 0
        if deleted_id in self.chars:
            self.chars.remove(deleted_id)
        save_character_menu(self.chars, removed)
        self._set_status(f"Deleted {deleted_id}", ok=False)
        if self.chars:
            self._load_char(self.chars[min(idx, len(self.chars) - 1)])
        else:
            self._clear_selection()

    # ── transformation actions ─────────────────────────────────────
    def _do_add_transformation(self, raw_id: str) -> None:
        if not self.selected_id:
            return
        existing = {t.get("id") for t in self.cfg["transformations"]}
        new_id, n = raw_id or "transformation", 2
        while new_id in existing:
            new_id = f"{raw_id}_{n}"
            n += 1
        base_costume = self._current_costume()
        # A new transformation always nests under the costume selected right
        # now — a costume is not itself a transformation.
        form = self.transform_forms[self.tf_form_idx] if self.transform_forms else new_id
        default_costume = f"{base_costume}/transformations/{form}"
        self.cfg["transformations"].append({
            "id":              new_id,
            "display_name":    new_id.replace("_", " ").title(),
            "costume":         default_costume,
            "sprite_width":    int(self.cfg.get("sprite_width", 32)),
            "sprite_height":   int(self.cfg.get("sprite_height", 32)),
            "power_mult":      1.0,
            "defense_mult":    1.0,
            "speed_mult":      1.0,
            "ki_drain":        0.0,
            "ki_color":        None,
            "ki_bar_enabled":  True,
            "charge_duration": None,
            "requires":        None,
        })
        self._blur()
        self.tf_idx = len(self.visible_transformations()) - 1
        self._sync_tf_form_idx()
        self._mark_dirty()
        self._set_preview_form(default_costume)
        self._set_status(f"Added transformation '{new_id}' to '{base_costume}'")

    def _do_remove_transformation(self) -> None:
        tf = self._tf()
        if not tf:
            return
        self._blur()
        self.cfg["transformations"].remove(tf)
        # Remember the path was deliberately deleted so sync_transformations()
        # doesn't re-add it next load while its sprite folder still exists.
        removed_costume = tf.get("costume", "")
        if removed_costume:
            rem = self.cfg.setdefault("removed_transformations", [])
            if removed_costume not in rem:
                rem.append(removed_costume)
        vis = self.visible_transformations()
        self.tf_idx = min(self.tf_idx, len(vis) - 1) if vis else -1
        self._sync_tf_form_idx()
        self._mark_dirty()
        if vis:
            self._set_preview_form(vis[self.tf_idx].get("costume", ""))
        else:
            self._set_preview_form(self._current_costume())
        self._set_status(f"Removed transformation '{tf.get('id', '')}'", ok=False)

    # ── save ───────────────────────────────────────────────────────
    def _normalize_cfg(self) -> None:
        """Fields are edited straight into self.cfg as the user works; this
        tidies them into the shape the runtime expects right before saving
        (trimmed names, real ints, and the keys older saves always carried)."""
        cfg = self.cfg
        cid = self.selected_id or cfg.get("id", "")
        cfg["display_name"] = (cfg.get("display_name") or "").strip() or cid
        cfg["description"] = (cfg.get("description") or "").strip()
        if self.costumes:
            cfg["costume"] = self.costumes[self.costume_idx]
        cfg["sprite_width"] = int(cfg.get("sprite_width", 32))
        cfg["sprite_height"] = int(cfg.get("sprite_height", 32))
        cfg["shadow_size"] = int(cfg.get("shadow_size", 32))
        cfg["halo_enabled"] = bool(cfg.get("halo_enabled", False))
        cfg["color"] = cfg.get("color") or "#FFD700"
        for k, dv in DEFAULT_CONFIG["stats"].items():
            cfg["stats"][k] = int(cfg["stats"].get(k, dv))
        atk = cfg["attacks"]
        atk["ki_attack_mode"] = atk.get("ki_attack_mode", "blast")
        for k in ("blast_cost", "beam_cost", "walk_speed", "run_speed", "fly_speed"):
            atk[k] = int(atk.get(k, DEFAULT_CONFIG["attacks"][k]))
        atk["melee_duration"] = round(float(atk.get("melee_duration", 0.5)), 3)

        tf = self._tf()
        if tf:
            tf["display_name"] = (tf.get("display_name") or "").strip() or tf.get("id", "")
            base = self._current_costume()
            if self.transform_forms:
                tf["costume"] = f"{base}/transformations/{self.transform_forms[self.tf_form_idx]}"
            else:
                tf.setdefault("costume", f"{base}/transformations/{tf.get('id', '')}")
            tf["sprite_width"] = int(tf.get("sprite_width", cfg["sprite_width"]))
            tf["sprite_height"] = int(tf.get("sprite_height", cfg["sprite_height"]))
            for k in ("power_mult", "defense_mult", "speed_mult"):
                tf[k] = round(float(tf.get(k, 1.0)), 2)
            tf["ki_drain"] = round(float(tf.get("ki_drain", 0.0)), 1)
            tf.setdefault("ki_color", None)
            tf["ki_bar_enabled"] = bool(tf.get("ki_bar_enabled", True))
            tf.setdefault("requires", None)

    def _save(self) -> None:
        if not self.selected_id:
            return
        self._blur()
        self._normalize_cfg()
        try:
            save_config(self.cfg)
        except OSError as exc:
            self._set_status(f"Could not save: {exc.strerror or exc}", ok=False)
            return
        self.dirty = False
        self._char_colors[self.selected_id] = hex_to_rgb(self.cfg.get("color"), _T.GOLD)
        self._set_status(f"Saved  {self.cfg['id']}.json")

    def _flush_settings(self) -> None:
        if self._settings_pending:
            self._settings_pending = False
            try:
                save_global_settings(self.global_settings)
                self._set_status(f"Max level set to {self.global_settings['max_level']}")
            except OSError as exc:
                self._set_status(f"Could not save settings: {exc.strerror or exc}", ok=False)

    # ── dialogs (non-blocking) ─────────────────────────────────────
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
        the header Back button or ESC), else None. The Dev Menu closes
        itself when it launches this creator, so the caller should reopen
        it on this signal rather than dropping all the way to gameplay —
        see world_map_editor / room_editor's identical convention."""
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
        return max(0, self._content_h[self.active_tab] - (self.panel_rect.h - 8))

    def _wheel(self, dy: int) -> None:
        pos = self._mouse
        if self.roster_view.collidepoint(pos):
            total = len(self.chars) * (ROSTER_ROW_H + ROSTER_ROW_GAP)
            self._roster_scroll = _clamp(self._roster_scroll - dy * (ROSTER_ROW_H + ROSTER_ROW_GAP),
                                         0, max(0, total - self.roster_view.h))
            return
        for key, (rect, total, rows) in self._ml_info.items():
            if rect.collidepoint(pos) and total > rows:
                self._tscroll[key] = _clamp(self._tscroll.get(key, 0) - dy, 0, total - rows)
                return
        if self.panel_rect.collidepoint(pos):
            self._scroll[self.active_tab] = _clamp(self._scroll[self.active_tab] - dy * 64, 0, self._max_scroll())

    # ── update ─────────────────────────────────────────────────────
    def update(self, dt: float, mouse_pos=None) -> None:
        if not self.active:
            uk.set_text_cursor(False)
            uk.set_hand_cursor(False)
            return
        if mouse_pos is not None:
            self._mouse = tuple(mouse_pos)
        dt = min(dt, 1 / 20)          # a slow frame shouldn't make animation jump
        self._dt = max(dt, 1 / 240)
        self._updated = True
        self._clock += dt * ANIM_FPS
        self._pulse += dt
        if self.status_timer > 0:
            self.status_timer -= dt
        self.preview.update(dt)
        if self.active_tab == TAB_IDENTITY:
            self._portrait_t += dt
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
        self._draw_tabs(screen)
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
        once-per-frame convention as CutsceneEditor._resolve_cursor (I-beam
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
        """Eased 0..1 hover amount for `key`, quantised to 20 steps so the
        bitmap-font / rounded-rect caches don't fill with near-duplicates."""
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

    # -- text ---------------------------------------------------------
    def _put(self, screen, font, text, color, x, base_y, anchor="l", dyn=False) -> int:
        if not text:
            return 0
        surf, desc = font.render(text, color)
        tw, th = surf.get_size()
        if anchor == "c":
            x -= tw // 2
        elif anchor == "r":
            x -= tw
        uk.blit_surface(screen, surf, (int(x), int(base_y - (th - desc))), transient=dyn)
        return tw

    def _text_top(self, screen, font, text, color, x, y, anchor="l", dyn=False) -> int:
        """Draw with the top of the capital letters at y."""
        return self._put(screen, font, text, color, x, y + font.cap_h, anchor, dyn)

    def _text_mid(self, screen, font, text, color, x, cy, anchor="l", dyn=False, max_w=None) -> int:
        """Draw vertically centred on cy (by cap-height, so 'no' and 'go' align)."""
        if max_w is not None:
            text = font.fit(text, max_w)
        return self._put(screen, font, text, color, x, cy + (font.cap_h + 1) // 2, anchor, dyn)

    # -- primitives ---------------------------------------------------
    @staticmethod
    def _panel(screen, rect, bg, border, bw=1, radius=10) -> None:
        if len(bg) == 3:
            bg = (*bg, 255)
        uk.draw_panel(screen, rect, bg=bg, border=border, border_width=bw, radius=radius, shadow=False)

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
        ic = 22 if icon else 0
        gap = 10 if icon and label else 0
        if icon and label and font.width(label) + 28 + ic + gap > rect.w:
            label, gap = "", 0            # too narrow for icon + label: keep just the icon
        text = font.fit(label, rect.w - 28 - ic - gap) if label else ""
        tw = font.width(text)
        x = rect.centerx - (ic + gap + tw) // 2
        if icon:
            icon(screen, pygame.Rect(x, rect.centery - ic // 2, ic, ic), fg, 3)
        if text:
            self._text_mid(screen, font, text, fg, x + ic + gap, rect.centery)
        if enabled and on_click:
            self._add_hit(rect, key=key, down=lambda p, cb=on_click: cb(), tip=tip)

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
        """Drop-in replacement for _ic_trash that draws the real trash.png
        art instead of the vector icon. Falls back to the vector icon if
        the asset is missing so the button never renders blank."""
        if self._trash_icon is not None:
            uk.blit_surface(screen, self._trash_icon, self._trash_icon.get_rect(center=rect.center))
        else:
            _ic_trash(screen, rect, color, width)

    def _icon_plus_png(self, screen, rect, color, width=3) -> None:
        """Drop-in replacement for _ic_plus that draws the real plus.png
        art instead of the vector icon. Falls back to the vector icon if
        the asset is missing so the button never renders blank."""
        if self._plus_icon is not None:
            uk.blit_surface(screen, self._plus_icon, self._plus_icon.get_rect(center=rect.center))
        else:
            _ic_plus(screen, rect, color, width)

    def _icon_save_png(self, screen, rect, color, width=3) -> None:
        """Drop-in replacement for _ic_check that draws the real save.png
        art instead of the vector icon. Falls back to the vector icon if
        the asset is missing so the button never renders blank."""
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
        """Section heading: small caps label with a hairline running out to
        the right edge (and an optional right-aligned count/note)."""
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
        cols = _clamp((w + gap) // (300 + gap), 1, 3)
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
                v = round(float(v), {"f1": 1, "f2": 2, "f3": 3}[kind])
            if container.get(key) != v:
                container[key] = v
                self._mark_dirty()
        return set_

    def _stepper(self, screen, key, rect, text, sub, on_prev, on_next, enabled=True) -> None:
        hov = enabled and self._hov(rect)
        t = self._anim(key, hov)
        self._panel(screen, rect, uk.lerp_color(_FIELD, _FIELD_HI, t),
                    uk.lerp_color(_T.CARD_BORDER, _T.GOLD, 0.5 * t))
        bw = rect.h
        sides = (("l", pygame.Rect(rect.x, rect.y, bw, rect.h), _ic_left, on_prev),
                 ("r", pygame.Rect(rect.right - bw, rect.y, bw, rect.h), _ic_right, on_next))
        for side, r, icon, cb in sides:
            bt = self._anim((key, side), enabled and self._hov(r))
            if bt > 0:
                uk.draw_rect_on(screen, uk.lerp_color(_FIELD_HI, _CARD_HI, bt), r.inflate(-8, -8), 0, 8)
            icon(screen, r, uk.lerp_color(_T.TEXT_MUTED, _T.GOLD, bt) if enabled else _T.TEXT_DIM, 3)
            if enabled:
                self._add_hit(r, key=(key, side), down=lambda p, cb=cb: cb())
        mid = pygame.Rect(rect.x + bw, rect.y, rect.w - 2 * bw, rect.h)
        subw = self.f_sm.width(sub) + 14 if sub else 0
        fg = _T.TEXT_PRIMARY if enabled else _T.TEXT_DIM
        self._text_mid(screen, self.f_md, text, fg, mid.centerx, mid.centery, "c", dyn=True,
                       max_w=mid.w - 2 * (subw + 8))
        if sub:
            self._text_mid(screen, self.f_sm, sub, _T.TEXT_DIM, mid.right - 10, mid.centery, "r", dyn=True)

    def _checkbox(self, screen, key, x, y, w, label, checked, on_toggle) -> int:
        h = 32
        row = pygame.Rect(x, y, min(w, 420), h)
        t = self._anim(key, self._hov(row))
        box = pygame.Rect(x, y + (h - 24) // 2, 24, 24)
        if checked:
            self._panel(screen, box, (48, 39, 19), _T.GOLD, 1, 6)
            _ic_check(screen, box, _T.GOLD_BRIGHT, 3)
        else:
            self._panel(screen, box, uk.lerp_color(_FIELD, _FIELD_HI, t),
                        uk.lerp_color(_T.CARD_BORDER, _T.GOLD, 0.78 * t), 1, 6)
        fg = _T.TEXT_PRIMARY if checked else uk.lerp_color(_T.TEXT_SECONDARY, _T.TEXT_PRIMARY, t)
        self._text_mid(screen, self.f_md, label, fg, box.right + 14, box.centery)
        self._add_hit(row, key=key, down=lambda p: on_toggle())
        return h

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
                    if lo < hi or (a <= s and b > e):     # selection spans this line
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
            if len(spans) > rows:       # tiny "more below" scroll hint
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

    # -- colour picker ------------------------------------------------
    def _color_picker(self, screen, key, x, y, w, get_hex, set_hex) -> int:
        """Swatch + editable hex textbox + saturation/value square + hue
        strip. Remembers hue / saturation while you drag through black or
        white, where a hex value alone can't say which hue you were on."""
        y0 = y
        hx = get_hex() or "#FFD700"
        st = self._hsv.get(key)
        if st is None or st[3].upper() != hx.upper():
            h_, s_, v_ = _hex_to_hsv(hx)
            st = (h_, s_, v_, hx)
            self._hsv[key] = st
        h_, s_, v_ = st[0], st[1], st[2]

        fh = self.m_field_h
        sw = pygame.Rect(x, y, fh, fh)
        uk.draw_rect_on(screen, hex_to_rgb(hx), sw, 0, 8)
        uk.draw_rect_on(screen, _T.CHIP_BORDER, sw, 1, 8)

        # Editable hex textbox next to the swatch - lets the player type a
        # code directly instead of only dragging the wheel. Only commits
        # (and repaints the swatch/wheel) once it's a complete, valid
        # "#RRGGBB" value; invalid/partial text while typing is left alone
        # rather than corrupting the stored color.
        hex_key = f"{key}:hex"
        field = pygame.Rect(sw.right + 14, y, min(140, max(100, w - fh - 14)), fh)

        def get_hex_text():
            return hx.upper()

        def set_hex_text(v):
            h = v.strip().lstrip("#").upper()
            if len(h) == 6 and all(c in "0123456789ABCDEF" for c in h):
                new_hex = "#" + h
                self._hsv[key] = (*_hex_to_hsv(new_hex), new_hex)
                if new_hex.upper() != hx.upper():
                    set_hex(new_hex)

        self._text_field(screen, hex_key, field, get_hex_text, set_hex_text, placeholder="#RRGGBB",
                         max_len=7, allowed=_hex_char_ok)
        y += sw.h + 14

        strip_w, gap = 24, 12
        sv_w = _clamp(w - strip_w - gap, 120, 300)
        sv_h = 150
        sv = pygame.Rect(x, y, sv_w, sv_h)
        hue = pygame.Rect(sv.right + gap, y, strip_w, sv_h)
        uk.blit_surface(screen, _sv_surface(h_, sv.w, sv.h), sv, transient=True)
        uk.blit_surface(screen, _hue_strip_surface(hue.w, hue.h), hue)
        uk.draw_rect_on(screen, _T.CHIP_BORDER, sv, 1, 6)
        uk.draw_rect_on(screen, _T.CHIP_BORDER, hue, 1, 6)
        # SV marker (dark + light ring so it reads on any colour)
        mx = sv.x + int(s_ * (sv.w - 1))
        my = sv.y + int((1.0 - v_) * (sv.h - 1))
        uk.draw_circle_on(screen, (0, 0, 0), (mx, my), 8, 2)
        uk.draw_circle_on(screen, (255, 255, 255), (mx, my), 6, 2)
        # hue marker
        hy = hue.y + int(h_ * (hue.h - 1))
        uk.draw_rect_on(screen, (0, 0, 0), pygame.Rect(hue.x - 4, hy - 3, hue.w + 8, 6), 0, 3)
        uk.draw_rect_on(screen, (255, 255, 255), pygame.Rect(hue.x - 3, hy - 2, hue.w + 6, 4), 0, 2)

        def commit(hh, ss, vv):
            new_hex = _hsv_to_hex(hh, ss, vv)
            self._hsv[key] = (hh, ss, vv, new_hex)
            if new_hex.upper() != hx.upper():
                set_hex(new_hex)

        def sv_apply(pos, sv=sv, hh=h_):
            s2 = _clamp((pos[0] - sv.x) / max(1, sv.w - 1), 0.0, 1.0)
            v2 = 1.0 - _clamp((pos[1] - sv.y) / max(1, sv.h - 1), 0.0, 1.0)
            commit(self._hsv[key][0], s2, v2)

        def hue_apply(pos, hue=hue):
            h2 = _clamp((pos[1] - hue.y) / max(1, hue.h - 1), 0.0, 1.0)
            cur = self._hsv[key]
            commit(h2, cur[1], cur[2])

        self._add_hit(sv, key=(key, "sv"), down=sv_apply, drag=sv_apply)
        self._add_hit(hue.inflate(8, 0), key=(key, "hue"), down=hue_apply, drag=hue_apply)
        return y + sv_h - y0

    # ══════════════════════════════════════════════════════════════
    #  Drawing: chrome (header, sidebar, tabs, footer)
    # ══════════════════════════════════════════════════════════════

    def _draw_header(self, screen) -> None:
        w, hh = self.screen_width, self.header_h
        uk.draw_rect_on(screen, _BAR, pygame.Rect(0, 0, w, hh), 0, 0)
        uk.draw_line_on(screen, _HAIR, (0, hh - 1), (w, hh - 1), 1)

        # back
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
                      tip="Close the Character Creator")

        # title
        cx = w // 2
        self._text_mid(screen, self.f_title, "CHARACTER CREATOR", _T.TEXT_PRIMARY, cx, hh // 2, "c")

        # save (+ unsaved chip)
        has = bool(self.selected_id)
        self._icon_btn(screen, "save", self.save_rect, self._icon_save_png, self._save,
                       enabled=has, tip="Save this character  (Ctrl+S)")
        if self.dirty and has:
            label = "UNSAVED"
            tw = self.f_sm.width(label)
            chip = pygame.Rect(0, 0, tw + 42, 32)
            chip.right = self.save_rect.left - 14
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

    # -- sidebar ------------------------------------------------------
    def _draw_sidebar(self, screen) -> None:
        lr = self.list_rect
        self._panel(screen, lr, _T.PANEL_BG, _T.PANEL_BORDER, 1, 12)
        self._text_top(screen, self.f_sm, "CHARACTERS", _T.TEXT_MUTED, lr.x + 18, lr.y + 18)
        self._text_top(screen, self.f_sm, str(len(self.chars)), _T.TEXT_DIM, lr.right - 18, lr.y + 18, "r", dyn=True)

        rv = self.roster_view
        pitch = ROSTER_ROW_H + ROSTER_ROW_GAP
        total = len(self.chars) * pitch
        self._roster_scroll = _clamp(self._roster_scroll, 0, max(0, total - rv.h))
        old_vp = self._vp
        self._vp = rv
        old = self._push_clip(screen, rv)
        if not self.chars:
            self._text_mid(screen, self.f_md, "No characters found", _T.TEXT_DIM, rv.centerx, rv.y + 40, "c")
        for i, cid in enumerate(self.chars):
            ry = rv.y + i * pitch - int(self._roster_scroll)
            row = pygame.Rect(rv.x, ry, rv.w - (8 if total > rv.h else 0), ROSTER_ROW_H)
            if row.bottom < rv.y or row.y > rv.bottom:
                continue
            sel = cid == self.selected_id
            t = self._anim(("char", cid), self._hov(row) and not sel)
            base = _SEL if sel else uk.lerp_color(_CARD, _CARD_HI, t)
            border = _T.GOLD if sel else uk.lerp_color(_T.CARD_BORDER, _T.GOLD, 0.78 * t)
            self._panel(screen, row, base, border, 1, 9)
            col = hex_to_rgb(self.cfg.get("color"), _T.GOLD) if sel else self._char_colors.get(cid, _T.GOLD)
            dot = (row.x + 22, row.centery)
            if sel or t > 0:
                uk.draw_soft_glow(screen, dot, 16, col, max_alpha=int(50 if sel else 40 * t))
            uk.draw_circle_on(screen, col, dot, 8)
            uk.draw_circle_on(screen, uk.lerp_color(col, (255, 255, 255), 0.4), dot, 8, 1)
            fg = _T.TEXT_PRIMARY if sel else uk.lerp_color(_T.TEXT_SECONDARY, _T.TEXT_PRIMARY, t)
            reserve = 34 if (sel and self.dirty) else 16
            self._text_mid(screen, self.f_md, cid, fg, row.x + 42, row.centery, max_w=row.w - 42 - reserve)
            if sel and self.dirty:
                uk.draw_circle_on(screen, _T.GOLD, (row.right - 18, row.centery), 4)
            self._add_hit(row, key=("char", cid), down=lambda p, c=cid: self._on_pick_char(c))
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
        has = bool(self.selected_id) and self.selected_id in self.chars
        idx = self.chars.index(self.selected_id) if has else -1
        self._icon_btn(screen, "c_up", pygame.Rect(bx, by, sq, sq), _ic_up, lambda: self._move_selected(-1),
                       enabled=has and idx > 0, tip="Move character up in the menu")
        self._icon_btn(screen, "c_dn", pygame.Rect(bx + sq + gap, by, sq, sq), _ic_down,
                       lambda: self._move_selected(1), enabled=has and idx < len(self.chars) - 1,
                       tip="Move character down in the menu")
        new_r = pygame.Rect(bx + 2 * (sq + gap), by, sq, sq)
        self._icon_btn(screen, "c_new", new_r, self._icon_plus_png, self._open_new_char,
                       tip="Create a new character")
        trash = pygame.Rect(new_r.right + gap, by, sq, sq)
        self._icon_btn(screen, "c_del", trash, self._icon_trash_png, self._ask_delete_char, danger=True,
                       enabled=has, tip="Delete this character's config")

        self._draw_preview_panel(screen)

    def _on_pick_char(self, cid: str) -> None:
        if cid != self.selected_id:
            self._switch_char(cid)
            self.active_tab = TAB_IDENTITY

    def _open_new_char(self) -> None:
        self._open_input("New character", "Enter an ID for the new character (letters, digits, _ and -).",
                         self._do_create_char)

    def _ask_delete_char(self) -> None:
        if self.selected_id:
            sid = self.selected_id
            self._open_confirm("Delete character", f"Delete the config for '{sid}'? This removes it from the "
                               "roster and can't be undone.", self._do_delete_selected, "Delete")

    def _draw_preview_panel(self, screen) -> None:
        pr = self.prev_rect
        self._panel(screen, pr, _T.PANEL_BG, _T.PANEL_BORDER, 1, 12)
        self._text_top(screen, self.f_sm, "PREVIEW", _T.TEXT_MUTED, pr.x + 18, pr.y + 18)
        form = self.preview_form.split("/")[-1] if self.selected_id else ""
        if form:
            self._text_top(screen, self.f_sm, self.f_sm.fit(form.upper(), pr.w // 2), _T.TEXT_DIM,
                           pr.right - 18, pr.y + 18, "r", dyn=True)
        stage = pygame.Rect(pr.x + 12, pr.y + 44, pr.w - 24, pr.h - 44 - 12)
        uk.draw_rect_on(screen, _INSET, stage, 0, 10)
        uk.draw_rect_on(screen, _T.CARD_BORDER, stage, 1, 10)

        col = hex_to_rgb(self.cfg.get("color"), _T.GOLD)
        old = self._push_clip(screen, stage.inflate(-2, -2))
        uk.draw_soft_glow(screen, (stage.centerx, stage.centery + 6), int(min(stage.w, stage.h) * 0.6), col,
                          max_alpha=34)
        pv = self.preview
        pv.shadow_width = self.cfg.get("shadow_size", 32)
        if pv.frames:
            fw, fh = pv.frames[0].get_size()
            scale = PREVIEW_SCALE
            while scale > 1 and (fw * scale > stage.w - 16 or fh * scale > stage.h - 16):
                scale -= 1
            frame = pv.frame(scale)
            rw, rh = frame.get_size()
            fx = stage.centerx - rw // 2
            fy = stage.centery - rh // 2
            # feet_y mirrors LayerManager._draw_shadow(): the frame's vertical
            # centre + entity_height * scale / 2.25. entity_height falls back to
            # the walk frame's own raw pixel height when the real hitbox height
            # (Player.height) isn't known here.
            raw_h = pv.entity_height if pv.entity_height is not None else rh / scale
            feet = stage.centery + (raw_h * scale) / 2.25
            shadow = get_preview_shadow(pv.shadow_width, scale=scale)
            # Shadow first (below the sprite), same draw order as LayerManager.draw_all().
            uk.blit_surface(screen, shadow, (round(stage.centerx - shadow.get_width() / 2),
                                             round(feet - shadow.get_height() / 2)))
            uk.blit_surface(screen, frame, (fx, fy))
            self._text_top(screen, self.f_sm, f"{int(pv.frame_i) % len(pv.frames) + 1}/{len(pv.frames)}",
                           _T.TEXT_DIM, stage.x + 10, stage.bottom - 10 - self.f_sm.cap_h, dyn=True)
        else:
            msg = "NO SPRITES" if self.selected_id else "NO CHARACTER"
            bw = min(stage.w - 40, 96)
            box = pygame.Rect(0, 0, bw, min(stage.h - 40, 120))
            box.center = (stage.centerx, stage.centery - 8)
            uk.draw_rect_on(screen, _T.CARD_BORDER, box, 1, 10)
            self._text_mid(screen, self.f_sm, msg, _T.TEXT_DIM, stage.centerx, box.bottom + 18, "c")
        self._pop_clip(screen, old)

    # -- tabs ---------------------------------------------------------
    def _draw_tabs(self, screen) -> None:
        for i, (name, r) in enumerate(zip(BAR_TAB_NAMES, self.tab_rects)):
            active = i == self.active_tab
            t = self._anim(("tab", i), self._hov(r) and not active)
            base = _SEL if active else uk.lerp_color(_CARD, _CARD_HI, t)
            border = _T.GOLD if active else uk.lerp_color(_T.CARD_BORDER, _T.GOLD, 0.78 * t)
            self._panel(screen, r, base, border, 1, 10)
            if active:
                uk.draw_rect_on(screen, _T.GOLD, pygame.Rect(r.x + 14, r.bottom - 4, r.w - 28, 3), 0, 1)
            fg = _T.GOLD_BRIGHT if active else uk.lerp_color(_T.TEXT_SECONDARY, _T.TEXT_PRIMARY, t)
            font = self._tab_font
            label = font.fit(name, r.w - 28)
            x = r.centerx - font.width(label) // 2
            self._text_mid(screen, font, label, fg, x, r.centery - 1)
            self._add_hit(r, key=("tab", i), down=lambda p, i=i: self._select_tab(i))
        self._draw_settings_button(screen)

    def _draw_settings_button(self, screen) -> None:
        """Small icon-only button (config.png) that opens the global
        Settings tab - kept out of the tab row since it isn't per-character
        like the rest of the tabs."""
        r = self.settings_rect
        active = self.active_tab == TAB_SETTINGS
        t = self._anim(("tab", TAB_SETTINGS), self._hov(r) and not active)
        base = _SEL if active else uk.lerp_color(_CARD, _CARD_HI, t)
        border = _T.GOLD if active else uk.lerp_color(_T.CARD_BORDER, _T.GOLD, 0.78 * t)
        self._panel(screen, r, base, border, 1, 10)
        if active:
            uk.draw_rect_on(screen, _T.GOLD, pygame.Rect(r.x + 8, r.bottom - 4, r.w - 16, 3), 0, 1)
        fg = _T.GOLD_BRIGHT if active else uk.lerp_color(_T.TEXT_SECONDARY, _T.TEXT_PRIMARY, t)
        if self._settings_icon is not None:
            uk.blit_surface(screen, self._settings_icon, self._settings_icon.get_rect(center=r.center))
        else:
            _gear_icon(screen, pygame.Rect(0, 0, 22, 22).move(r.centerx - 11, r.centery - 11), fg)
        self._add_hit(r, key=("tab", TAB_SETTINGS), down=lambda p: self._select_tab(TAB_SETTINGS),
                      tip="Game settings")

    def _select_tab(self, i: int) -> None:
        if i == self.active_tab:
            return
        self._flush_settings()
        self._blur()
        self.active_tab = i
        self._scroll[i] = 0.0
        if i == TAB_TRANSFORM:
            tf = self._tf()
            if tf:      # show the selected transformation's sprites, like the old tab did
                self._set_preview_form(tf.get("costume", ""))
        if i == TAB_IDENTITY:
            self._portrait_t = 0.0

    # -- scrolling content panel --------------------------------------
    def _draw_content(self, screen) -> None:
        pr = self.panel_rect
        self._panel(screen, pr, _T.PANEL_BG, _T.PANEL_BORDER, 1, 12)
        tab = self.active_tab
        if not self.selected_id and tab != TAB_SETTINGS:
            self._text_mid(screen, self.f_lg, "No character selected", _T.TEXT_MUTED, pr.centerx, pr.centery - 14, "c")
            self._text_mid(screen, self.f_md, "Pick one from the list, or create one with New.", _T.TEXT_DIM,
                           pr.centerx, pr.centery + 22, "c")
            return
        pad = 24
        vp = pygame.Rect(pr.x + 3, pr.y + 3, pr.w - 6, pr.h - 6)
        self._scroll[tab] = _clamp(self._scroll[tab], 0, self._max_scroll())
        fn = (self._tab_identity, self._tab_stats, self._tab_attacks, self._tab_transform,
              self._tab_preview, self._tab_settings)[tab]
        old_vp = self._vp
        self._vp = vp
        old = self._push_clip(screen, vp)
        x = pr.x + pad
        w = pr.w - pad * 2 - 10
        y = pr.y + pad - int(self._scroll[tab])
        used = fn(screen, x, y, w)
        self._pop_clip(screen, old)
        self._vp = old_vp
        self._content_h[tab] = used + pad * 2

        max_scroll = self._max_scroll()
        if max_scroll > 0:
            track = pygame.Rect(pr.right - 12, pr.y + 14, 5, pr.h - 28)
            th = max(30, int(track.h * (pr.h - 8) / self._content_h[tab]))
            frac = self._scroll[tab] / max_scroll
            thumb = pygame.Rect(track.x, track.y + int((track.h - th) * frac), track.w, th)
            grab = self._drag is not None and self._drag.get("key") == "scrollbar"
            t = self._anim("scrollbar", self._hov(track.inflate(10, 0)) or grab)
            uk.draw_rect_on(screen, (24, 28, 38), track, 0, 2)
            uk.draw_rect_on(screen, uk.lerp_color(_T.CHIP_BORDER, _T.GOLD, t), thumb, 0, 2)

            def scrub(pos, track=track, th=th, ms=max_scroll, tab=tab):
                f = _clamp((pos[1] - track.y - th / 2) / max(1, track.h - th), 0.0, 1.0)
                self._scroll[tab] = f * ms

            self._add_hit(track.inflate(12, 0), key="scrollbar", down=scrub, drag=scrub)

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
                self._text_mid(screen, md, "e.g. gohan", _T.TEXT_DIM, inner.x, f.centery)
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

        # buttons (hover computed with the real mouse; base UI is blocked)
        for key, rect, label, acc, icon in (
                ("d_ok", r["ok"], d["confirm_label"], accent, _ic_check if d["kind"] == "input" else _ic_trash),
                ("d_cancel", r["cancel"], "Cancel", _T.TEXT_SECONDARY, None)):
            hov = rect.collidepoint(self._mouse)
            t = self._anim(key, hov)
            self._panel(screen, rect, uk.lerp_color(_CARD, _CARD_HI, t), uk.lerp_color(_T.CARD_BORDER, acc, 0.55 + 0.45 * t))
            fg = uk.lerp_color(_T.TEXT_SECONDARY, acc, 0.55 + 0.45 * t)
            ic = 20 if icon else 0
            tw = self.f_md.width(label)
            x = rect.centerx - (tw + (ic + 10 if icon else 0)) // 2
            if icon:
                icon(screen, pygame.Rect(x, rect.centery - 10, 20, 20), fg, 3)
                x += 30
            self._text_mid(screen, self.f_md, label, fg, x, rect.centery)

    # ══════════════════════════════════════════════════════════════
    #  Tab: Identity
    # ══════════════════════════════════════════════════════════════

    PORTRAIT_BOX          = 132   # px square the portrait preview is framed in
    PORTRAIT_HOLD_SECONDS = 2.5   # how long each form's portrait is shown

    def _tab_identity(self, screen, x, y, w) -> int:
        cfg = self.cfg
        y0 = y
        two = w >= 720
        gap = 40
        lw = (w - gap) // 2 if two else w
        if two:
            hl = self._identity_left(screen, x, y, lw)
            hr = self._identity_right(screen, x + lw + gap, y, w - gap - lw)
            y += max(hl, hr)
        else:
            y += self._identity_left(screen, x, y, w)
            y += 12
            y += self._identity_right(screen, x, y, w)
        y += 14
        desc = cfg.get("description") or ""
        y += self._caption(screen, "Description", x, y, w, right=f"{len(desc)}/600")

        def set_desc(v):
            if cfg.get("description") != v:
                cfg["description"] = v
                self._mark_dirty()

        self._text_field(screen, "desc", pygame.Rect(x, y, w, 132), lambda: cfg.get("description", ""), set_desc,
                         "Short description shown in the scouter...", multiline=True, max_len=600)
        y += 132
        return y - y0

    def _identity_left(self, screen, x, y, w) -> int:
        cfg = self.cfg
        y0, fh = y, self.m_field_h
        y += self._caption(screen, "Basics", x, y, w)
        y += self._field_label(screen, "ID (read-only)", x, y)
        self._static_field(screen, pygame.Rect(x, y, w, fh), self.selected_id or "")
        y += fh + 16

        y += self._field_label(screen, "Display name", x, y)

        def set_name(v):
            if cfg.get("display_name") != v:
                cfg["display_name"] = v
                self._mark_dirty()

        self._text_field(screen, "name", pygame.Rect(x, y, w, fh), lambda: cfg.get("display_name", ""), set_name,
                         "Click to add a display name...", max_len=40)
        y += fh + 16

        y += self._field_label(screen, "Costume", x, y)
        n = len(self.costumes)
        self._stepper(screen, "costume", pygame.Rect(x, y, w, fh), self._current_costume(),
                      f"{self.costume_idx + 1}/{n}" if n > 1 else "",
                      lambda: self._select_costume(-1), lambda: self._select_costume(1), enabled=n > 1)
        y += fh + 22

        y += self._caption(screen, "Sprite and shadow", x, y, w)
        specs = [
            dict(key="sprite_width", label="Sprite width", vmin=4, vmax=256, step=1, fmt="{:.0f}px",
                 get=lambda: cfg.get("sprite_width", 32), set=self._num_setter(cfg, "sprite_width", "int")),
            dict(key="sprite_height", label="Sprite height", vmin=4, vmax=256, step=1, fmt="{:.0f}px",
                 get=lambda: cfg.get("sprite_height", 32), set=self._num_setter(cfg, "sprite_height", "int")),
            dict(key="shadow_size", label="Shadow size", vmin=8, vmax=96, step=4, fmt="{:.0f}px",
                 get=lambda: cfg.get("shadow_size", 32), set=self._num_setter(cfg, "shadow_size", "int")),
        ]
        y += self._slider_grid(screen, x, y, w, specs)

        # Draws assets/sprites/universal/halo.png over the sprite; its offset is
        # tuned in player.py (halo_offset_x / halo_offset_y), not here.
        def toggle_halo():
            cfg["halo_enabled"] = not cfg.get("halo_enabled", False)
            self._mark_dirty()

        y += self._checkbox(screen, "halo", x, y, w, "Halo", bool(cfg.get("halo_enabled", False)), toggle_halo)
        return y - y0

    def _identity_right(self, screen, x, y, w) -> int:
        cfg = self.cfg
        y0 = y
        y += self._caption(screen, "Gate color", x, y, w)

        def set_color(hx):
            cfg["color"] = hx
            self._mark_dirty()

        y += self._color_picker(screen, "gate", x, y, w, lambda: cfg.get("color") or "#FFD700", set_color)
        y += 12
        y += self._note(screen, x, y, w, "Colour-codes anything tied to this character in game, e.g. the number "
                                         "on a level gate locked to them.")
        y += 18
        y += self._caption(screen, "Portrait", x, y, w)
        y += self._identity_portrait(screen, x, y, w)
        return y - y0

    # -- portrait cycle -----------------------------------------------
    def _portrait_cycle_forms(self) -> list:
        """(form_suffix, label) pairs: the selected costume's base look, then
        only *that costume's own* transformations, in order. '' = base look
        (portrait file has no suffix); otherwise the bare form name."""
        forms = [("", "Base")]
        for tf in self.visible_transformations():
            costume = tf.get("costume", "")
            form = costume.split("/")[-1] if costume else ""
            if form:
                forms.append((form, tf.get("display_name") or tf.get("id", "?")))
        return forms

    def _load_portrait(self, form: str, box: int):
        costume = self._current_costume()
        key = (self.selected_id, costume, form, box)
        if key in self._portraits:
            return self._portraits[key]
        surf = None
        path = resolve_portrait_path(self.selected_id, costume, form)
        if path:
            try:
                img = pygame.image.load(str(path)).convert_alpha()
                s = min((box - 16) / max(img.get_width(), 1), (box - 16) / max(img.get_height(), 1))
                surf = pygame.transform.smoothscale(
                    img, (max(1, int(img.get_width() * s)), max(1, int(img.get_height() * s))))
            except Exception:
                surf = None
        self._portraits[key] = surf
        return surf

    def _identity_portrait(self, screen, x, y, w) -> int:
        forms = self._portrait_cycle_forms()
        n = len(forms)
        span = self.PORTRAIT_HOLD_SECONDS * n
        self._portrait_t %= span
        idx = int(self._portrait_t // self.PORTRAIT_HOLD_SECONDS) % n
        form, label = forms[idx]
        box = self.PORTRAIT_BOX
        rect = pygame.Rect(x, y, box, box)
        self._panel(screen, rect, _INSET, _T.CARD_BORDER, 1, 10)
        img = self._load_portrait(form, box)
        if img is not None:
            uk.blit_surface(screen, img, img.get_rect(center=rect.center))
        else:
            self._text_mid(screen, self.f_sm, "NO PORTRAIT", _T.TEXT_DIM, rect.centerx, rect.centery, "c")

        tx = rect.right + 20
        cy = rect.y + 14
        self._text_top(screen, self.f_md, self.f_md.fit(label, w - box - 20), _T.TEXT_PRIMARY, tx, cy, dyn=True)
        if n > 1:
            self._text_top(screen, self.f_sm, f"{idx + 1} of {n}", _T.TEXT_DIM, tx, cy + self.f_md.line_h + 6,
                           dyn=True)
            dot_y = cy + self.f_md.line_h + self.f_sm.line_h + 26
            for i in range(n):
                on = i == idx
                uk.draw_circle_on(screen, _T.GOLD if on else _T.CHIP_BORDER, (tx + 4 + i * 14, dot_y), 4 if on else 3)
        return box

    # ══════════════════════════════════════════════════════════════
    #  Tab: Stats
    # ══════════════════════════════════════════════════════════════

    _STAT_LABELS = [
        ("max_hp",   "Max HP"),
        ("max_ki",   "Max Ki"),
        ("power",    "STR (Melee)"),
        ("ki_power", "POW (Ki Blast)"),
        ("vitality", "END (Defense)"),
        ("speed",    "SPD"),
        ("ki_regen", "Ki Regen"),
    ]

    def _tab_stats(self, screen, x, y, w) -> int:
        stats = self.cfg["stats"]
        y0 = y
        y += self._caption(screen, "Combat stats", x, y, w)
        specs = [dict(key=f"stat:{k}", label=label, vmin=1, vmax=255, step=1, fmt="{:.0f}",
                      get=lambda k=k: stats.get(k, DEFAULT_CONFIG["stats"][k]),
                      set=self._num_setter(stats, k, "int"))
                 for k, label in self._STAT_LABELS]
        y += self._slider_grid(screen, x, y, w, specs)
        return y - y0

    # ══════════════════════════════════════════════════════════════
    #  Tab: Attacks
    # ══════════════════════════════════════════════════════════════

    def _tab_attacks(self, screen, x, y, w) -> int:
        atk = self.cfg["attacks"]
        y0 = y
        y += self._caption(screen, "Ki and melee", x, y, w)
        specs = [
            dict(key="atk:blast_cost", label="Blast Ki cost", vmin=0, vmax=100, step=1, fmt="{:.0f}",
                 get=lambda: atk.get("blast_cost", 20), set=self._num_setter(atk, "blast_cost", "int")),
            dict(key="atk:beam_cost", label="Beam Ki cost", vmin=0, vmax=100, step=1, fmt="{:.0f}",
                 get=lambda: atk.get("beam_cost", 50), set=self._num_setter(atk, "beam_cost", "int")),
            dict(key="atk:melee_duration", label="Melee duration", vmin=0.1, vmax=2.0, step=0.05, fmt="{:.2f}s",
                 get=lambda: atk.get("melee_duration", 0.5), set=self._num_setter(atk, "melee_duration", "f3")),
        ]
        y += self._slider_grid(screen, x, y, w, specs)

        y += self._field_label(screen, "Charged melee style", x, y)

        def set_style(v):
            if atk.get("charged_melee_style") != v:
                atk["charged_melee_style"] = v
                self._mark_dirty()

        y += self._segmented(screen, "melee_style", x, y, min(w, 300), [("lunge", "Lunge"), ("spin", "Spin")],
                             atk.get("charged_melee_style", "lunge"), set_style)
        y += 8
        y += self._note(screen, x, y, w, "What a fully charged melee does: dash forward at the target, or spin "
                                         "in place.")
        y += 22

        equipped = atk.setdefault("equipped_attacks", [])
        y += 4
        y += self._caption(screen, "Equipped attacks", x, y, w,
                           right=f"{len(equipped)} equipped" if self.available_attacks else None)
        if not self.available_attacks:
            y += self._note(screen, x, y, w, "No attacks found in assets/sprites/attacks/ - add an attack folder "
                                             "(e.g. 'ki_blast') to populate this list.")
            return y - y0
        tile_w, tile_h, tg = 116, 124, 14
        cols = max(1, (w + tg) // (tile_w + tg))
        for i, aid in enumerate(self.available_attacks):
            r, c = divmod(i, cols)
            self._attack_tile(screen, aid, pygame.Rect(x + c * (tile_w + tg), y + r * (tile_h + tg), tile_w, tile_h),
                              aid in equipped, equipped)
        rows = (len(self.available_attacks) + cols - 1) // cols
        y += rows * (tile_h + tg)
        y += self._note(screen, x, y, w, "Click an icon to equip / unequip that attack for this character.")
        return y - y0

    def _attack_icon(self, aid: str, box: int):
        key = (aid, box)
        surf = self._icon_cache.get(key)
        if surf is None:
            surf = _fit_surface(load_attack_icon(aid), box, box)
            self._icon_cache[key] = surf
        return surf

    def _attack_tile(self, screen, aid, rect, selected, equipped) -> None:
        t = self._anim(("atk", aid), self._hov(rect))
        lift = int(2 * t)
        r = rect.move(0, -lift)
        base = _SEL if selected else uk.lerp_color(_CARD, _CARD_HI, t)
        border = _T.GOLD if selected else uk.lerp_color(_T.CARD_BORDER, _T.GOLD, 0.78 * t)
        if selected or t > 0:
            uk.draw_soft_glow(screen, r.center, int(r.w * 0.62), _T.GOLD, max_alpha=int(22 if selected else 20 * t))
        self._panel(screen, r, base, border, 2 if selected else 1, 10)
        icon = self._attack_icon(aid, 56)
        uk.blit_surface(screen, icon, icon.get_rect(center=(r.centerx, r.y + 14 + 28)))
        label = aid.replace("_", " ").title()
        fg = _T.TEXT_PRIMARY if selected else uk.lerp_color(_T.TEXT_MUTED, _T.TEXT_PRIMARY, t)
        self._text_mid(screen, self.f_sm, label, fg, r.centerx, r.bottom - 22, "c", max_w=r.w - 14)
        if selected:
            badge = pygame.Rect(0, 0, 22, 22)
            badge.topright = (r.right - 6, r.y + 6)
            uk.draw_circle_on(screen, _T.GOLD, badge.center, 11)
            _ic_check(screen, badge, (30, 24, 10), 3)

        def toggle(pos, aid=aid):
            if aid in equipped:
                equipped.remove(aid)
            else:
                equipped.append(aid)
            self._mark_dirty()

        self._add_hit(rect, key=("atk", aid), down=toggle)

    # ══════════════════════════════════════════════════════════════
    #  Tab: Transformations
    # ══════════════════════════════════════════════════════════════

    def _tab_transform(self, screen, x, y, w) -> int:
        y0, fh = y, self.m_field_h
        visible = self.visible_transformations()
        has = bool(visible)
        if has and not (0 <= self.tf_idx < len(visible)):
            self.tf_idx = 0
            self._sync_tf_form_idx()
        costume = self._current_costume()

        y += self._field_label(screen, f"Transformation ({costume})", x, y)
        sq = fh
        st_w = _clamp(w - sq - sq - 24, 200, 440)
        if has:
            tf0 = visible[self.tf_idx]
            name = tf0.get("display_name") or tf0.get("id", "-")
            sub = f"{self.tf_idx + 1}/{len(visible)}"
        else:
            name, sub = "- none -", ""
        self._stepper(screen, "tf_sel", pygame.Rect(x, y, st_w, fh), name, sub,
                      lambda: self._tf_step(-1), lambda: self._tf_step(1), enabled=has and len(visible) > 1)
        bx = x + st_w + 12
        self._icon_btn(screen, "tf_add", pygame.Rect(bx, y, sq, sq), self._icon_plus_png,
                       on_click=lambda: self._open_input(
                           "New transformation",
                           f"Enter an ID for the new transformation of '{costume}' (e.g. ssj, ssj2, kaioken).",
                           self._do_add_transformation),
                       tip="Add a transformation to this costume")
        self._icon_btn(screen, "tf_rem", pygame.Rect(bx + sq + 12, y, sq, sq), self._icon_trash_png,
                       danger=True, enabled=has,
                       on_click=lambda: self._open_confirm(
                           "Remove transformation",
                           f"Remove '{(self._tf() or {}).get('id', '')}' from the list?",
                           self._do_remove_transformation, "Remove"),
                       tip="Remove the selected transformation")
        y += fh + 24

        if not has:
            y += self._note(screen, x, y, w, f"'{costume}' has no transformations yet. Click Add to create one "
                                              "(e.g. 'ssj', 'ssj2', 'kaioken').", _T.TEXT_MUTED, self.f_md)
            return y - y0

        tf = visible[self.tf_idx]
        tfk = tf.get("costume") or tf.get("id", "")
        y += self._caption(screen, "Edit selected", x, y, w)

        # Display name + Form + Requires side by side when there's room.
        cols = 3 if w >= 900 else (2 if w >= 620 else 1)
        gap = 24
        cw = (w - gap * (cols - 1)) // cols
        cells = []

        def set_name(v):
            if tf.get("display_name") != v:
                tf["display_name"] = v
                self._mark_dirty()

        cells.append(("Display name", lambda r: self._text_field(
            screen, f"tf_name:{tfk}", r, lambda: tf.get("display_name", ""), set_name, "Display name",
            max_len=40)))
        forms = self.transform_forms
        form_txt = forms[self.tf_form_idx] if forms and 0 <= self.tf_form_idx < len(forms) else "-"
        cells.append(("Form (sprite folder)", lambda r: self._stepper(
            screen, "tf_form", r, form_txt, f"{self.tf_form_idx + 1}/{len(forms)}" if len(forms) > 1 else "",
            lambda: self._tf_form_step(-1), lambda: self._tf_form_step(1), enabled=len(forms) > 1)))

        options = [None] + self._requires_candidates()
        cur = tf.get("requires")
        if cur:
            req_tf = next((t for t in visible if self._form_name_of(t) == cur), None)
            req_txt = (req_tf.get("display_name") if req_tf else None) or cur
        else:
            req_txt = "None (base form)"
        cells.append(("Requires", lambda r: self._stepper(
            screen, "tf_req", r, req_txt, "", lambda: self._tf_requires_step(-1),
            lambda: self._tf_requires_step(1), enabled=len(options) > 1)))

        for i in range(0, len(cells), cols):
            for c, (label, draw_cell) in enumerate(cells[i:i + cols]):
                cx = x + c * (cw + gap)
                ly = self._field_label(screen, label, cx, y)
                draw_cell(pygame.Rect(cx, y + ly, cw, fh))
            y += (self.f_sm.cap_h + 8) + fh + 18

        y += 6
        y += self._caption(screen, "Multipliers and size", x, y, w)
        specs = [
            dict(key="tf:sprite_width", label="Sprite width", vmin=4, vmax=256, step=1, fmt="{:.0f}px",
                 get=lambda: tf.get("sprite_width", self.cfg.get("sprite_width", 32)),
                 set=self._num_setter(tf, "sprite_width", "int")),
            dict(key="tf:sprite_height", label="Sprite height", vmin=4, vmax=256, step=1, fmt="{:.0f}px",
                 get=lambda: tf.get("sprite_height", self.cfg.get("sprite_height", 32)),
                 set=self._num_setter(tf, "sprite_height", "int")),
            dict(key="tf:power_mult", label="Power multiplier", vmin=0.5, vmax=5.0, step=0.05, fmt="{:.2f}x",
                 get=lambda: tf.get("power_mult", 1.0), set=self._num_setter(tf, "power_mult", "f2")),
            dict(key="tf:defense_mult", label="Defense multiplier", vmin=0.5, vmax=5.0, step=0.05, fmt="{:.2f}x",
                 get=lambda: tf.get("defense_mult", 1.0), set=self._num_setter(tf, "defense_mult", "f2")),
            dict(key="tf:speed_mult", label="Speed multiplier", vmin=0.5, vmax=5.0, step=0.05, fmt="{:.2f}x",
                 get=lambda: tf.get("speed_mult", 1.0), set=self._num_setter(tf, "speed_mult", "f2")),
            dict(key="tf:ki_drain", label="Ki drain", vmin=0.0, vmax=50.0, step=0.5, fmt="{:.1f}/s",
                 get=lambda: tf.get("ki_drain", 0.0), set=self._num_setter(tf, "ki_drain", "f1")),
        ]
        # Sliders are keyed by form so a drag can't carry over between forms.
        for sp in specs:
            sp["key"] = f"{sp['key']}:{tfk}"
        y += self._slider_grid(screen, x, y, w, specs)

        y += 4
        y += self._caption(screen, "Transformation sequence", x, y, w)
        bar_on = bool(tf.get("ki_bar_enabled", True))

        def toggle_bar():
            tf["ki_bar_enabled"] = not bool(tf.get("ki_bar_enabled", True))
            self._mark_dirty()

        y += self._checkbox(screen, f"tf_bar:{tfk}", x, y, w, "Show charge bar", bar_on, toggle_bar)
        y += 10
        if bar_on:
            spec = [dict(key=f"tf:charge_duration:{tfk}", label="Charge duration", vmin=0.5, vmax=10.0, step=0.05,
                         fmt="{:.2f}s", get=lambda: tf.get("charge_duration") or 3.75,
                         set=self._num_setter(tf, "charge_duration", "f2"))]
            y += self._slider_grid(screen, x, y, w, spec)
        else:
            y += self._note(screen, x, y, w, "Bar disabled - the transform animation plays through at its own "
                                             "pace.") + 14

        y += 6
        y += self._caption(screen, "Ki bar color", x, y, w)
        custom = bool(tf.get("ki_color"))

        def toggle_color():
            if tf.get("ki_color"):
                self._tf_color_memory[tfk] = tf["ki_color"]
                tf["ki_color"] = None
            else:
                tf["ki_color"] = self._tf_color_memory.get(tfk) or "#FFD700"
            self._mark_dirty()

        y += self._checkbox(screen, f"tf_kic:{tfk}", x, y, w, "Custom", custom, toggle_color)
        if custom:
            y += 12

            def set_ki(hx):
                tf["ki_color"] = hx
                self._mark_dirty()

            y += self._color_picker(screen, f"kicolor:{tfk}", x, y, min(w, 420), lambda: tf.get("ki_color") or "#FFD700",
                                    set_ki)
        else:
            y += 6
            y += self._note(screen, x, y, w, "Off: the bar keeps the colours baked into its artwork.")
        return y - y0

    # ══════════════════════════════════════════════════════════════
    #  Tab: Preview (every animation of one form, animated)
    # ══════════════════════════════════════════════════════════════

    def _thumb_frames(self, form: str, box: int) -> list:
        key = (self.selected_id, form, box)
        frames = self._thumbs.get(key)
        if frames is None:
            frames = [_fit_surface(f, box, box) for f in load_walk_frames(self.selected_id, form)]
            self._thumbs[key] = frames
        return frames

    def _ensure_grid(self) -> None:
        key = (self.selected_id, self.preview_form)
        if self._grid_key != key:
            self._grid_key = key
            self._grid_anims = discover_animations(self.selected_id, self.preview_form)
            self._grid_scaled_cache = {}

    def _grid_frames(self, name: str, area_w: int, area_h: int) -> list:
        key = (name, area_w, area_h)
        frames = self._grid_scaled_cache.get(key)
        if frames is None:
            frames = [_fit_surface(f, area_w, area_h) for f in self._grid_anims.get(name, [])]
            self._grid_scaled_cache[key] = frames
        return frames

    def _tab_preview(self, screen, x, y, w) -> int:
        y0 = y
        forms = self._all_preview_forms()
        y += self._caption(screen, "Form", x, y, w, right=f"{len(forms)}")
        gap, chip_h = 12, 68
        cols = max(1, (w + gap) // (230 + gap))
        chip_w = (w - gap * (cols - 1)) // cols
        for i, form in enumerate(forms):
            r, c = divmod(i, cols)
            rect = pygame.Rect(x + c * (chip_w + gap), y + r * (chip_h + gap), chip_w, chip_h)
            self._form_chip(screen, form, rect, i, len(forms))
        y += ((len(forms) + cols - 1) // cols) * (chip_h + gap) + 8

        self._ensure_grid()
        anims = self._grid_anims
        y += self._caption(screen, "Animations", x, y, w, right=f"{len(anims)}")
        if not anims:
            y += self._note(screen, x, y, w, "No animations found for this character / form.", _T.TEXT_MUTED,
                            self.f_md)
            return y - y0
        cols = max(2, (w + gap) // (200 + gap))
        cw = (w - gap * (cols - 1)) // cols
        ch = min(cw + 26, 250)
        head = self.f_sm.line_h + 18
        for i, name in enumerate(anims):
            r, c = divmod(i, cols)
            rect = pygame.Rect(x + c * (cw + gap), y + r * (ch + gap), cw, ch)
            if self._vp is not None and not rect.colliderect(self._vp):
                continue
            self._panel(screen, rect, _CARD, _T.CARD_BORDER, 1, 10)
            self._text_top(screen, self.f_sm, self.f_sm.fit(name.upper(), rect.w - 24), _T.TEXT_MUTED,
                           rect.x + 12, rect.y + 12)
            frames = self._grid_frames(name, rect.w - 24, rect.h - head - 20)
            if frames:
                fi = int(self._clock) % len(frames)
                fr = frames[fi]
                cy = rect.y + head + (rect.h - head - 10) // 2
                uk.blit_surface(screen, fr, fr.get_rect(center=(rect.centerx, cy)))
                self._text_top(screen, self.f_sm, f"{fi + 1}/{len(frames)}", _T.TEXT_DIM,
                               rect.right - 12, rect.bottom - 12 - self.f_sm.cap_h, "r", dyn=True)
        y += ((len(anims) + cols - 1) // cols) * (ch + gap)
        return y - y0

    def _form_chip(self, screen, form, rect, i, n) -> None:
        sel = form == self.preview_form
        t = self._anim(("chip", form), self._hov(rect) and not sel)
        r = rect.move(0, -int(2 * t))
        self._panel(screen, r, _SEL if sel else uk.lerp_color(_CARD, _CARD_HI, t),
                    _T.GOLD if sel else uk.lerp_color(_T.CARD_BORDER, _T.GOLD, 0.78 * t), 2 if sel else 1, 10)
        box = r.h - 20
        thumb = pygame.Rect(r.x + 10, r.y + 10, box, box)
        uk.draw_rect_on(screen, _INSET, thumb, 0, 8)
        frames = self._thumb_frames(form, box - 6)
        if frames:
            fr = frames[int(self._clock) % len(frames)]
            uk.blit_surface(screen, fr, fr.get_rect(center=thumb.center))
        label = form.split("/")[-1]
        tx = thumb.right + 14
        fg = _T.GOLD_BRIGHT if sel else uk.lerp_color(_T.TEXT_SECONDARY, _T.TEXT_PRIMARY, t)
        self._text_mid(screen, self.f_md, label, fg, tx, r.centery - 9, max_w=r.right - tx - 12)
        kind = "BASE" if form == self._current_costume() else "TRANSFORMATION"
        self._text_mid(screen, self.f_sm, kind, _T.TEXT_DIM, tx, r.centery + 12, max_w=r.right - tx - 12)
        self._add_hit(rect, key=("chip", form), down=lambda p, f=form: self._set_preview_form(f))

    # ══════════════════════════════════════════════════════════════
    #  Tab: Settings (global, not per-character)
    # ══════════════════════════════════════════════════════════════

    def _tab_settings(self, screen, x, y, w) -> int:
        y0 = y
        y += self._caption(screen, "Game settings", x, y, w)
        gs = self.global_settings

        def set_max(v):
            v = int(round(v))
            if gs.get("max_level") != v:
                gs["max_level"] = v
                self._settings_pending = True

        sw = min(w, 560)
        # Persisted when the drag ends (see _flush_settings) rather than on every mouse-move.
        hit_before = len(self._hits)
        y += self._slider(screen, "max_level", x, y, sw, "Max level", gs.get("max_level", 99), 1, 999, 1,
                          "{:.0f}", set_max)
        if len(self._hits) > hit_before:
            self._hits[-1]["up"] = lambda pos: self._flush_settings()
        y += 14
        y += self._note(screen, x, y, sw, "Applies game-wide (all characters share the same level cap). "
                                          "Takes effect next time the game starts.")
        return y - y0