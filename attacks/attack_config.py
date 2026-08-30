"""
attacks/attack_config.py — Universal schema + data-driven runtime for
EVERY attack archetype, in one file.

This is the one place all "build an attack from data" machinery lives:
shared primitives at the top, then one clearly-delimited section per
archetype (beam today; projectile/melee/charge-wrapper/chain later),
each following the same shape. dev_tools/attack_creator.py and game.py
only ever need to import from here.

────────────────────────────────────────────────────────────────────────
HOW TO ADD A NEW ARCHETYPE
────────────────────────────────────────────────────────────────────────
1. Add a new "SECTION — <name> archetype" block below, containing:
     - <NAME>_GROUPS / <NAME>_FIELDS  (FieldSpec schema, see BEAM_GROUPS)
     - a <Name>AttackConfig(AttackConfigBase) subclass — set `archetype`
       and `PARAM_SETS`, implement `build_attack(...)` (and
       `build_charge_effect(...)` / whatever else that archetype needs
       to hand back real, playable objects).
2. Register it: add `"<name>": <Name>AttackConfig` to ARCHETYPES at the
   bottom of this file.
3. That's it for this file — `load_config()`, `list_saved_configs()`,
   save/load/clone/to_dict are all inherited from AttackConfigBase, so a
   new archetype doesn't re-implement any of that plumbing.
   (dev_tools/attack_creator.py's UI is a separate step — its sidebar/
   save/load calls already go through the functions here, but its
   parameter-panel and stage-preview code is still beam-specific and
   will need an archetype-aware branch when the second one lands.)

────────────────────────────────────────────────────────────────────────
SECTION — Shared primitives
────────────────────────────────────────────────────────────────────────
FieldSpec is the single source of truth for "what one configurable value
looks like" across every archetype. It's consumed by two very different
things, on purpose:

  1. Each archetype's AttackConfigBase subclass — reads DEFAULT values
     and NULLABLE-ness to turn a JSON dict into real, correctly-typed
     kwargs for the underlying game classes (BeamAttack, a future
     Projectile-family class, etc.).
  2. dev_tools/attack_creator.py — reads LABEL/GROUP/KIND/MIN/MAX/CHOICES
     to auto-generate the parameter editor panel, so adding a field to a
     schema below is enough to make it editable in the tool. Nobody
     hand-builds a bespoke widget per attack.

AttackConfigBase is the shared skeleton every archetype's config class
inherits: id/display_name, generic load/save/to_dict/clone, and a
uniform way of turning a flat FieldSpec list into a defaulted dict and
back. What's NOT generic (because it's genuinely different per archetype)
is `build_attack()` and friends — each subclass implements those against
its own real game classes.
"""

from __future__ import annotations

import json
import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Any, List, Tuple, Dict, Type

from attacks.beam import BeamAttack, KamehamehaChargeEffect
from attacks.flame_kamehameha import FlameKamehamehaAttack
from attacks.burning_attack import BurningAttack, BurningChargeEffect
from attacks.energy_sword import EnergySwordChargeEffect, EnergySwordSpinEffect
from attacks.dragon_fist import DragonFistAttack
from attacks.genkidama import GenkidamaChargeEffect, GenkidamaBlast

CONFIG_VERSION = 1


@dataclass(frozen=True)
class FieldSpec:
    key: str                    # must match the underlying class's kwarg name
    label: str                  # shown in the editor UI
    kind: str                   # 'int' | 'float' | 'bool' | 'str' | 'choice'
    default: Any
    nullable: bool = False       # True == "None means: inherit from another field / class default"
    min: Optional[float] = None
    max: Optional[float] = None
    step: float = 1
    choices: Optional[Tuple[str, ...]] = None
    help: str = ""               # one-line tooltip/status-bar text


def field_defaults(fields: List[FieldSpec]) -> dict:
    """A fresh {key: default} dict for the given field list."""
    return {f.key: f.default for f in fields}


def field_by_key(fields: List[FieldSpec], key: str) -> Optional[FieldSpec]:
    for f in fields:
        if f.key == key:
            return f
    return None


def flatten_groups(groups: List[Tuple[str, List[FieldSpec]]]) -> List[FieldSpec]:
    """FIELD_GROUPS (section name -> fields, for the editor UI) to a flat
    FieldSpec list (for defaulting/validation) — every archetype below
    derives its *_FIELDS list from its *_GROUPS this way, so the two
    never drift out of sync."""
    return [f for _, fields in groups for f in fields]


class AttackConfigBase:
    """Shared skeleton for every archetype's config class.

    A concrete subclass sets:
        archetype: str                        — e.g. "beam", "projectile"
        PARAM_SETS: Dict[str, List[FieldSpec]] — named groups of scalar
            fields, e.g. {"beam": BEAM_FIELDS, "charge": CHARGE_FIELDS}.
            Each set becomes `self.params["<name>"]`, a plain dict.
        OPTIONAL_SETS: set of param-set names that can be toggled off
            entirely (get an `<name>_enabled` bool) — e.g. "charge" for
            beam-family attacks, since not every attack needs a wind-up.

    and may override the `_extra_*` hooks below for any param set that
    needs something beyond flat scalars (beam-family's "charge" set has
    one: `direction_offsets`, a per-direction (x, y) dict, edited in the
    UI as four numeric pairs rather than one opaque field).

    Every subclass still writes its own `build_attack(...)` (and any
    other `build_*` methods for secondary objects like a charge effect)
    — turning params into real, playable game objects is the one part
    that's genuinely archetype-specific and isn't abstracted here.
    """

    archetype: str = ""
    PARAM_SETS: Dict[str, List[FieldSpec]] = {}
    OPTIONAL_SETS: set = frozenset()
    # False for every archetype except "genkidama" (see GenkidamaAttackConfig
    # below). Every archetype so far fires either immediately (no charge) or
    # once its charge-up finishes on its own timer, with release triggering
    # decay/stop on whatever's already flying (see dev_tools/attack_creator.py's
    # _on_fire_release). Genkidama is the odd one out: there is no "finished
    # charging" moment at all — you hold to build up through power states 1-5
    # and RELEASE is what fires it, at whatever state it's currently sitting
    # in. Setting this True is what tells that generic release handler to
    # build the real attack right then (passing along the live charge object
    # so its current state/sprite can be read off) instead of just cancelling
    # the charge the way every other archetype's mid-charge release does.
    fires_on_release: bool = False
    # set_name -> its FIELD_GROUPS (section name -> fields), the *_GROUPS
    # list each archetype section below builds its *_FIELDS from. Purely
    # for UI consumers (dev_tools/attack_creator.py's ParamPanel) that want
    # the same section headers the field-flattening already derived
    # PARAM_SETS from — nothing in this file reads it. Optional: a subclass
    # that leaves this empty just means its editor renders one flat,
    # unsectioned list instead of grouped headers.
    GROUPS: Dict[str, List[Tuple[str, List[FieldSpec]]]] = {}
    # set_name -> names of non-scalar keys handled by _extra_from_data/
    # _extra_to_dict instead of the generic FieldSpec pipeline (e.g. beam's
    # "charge" set has "direction_offsets", a dict rather than a scalar).
    # Declaring them here is what keeps _apply_known_fields from flagging
    # them as unrecognized.
    EXTRA_KEYS: Dict[str, Tuple[str, ...]] = {}

    def __init__(self, data: Optional[dict] = None):
        data = data or {}
        self.id: str = data.get("id", "untitled_attack")
        self.display_name: str = data.get("display_name", self.id.replace("_", " ").title())

        self.params: Dict[str, dict] = {}
        self.set_enabled: Dict[str, bool] = {}

        for set_name, fields in self.PARAM_SETS.items():
            incoming = data.get(set_name, {})
            values = field_defaults(fields)
            reserved = {"enabled"} | set(self.EXTRA_KEYS.get(set_name, ()))
            self._apply_known_fields(values, fields, incoming, reserved, context=f"{self.archetype}.{set_name}")
            self.params[set_name] = values
            if set_name in self.OPTIONAL_SETS:
                self.set_enabled[set_name] = bool(incoming.get("enabled", True))
            self._extra_from_data(set_name, incoming)

    @staticmethod
    def _apply_known_fields(target: dict, fields: List[FieldSpec], incoming: dict, reserved: set, context: str) -> None:
        valid_keys = {f.key for f in fields}
        for key, value in incoming.items():
            if key in valid_keys:
                target[key] = value
            elif key not in reserved:
                print(f"[attack_config] Ignoring unknown {context} field '{key}' in config")

    # ── hooks for non-scalar, per-archetype extras (override as needed) ──
    def _extra_from_data(self, set_name: str, incoming: dict) -> None:
        """Called once per param set while loading — override to pull
        non-scalar values (dicts, lists) out of `incoming` into instance
        attributes. No-op by default."""
        pass

    def _extra_to_dict(self, set_name: str) -> dict:
        """Override to merge non-scalar extras back into the dict for
        `to_dict()`'s output for this param set. Returns {} by default."""
        return {}

    # ── load / save / clone — identical for every archetype ─────────
    @classmethod
    def load(cls, path) -> "AttackConfigBase":
        with open(path, "r") as f:
            return cls(json.load(f))

    def to_dict(self) -> dict:
        out = {
            "version": CONFIG_VERSION,
            "archetype": self.archetype,
            "id": self.id,
            "display_name": self.display_name,
        }
        for set_name in self.PARAM_SETS:
            block = copy.deepcopy(self.params[set_name])
            block.update(self._extra_to_dict(set_name))
            if set_name in self.OPTIONAL_SETS:
                block["enabled"] = self.set_enabled[set_name]
            out[set_name] = block
        return out

    def save(self, path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    def clone(self, new_id: str, new_display_name: Optional[str] = None) -> "AttackConfigBase":
        """Used by the creator's 'Duplicate' action — start a new attack
        from an existing one's values (the same workflow final_flash.py /
        banshee_blast.py follow by hand: copy a working attack, retune it)."""
        data = self.to_dict()
        data["id"] = new_id
        data["display_name"] = new_display_name or new_id.replace("_", " ").title()
        return type(self)(data)

    @staticmethod
    def _kwargs(param_dict: dict) -> dict:
        """Every nullable field uses None to mean 'let the underlying
        class use its own default' — filtering None out in one place is
        what makes that convention work for every param set/archetype."""
        return {k: v for k, v in param_dict.items() if v is not None}


def list_saved_configs(configs_dir, archetype: Optional[str] = None) -> list:
    """Scan a directory for *.json attack configs of any archetype, for
    the creator's sidebar list and for game.py to enumerate player-made
    attacks at load time. Pass `archetype` to filter to just one kind.
    Skips anything that fails to parse rather than crashing the whole
    scan. Returns (path, id, display_name, archetype) tuples."""
    configs_dir = Path(configs_dir)
    if not configs_dir.exists():
        return []
    results = []
    for path in sorted(configs_dir.glob("*.json")):
        try:
            with open(path, "r") as f:
                data = json.load(f)
            found_archetype = data.get("archetype", "beam")
            if archetype is not None and found_archetype != archetype:
                continue
            results.append((path, data.get("id", path.stem), data.get("display_name", path.stem), found_archetype))
        except Exception as e:
            print(f"[attack_config] Skipping unreadable config {path}: {e}")
    return results


def load_config(path) -> AttackConfigBase:
    """Archetype-agnostic loader: reads the JSON's own `archetype` field
    and dispatches to the right registered config class. Use this
    (rather than a specific e.g. BeamAttackConfig.load) anywhere that
    needs to load a config without knowing in advance what kind it is —
    e.g. game.py enumerating every player-made attack at startup."""
    with open(path, "r") as f:
        data = json.load(f)
    archetype = data.get("archetype", "beam")
    cls = ARCHETYPES.get(archetype)
    if cls is None:
        raise ValueError(f"Unknown attack archetype '{archetype}' in {path}")
    return cls(data)


# ═══════════════════════════════════════════════════════════════════════
# SECTION — Beam archetype (final_flash / banshee_blast / big_bang_
# kamehameha style: BeamAttack + optional KamehamehaChargeEffect)
# ═══════════════════════════════════════════════════════════════════════

# Excludes x, y, direction (runtime spawn args, not attack identity) and
# `scale` (left at the engine's RENDER_SCALE unless deliberately overridden
# — exposed as an advanced field at the bottom instead of cluttering the
# top of the form).
BEAM_GROUPS: List[Tuple[str, List[FieldSpec]]] = [
    ("Identity", [
        FieldSpec("attack_name", "Asset folder (attack_name)", "str", "kamehameha",
                   help="assets/sprites/attacks/{attack_name}/ — begin/middle/end/etc. sheets live here"),
    ]),

    ("Sizing — shared default", [
        FieldSpec("frame_width", "Frame width (fallback)", "int", 16, min=1, max=256),
        FieldSpec("frame_height", "Frame height (fallback)", "int", 16, min=1, max=256),
    ]),

    ("Sizing — per-part overrides", [
        FieldSpec("begin_frame_width", "Begin width", "int", None, nullable=True, min=1, max=256,
                   help="blank = use Frame width"),
        FieldSpec("begin_frame_height", "Begin height", "int", None, nullable=True, min=1, max=256,
                   help="blank = use Frame height"),
        FieldSpec("middle_frame_width", "Middle width", "int", 6, min=1, max=256),
        FieldSpec("middle_frame_height", "Middle height", "int", 6, min=1, max=256),
        FieldSpec("end_frame_width", "End width", "int", None, nullable=True, min=1, max=256,
                   help="blank = use Frame width"),
        FieldSpec("end_frame_height", "End height", "int", None, nullable=True, min=1, max=256,
                   help="blank = use Frame height"),
        FieldSpec("collision_frame_width", "Collision width", "int", None, nullable=True, min=1, max=256,
                   help="blank = use Frame width"),
        FieldSpec("collision_frame_height", "Collision height", "int", None, nullable=True, min=1, max=256,
                   help="blank = use Frame height"),
        FieldSpec("decay_frame_width", "Decay width", "int", None, nullable=True, min=1, max=256,
                   help="blank = use Middle width"),
        FieldSpec("decay_frame_height", "Decay height", "int", None, nullable=True, min=1, max=256,
                   help="blank = use Middle height"),
    ]),

    ("Growth & travel", [
        FieldSpec("instant_length", "Instant length (no ramp)", "bool", False,
                   help="Final Flash style — snaps straight to full reach"),
        FieldSpec("grow_speed", "Grow speed (px/s)", "float", 900, min=0, max=5000, step=10),
        FieldSpec("instant_reach", "Instant reach (px)", "int", 5000, min=100, max=20000, step=100,
                   help="only used when Instant length is on and nothing obstructs it"),
    ]),

    ("Decay / release", [
        FieldSpec("decay_style", "Decay style", "choice", "sweep", choices=("sweep", "thickness"),
                   help="sweep = retracting front along the beam; thickness = closes like a pillar"),
        FieldSpec("decay_speed", "Decay sweep speed (px/s)", "float", 900, min=0, max=5000, step=10),
        FieldSpec("decay_uses_begin_sprite", "Decay reuses Begin sprite", "bool", False,
                   help="skip loading decay_{attack_name}.png entirely"),
    ]),

    ("Thickness animation", [
        FieldSpec("thickness_grow_duration", "Thickness grow-in (s)", "float", 0.0, min=0, max=5, step=0.05,
                   help="0 = full width instantly, like a plain Kamehameha"),
        FieldSpec("thickness_shrink_duration", "Thickness shrink-out (s)", "float", 0.0, min=0, max=5, step=0.05),
    ]),

    ("Orientation", [
        FieldSpec("rotate_to_direction", "Rotate right-facing art", "bool", False,
                   help="on: sheets are single-row, drawn facing right, rotated per direction. "
                        "off: sheets have one row per direction (kamehameha convention)"),
        FieldSpec("begin_overlap_ratio", "Begin/middle overlap ratio", "float", 0.5, min=0, max=1, step=0.05,
                   help="0.5 for tapered begin art (plain kamehameha); 1.0 for edge-to-edge begin art"),
        FieldSpec("middle_sync_random", "Middle tiles flicker in sync", "bool", False,
                   help="banshee_blast style — every middle tile shows the same random frame at once"),
    ]),

    ("Enemy contact", [
        FieldSpec("ignore_enemy_obstruction", "Passes through enemies", "bool", False,
                   help="still damages on contact (enemy.py's job) — just doesn't stop/show the impact tip"),
        FieldSpec("push_force", "Push force override (px/frame)", "float", None, nullable=True, min=0, max=20, step=0.5,
                   help="blank = use the enemy's own default push force; 0 = no pushback at all"),
    ]),

    ("Ball / circle overlay (optional)", [
        FieldSpec("ball_frame_width", "Ball width", "int", None, nullable=True, min=1, max=256,
                   help="blank = use Frame width. Leave both ball fields blank and skip the art file for no overlay."),
        FieldSpec("ball_frame_height", "Ball height", "int", None, nullable=True, min=1, max=256),
        FieldSpec("circle_frame_width", "Circle width", "int", None, nullable=True, min=1, max=256),
        FieldSpec("circle_frame_height", "Circle height", "int", None, nullable=True, min=1, max=256),
        FieldSpec("ball_gap", "Ball gap from player (px)", "int", 0, min=-200, max=200),
        FieldSpec("circle_gap", "Circle gap from ball (px)", "int", 0, min=-200, max=200),
        FieldSpec("beam_gap", "Beam gap from circle (px)", "int", 0, min=-200, max=200),
    ]),

    ("Advanced", [
        FieldSpec("scale", "Render scale override", "float", None, nullable=True, min=0.5, max=8, step=0.5,
                   help="blank = use the engine's RENDER_SCALE"),
    ]),
]
BEAM_FIELDS: List[FieldSpec] = flatten_groups(BEAM_GROUPS)


# Excludes `player` (runtime-only) and `scale` (same advanced override
# pattern as beam.scale). direction_offsets is intentionally NOT a single
# field here — see BEAM_DIRECTIONS/BEAM_DEFAULT_DIRECTION_OFFSETS below,
# edited as four separate (x, y) pairs in the UI instead of one opaque
# dict field (see BeamAttackConfig._extra_from_data/_extra_to_dict).
CHARGE_GROUPS: List[Tuple[str, List[FieldSpec]]] = [
    ("Identity", [
        FieldSpec("attack_name", "Charge asset folder", "str", "kamehameha",
                   help="assets/sprites/attacks/{attack_name}/charging_{attack_name}.png — "
                        "can differ from the fired beam's own attack_name (e.g. reusing plain "
                        "kamehameha's charge-up art)"),
    ]),
    ("Sizing", [
        FieldSpec("frame_width", "Frame width", "int", 16, min=1, max=256),
        FieldSpec("frame_height", "Frame height", "int", 16, min=1, max=256),
    ]),
    ("Timing & playback", [
        FieldSpec("target_charge_duration", "Charge duration (s)", "float", 1.0, min=0.1, max=10, step=0.1),
        FieldSpec("pulse_steps", "Pulse steps after run-up", "int", 4, min=0, max=20,
                   help="0 = straight run-up that holds on the last frame (e.g. banshee_blast)"),
        FieldSpec("hold_after_pulse", "Freeze after pulse (don't loop)", "bool", False),
    ]),
    ("Orientation", [
        FieldSpec("rotate_to_direction", "Rotate right-facing art", "bool", False,
                   help="sheet is drawn facing right only; rotated to match fire direction"),
    ]),
]
CHARGE_FIELDS: List[FieldSpec] = flatten_groups(CHARGE_GROUPS)

BEAM_DIRECTIONS: Tuple[str, ...] = ("down", "left", "right", "up")
BEAM_DEFAULT_DIRECTION_OFFSETS = {
    "down": (-4, 21), "left": (8, 23), "right": (-8, 23), "up": (3, 21),
}
# Zero-default per-direction offset, shared by every archetype's *own*
# (non-charge) spawn-position field — beam_offsets, chain_offsets, etc.
# Unlike direction_offsets (which nudges the charge-up effect relative to
# a player who visually needs one), the fired attack itself has always
# spawned exactly at the actor's position with no offset, so (0, 0) is
# the only default that doesn't change existing configs' behavior.
ZERO_DIRECTION_OFFSETS = {d: (0, 0) for d in BEAM_DIRECTIONS}


class BeamAttackConfig(AttackConfigBase):
    """Beam-family attack: `self.params["beam"]` maps 1:1 to BeamAttack's
    kwargs, `self.params["charge"]` to KamehamehaChargeEffect's (gated by
    `self.set_enabled["charge"]`). See attacks/beam.py for the real
    classes this builds.
    """

    archetype = "beam"
    PARAM_SETS = {"beam": BEAM_FIELDS, "charge": CHARGE_FIELDS}
    OPTIONAL_SETS = {"charge"}
    EXTRA_KEYS = {"charge": ("direction_offsets",), "beam": ("beam_offsets",)}
    GROUPS = {"beam": BEAM_GROUPS, "charge": CHARGE_GROUPS}

    def __init__(self, data: Optional[dict] = None):
        self.direction_offsets: dict = dict(BEAM_DEFAULT_DIRECTION_OFFSETS)
        # Per-direction (x, y) spawn-position offset for the beam itself,
        # same shape/convention as direction_offsets above but applied to
        # build_attack() instead of build_charge_effect(). Defaults to
        # (0, 0) — i.e. exactly where it always spawned before this field
        # existed — so old configs load unchanged until someone drags it.
        self.beam_offsets: dict = dict(ZERO_DIRECTION_OFFSETS)
        super().__init__(data)

    # convenience aliases — read better than self.params["beam"] at call sites
    @property
    def beam(self) -> dict:
        return self.params["beam"]

    @property
    def charge(self) -> dict:
        return self.params["charge"]

    @property
    def charge_enabled(self) -> bool:
        return self.set_enabled["charge"]

    @charge_enabled.setter
    def charge_enabled(self, value: bool) -> None:
        self.set_enabled["charge"] = bool(value)

    def _extra_from_data(self, set_name: str, incoming: dict) -> None:
        if set_name == "charge":
            offsets = incoming.get("direction_offsets") or {}
            self.direction_offsets = {
                d: tuple(offsets.get(d, BEAM_DEFAULT_DIRECTION_OFFSETS[d])) for d in BEAM_DIRECTIONS
            }
        elif set_name == "beam":
            offsets = incoming.get("beam_offsets") or {}
            self.beam_offsets = {
                d: tuple(offsets.get(d, ZERO_DIRECTION_OFFSETS[d])) for d in BEAM_DIRECTIONS
            }

    def _extra_to_dict(self, set_name: str) -> dict:
        if set_name == "charge":
            return {"direction_offsets": {d: list(v) for d, v in self.direction_offsets.items()}}
        if set_name == "beam":
            return {"beam_offsets": {d: list(v) for d, v in self.beam_offsets.items()}}
        return {}

    # ── building the real objects ───────────────────────────────────
    def build_attack(self, x: float, y: float, direction: str, player=None) -> BeamAttack:
        # player=None accepted-and-ignored here purely so every archetype's
        # build_attack shares one call signature — see the "sword" archetype
        # below (EnergySwordAttackConfig), whose fired object genuinely
        # needs a live player reference rather than a one-time x/y.
        ox, oy = self.beam_offsets.get(direction, (0, 0))
        return BeamAttack(x + ox, y + oy, direction, **self._kwargs(self.beam))

    def build_charge_effect(self, player) -> Optional[KamehamehaChargeEffect]:
        if not self.charge_enabled:
            return None
        kwargs = self._kwargs(self.charge)
        kwargs["direction_offsets"] = dict(self.direction_offsets)
        return KamehamehaChargeEffect(player, **kwargs)

    # ── validation ──────────────────────────────────────────────────
    def missing_asset_warnings(self, assets_root: Path) -> list:
        """Cheap sanity check the creator UI can surface before firing:
        which expected sprite files aren't on disk for this attack_name.
        Doesn't block anything — BeamAttack already degrades gracefully
        (fallback rectangle rendering) when a sheet is missing — this is
        purely informational."""
        warnings = []
        beam_dir = assets_root / "sprites" / "attacks" / self.beam["attack_name"]
        for part in ("begin", "middle", "end"):
            fname = f"{part}_{self.beam['attack_name']}.png"
            if not (beam_dir / fname).exists():
                warnings.append(f"Missing {fname} (falls back to a plain rectangle beam)")
        if self.charge_enabled:
            charge_dir = assets_root / "sprites" / "attacks" / self.charge["attack_name"]
            fname = f"charging_{self.charge['attack_name']}.png"
            if not (charge_dir / fname).exists():
                warnings.append(f"Missing {fname} (charge-up won't draw anything)")
        return warnings


# ═══════════════════════════════════════════════════════════════════════
# SECTION — Chain archetype (flame_kamehameha style: a fixed-length,
# player-steered chain — FlameKamehamehaAttack + optional
# KamehamehaChargeEffect). See attacks/flame_kamehameha.py.
#
# Unlike BeamAttack, FlameKamehamehaAttack doesn't expose per-part frame
# sizes (begin/middle are hardcoded 16x16, end is hardcoded 24x20 inside
# the class) — so, unlike BEAM_FIELDS, there are no *_frame_width/height
# fields here. Only fields that are real __init__ kwargs on
# FlameKamehamehaAttack are listed; adding a field whose key doesn't match
# a real kwarg would silently do nothing when build_attack() calls it.
# ═══════════════════════════════════════════════════════════════════════

CHAIN_GROUPS: List[Tuple[str, List[FieldSpec]]] = [
    ("Identity", [
        FieldSpec("attack_name", "Asset folder (attack_name)", "str", "flame_kamehameha",
                   help="assets/sprites/attacks/{attack_name}/ — begin_/end_ sheets, single row facing right"),
    ]),

    ("Chain shape", [
        FieldSpec("push_force", "Push force override (px/frame)", "float", 1, min=0, max=20, step=0.1,
                   help="how hard the chain shoves an enemy it stays in contact with"),
    ]),

    ("Whip control", [
        FieldSpec("max_offset", "Max whip offset (px)", "int", 10, min=0, max=200,
                   help="how far the tip can be steered off the chain's fixed travel line"),
        FieldSpec("step_size", "Whip step size (px)", "int", 5, min=1, max=100,
                   help="chunky per-hop distance — not a smooth slide"),
        FieldSpec("step_duration", "Whip step duration (s)", "float", 0.12, min=0.01, max=2, step=0.01,
                   help="time between hops while a direction is held"),
        FieldSpec("middle_offset_scale", "Middle segment offset scale", "float", 0.5, min=0, max=1, step=0.05,
                   help="how far segment 2 swings relative to the tip (segment 3)"),
    ]),

    ("Advanced", [
        FieldSpec("scale", "Render scale override", "float", None, nullable=True, min=0.5, max=8, step=0.5,
                   help="blank = use the engine's RENDER_SCALE"),
    ]),
]
CHAIN_FIELDS: List[FieldSpec] = flatten_groups(CHAIN_GROUPS)


class ChainAttackConfig(AttackConfigBase):
    """Fixed-length, player-steered chain: `self.params["chain"]` maps 1:1
    to FlameKamehamehaAttack's kwargs, `self.params["charge"]` to
    KamehamehaChargeEffect's (gated by `self.set_enabled["charge"]`) —
    same charge-effect class and direction_offsets convention the beam
    archetype uses (see FlameKamehamehaAttack's own docstring: the
    hold-to-charge beat is driven by the same KamehamehaChargeEffect,
    just constructed one level up before this class ever exists).

    Unlike BeamAttack, there's no decay sweep to fire — release just
    calls FlameKamehamehaAttack.stop() outright (see build_attack's
    caller in dev_tools/attack_creator.py, which already generically
    prefers start_decay() when present and falls back to stop()).
    """

    archetype = "chain"
    PARAM_SETS = {"chain": CHAIN_FIELDS, "charge": CHARGE_FIELDS}
    OPTIONAL_SETS = {"charge"}
    EXTRA_KEYS = {"charge": ("direction_offsets",), "chain": ("chain_offsets",)}
    GROUPS = {"chain": CHAIN_GROUPS, "charge": CHARGE_GROUPS}

    def __init__(self, data: Optional[dict] = None):
        self.direction_offsets: dict = dict(BEAM_DEFAULT_DIRECTION_OFFSETS)
        # Same beam_offsets idea as BeamAttackConfig, just named for this
        # archetype's own param set — per-direction (x, y) spawn offset
        # for the chain itself, defaulting to (0, 0) (unchanged behavior).
        self.chain_offsets: dict = dict(ZERO_DIRECTION_OFFSETS)
        super().__init__(data)

    @property
    def chain(self) -> dict:
        return self.params["chain"]

    @property
    def charge(self) -> dict:
        return self.params["charge"]

    @property
    def charge_enabled(self) -> bool:
        return self.set_enabled["charge"]

    @charge_enabled.setter
    def charge_enabled(self, value: bool) -> None:
        self.set_enabled["charge"] = bool(value)

    def _extra_from_data(self, set_name: str, incoming: dict) -> None:
        if set_name == "charge":
            offsets = incoming.get("direction_offsets") or {}
            self.direction_offsets = {
                d: tuple(offsets.get(d, BEAM_DEFAULT_DIRECTION_OFFSETS[d])) for d in BEAM_DIRECTIONS
            }
        elif set_name == "chain":
            offsets = incoming.get("chain_offsets") or {}
            self.chain_offsets = {
                d: tuple(offsets.get(d, ZERO_DIRECTION_OFFSETS[d])) for d in BEAM_DIRECTIONS
            }

    def _extra_to_dict(self, set_name: str) -> dict:
        if set_name == "charge":
            return {"direction_offsets": {d: list(v) for d, v in self.direction_offsets.items()}}
        if set_name == "chain":
            return {"chain_offsets": {d: list(v) for d, v in self.chain_offsets.items()}}
        return {}

    # ── building the real objects ───────────────────────────────────
    def build_attack(self, x: float, y: float, direction: str, player=None) -> FlameKamehamehaAttack:
        # player=None accepted-and-ignored — see BeamAttackConfig.build_attack's
        # comment on why every archetype now shares this signature.
        ox, oy = self.chain_offsets.get(direction, (0, 0))
        return FlameKamehamehaAttack(x + ox, y + oy, direction, **self._kwargs(self.chain))

    def build_charge_effect(self, player) -> Optional[KamehamehaChargeEffect]:
        if not self.charge_enabled:
            return None
        kwargs = self._kwargs(self.charge)
        kwargs["direction_offsets"] = dict(self.direction_offsets)
        return KamehamehaChargeEffect(player, **kwargs)

    # ── validation ──────────────────────────────────────────────────
    def missing_asset_warnings(self, assets_root: Path) -> list:
        warnings = []
        chain_dir = assets_root / "sprites" / "attacks" / self.chain["attack_name"]
        for part in ("begin", "end"):
            fname = f"{part}_{self.chain['attack_name']}.png"
            if not (chain_dir / fname).exists():
                warnings.append(f"Missing {fname} (that segment just won't draw)")
        if self.charge_enabled:
            charge_dir = assets_root / "sprites" / "attacks" / self.charge["attack_name"]
            fname = f"charging_{self.charge['attack_name']}.png"
            if not (charge_dir / fname).exists():
                warnings.append(f"Missing {fname} (charge-up won't draw anything)")
        return warnings


# ═══════════════════════════════════════════════════════════════════════
# SECTION — Projectile archetype (burning_attack style: a straight-line
# Projectile subclass + optional BurningChargeEffect). See
# attacks/burning_attack.py and attacks/projectile.py.
#
# Unlike BeamAttack/FlameKamehamehaAttack, BurningAttack doesn't grow,
# tile, or get steered — it just flies in a straight line at a fixed
# speed until it goes out of bounds or hits something (game.py's job).
# So there's no "Growth & travel"/"Whip control"-style section here; the
# whole shape is: which sprite folder to load, how big/fast the bolt is,
# and how long it stuns on hit.
# ═══════════════════════════════════════════════════════════════════════

PROJECTILE_GROUPS: List[Tuple[str, List[FieldSpec]]] = [
    ("Identity", [
        FieldSpec("attack_name", "Asset folder (attack_name)", "str", "burning_attack",
                   help="assets/sprites/attacks/{attack_name}/charge_{attack_name}.png — "
                        "doubles as both the charge-up art and the fired bolt's own frames"),
    ]),

    ("Sizing & motion", [
        FieldSpec("frame_width", "Frame width", "int", None, nullable=True, min=1, max=256,
                   help="blank = Projectile's own default (16)"),
        FieldSpec("frame_height", "Frame height", "int", None, nullable=True, min=1, max=256,
                   help="blank = Projectile's own default (16)"),
        FieldSpec("speed", "Speed (px/frame)", "float", None, nullable=True, min=0.5, max=40, step=0.5,
                   help="blank = Projectile's own default (4)"),
        FieldSpec("radius", "Fallback circle radius (px)", "int", None, nullable=True, min=1, max=64,
                   help="only drawn if the sprite sheet fails to load; blank = default (8)"),
    ]),

    ("On hit", [
        FieldSpec("stun_duration", "Stun duration (s)", "float", 1.5, min=0, max=10, step=0.1,
                   help="how long enemy.stun(...) freezes the target — 0 still counts as a hit, just no stun"),
    ]),
]
PROJECTILE_FIELDS: List[FieldSpec] = flatten_groups(PROJECTILE_GROUPS)


# Excludes `player` (runtime-only). Unlike BEAM/CHAIN's charge (which
# shares KamehamehaChargeEffect's run-up/pulse playback), BurningChargeEffect
# is a simple straight loop — no charge-duration/pulse timing to configure,
# just which art to loop and how fast.
PROJECTILE_CHARGE_GROUPS: List[Tuple[str, List[FieldSpec]]] = [
    ("Identity", [
        FieldSpec("attack_name", "Charge asset folder", "str", "burning_attack",
                   help="assets/sprites/attacks/{attack_name}/charge_{attack_name}.png — "
                        "can differ from the fired bolt's own attack_name to reuse another attack's art"),
    ]),
    ("Sizing & playback", [
        FieldSpec("frame_width", "Frame width", "int", 16, min=1, max=256),
        FieldSpec("frame_height", "Frame height", "int", 16, min=1, max=256),
        FieldSpec("frame_duration", "Frame duration (s)", "float", 0.08, min=0.01, max=2, step=0.01,
                   help="how long each loop frame holds while the button is held"),
        FieldSpec("target_charge_duration", "Charge duration (s)", "float", 1.0, min=0.1, max=10, step=0.1,
                   help="only used by the creator's hold-to-fire preview to know when to auto-fire; "
                        "the sprite itself just loops for as long as the button is held either way"),
    ]),
]
PROJECTILE_CHARGE_FIELDS: List[FieldSpec] = flatten_groups(PROJECTILE_CHARGE_GROUPS)

# BurningChargeEffect's own hardcoded defaults — reused here so a config
# that never touches the charge tab still spawns with the exact offsets
# the class shipped with, rather than silently zeroing them out.
PROJECTILE_DEFAULT_DIRECTION_OFFSETS = {
    "down": (7, -2), "left": (12, 0), "right": (-12, 0), "up": (6, 3),
}


class ProjectileAttackConfig(AttackConfigBase):
    """Straight-line projectile: `self.params["projectile"]` maps 1:1 to
    BurningAttack's kwargs, `self.params["charge"]` to
    BurningChargeEffect's (gated by `self.set_enabled["charge"]`).

    BurningAttack (like plain Projectile) has no built-in per-direction
    spawn nudge of its own the way BeamAttack's begin sprite does — but
    the creator still offers one here anyway, the same way beam_offsets/
    chain_offsets do it: added to x/y in build_attack() below, purely on
    the config side, before the real object is even constructed, so it
    doesn't require touching BurningAttack/Projectile at all. Defaults to
    (0, 0) — exactly where it always spawned before this field existed —
    so old configs load unchanged until someone drags it.
    """

    archetype = "projectile"
    PARAM_SETS = {"projectile": PROJECTILE_FIELDS, "charge": PROJECTILE_CHARGE_FIELDS}
    OPTIONAL_SETS = {"charge"}
    EXTRA_KEYS = {"charge": ("direction_offsets",), "projectile": ("projectile_offsets",)}
    GROUPS = {"projectile": PROJECTILE_GROUPS, "charge": PROJECTILE_CHARGE_GROUPS}

    def __init__(self, data: Optional[dict] = None):
        self.direction_offsets: dict = dict(PROJECTILE_DEFAULT_DIRECTION_OFFSETS)
        self.projectile_offsets: dict = dict(ZERO_DIRECTION_OFFSETS)
        super().__init__(data)

    @property
    def projectile(self) -> dict:
        return self.params["projectile"]

    @property
    def charge(self) -> dict:
        return self.params["charge"]

    @property
    def charge_enabled(self) -> bool:
        return self.set_enabled["charge"]

    @charge_enabled.setter
    def charge_enabled(self, value: bool) -> None:
        self.set_enabled["charge"] = bool(value)

    def _extra_from_data(self, set_name: str, incoming: dict) -> None:
        if set_name == "charge":
            offsets = incoming.get("direction_offsets") or {}
            self.direction_offsets = {
                d: tuple(offsets.get(d, PROJECTILE_DEFAULT_DIRECTION_OFFSETS[d])) for d in BEAM_DIRECTIONS
            }
        elif set_name == "projectile":
            offsets = incoming.get("projectile_offsets") or {}
            self.projectile_offsets = {
                d: tuple(offsets.get(d, ZERO_DIRECTION_OFFSETS[d])) for d in BEAM_DIRECTIONS
            }

    def _extra_to_dict(self, set_name: str) -> dict:
        if set_name == "charge":
            return {"direction_offsets": {d: list(v) for d, v in self.direction_offsets.items()}}
        if set_name == "projectile":
            return {"projectile_offsets": {d: list(v) for d, v in self.projectile_offsets.items()}}
        return {}

    # ── building the real objects ───────────────────────────────────
    def build_attack(self, x: float, y: float, direction: str, player=None) -> BurningAttack:
        # player=None accepted-and-ignored — see BeamAttackConfig.build_attack's
        # comment on why every archetype now shares this signature.
        ox, oy = self.projectile_offsets.get(direction, (0, 0))
        return BurningAttack(x + ox, y + oy, direction, **self._kwargs(self.projectile))

    def build_charge_effect(self, player) -> Optional[BurningChargeEffect]:
        if not self.charge_enabled:
            return None
        kwargs = self._kwargs(self.charge)
        kwargs["direction_offsets"] = dict(self.direction_offsets)
        return BurningChargeEffect(player, **kwargs)

    # ── validation ──────────────────────────────────────────────────
    def missing_asset_warnings(self, assets_root: Path) -> list:
        warnings = []
        attack_dir = assets_root / "sprites" / "attacks" / self.projectile["attack_name"]
        fname = f"charge_{self.projectile['attack_name']}.png"
        if not (attack_dir / fname).exists():
            warnings.append(f"Missing {fname} (falls back to Projectile's ki_blast art, or a plain circle)")
        if self.charge_enabled:
            charge_dir = assets_root / "sprites" / "attacks" / self.charge["attack_name"]
            fname = f"charge_{self.charge['attack_name']}.png"
            if not (charge_dir / fname).exists():
                warnings.append(f"Missing {fname} (charge-up shows a fallback glow instead)")
        return warnings


# ═══════════════════════════════════════════════════════════════════════
# SECTION — Sword archetype (energy_sword style: a hold-to-charge wind-up
# — EnergySwordChargeEffect — that, once the charge animation finishes,
# auto-fires into a fixed-duration, player-anchored spin — EnergySwordSpinEffect.
# See attacks/energy_sword.py.
#
# Unlike beam/chain, the "charge" set here isn't optional (there's no
# uncharged way to fire the sword — see the class docstrings in
# energy_sword.py) and the fired object needs a *live* player reference
# (it re-reads player.x/y every frame to follow them around) rather than a
# one-time x/y/direction snapshot — see build_attack() below and the
# player=None plumbing added to every other archetype's build_attack for
# this reason.
# ═══════════════════════════════════════════════════════════════════════

SWORD_GROUPS: List[Tuple[str, List[FieldSpec]]] = [
    ("Identity", [
        FieldSpec("attack_name", "Asset folder (attack_name)", "str", "energy_sword",
                   help="assets/sprites/attacks/{attack_name}/ — sword_cardinal.png / sword_diagonal.png"),
    ]),

    ("Spin", [
        FieldSpec("damage", "Damage per tick", "int", 15, min=0, max=999),
        FieldSpec("rotations_per_second", "Rotations / second", "float", 2.0, min=0.1, max=10, step=0.1),
        FieldSpec("hit_interval", "Re-hit interval (s)", "float", 0.2, min=0.01, max=5, step=0.01,
                   help="how soon the same enemy can be hit again while inside the spin"),
        FieldSpec("clockwise", "Spins clockwise", "bool", True,
                   help="in the real game this is set from which way the player was facing when "
                        "they charged; here it's just a direct toggle"),
        FieldSpec("duration", "Spin duration (s)", "float", 2.5, min=0.1, max=20, step=0.1,
                   help="how long the spin plays before ending on its own — in the real game "
                        "player.py owns this timer instead; the creator's preview needs its own "
                        "since there's no player.py driving it here"),
        FieldSpec("hit_radius", "Hit radius (px)", "int", None, nullable=True, min=1, max=200,
                   help="blank = class default (34)"),
        FieldSpec("frame_width", "Frame width", "int", 24, min=1, max=256),
        FieldSpec("frame_height", "Frame height", "int", 32, min=1, max=256),
    ]),
]
SWORD_FIELDS: List[FieldSpec] = flatten_groups(SWORD_GROUPS)

SWORD_CHARGE_GROUPS: List[Tuple[str, List[FieldSpec]]] = [
    ("Identity", [
        FieldSpec("attack_name", "Charge asset folder", "str", "energy_sword",
                   help="assets/sprites/attacks/{attack_name}/charging_{attack_name}.png — "
                        "can differ from the spin's own attack_name to reuse another attack's art"),
    ]),
    ("Sizing & playback", [
        FieldSpec("frame_width", "Frame width", "int", 24, min=1, max=256),
        FieldSpec("frame_height", "Frame height", "int", 27, min=1, max=256),
        FieldSpec("target_charge_duration", "Charge duration (s)", "float", 3.0, min=0.1, max=15, step=0.1,
                   help="how long the run-up-then-pulse sequence takes; the spin auto-fires once this elapses"),
        FieldSpec("pulse_steps", "Pulse steps", "int", 2, min=0, max=10,
                   help="extra hold-time (in frame_duration units) on the final charge frame"),
    ]),
]
SWORD_CHARGE_FIELDS: List[FieldSpec] = flatten_groups(SWORD_CHARGE_GROUPS)

# EnergySwordChargeEffect's own hardcoded defaults — reused here so a
# config that never touches the charge tab still spawns with the exact
# offsets the class shipped with, rather than silently zeroing them out.
SWORD_DEFAULT_DIRECTION_OFFSETS = {
    "down": (-20, 5), "left": (-20, 5), "right": (20, 5), "up": (-20, 5),
}


class EnergySwordAttackConfig(AttackConfigBase):
    """Hold-to-charge, auto-fire-into-a-timed-spin: `self.params["sword"]`
    maps 1:1 to EnergySwordSpinEffect's kwargs, `self.params["charge"]` to
    EnergySwordChargeEffect's. Charge is NOT in OPTIONAL_SETS — there's no
    way to fire this attack without charging first — but `charge_enabled`
    is still exposed as a property (always True) since
    dev_tools/attack_creator.py's fire lifecycle reads it unconditionally
    for every archetype.

    sword_offsets is the same per-direction (x, y) spawn-nudge convention
    as beam_offsets/chain_offsets/etc., but unlike those it isn't just
    added once at construction — the spin re-reads player position every
    frame (see EnergySwordSpinEffect.update()), so the offset itself now
    lives on the built object too (EnergySwordSpinEffect.direction_offsets)
    and gets re-applied every tick instead of being baked into a one-time
    x/y. Defaults to (0, 0) — centered exactly on the player, same as
    before this field existed.
    """

    archetype = "sword"
    PARAM_SETS = {"sword": SWORD_FIELDS, "charge": SWORD_CHARGE_FIELDS}
    OPTIONAL_SETS = frozenset()  # charge is mandatory — no checkbox to toggle it off
    EXTRA_KEYS = {"charge": ("direction_offsets",), "sword": ("sword_offsets",)}
    GROUPS = {"sword": SWORD_GROUPS, "charge": SWORD_CHARGE_GROUPS}

    def __init__(self, data: Optional[dict] = None):
        self.direction_offsets: dict = dict(SWORD_DEFAULT_DIRECTION_OFFSETS)
        self.sword_offsets: dict = dict(ZERO_DIRECTION_OFFSETS)
        super().__init__(data)

    @property
    def sword(self) -> dict:
        return self.params["sword"]

    @property
    def charge(self) -> dict:
        return self.params["charge"]

    @property
    def charge_enabled(self) -> bool:
        # Always on for this archetype — see class docstring.
        return True

    def _extra_from_data(self, set_name: str, incoming: dict) -> None:
        if set_name == "charge":
            offsets = incoming.get("direction_offsets") or {}
            self.direction_offsets = {
                d: tuple(offsets.get(d, SWORD_DEFAULT_DIRECTION_OFFSETS[d])) for d in BEAM_DIRECTIONS
            }
        elif set_name == "sword":
            offsets = incoming.get("sword_offsets") or {}
            self.sword_offsets = {
                d: tuple(offsets.get(d, ZERO_DIRECTION_OFFSETS[d])) for d in BEAM_DIRECTIONS
            }

    def _extra_to_dict(self, set_name: str) -> dict:
        if set_name == "charge":
            return {"direction_offsets": {d: list(v) for d, v in self.direction_offsets.items()}}
        if set_name == "sword":
            return {"sword_offsets": {d: list(v) for d, v in self.sword_offsets.items()}}
        return {}

    # ── building the real objects ───────────────────────────────────
    def build_attack(self, x: float, y: float, direction: str, player=None) -> EnergySwordSpinEffect:
        # x/y/direction are ignored here (unlike every other archetype) —
        # the spin reads player.x/y live every frame instead of taking a
        # one-time spawn point, so it stays anchored to a moving player
        # (see EnergySwordSpinEffect's own docstring). `player` is
        # required for this archetype; dev_tools/attack_creator.py's
        # generic fire lifecycle always passes it (see its _on_fire_press/
        # update()), so this only breaks for a caller that never wires
        # player through.
        if player is None:
            raise ValueError("EnergySwordAttackConfig.build_attack(...) requires player= for this archetype")
        kwargs = self._kwargs(self.sword)
        kwargs["direction_offsets"] = dict(self.sword_offsets)
        return EnergySwordSpinEffect(player, **kwargs)

    def build_charge_effect(self, player) -> EnergySwordChargeEffect:
        kwargs = self._kwargs(self.charge)
        kwargs["direction_offsets"] = dict(self.direction_offsets)
        return EnergySwordChargeEffect(player, **kwargs)

    # ── validation ──────────────────────────────────────────────────
    def missing_asset_warnings(self, assets_root: Path) -> list:
        warnings = []
        sword_dir = assets_root / "sprites" / "attacks" / self.sword["attack_name"]
        for fname in ("sword_cardinal.png", "sword_diagonal.png"):
            if not (sword_dir / fname).exists():
                warnings.append(f"Missing {fname} (spin falls back to a plain line)")
        charge_dir = assets_root / "sprites" / "attacks" / self.charge["attack_name"]
        fname = f"charging_{self.charge['attack_name']}.png"
        if not (charge_dir / fname).exists():
            warnings.append(f"Missing {fname} (charge-up won't draw anything)")
        return warnings


# ═══════════════════════════════════════════════════════════════════════
# SECTION — Dragon Fist archetype: a steerable spring-damped chain.
# See attacks/dragon_fist.py for the full mechanics — shoot straight out
# to shoot_distance, then the head is free to roam a leash box in front
# of the player (steering itself isn't something this creator drives —
# there's no live player input here — but every other beat plays out:
# shoot, sit, then the two-phase closing sequence on release) while the
# body segments trail behind it on a critically-damped spring.
#
# Two things set this apart from every archetype above:
#   - No separate charge-effect object/tab. The real wind-up is just the
#     player holding the 'dragon_fist' pose in player.py before the head
#     is ever spawned — there's nothing here to hold a charge object for,
#     so charge_enabled is hardcoded False and OPTIONAL_SETS stays empty.
#   - build_attack() needs the *live* player position every tick (the
#     leash box's near edge and the anchor segment both track wherever
#     the player currently is — see DragonFistAttack.update()), not a
#     one-time spawn point the way every beam/chain/projectile/sword
#     object above does. dev_tools/attack_creator.py's update() detects
#     this from the update() method's own parameter names (player_x/
#     player_y) rather than a hardcoded archetype check, so a future
#     archetype needing the same thing gets it for free.
# ═══════════════════════════════════════════════════════════════════════

DRAGON_FIST_GROUPS: List[Tuple[str, List[FieldSpec]]] = [
    ("Identity", [
        FieldSpec("attack_name", "Asset folder (attack_name)", "str", "dragon_fist",
                   help="assets/sprites/attacks/{attack_name}/ — dragon_fist_head.png / "
                        "_body.png / _head_end.png"),
        FieldSpec("destruction_asset", "Closing puff asset", "str", "brown_destruction",
                   help="assets/objects/{this}.png — the shared destruction puff played "
                        "across the whole assembly once retracted"),
    ]),

    ("Chain shape", [
        FieldSpec("num_segments", "Body segments", "int", 5, min=1, max=20),
        FieldSpec("link_distance", "Link distance (px)", "int", 25, min=1, max=200),
        FieldSpec("anchor_offset", "Anchor offset (px)", "int", 20, min=0, max=200,
                   help="how far in front of the player the trailing anchor segment sits"),
        FieldSpec("push_force", "Enemy push force", "float", 0.4, min=0, max=10, step=0.1),
    ]),

    ("Shoot & leash", [
        FieldSpec("shoot_speed", "Shoot speed (px/s)", "int", 300, min=1, max=2000),
        FieldSpec("shoot_distance", "Shoot distance (px)", "int", 60, min=1, max=1000),
        FieldSpec("forward_range", "Leash forward range (px)", "int", 130, min=1, max=1000),
        FieldSpec("lateral_range", "Leash lateral range (px)", "int", 50, min=1, max=1000),
    ]),

    ("Chain feel", [
        FieldSpec("chain_update_fps", "Chain tick rate (fps)", "int", 24, min=1, max=60,
                   help="how often the spring/waypoints actually step — lower reads as choppier"),
        FieldSpec("chain_head_smooth_time", "Head-end smoothing (s)", "float", 0.05, min=0.01, max=2, step=0.01,
                   help="catch-up time for the segment right behind the head — snappier when low"),
        FieldSpec("chain_tail_smooth_time", "Tail-end smoothing (s)", "float", 0.22, min=0.01, max=2, step=0.01,
                   help="catch-up time for the segment right before the anchor — slacker when high"),
        FieldSpec("chain_gap_safety_margin", "Gap safety margin (x link_distance)", "float", 2.0, min=1.0, max=10, step=0.1,
                   help="hard cap on any consecutive gap, as a multiple of link_distance — a rare "
                        "safety net, not routine enforcement"),
    ]),

    ("Sizing", [
        FieldSpec("head_width", "Head width", "int", 64, min=1, max=512),
        FieldSpec("head_height", "Head height", "int", 64, min=1, max=512),
        FieldSpec("body_width", "Body width", "int", 29, min=1, max=512),
        FieldSpec("body_height", "Body height", "int", 32, min=1, max=512),
    ]),

    ("Closing sequence", [
        FieldSpec("head_end_frame_count", "Head-end frame count", "int", 2, min=1, max=32),
        FieldSpec("head_end_frame_duration", "Head-end frame duration (s)", "float", 0.06, min=0.01, max=2, step=0.01),
        FieldSpec("destruction_frame_count", "Destruction frame count", "int", 4, min=1, max=32),
        FieldSpec("destruction_frame_duration", "Destruction frame duration (s)", "float", 0.06, min=0.01, max=2, step=0.01),
    ]),
]
DRAGON_FIST_FIELDS: List[FieldSpec] = flatten_groups(DRAGON_FIST_GROUPS)


class DragonFistAttackConfig(AttackConfigBase):
    """`self.params["dragon_fist"]` maps onto DragonFistAttack's kwargs
    almost 1:1 — head_size/body_size are the only mismatch (each is a
    single (w, h) tuple on the real class, split here into four plain
    int fields since FieldSpec has no tuple kind — see build_attack()
    for where they're recombined).

    dragon_fist_offsets is the same per-direction (x, y) spawn-nudge
    convention as beam_offsets/chain_offsets/projectile_offsets — applied
    on top of DragonFistAttack's own internal anchor_offset (which only
    moves the spawn point along the throw direction's own axis, not
    sideways) so the launch point can be fine-tuned to actually sit at
    the character's fist/hand for every direction.
    """

    archetype = "dragon_fist"
    PARAM_SETS = {"dragon_fist": DRAGON_FIST_FIELDS}
    OPTIONAL_SETS = frozenset()  # no charge tab at all — see class docstring
    EXTRA_KEYS = {"dragon_fist": ("dragon_fist_offsets",)}
    GROUPS = {"dragon_fist": DRAGON_FIST_GROUPS}

    def __init__(self, data: Optional[dict] = None):
        self.dragon_fist_offsets: dict = dict(ZERO_DIRECTION_OFFSETS)
        super().__init__(data)

    @property
    def dragon_fist(self) -> dict:
        return self.params["dragon_fist"]

    @property
    def charge_enabled(self) -> bool:
        # Always off — there's no separate charge-effect object for this
        # archetype at all (see class docstring); dev_tools/attack_creator.py
        # reads this unconditionally for every archetype and, when False,
        # goes straight from press to build_attack().
        return False

    def build_charge_effect(self, player):
        return None  # never called while charge_enabled is False, kept for interface parity

    def _extra_from_data(self, set_name: str, incoming: dict) -> None:
        if set_name == "dragon_fist":
            offsets = incoming.get("dragon_fist_offsets") or {}
            self.dragon_fist_offsets = {
                d: tuple(offsets.get(d, ZERO_DIRECTION_OFFSETS[d])) for d in BEAM_DIRECTIONS
            }

    def _extra_to_dict(self, set_name: str) -> dict:
        if set_name == "dragon_fist":
            return {"dragon_fist_offsets": {d: list(v) for d, v in self.dragon_fist_offsets.items()}}
        return {}

    # ── building the real object ────────────────────────────────────
    def build_attack(self, x: float, y: float, direction: str, player=None) -> DragonFistAttack:
        ox, oy = self.dragon_fist_offsets.get(direction, (0, 0))
        kwargs = self._kwargs(self.dragon_fist)
        head_w = kwargs.pop("head_width")
        head_h = kwargs.pop("head_height")
        body_w = kwargs.pop("body_width")
        body_h = kwargs.pop("body_height")
        kwargs["head_size"] = (head_w, head_h)
        kwargs["body_size"] = (body_w, body_h)
        return DragonFistAttack(x + ox, y + oy, direction, **kwargs)

    # ── validation ──────────────────────────────────────────────────
    def missing_asset_warnings(self, assets_root: Path) -> list:
        warnings = []
        sprite_dir = assets_root / "sprites" / "attacks" / self.dragon_fist["attack_name"]
        for fname in ("dragon_fist_head.png", "dragon_fist_body.png", "dragon_fist_head_end.png"):
            if not (sprite_dir / fname).exists():
                warnings.append(f"Missing {fname} (falls back to a plain colored circle)")
        destruction_file = assets_root / "objects" / f"{self.dragon_fist['destruction_asset']}.png"
        if not destruction_file.exists():
            warnings.append(f"Missing {destruction_file.name} (closing puff won't draw anything)")
        return warnings


# ═══════════════════════════════════════════════════════════════════════
# SECTION — Genkidama archetype: a hold-to-build-power charge with FIVE
# discrete states (rather than one continuous grow/pulse like the beam
# family), where releasing at ANY point fires whatever state you're
# currently sitting in. See attacks/genkidama.py for the full mechanics.
#
# Two things set this apart from every archetype above:
#   - There's no "charge finishes, then it auto-fires" moment the way
#     beam/chain/sword have — GenkidamaChargeEffect.get_total_duration()
#     returns infinity specifically so the generic auto-fire check in
#     dev_tools/attack_creator.py never trips. Instead, RELEASE is the
#     fire trigger, at whatever state hold_time has reached (capped at 5).
#     fires_on_release=True below is what tells that generic release
#     handler to build the real attack on release rather than cancel the
#     charge, the way every other archetype's mid-charge release does.
#   - build_attack() needs the *live charge object* at the moment of
#     release (not just x/y/direction/player) to read off its current
#     state and hand over its already-scaled sprite — GenkidamaBlast
#     deliberately doesn't reload/rescale its own art (see that class's
#     docstring). dev_tools/attack_creator.py's release handler passes the
#     charge object through via a charge_obj= kwarg specifically for this
#     archetype; every other archetype's build_attack still ignores it
#     (they don't declare the parameter at all).
# ═══════════════════════════════════════════════════════════════════════

GENKIDAMA_GROUPS: List[Tuple[str, List[FieldSpec]]] = [
    ("Fired ball", [
        FieldSpec("base_speed", "Base speed (px/frame)", "float", None, nullable=True, min=0.1, max=20, step=0.1,
                   help="blank = class default (1) — actual travel speed is this x each state's "
                        "speed multiplier below"),
    ]),

    ("Per-state stats — radius (px)", [
        FieldSpec("state_1_radius", "State 1 radius", "int", 10, min=1, max=200),
        FieldSpec("state_2_radius", "State 2 radius", "int", 13, min=1, max=200),
        FieldSpec("state_3_radius", "State 3 radius", "int", 16, min=1, max=200),
        FieldSpec("state_4_radius", "State 4 radius", "int", 20, min=1, max=200),
        FieldSpec("state_5_radius", "State 5 radius", "int", 26, min=1, max=200),
    ]),

    ("Per-state stats — speed multiplier", [
        FieldSpec("state_1_speed_mult", "State 1 speed x", "float", 1.0, min=0.1, max=5, step=0.05),
        FieldSpec("state_2_speed_mult", "State 2 speed x", "float", 1.0, min=0.1, max=5, step=0.05),
        FieldSpec("state_3_speed_mult", "State 3 speed x", "float", 0.9, min=0.1, max=5, step=0.05),
        FieldSpec("state_4_speed_mult", "State 4 speed x", "float", 0.85, min=0.1, max=5, step=0.05),
        FieldSpec("state_5_speed_mult", "State 5 speed x", "float", 0.75, min=0.1, max=5, step=0.05,
                   help="heavier states fly slightly slower — feels heavier, still hits harder "
                        "(actual damage scaling is read wherever collision damage normally comes "
                        "from, e.g. enemy.check_collision_with_attack, keyed off .state)"),
    ]),
]
GENKIDAMA_FIELDS: List[FieldSpec] = flatten_groups(GENKIDAMA_GROUPS)


GENKIDAMA_CHARGE_GROUPS: List[Tuple[str, List[FieldSpec]]] = [
    ("Identity", [
        FieldSpec("attack_name", "Asset folder (attack_name)", "str", "genkidama",
                   help="assets/sprites/attacks/{attack_name}/ — state1.png..state5.png, "
                        "charge1.png/charge2.png (drifting orbs), hit.png"),
    ]),

    ("Power states", [
        FieldSpec("state_advance_time_2", "Time to reach state 2 (s)", "float", 1.2, min=0.1, max=30, step=0.1),
        FieldSpec("state_advance_time_3", "Time to reach state 3 (s)", "float", 2.4, min=0.1, max=30, step=0.1),
        FieldSpec("state_advance_time_4", "Time to reach state 4 (s)", "float", 3.6, min=0.1, max=30, step=0.1),
        FieldSpec("state_advance_time_5", "Time to reach state 5 (s)", "float", 4.8, min=0.1, max=30, step=0.1,
                   help="holding longer than this just keeps you at state 5 — there's no state 6"),
    ]),

    ("Feel", [
        FieldSpec("pulse_duration", "Pulse half-step duration (s)", "float", 0.15, min=0.01, max=2, step=0.01,
                   help="how fast the ball breathes between its current state and a tease of the "
                        "next one, while not yet at max state"),
    ]),

    ("Charge orbs", [
        FieldSpec("orb_spawn_interval", "Orb spawn interval (s)", "float", 0.12, min=0.01, max=5, step=0.01),
        FieldSpec("orb_spawn_radius", "Orb spawn radius (px)", "int", 70, min=1, max=1000,
                   help="how far out the little feeding orbs first appear"),
        FieldSpec("orb_speed", "Orb drift speed (px/s)", "int", 90, min=1, max=2000),
    ]),
]
GENKIDAMA_CHARGE_FIELDS: List[FieldSpec] = flatten_groups(GENKIDAMA_CHARGE_GROUPS)

# GenkidamaChargeEffect's own hardcoded defaults — reused here so a config
# that never touches the charge tab still spawns with the exact offsets the
# class shipped with, rather than silently zeroing them out.
GENKIDAMA_DEFAULT_DIRECTION_OFFSETS = {
    "down": (0, -14), "left": (0, -12), "right": (0, -12), "up": (0, -14),
}


class GenkidamaAttackConfig(AttackConfigBase):
    """Hold-to-build-power, release-to-throw: `self.params["genkidama"]`
    covers the fired GenkidamaBlast's own tuning (speed and per-state
    radius/speed multiplier), `self.params["charge"]` covers
    GenkidamaChargeEffect's (mandatory — see fires_on_release below, there's
    no "charge" checkbox to switch off since this attack can't exist
    without one).

    Two offset dicts, both draggable on the creator's stage:
      - direction_offsets: where the floating charge ball sits relative to
        the player (GenkidamaChargeEffect._center_world_pos already anchors
        this at player.x, player.y - player.height/2 + offset — see
        build_attack()/dev_tools/attack_creator.py's _offset_anchor_extra
        for where that -height/2 gets folded in so the crosshair sits
        exactly on the visible ball).
      - genkidama_offsets: an additional nudge applied ONLY at the moment
        of firing, on top of direction_offsets — lets the thrown ball's
        exact release point be tuned separately from where it floats while
        charging. Defaults to (0, 0): the ball leaves from exactly where it
        was floating unless this is deliberately dragged elsewhere.
    """

    archetype = "genkidama"
    PARAM_SETS = {"genkidama": GENKIDAMA_FIELDS, "charge": GENKIDAMA_CHARGE_FIELDS}
    OPTIONAL_SETS = frozenset()  # charge is mandatory — see class docstring
    EXTRA_KEYS = {"charge": ("direction_offsets",), "genkidama": ("genkidama_offsets",)}
    GROUPS = {"genkidama": GENKIDAMA_GROUPS, "charge": GENKIDAMA_CHARGE_GROUPS}
    fires_on_release = True  # see AttackConfigBase.fires_on_release

    def __init__(self, data: Optional[dict] = None):
        self.direction_offsets: dict = dict(GENKIDAMA_DEFAULT_DIRECTION_OFFSETS)
        self.genkidama_offsets: dict = dict(ZERO_DIRECTION_OFFSETS)
        super().__init__(data)

    @property
    def genkidama(self) -> dict:
        return self.params["genkidama"]

    @property
    def charge(self) -> dict:
        return self.params["charge"]

    @property
    def charge_enabled(self) -> bool:
        # Always on for this archetype — see class docstring.
        return True

    def _extra_from_data(self, set_name: str, incoming: dict) -> None:
        if set_name == "charge":
            offsets = incoming.get("direction_offsets") or {}
            self.direction_offsets = {
                d: tuple(offsets.get(d, GENKIDAMA_DEFAULT_DIRECTION_OFFSETS[d])) for d in BEAM_DIRECTIONS
            }
        elif set_name == "genkidama":
            offsets = incoming.get("genkidama_offsets") or {}
            self.genkidama_offsets = {
                d: tuple(offsets.get(d, ZERO_DIRECTION_OFFSETS[d])) for d in BEAM_DIRECTIONS
            }

    def _extra_to_dict(self, set_name: str) -> dict:
        if set_name == "charge":
            return {"direction_offsets": {d: list(v) for d, v in self.direction_offsets.items()}}
        if set_name == "genkidama":
            return {"genkidama_offsets": {d: list(v) for d, v in self.genkidama_offsets.items()}}
        return {}

    # ── building the real objects ───────────────────────────────────
    def build_charge_effect(self, player) -> GenkidamaChargeEffect:
        c = self.charge
        kwargs = dict(
            attack_name=c["attack_name"],
            direction_offsets=dict(self.direction_offsets),
            state_advance_times=[
                c["state_advance_time_2"], c["state_advance_time_3"],
                c["state_advance_time_4"], c["state_advance_time_5"],
            ],
            pulse_duration=c["pulse_duration"],
            orb_spawn_interval=c["orb_spawn_interval"],
            orb_spawn_radius=c["orb_spawn_radius"],
            orb_speed=c["orb_speed"],
        )
        return GenkidamaChargeEffect(player, **kwargs)

    def build_attack(self, x: float, y: float, direction: str, player=None,
                      charge_obj: Optional[GenkidamaChargeEffect] = None) -> GenkidamaBlast:
        """Unlike every archetype above, x/y here are just the player's
        raw position (dev_tools/attack_creator.py always passes
        self.actor.x/y) — the actual spawn point is computed the same way
        GenkidamaChargeEffect._center_world_pos() computes the floating
        ball's center (player position - half height + direction_offsets),
        plus genkidama_offsets as a further release-time nudge. This keeps
        the thrown ball starting exactly where the charge ball was
        visibly floating, by default.

        charge_obj carries over the live state/sprite at the moment of
        release — see fires_on_release / dev_tools/attack_creator.py's
        release handler, which is the only caller that has one to pass.
        charge_obj=None (e.g. a caller that fires without ever charging)
        falls back to state 5 with no sprite, which GenkidamaBlast already
        renders as its own fallback circle rather than crashing.
        """
        height = getattr(player, "height", 0) if player is not None else 0
        dox, doy = self.direction_offsets.get(direction, (0, 0))
        gox, goy = self.genkidama_offsets.get(direction, (0, 0))
        spawn_x = x + dox + gox
        spawn_y = y - height / 2 + doy + goy

        if charge_obj is not None:
            state = charge_obj.state
            sprite = charge_obj.get_state_sprite(state)
        else:
            state = 5
            sprite = None

        g = self.genkidama
        state_stats = {
            1: (g["state_1_radius"], g["state_1_speed_mult"]),
            2: (g["state_2_radius"], g["state_2_speed_mult"]),
            3: (g["state_3_radius"], g["state_3_speed_mult"]),
            4: (g["state_4_radius"], g["state_4_speed_mult"]),
            5: (g["state_5_radius"], g["state_5_speed_mult"]),
        }
        kwargs = {"attack_name": self.charge["attack_name"], "state_stats": state_stats}
        if g.get("base_speed") is not None:
            kwargs["base_speed"] = g["base_speed"]

        return GenkidamaBlast(spawn_x, spawn_y, direction, state, sprite=sprite, **kwargs)

    # ── validation ──────────────────────────────────────────────────
    def missing_asset_warnings(self, assets_root: Path) -> list:
        warnings = []
        sprite_dir = assets_root / "sprites" / "attacks" / self.charge["attack_name"]
        for i in range(1, 6):
            fname = f"state{i}.png"
            if not (sprite_dir / fname).exists():
                warnings.append(f"Missing {fname} (that power state falls back to a plain circle)")
        for fname in ("charge1.png", "charge2.png"):
            if not (sprite_dir / fname).exists():
                warnings.append(f"Missing {fname} (feeding orbs won't draw)")
        return warnings


# ═══════════════════════════════════════════════════════════════════════
# SECTION — future archetypes go here, same shape as the ones above:
#   MELEE_GROUPS / MELEE_FIELDS + MeleeAttackConfig(...)
# Candidates not yet covered: ghost_kamikaze_attack / ultra_volleyball_attack
# (each their own bespoke state machine), melee, and instant_transmission
# (cursor-targeting UI phase, not really a "fire and watch" attack at all
# in the sense this creator models).
# ═══════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════
# Registry — every archetype's config class, keyed by its `archetype`
# string (the same string stored in each config's JSON). Add one line
# here per new archetype; load_config()/list_saved_configs() already
# work against whatever's registered.
# ═══════════════════════════════════════════════════════════════════════

ARCHETYPES: Dict[str, Type[AttackConfigBase]] = {
    "beam": BeamAttackConfig,
    "chain": ChainAttackConfig,
    "projectile": ProjectileAttackConfig,
    "sword": EnergySwordAttackConfig,
    "dragon_fist": DragonFistAttackConfig,
    "genkidama": GenkidamaAttackConfig,
}