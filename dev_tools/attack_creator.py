"""
dev_tools/attack_creator.py — In-engine Attack Creator
============================================================================
Lets you build an attack (currently: beam-family — final_flash /
banshee_blast / big_bang_kamehameha style — chain-family —
flame_kamehameha style — projectile-family — burning_attack style —
sword-family — energy_sword style — dragon-fist-family,
genkidama-family — a five-power-state hold-then-throw ball, or
ultra-volleyball-family — a fixed-length, three-segment chain that
travels rigidly to a fixed distance then despawns) entirely from
data, preview it live on any player character, tweak every parameter
while it's charging/firing/decaying (firing/stopping for chain,
firing/despawning for projectile, genkidama and ultra volleyball), and
save it to assets/attack_configs/{id}.json.

This is WYSIWYG on purpose: the stage doesn't simulate the attack, it
constructs and drives the REAL attacks.beam.BeamAttack and
KamehamehaChargeEffect classes (via attacks/attack_config.py) with whatever
values are currently in the form. What you see here is exactly what
game.py will render if it loads the same config.

VISUAL LANGUAGE — rebuilt from scratch on dev_tools/ui_kit.py, matching
DevMenu's "modern DBZ" look (deep navy background, gold/ki-blue accents,
dark hairline-bordered cards, header/footer chrome, inline field-row text
editing with a live caret) the same way item_creator.py's own rebuild did.
None of the old widget classes (Button/HoldButton/FieldEditor/ParamPanel/
draw_rect-on-a-GPUScreen-wrapper colour constants) are used any more —
every visual here is built directly on ui_kit primitives and this file's
own drawing helpers. The underlying data model, archetype registry,
save/load/duplicate/delete behaviour, the live fire/charge/decay state
machine, the real LayerManager-driven stage preview, and the per-direction
offset drag handle are all unchanged from the previous version of this
file — only how they're drawn and clicked has changed.

Wire-up (game.py) — same shape as character_creator.CharacterCreator:
------------------------------------------------------------------
    from dev_tools import attack_creator
    ...
    self.attack_creator = attack_creator.AttackCreator(SCREEN_WIDTH, SCREEN_HEIGHT)
    ...
    # event loop
    if self.attack_creator.active:
        result = self.attack_creator.handle_input(event)
        if result == 'back_to_dev_menu':
            # Back-arrow / ESC — the Dev Menu closed itself when it
            # launched this creator (see DevMenu._activate_selected), so
            # reopen it here instead of dropping all the way back into
            # gameplay. Mirrors CharacterCreator / RoomEditor's handling.
            self.dev_menu.open()
        continue
    ...
    if self.dev_menu.active:
        result = self.dev_menu.handle_input(event)
        ...
        elif result == 'open_attack_creator':
            self.dev_menu.active = False
            self.attack_creator.toggle()
    ...
    # per-frame update
    if self.attack_creator.active:
        self.attack_creator.update(dt)
        return
    ...
    # draw, alongside the other dev tools
    self.attack_creator.draw(self.logical_surface, self.dt)

Controls
--------
    Sidebar         — archetype picker (which kind [+ New] creates), then
                       saved attack configs across every archetype: click
                       to load (switches the archetype picker to match),
                       [+ New], [Duplicate], [Delete Selected].
    Top bar         — character picker, facing direction picker.
    Stage           — live preview. Press and HOLD the "Hold to Fire"
                       button (or hold SPACE, same effect): charges (if
                       enabled) -> auto-fires once the charge animation
                       finishes -> keeps growing while held -> release to
                       trigger decay, same lifecycle player.py drives in
                       the real game.
    Right panel     — tabbed parameter form (tabs come from the current
                       archetype's config.PARAM_SETS, e.g. Beam/Charge or
                       Chain/Charge), auto-built from attacks/attack_config.py.
                       Click a field to edit it, Enter or click away to
                       commit.
    Drag handle     — Every tab whose param set has a per-direction spawn
                       offset shows a crosshair on the stage for it (Beam/
                       Chain/Projectile/Dragon Fist's own tab, plus every
                       archetype's Charge tab). Hit [Pause] first (freezes
                       whatever's currently on screen), then drag the
                       crosshair to reposition it.
    [Save]          — header icon button, writes the current config to
                       assets/attack_configs/.

Adding another archetype (projectile, melee, ...) means: give it a config
class in attack_config.py with the same shape as BeamAttackConfig/
ChainAttackConfig (PARAM_SETS, GROUPS, OPTIONAL_SETS, build_attack(),
build_charge_effect() if it has a charge beat), then add one entry to
ARCHETYPES below. The sidebar, tab bar, form-builder, save/load, and fire/
stop lifecycle here are all archetype-generic already — none of that needs
touching. The one convention worth keeping: name the optional wind-up
param set "charge" (as both existing archetypes do) so it keeps sharing
the offset drag handle and checkbox wiring below rather than needing its
own. If the new archetype's own param set has a per-direction (x, y)
spawn-offset dict too (like beam_offsets/chain_offsets/projectile_offsets/
dragon_fist_offsets/sword_offsets), add one entry to OFFSET_ATTR_FOR_TAB
below and it gets a drag handle for free as well.
"""

from __future__ import annotations

import os
import sys
import math
import copy
import inspect
from pathlib import Path
from typing import Optional

import pygame

import dev_tools.ui_kit as uk

# ── Paths — anchored to the running program's own folder, not CWD. Same
# rationale as character_creator.py's BASE_DIR: a relative path only
# happens to work from an IDE's default CWD, and silently breaks once
# this is packaged (PyInstaller etc.) and double-clicked from elsewhere. ──
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent.parent

PLAYER_SPRITES_DIR = BASE_DIR / "assets/sprites/player"
ATTACKS_ASSET_DIR = BASE_DIR / "assets/sprites/attacks"
CONFIGS_DIR = BASE_DIR / "assets/attack_configs"

sys.path.insert(0, str(BASE_DIR))  # so `attacks.beam` / `config.settings` resolve when run standalone

from attacks.attack_config import (
    list_saved_configs, load_config, BEAM_DIRECTIONS as DIRECTIONS,
)
from dev_tools import character_creator  # canonical roster — see _scan_characters()
from core.draw_layers import DrawLayer, LayerManager  # stage layering — see _draw_stage()

from attacks.attack_config import ARCHETYPES as _ATTACK_CONFIG_REGISTRY
from attacks.dragon_fist import _DIRECTION_UNIT as _DRAGON_FIST_DIRECTION_UNIT

# Archetypes with a working UI in this tool — a display label plus the
# config class, pulled from the shared registry (attacks/attack_config.py)
# rather than hand-listing classes separately. This creator's form/tab/
# stage code is now archetype-generic (drives entirely off
# config.PARAM_SETS / config.GROUPS / config.OPTIONAL_SETS /
# config.build_attack() / config.build_charge_effect()), so adding a new
# archetype here is just: give it a config class in attack_config.py with
# that shape, then add one line below.
ARCHETYPES = {
    "beam": ("Beam", _ATTACK_CONFIG_REGISTRY["beam"]),
    "chain": ("Chain", _ATTACK_CONFIG_REGISTRY["chain"]),
    "projectile": ("Projectile", _ATTACK_CONFIG_REGISTRY["projectile"]),
    "sword": ("Sword", _ATTACK_CONFIG_REGISTRY["sword"]),
    "dragon_fist": ("Dragon Fist", _ATTACK_CONFIG_REGISTRY["dragon_fist"]),
    "genkidama": ("Genkidama", _ATTACK_CONFIG_REGISTRY["genkidama"]),
    "ultra_volleyball": ("Ultra Volleyball", _ATTACK_CONFIG_REGISTRY["ultra_volleyball"]),
}


FALLBACK_COLORS = {
    "CYAN": (80, 220, 230), "YELLOW": (240, 220, 90), "WHITE": (240, 240, 240),
    "ORANGE": (255, 150, 60), "RED": (220, 80, 80), "GREEN": (110, 210, 120),
}

# The preview actor's world-space anchor and the bounds handed to any
# fired attack's update(world_width, world_height, dt) (Projectile-style
# archetypes: projectile, genkidama — see the STATE_FIRING/STATE_DECAYING
# branch in update() below). Both attacks.projectile.Projectile.update()
# and attacks.genkidama.GenkidamaBlast.update() do a hardcoded
# `x < 0 or x > world_width or y < 0 or y > world_height` bounds check —
# a corner-origin convention that matches the real game (room coordinates
# start at (0, 0) and a player is realistically thousands of pixels from
# that corner, so a modest attack-spawn offset never goes negative).
# This preview used to anchor the actor at world (0, 0) instead — fine
# for archetypes whose spawn offset stays positive, but genkidama's
# build_attack() subtracts player.height/2 (~20px) plus a small negative
# default direction offset, landing spawn_y around -32. Against a
# corner-origin bounds check, that's instantly "out of bounds": the
# GenkidamaBlast went inactive on its very first update() tick, the same
# frame it was thrown, and vanished before ever being visibly drawn —
# looked exactly like release wasn't firing anything. Anchoring the actor
# well away from the corner (with the camera compensating so it still
# draws in the same screen spot — see _new_actor()) keeps every
# archetype's spawn point safely positive regardless of offset tuning.
PREVIEW_WORLD_ANCHOR = 1000
PREVIEW_WORLD_BOUND = 4000

# Tab name -> attribute name on the active config holding a per-direction
# (x, y) pixel-offset dict, e.g. BeamAttackConfig.beam_offsets for the
# "beam" tab. This is what makes the on-stage drag handle archetype- and
# tab-generic: any tab whose param set has one of these dicts (checked
# with hasattr, since e.g. the "chain" tab only exists on ChainAttackConfig)
# gets a draggable crosshair for free, rather than each archetype/tab
# needing its own hand-wired drag code. "charge" is shared by every
# archetype that has a wind-up beat (see the class docstring's note on
# that naming convention); "beam"/"chain"/"projectile"/"dragon_fist"/
# "sword"/"ultra_volleyball" are each archetype's own fired-attack spawn
# offset (attacks/attack_config.py's beam_offsets/chain_offsets/
# projectile_offsets/dragon_fist_offsets/sword_offsets/
# ultra_volleyball_offsets). Two of these need extra handling beyond "add
# the offset to actor.x/y" — see _offset_anchor_extra() (dragon_fist's
# crosshair needs to sit at the real anchor point, not the raw player
# position) and _drag_offset_to()'s per-archetype live-nudge branches
# (sword's spin re-reads its offset live every frame rather than baking
# it in once at construction).
OFFSET_ATTR_FOR_TAB = {
    "charge": "direction_offsets",
    "beam": "beam_offsets",
    "chain": "chain_offsets",
    "projectile": "projectile_offsets",
    "dragon_fist": "dragon_fist_offsets",
    "sword": "sword_offsets",
    "genkidama": "genkidama_offsets",
    "ultra_volleyball": "ultra_volleyball_offsets",
}


# =============================================================================
# Bitmap-font adapter and small vector icon glyphs — same shapes
# item_creator.py's own rebuild uses, so this tool reads as part of the
# same icon/font family rather than a one-off.
# =============================================================================

class _BitmapFontView:
    """Adapts a BitmapFont to the plain pygame.font.Font call shape —
    render(text, antialias, color) / size(text) — at one fixed pixel
    height, same adapter DevMenu.py / RoomEditor.py / item_creator.py use
    for their own bitmap fonts."""

    def __init__(self, bitmap_font, height):
        self._font = bitmap_font
        self._height = height

    def render(self, text, antialias=True, color=(255, 255, 255)):
        return self._font.render(text, color=color, height=self._height)

    def size(self, text):
        return self._font.size(text, height=self._height)


def _draw_chevron_left(surface, rect, color, width=2):
    cx, cy = rect.center
    s = min(rect.w, rect.h) * 0.22
    uk.draw_line_on(surface, color, (cx + s * 0.4, cy - s), (cx - s * 0.6, cy), width)
    uk.draw_line_on(surface, color, (cx - s * 0.6, cy), (cx + s * 0.4, cy + s), width)


def _draw_chevron_right(surface, rect, color, width=2):
    cx, cy = rect.center
    s = min(rect.w, rect.h) * 0.22
    uk.draw_line_on(surface, color, (cx - s * 0.4, cy - s), (cx + s * 0.6, cy), width)
    uk.draw_line_on(surface, color, (cx + s * 0.6, cy), (cx - s * 0.4, cy + s), width)


def _draw_plus_icon(surface, rect, color, width=3):
    cx, cy = rect.center
    s = min(rect.w, rect.h) * 0.34
    uk.draw_line_on(surface, color, (cx - s, cy), (cx + s, cy), width)
    uk.draw_line_on(surface, color, (cx, cy - s), (cx, cy + s), width)


def _draw_duplicate_icon(surface, rect, color, width=2):
    """Fallback 'two overlapping sheets' copy/duplicate glyph, used only
    if assets/ui/dev_menu/icons/duplicate.png isn't present."""
    cx, cy = rect.center
    s = min(rect.w, rect.h) * 0.42
    back = pygame.Rect(0, 0, s, s)
    back.center = (cx - s * 0.18, cy - s * 0.18)
    uk.draw_rect_on(surface, color, back, width, 2)
    front = pygame.Rect(0, 0, s, s)
    front.center = (cx + s * 0.18, cy + s * 0.18)
    uk.draw_rect_on(surface, color, front, width, 2)


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


def _draw_play_icon(surface, rect, color):
    """Filled right-pointing triangle — 'Hold to Fire' button glyph.
    pygame.draw.polygon only accepts a real pygame.Surface, but `surface`
    here can be the engine's GPUScreen wrapper (see ui_kit.py's own
    'Supersampled shapes' note) — so this draws the triangle on a small
    throwaway real Surface first, then blits that through
    uk.blit_surface, which both backends support."""
    cx, cy = rect.center
    s = min(rect.w, rect.h) * 0.32
    w, h = int(s * 1.5) + 2, int(s * 2) + 2
    tri = pygame.Surface((w, h), pygame.SRCALPHA)
    pts = [(1, 1), (1, h - 1), (w - 1, h / 2)]
    pygame.draw.polygon(tri, color, pts)
    uk.blit_surface(surface, tri, tri.get_rect(center=(int(cx), int(cy))), transient=True)


def _draw_pause_icon(surface, rect, color):
    cx, cy = rect.center
    s = min(rect.w, rect.h) * 0.30
    for dx in (-s * 0.5, s * 0.15):
        bar = pygame.Rect(0, 0, s * 0.32, s * 2)
        bar.centerx = cx + dx
        bar.centery = cy
        uk.draw_rect_on(surface, color, bar, 0, 2)


# =============================================================================
# Preview actor — a minimal stand-in for player.py's Player class.
# Unchanged from the previous version of this file: this is the simulation
# side (it drives the REAL KamehamehaChargeEffect/etc. classes), not UI
# chrome, so the visual rework above doesn't touch it.
# =============================================================================

class PreviewActor:
    # Player sprite sheets (see CharacterSpriteLoader.load_character in
    # sprite_system.py) have no plain "attack" animation folder — beams
    # use 'charge' while charging then swap to 'firebeam' once fired,
    # same as player.py does mid-game. "attack" here would never resolve
    # and silently fall through to 'charge' for every state, which is why
    # firing/decaying used to look identical to charging (same clip,
    # just restarted at frame 0).
    #
    # This is archetype-specific, though: burning_attack (the "projectile"
    # archetype) is a kiblast-family attack, not a beam — per player.py's
    # start_charging_burning()/update_burning_charge()/release_burning(),
    # the player is pinned on the 'kiblast' wind-up pose while charging and
    # snaps to a single held throw-frame on release, and never touches
    # 'charge'/'firebeam' at all.
    #
    # It's *not* just a different clip name, either. kiblast.png is one
    # sheet laid out as [frame 0: wind-up, frame 1: right-hand throw,
    # frame 2: left-hand throw] (see CharacterSpriteLoader in
    # sprite_system.py). The real game never plays that sheet as a normal
    # 0->1->2->loop cycle:
    #   - update_burning_charge() calls sprite.restart_animation('kiblast', ...)
    #     every single tick, which forces the anim back to frame 0 before it
    #     can ever advance — so charging holds on frame 0 forever, not a loop.
    #   - release_burning() switches to 'kiblast_hold1', which sprite_system.py
    #     registers as a *synthetic* one-frame animation (frame_indices=(1,),
    #     source_name='kiblast') — there's no kiblast_hold1.png file on disk.
    # discover_animations() (used below to build this preview's frame lists)
    # only discovers real per-name sheet files, so it has no 'kiblast_hold1'
    # entry at all — it would silently fall back to plain 'kiblast' and then
    # this class's generic cycle-through-every-frame update() would play
    # wind-up -> right-throw -> left-throw -> wind-up -> ... on a loop
    # forever, which is the "just loops again and again" bug.
    #
    # Fix: each state maps to a (anim_name, hold_frame) pair. hold_frame=None
    # means "cycle normally" (idle/walk/charge/firebeam all behave as
    # before). hold_frame=<int> means "use this anim's frame list, but pin
    # on that single index instead of cycling" — frame 0 for charging (mirrors
    # restart_animation pinning it there every tick), frame 1 for firing/
    # decaying (mirrors kiblast_hold1's synthetic single-frame animation).
    _BEAM_STYLE_ANIM_FOR_STATE = {
        "idle":     [("idle", None), ("walk", None), ("run", None)],
        "charging": [("charge", None), ("idle", None), ("walk", None)],
        "firing":   [("firebeam", None), ("charge", None), ("idle", None), ("walk", None)],
        "decaying": [("firebeam", None), ("charge", None), ("idle", None), ("walk", None)],
    }
    # Sword archetype (energy_sword.py): its own dedicated clip names —
    # 'charge_sword' while drawing the blade, 'sword_spin_cw'/'sword_spin_ccw'
    # once the spin auto-fires (see sprite_system.py's CharacterSpriteLoader
    # and player.py's start_sword_spin()) — NOT 'charge'/'firebeam', which
    # this character sheet doesn't use for this attack at all and would
    # silently fall through to idle/walk instead (same trap the projectile
    # archetype's kiblast/kiblast_hold1 note above already had to work
    # around). Split into cw/ccw variants since the two spin directions are
    # hand-drawn as separate sheets, not mirrored — see _anim_archetype_key()
    # below for how the right one is picked each frame from the built
    # attack_obj's own .clockwise (falling back to the config's Clockwise
    # field while only charging, since there's no attack_obj yet to read).
    # 'decaying' never actually happens for this archetype in practice (see
    # EnergySwordSpinEffect.no_release_cancel) but is filled in the same
    # shape as the others for consistency/safety.
    _SWORD_CW_ANIM_FOR_STATE = {
        "idle":     [("idle", None), ("walk", None), ("run", None)],
        "charging": [("charge_sword", None), ("idle", None), ("walk", None)],
        "firing":   [("sword_spin_cw", None), ("charge_sword", None), ("idle", None), ("walk", None)],
        "decaying": [("sword_spin_cw", None), ("charge_sword", None), ("idle", None), ("walk", None)],
    }
    _SWORD_CCW_ANIM_FOR_STATE = {
        "idle":     [("idle", None), ("walk", None), ("run", None)],
        "charging": [("charge_sword", None), ("idle", None), ("walk", None)],
        "firing":   [("sword_spin_ccw", None), ("charge_sword", None), ("idle", None), ("walk", None)],
        "decaying": [("sword_spin_ccw", None), ("charge_sword", None), ("idle", None), ("walk", None)],
    }
    ANIM_FOR_STATE_BY_ARCHETYPE = {
        "beam":  _BEAM_STYLE_ANIM_FOR_STATE,
        "chain": _BEAM_STYLE_ANIM_FOR_STATE,
        "projectile": {
            "idle":     [("idle", None), ("walk", None), ("run", None)],
            # Pinned to frame 0 — matches restart_animation('kiblast', ...)
            # re-resetting to the wind-up frame every tick while charging.
            "charging": [("kiblast", 0), ("charge", None), ("idle", None), ("walk", None)],
            # Pinned to frame 1 — matches the synthetic 'kiblast_hold1'
            # (frame_indices=(1,)) the real game switches to on release.
            "firing":   [("kiblast", 1), ("charge", None), ("idle", None), ("walk", None)],
            "decaying": [("kiblast", 1), ("charge", None), ("idle", None), ("walk", None)],
        },
        # Looked up via the "sword_cw"/"sword_ccw" keys _anim_archetype_key()
        # produces below — "sword" itself is never used as a key directly.
        "sword_cw":  _SWORD_CW_ANIM_FOR_STATE,
        "sword_ccw": _SWORD_CCW_ANIM_FOR_STATE,
        "dragon_fist": {
            "idle":     [("idle", None), ("walk", None), ("run", None)],
            # "charging" never actually happens for this archetype (its
            # config's charge_enabled is hardcoded False — see
            # DragonFistAttackConfig's docstring — so _on_fire_press goes
            # straight to STATE_FIRING) — filled in anyway for safety.
            # 'dragon_fist' is a single un-split clip (not a charge/fire
            # pair like the beam-style archetypes' 'charge'/'firebeam') —
            # sprite_system.py loads it with loop_tail_frames=2: it plays
            # its wind-up through once, then holds/loops just its last 2
            # frames forever — the third tuple element below (2) replicates
            # that exactly (see PreviewActor.update()'s _loop_tail handling)
            # instead of naively wrapping the whole clip back to frame 0.
            "charging": [("dragon_fist", None, 2), ("idle", None), ("walk", None)],
            "firing":   [("dragon_fist", None, 2), ("idle", None), ("walk", None)],
            "decaying": [("dragon_fist", None, 2), ("idle", None), ("walk", None)],
        },
        # Genkidama (attacks/genkidama.py): real clip name is
        # 'charge_genkidama' (sprite_system.py's CharacterSpriteLoader),
        # loaded with hold_frames=(1, 2) — loops mid-sheet while charging,
        # then release_genkidama() calls release_hold() and it plays
        # through to its final frame — the throw pose — same as
        # 'transform'. Charging is pinned to frame 1 (confirmed working).
        # Firing/decaying is pinned to -1 (Python's last-element index,
        # NOT a literal frame-count guess) rather than a fixed number like
        # 3 — the earlier fixed guess was almost certainly clamping to the
        # same frame charging already pins to (see _refresh_sprite's
        # `min(hold_frame, len(frames) - 1)` and update()'s
        # `self._active_frames[self._hold_frame]`, both of which handle a
        # negative index the normal Python way), which is exactly why
        # firing looked identical to charging regardless of which small
        # positive number was tried. -1 always resolves to whatever the
        # actual last frame is, however many frames charge_genkidama.png
        # really has, so it's guaranteed distinct from frame 1 as long as
        # the sheet has more than one frame at all.
        "genkidama": {
            "idle":     [("idle", None), ("walk", None), ("run", None)],
            "charging": [("charge_genkidama", 1), ("charge", None), ("idle", None), ("walk", None)],
            "firing":   [("charge_genkidama", -1), ("charge", None), ("idle", None), ("walk", None)],
            "decaying": [("charge_genkidama", -1), ("charge", None), ("idle", None), ("walk", None)],
        },
        # Ultra Volleyball (attacks/ultra_volleyball_attack.py): unlike
        # every archetype above, there's no dedicated 'ultra_volleyball'
        # clip on disk at all — Player.shoot_ultra_volleyball() reuses the
        # exact same kiblast.png wind-up/throw sheet a regular blast uses
        # (see that method's own docstring). Unlike the 'projectile' entry
        # above though, this archetype has no charge tab at all (goes
        # straight from press to STATE_FIRING — see
        # UltraVolleyballAttackConfig's docstring), so there's no separate
        # "charging" phase to pin frame 0 during and a "firing" phase to
        # pin frame 1 during the way projectile does — pinning straight to
        # frame 1 the instant firing starts skipped the wind-up entirely
        # and just snapped to a static throw pose.
        #
        # The (None, 1, 2) here means: play kiblast_{direction} through
        # once from frame 0 (reset by set_anim_state()'s frame reset on
        # the idle->firing transition), holding/looping just its last 1
        # frame once it gets there (frame 1, the throw pose) — same
        # loop_tail_frames convention 'dragon_fist' uses above — but
        # capped to the first 2 raw frames before any of that runs (the
        # max_frames=2 — see _refresh_sprite's own comment on why this
        # preview's raw kiblast list is longer than what the real game
        # ever shows). Without the cap, a bare loop_tail would play
        # through every frame the sheet actually has — including the
        # extra one the real 'kiblast' animation never uses — before
        # settling, which is what "plays the whole thing instead of just
        # specific frames" looked like before this was added. There's
        # also no decay pose — release doesn't stop this attack early
        # (see UltraVolleyballAttack.no_release_cancel / _on_fire_release
        # below), it just keeps flying until it self-despawns — so
        # "firing" and "decaying" share the same entry.
        "ultra_volleyball": {
            "idle":     [("idle", None), ("walk", None), ("run", None)],
            "charging": [("kiblast", None, 1, 2), ("idle", None), ("walk", None)],
            "firing":   [("kiblast", None, 1, 2), ("idle", None), ("walk", None)],
            "decaying": [("kiblast", None, 1, 2), ("idle", None), ("walk", None)],
        },
    }
    FRAME_DURATION = 0.12  # seconds per frame, animated regardless of state

    def __init__(self, x, y, char_id: str):
        self.x = x
        self.y = y
        self.height = 40
        self.direction = "down"
        self.char_id = char_id
        self.anim_state = "idle"     # set by AttackCreator.update() from its own state machine
        self.anim_archetype = "beam"  # ditto — which archetype's clip-name map to use
        # Sword-spin-only: overrides self.direction for frame lookup while
        # non-None, without touching self.direction itself (the top bar's
        # direction picker still reflects the facing the attack was fired
        # in). Driven every frame from the real EnergySwordSpinEffect's own
        # current_octant() while it's spinning — see set_octant() and
        # AttackCreator.update() — so the player's own body visibly sweeps
        # through the same 8 facings the attack itself is stepping through,
        # matching player.py's start_sword_spin() in the real game, instead
        # of freezing on whatever single direction was picked before firing.
        self._octant_override = None
        self._frame_timer = 0.0
        self._frame_index = 0
        self._hold_frame = None      # None = cycle self._active_frames normally; int = pin on that index
        # Mirrors sprite_system.Animation's loop_tail_frames: None = plain
        # wrap-around loop (the old, only behavior here); an int N = play
        # through every frame once, then loop just the last N frames
        # forever — see _refresh_sprite()/update() and the class docstring
        # note on 'dragon_fist' below for why this exists.
        self._loop_tail = None
        self._finished = False
        self.sprite_frames = self._try_load_sprites(char_id)  # {"walk_down": [...], "attack_left": [...], ...}
        self.sprite = None
        self._refresh_sprite()

        # Same draw_layer / y_sort / get_sort_key contract
        # LayerIntegrationHelper.setup_player() gives the real Player (see
        # core/draw_layers.py) — this is what lets the stage run the actor
        # through the real LayerManager alongside charge_obj/attack_obj
        # (see AttackCreator._draw_stage()) and get the same front/behind
        # ordering the actual game would produce, instead of a hand-picked
        # fixed draw order that only happened to be right for some
        # directions (down/left/right) and silently wrong for others (up).
        self.draw_layer = DrawLayer.PLAYER
        self.y_sort = False

    def get_sort_key(self):
        return (self.draw_layer, 0)

    @staticmethod
    def _try_load_sprites(char_id: str) -> dict:
        """Load every directional animation for this character's default
        costume via character_creator.discover_animations() — the same
        sheet-slicing code the character creator itself uses — instead of
        guessing paths or only ever reading a single "down" frame.

        Returns {"{anim_name}_{direction}": [frames...]}, e.g.
        "walk_down", "attack_left". Missing/unreadable art just means an
        empty dict; the placeholder capsule is drawn instead."""
        try:
            costumes = character_creator.discover_costumes(char_id)
            costume = "default" if "default" in costumes else (costumes[0] if costumes else "base")
            return character_creator.discover_animations(char_id, costume)
        except Exception:
            return {}

    def _frames_for(self, anim_name: str, direction: str):
        return self.sprite_frames.get(f"{anim_name}_{direction}")

    def _refresh_sprite(self):
        """Pick the frame list for the current (anim_state, direction),
        falling back through the active archetype's ANIM_FOR_STATE map, then
        to any direction at all if this character lacks the exact direction
        (better than nothing), then to the placeholder if there's no art
        whatsoever. Each entry can also pin a single frame index (hold_frame)
        instead of cycling — see the class docstring note on kiblast/
        kiblast_hold1 above — or, as a third optional element, a
        loop_tail_frames count (see __init__'s note on _loop_tail and the
        'dragon_fist' entry below), or, as a fourth optional element,
        max_frames — entries can be 2-, 3-, or 4-tuples, mixed freely
        within the same map.

        max_frames caps how much of the loaded sheet this entry actually
        uses, taken from the front: frames[:max_frames]. This exists
        because this preview loads character art via
        character_creator.discover_animations(), which just slices a
        whole row into frames — it has no idea that the real game's
        CharacterSpriteLoader only ever registers a SUBSET of kiblast.png
        under the 'kiblast' key (frame_indices=(0, 1) — see
        sprite_system.py's own comment: "kiblast.png is laid out as
        [start, right-hand throw, left-hand throw]", i.e. 3 raw frames on
        disk, only the first 2 of which the real 'kiblast' animation ever
        shows; the third is loaded separately under 'kiblast_hold1'). So
        this preview's raw 'kiblast_{direction}' list is longer than what
        actually plays in-game, and a loop_tail here without a cap would
        visibly cycle through that extra frame before settling — see the
        'ultra_volleyball' entry below, which caps at 2 for exactly this
        reason."""
        anim_map = self.ANIM_FOR_STATE_BY_ARCHETYPE.get(
            self.anim_archetype, self._BEAM_STYLE_ANIM_FOR_STATE)
        lookup_direction = self._octant_override or self.direction
        for entry in anim_map.get(self.anim_state, [("walk", None)]):
            anim_name = entry[0]
            hold_frame = entry[1] if len(entry) > 1 else None
            loop_tail = entry[2] if len(entry) > 2 else None
            max_frames = entry[3] if len(entry) > 3 else None
            frames = self._frames_for(anim_name, lookup_direction)
            if frames:
                if max_frames:
                    frames = frames[:max_frames]
                self._active_frames = frames
                self._loop_tail = loop_tail
                self._finished = False
                if hold_frame is not None:
                    self._hold_frame = min(hold_frame, len(frames) - 1)
                    self._frame_index = self._hold_frame
                else:
                    self._hold_frame = None
                    self._frame_index %= len(frames)
                self.sprite = frames[self._frame_index]
                return
        self._active_frames = None
        self._hold_frame = None
        self._loop_tail = None
        self._finished = False
        self.sprite = None

    def set_direction(self, direction: str):
        if direction != self.direction:
            self.direction = direction
            self._refresh_sprite()

    def set_octant(self, octant: Optional[str]):
        """Sword-spin-only override — see the field's own comment in
        __init__. Pass one of core.draw_layers' 8-direction octant names
        (matching sprite_system.DIRECTIONS_8: 'down', 'down_left', 'left',
        'up_left', 'up', 'up_right', 'right', 'down_right') each frame
        while EnergySwordSpinEffect is actually spinning, or None to fall
        back to self.direction (charging/idle/every other archetype)."""
        if octant != self._octant_override:
            self._octant_override = octant
            self._refresh_sprite()

    def set_anim_state(self, anim_state: str, archetype: str = "beam"):
        if anim_state != self.anim_state or archetype != self.anim_archetype:
            self.anim_state = anim_state
            self.anim_archetype = archetype
            self._frame_index = 0
            self._frame_timer = 0.0
            self._refresh_sprite()

    def update(self, dt: float):
        """Advance the walk/attack cycle so the preview actually animates
        instead of showing one frozen frame — unless the current state has
        pinned a single hold_frame (see _refresh_sprite), in which case it
        should stay put exactly like restart_animation()/a synthetic
        single-frame animation would in the real game, not cycle through
        every other frame on the sheet.

        With no _loop_tail set, this is a plain wrap-around loop (0..N-1,
        0..N-1, ...) — fine for idle/walk/run and every beam/chain/sword
        clip. _loop_tail mirrors sprite_system.Animation's loop_tail_frames
        instead: play through 0..N-1 exactly once, then loop only the last
        _loop_tail frames forever — same two-branch shape as that class's
        own update() (see its comment on tail_start), just against
        self._frame_index instead of an Animation instance. 'dragon_fist'
        needs this specifically: sprite_system.py loads it with
        loop_tail_frames=2 (plays its wind-up once, then holds/loops its
        last 2 frames) — without this, the preview just wrapped the whole
        clip back to frame 0 every cycle, replaying the wind-up over and
        over instead of settling into the held pose."""
        if self._hold_frame is not None:
            self.sprite = self._active_frames[self._hold_frame]
            return
        if not self._active_frames or len(self._active_frames) <= 1:
            return
        self._frame_timer += dt
        while self._frame_timer >= self.FRAME_DURATION:
            self._frame_timer -= self.FRAME_DURATION
            self._frame_index += 1
            tail_start = (
                max(0, len(self._active_frames) - self._loop_tail)
                if self._loop_tail else None
            )
            if self._frame_index >= len(self._active_frames):
                if self._loop_tail:
                    self._finished = True
                    self._frame_index = tail_start
                else:
                    self._frame_index = 0
            elif self._finished and self._loop_tail and self._frame_index < tail_start:
                self._frame_index = tail_start
        self.sprite = self._active_frames[self._frame_index]

    def draw(self, surf, camera, colors=None):
        # Signature matches the LayerManager contract (screen, camera,
        # colors) — see core/draw_layers.py's LayerManager.draw_all(),
        # which calls every registered object's draw() the same way
        # regardless of type. `colors` isn't used by the placeholder/
        # sprite rendering below; RENDER_SCALE is looked up directly
        # instead of being passed in, same lazy-import pattern used
        # elsewhere in this file to sidestep import-order issues when run
        # standalone.
        from config.settings import RENDER_SCALE as scale
        screen_x = (self.x * scale) - camera.x
        screen_y = (self.y * scale) - camera.y
        if self.sprite:
            frame = pygame.transform.scale(
                self.sprite, (self.sprite.get_width() * scale, self.sprite.get_height() * scale)
            )
            rect = frame.get_rect(midbottom=(int(screen_x), int(screen_y)))
            surf.blit(frame, rect)
        else:
            w, h = 20 * scale, self.height * scale
            body = pygame.Rect(0, 0, w, h)
            body.midbottom = (int(screen_x), int(screen_y))
            surf.draw_ellipse((90, 130, 170), body)
            surf.draw_ellipse(uk.Theme.CARD_BORDER, body, width=2)
            label = pygame.font.Font(None, 16).render(self.char_id, True, uk.Theme.TEXT_DIM)
            surf.blit(label, label.get_rect(midtop=(body.centerx, body.bottom + 2)))


class FakeCamera:
    x = 0.0
    y = 0.0
# ─────────────────────────────────────────────────────────────────────────
#  Layout constants
# ─────────────────────────────────────────────────────────────────────────
ROW_H = 32          # parameter field row height
SECTION_H = 26       # parameter section header row height
TAB_H = 34
FIELD_VALUE_W = 128
SIDEBAR_ROW_H = 34
FIELD_MAX_LEN = 40

# Per-archetype accent colour — same "colored dot = identity" idea
# item_creator.py uses for its category dots, here identifying which
# archetype a saved config (or the active tab row) belongs to.
ARCHETYPE_ACCENT = {
    "beam": uk.Theme.GOLD,
    "chain": uk.Theme.KI_BLUE,
    "projectile": (240, 146, 92),
    "sword": (167, 139, 250),
    "dragon_fist": (235, 110, 150),
    "genkidama": (94, 210, 148),
    "ultra_volleyball": (140, 200, 255),
}


class AttackCreator:
    STATE_IDLE, STATE_CHARGING, STATE_FIRING, STATE_DECAYING = "idle", "charging", "firing", "decaying"

    def __init__(self, screen_width, screen_height):
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.active = False   # same contract as CharacterCreator: game.py gates
        self._logical_mouse_pos = (screen_width // 2, screen_height // 2)

        # Same bitmap-font family / split as DevMenu / item_creator: title
        # text uses the plain uppercase/lowercase glyph set, everything
        # else uses the menu glyph set.
        self._menu_font = uk.BitmapFont('assets\\ui\\fonts', letter_spacing=1)
        self._title_bitmap_font = uk.BitmapFont('assets\\ui\\fonts', letter_spacing=1)
        self._title_bitmap_font.uppercase_dir = os.path.join('assets', 'ui', 'fonts', 'uppercase')
        self._title_bitmap_font.lowercase_dir = os.path.join('assets', 'ui', 'fonts', 'lowercase')

        self.font_title = _BitmapFontView(self._title_bitmap_font, 28)
        self.font_large = _BitmapFontView(self._menu_font, 17)
        self.font_medium = _BitmapFontView(self._menu_font, 14)
        self.font_small = _BitmapFontView(self._menu_font, 12)
        self.font_tiny = _BitmapFontView(self._menu_font, 10)

        self._back_icon = self._load_dev_menu_icon('back', 34)
        self._save_icon = self._load_dev_menu_icon('save', 26)
        self._trash_icon = self._load_dev_menu_icon('trash', 22)
        self._plus_icon = self._load_dev_menu_icon('plus', 18)
        self._dup_icon = self._load_dev_menu_icon('duplicate', 18)

        # ── functional state (unchanged from the previous version) ──────
        self.active_archetype = "beam"
        self.config = self._make_config(self.active_archetype, {"id": "new_attack", "display_name": "New Attack"})
        self.active_tab = self._first_tab(self.config)   # e.g. 'beam' | 'charge', or 'chain' | 'charge'
        self.status_msg = ""
        self.status_ok = True
        self.status_timer = 0.0

        self.characters = self._scan_characters()
        self.char_index = 0
        self.direction_index = 0

        self.state = self.STATE_IDLE
        self.charge_obj = None
        self.attack_obj = None
        self.charge_elapsed = 0.0
        self._pending_ultra_volleyball = None
        self._pending_direction = None
        self._pending_ultra_volleyball_elapsed = 0.0

        # Real LayerManager (core/draw_layers.py) — the same one game.py
        # uses — so the stage sorts actor/charge_obj/attack_obj by their
        # actual draw_layer/get_sort_key() instead of a fixed draw order.
        self.layer_manager = LayerManager()

        self.paused = False
        self.dragging_offset = False
        self._drag_offset_attr: Optional[str] = None

        # ── sidebar (saved-config list + archetype picker) ──────────────
        self.sidebar_scroll = 0
        self._sidebar_row_rects: list = []

        # ── param panel (tabs + field list) ──────────────────────────────
        self._param_rows: list = []            # [("header", label) | ("field", spec, target)]
        self.panel_scroll = 0
        self._field_row_rects: dict = {}        # (tab, key) -> rect, rebuilt each draw()
        self.tab_rects: dict = {}
        self._panel_scroll_dragging = False
        self._panel_scrollbar_track: Optional[pygame.Rect] = None
        self._panel_scrollbar_thumb: Optional[pygame.Rect] = None
        self._panel_scrollbar_max_scroll = 0

        # ── inline field-edit engine (single-line only — every field in
        # this tool's param panel is a short int/float/str value) ───────
        self.editing_field: Optional[tuple] = None   # (tab, key) or None
        self._editing_spec = None
        self._editing_target = None
        self.text_input = ""
        self.cursor_pos = 0
        self.selection_anchor = None
        self.cursor_blink = 0.0
        self._text_drag = False
        self._text_max_len = FIELD_MAX_LEN
        self._active_edit_rect: Optional[pygame.Rect] = None
        self._active_edit_text_x: Optional[int] = None
        self._active_edit_font = None
        self.text_field_rects: list = []

        # ── hover/press anim buckets ──────────────────────────────────────
        self._back_hovered = False
        self._back_hover_anim = 0.0
        self._save_hovered = False
        self._save_hover_anim = 0.0
        self._new_hovered = False
        self._new_hover_anim = 0.0
        self._dup_hovered = False
        self._dup_hover_anim = 0.0
        self._delete_hovered = False
        self._delete_hover_anim = 0.0
        self._pause_hovered = False
        self._pause_hover_anim = 0.0
        self._fire_hovered = False
        self._fire_hover_anim = 0.0
        self._arch_prev_hovered = False
        self._arch_next_hovered = False
        self._char_prev_hovered = False
        self._char_next_hovered = False
        self._dir_prev_hovered = False
        self._dir_next_hovered = False
        self.sidebar_hover_index = -1
        self._sidebar_hover_anim: list = [0.0] * 64
        self._enabled_chip_hovered = False
        self._enabled_chip_hover_anim = 0.0

        # Fire is a hold button — pressed state is tracked separately from
        # hover so the stage knows to keep firing across MOUSEMOTION events.
        self._fire_pressed = False

        self._build_layout()
        self._reload_panel()
        self._new_actor()

    # ------------------------------------------------------------------ setup
    @staticmethod
    def _load_dev_menu_icon(icon_key, box_size):
        """Same shared dev-menu PNG icon loader (crop + point-sample scale)
        DevMenu._load_icon / item_creator._load_dev_menu_icon use, so these
        icons match theirs pixel-for-pixel."""
        path = os.path.join('assets', 'ui', 'dev_menu', 'icons', f'{icon_key}.png')
        try:
            raw = pygame.image.load(path).convert_alpha()
        except (FileNotFoundError, pygame.error):
            return None

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

    @staticmethod
    def _make_config(archetype: str, data: dict):
        """Construct a fresh config of the given archetype — the one
        place that dispatches through ARCHETYPES, so every call site
        (new/duplicate/archetype-switch) stays archetype-agnostic."""
        _, config_cls = ARCHETYPES[archetype]
        return config_cls(data)

    @staticmethod
    def _first_tab(config) -> str:
        return next(iter(config.PARAM_SETS))

    @staticmethod
    def _scan_characters() -> list:
        """Delegate to character_creator.discover_characters() instead of
        listing assets/sprites/player/ ourselves — see the previous
        version of this file's own comment: this is the same roster
        game.py plays from, and excludes soft-deleted characters."""
        names = character_creator.discover_characters()
        if names:
            return names
        return ["default"]

    def _build_layout(self):
        sw, sh = self.screen_width, self.screen_height
        # Same header/footer formula as DevMenu / item_creator.
        self.header_h = max(86, round(sh * 0.12))
        self.footer_h = max(42, round(sh * 0.065))
        self.margin_x = max(24, round(sw * 0.03))

        back_size = max(40, round(self.header_h * 0.55))
        self._back_rect = pygame.Rect(0, 0, back_size, back_size)
        self._back_rect.left = self.margin_x
        self._back_rect.centery = self.header_h // 2

        self._title_surf = self.font_title.render("ATTACK CREATOR", True, uk.Theme.TEXT_PRIMARY)

        self._save_rect = pygame.Rect(0, 0, back_size, back_size)
        self._save_rect.right = sw - self.margin_x
        self._save_rect.centery = self.header_h // 2

        gap = 16
        content_top = self.header_h + gap
        content_bottom = sh - self.footer_h - gap
        content_h = max(100, content_bottom - content_top)

        sidebar_w = min(300, max(230, int(sw * 0.19)))
        panel_w = min(400, max(300, int(sw * 0.26)))
        self.panel_w = panel_w

        self.sidebar_rect = pygame.Rect(self.margin_x, content_top, sidebar_w, content_h)
        self.panel_rect_outer = pygame.Rect(sw - self.margin_x - panel_w, content_top, panel_w, content_h)
        center_x = self.sidebar_rect.right + gap
        center_w = self.panel_rect_outer.x - gap - center_x

        top_h = 60
        self.top_rect = pygame.Rect(center_x, content_top, center_w, top_h)
        self.stage_rect = pygame.Rect(center_x, self.top_rect.bottom + gap,
                                       center_w, content_h - top_h - gap)

        # ── sidebar internals ──────────────────────────────────────────
        pad = 14
        x = self.sidebar_rect.x + pad
        w = self.sidebar_rect.w - pad * 2
        y = self.sidebar_rect.y + pad

        self._sidebar_title_pos = (x, y)
        y += 30

        arch_btn = 28
        self._arch_prev_rect = pygame.Rect(x, y, arch_btn, arch_btn)
        self._arch_next_rect = pygame.Rect(x + w - arch_btn, y, arch_btn, arch_btn)
        self._arch_row_rect = pygame.Rect(x, y, w, arch_btn)
        y += arch_btn + 10

        bw = (w - 10) // 2
        self._new_rect = pygame.Rect(x, y, bw, 30)
        self._dup_rect = pygame.Rect(x + bw + 10, y, bw, 30)
        y += 30 + 12

        list_bottom = self.sidebar_rect.bottom - pad - 34 - 10
        self._sidebar_list_rect = pygame.Rect(x, y, w, max(40, list_bottom - y))

        self._delete_rect = pygame.Rect(x, self.sidebar_rect.bottom - pad - 34, w, 34)

        # ── top bar internals (character + direction pickers) ───────────
        nav_btn = 30
        tx = self.top_rect.x + 14
        ty = self.top_rect.centery
        self._char_prev_rect = pygame.Rect(tx, ty - nav_btn // 2, nav_btn, nav_btn)
        self._char_label_rect = pygame.Rect(self._char_prev_rect.right + 6, self.top_rect.y, 130, self.top_rect.h)
        self._char_next_rect = pygame.Rect(self._char_label_rect.right + 6, ty - nav_btn // 2, nav_btn, nav_btn)

        dx = self._char_next_rect.right + 26
        self._dir_prev_rect = pygame.Rect(dx, ty - nav_btn // 2, nav_btn, nav_btn)
        self._dir_label_rect = pygame.Rect(self._dir_prev_rect.right + 6, self.top_rect.y, 110, self.top_rect.h)
        self._dir_next_rect = pygame.Rect(self._dir_label_rect.right + 6, ty - nav_btn // 2, nav_btn, nav_btn)

        # ── stage internals (hold-to-fire + pause) — bottom-right, clear
        # of the offset-handle readout text at the bottom-left, and
        # thinner than a standard pill so the row stays compact. Centered
        # in the 40px band between the ground line and the stage's own
        # bottom edge, rather than pinned to either. ─────────────────────
        fire_w, fire_h = 168, 22
        self._fire_rect = pygame.Rect(0, 0, fire_w, fire_h)
        self._fire_rect.right = self.stage_rect.right - 12
        self._fire_rect.centery = self.stage_rect.bottom - 20

        pause_w = 88
        self._pause_rect = pygame.Rect(0, 0, pause_w, fire_h)
        self._pause_rect.right = self._fire_rect.left - 8
        self._pause_rect.centery = self._fire_rect.centery

        self._build_tabs()

    def _build_tabs(self):
        """(Re)build self.tab_rects — one Rect per entry in
        self.config.PARAM_SETS, evenly dividing the fixed-height tab row.
        Called from _build_layout() and again whenever self.config swaps
        to a config of a different archetype (different PARAM_SETS)."""
        names = list(self.config.PARAM_SETS.keys())
        n = max(1, len(names))
        self.tab_rects = {}
        x = self.panel_rect_outer.x
        for i, name in enumerate(names):
            w = self.panel_w // n if i < n - 1 else self.panel_w - (self.panel_w // n) * (n - 1)
            self.tab_rects[name] = pygame.Rect(x, self.panel_rect_outer.y, w, TAB_H)
            x += w

        enable_h = 26
        self._enabled_chip_rect = pygame.Rect(
            self.panel_rect_outer.right - 14 - 84, self.panel_rect_outer.y + TAB_H + 8, 84, enable_h)

        field_top = self.panel_rect_outer.y + TAB_H + (enable_h + 16 if self.active_tab in self.config.OPTIONAL_SETS else 8)
        self._panel_list_rect = pygame.Rect(
            self.panel_rect_outer.x, field_top,
            self.panel_w, self.panel_rect_outer.bottom - field_top - 8)

    def _reload_panel(self):
        groups = self.config.GROUPS.get(self.active_tab) or [("Parameters", self.config.PARAM_SETS[self.active_tab])]
        target = self.config.params[self.active_tab]
        rows = []
        for section_name, fields in groups:
            rows.append(("header", section_name))
            for spec in fields:
                rows.append(("field", spec, target))
        self._param_rows = rows
        self.panel_scroll = 0

    def _panel_content_height(self) -> int:
        h = 0
        for row in self._param_rows:
            h += SECTION_H if row[0] == "header" else ROW_H
        return h

    def _panel_row_top_offsets(self):
        """Cumulative top-y (in content space) of every row, plus a
        trailing sentinel equal to the total content height."""
        offsets = []
        y = 0
        for row in self._param_rows:
            offsets.append(y)
            y += SECTION_H if row[0] == "header" else ROW_H
        offsets.append(y)
        return offsets

    def _snap_panel_scroll(self, raw: int) -> int:
        """Snap a raw scroll offset down to the top of whichever row it
        falls in. Rows have two different heights (section headers vs.
        fields), so a free pixel scroll can leave a row's text starting
        a few pixels above self._panel_list_rect.y — clipping is
        supposed to hide that sliver, but on the real engine's GPUScreen
        that clip didn't reliably hold, and a section header ('Identity',
        'Asset Folder', ...) bled up over the tab row above it. Snapping
        to row boundaries means a row is never drawn starting above the
        list's top edge in the first place, so there's nothing for a
        clip bug to fail to hide."""
        offsets = self._panel_row_top_offsets()
        total = offsets[-1]
        max_scroll = max(0, total - self._panel_list_rect.h)
        raw = max(0, min(max_scroll, raw))
        best = 0
        for off in offsets:
            if off <= raw:
                best = off
            else:
                break
        return min(best, max_scroll)

    def _scrub_panel_scroll(self, mouse_y: int) -> None:
        """Map a mouse y position to a scroll offset, for click-to-jump
        and drag-follow on the field-list scrollbar thumb."""
        track = self._panel_scrollbar_track
        thumb = self._panel_scrollbar_thumb
        if track is None or thumb is None:
            return
        usable = max(1, track.height - thumb.height)
        rel = mouse_y - track.y - thumb.height / 2
        frac = max(0.0, min(1.0, rel / usable))
        raw = int(round(frac * self._panel_scrollbar_max_scroll))
        self.panel_scroll = self._snap_panel_scroll(raw)

    def _new_actor(self):
        cx = self.stage_rect.centerx
        cy = self.stage_rect.centery + 60
        self.actor = PreviewActor(PREVIEW_WORLD_ANCHOR, PREVIEW_WORLD_ANCHOR, self.characters[self.char_index])
        self.actor.set_direction(DIRECTIONS[self.direction_index])  # keep facing on character switch
        self.camera = FakeCamera()
        from config.settings import RENDER_SCALE
        self.camera.x = PREVIEW_WORLD_ANCHOR * RENDER_SCALE - cx
        self.camera.y = PREVIEW_WORLD_ANCHOR * RENDER_SCALE - cy

    # ── sidebar actions (unchanged) ─────────────────────────────────────
    def _saved_configs(self):
        return list_saved_configs(CONFIGS_DIR)

    def _cycle_archetype(self, step):
        keys = list(ARCHETYPES.keys())
        idx = (keys.index(self.active_archetype) + step) % len(keys)
        self.active_archetype = keys[idx]
        self._on_new()

    def _on_new(self):
        self._stop_preview()
        self.config = self._make_config(self.active_archetype, {"id": "new_attack", "display_name": "New Attack"})
        self.active_tab = self._first_tab(self.config)
        self._build_tabs()
        self._reload_panel()
        self._set_status("New attack — edit it, then Save.", True)

    def _on_duplicate(self):
        self._stop_preview()
        new_id = f"{self.config.id}_copy"
        self.config = self.config.clone(new_id)
        self._reload_panel()
        self._set_status(f"Duplicated as '{new_id}' (not saved yet).", True)

    def _on_delete(self):
        path = CONFIGS_DIR / f"{self.config.id}.json"
        if path.exists():
            path.unlink()
            self._set_status(f"Deleted {self.config.id}.json", True)
        else:
            self._set_status("Nothing saved under this id yet.", False)

    def _on_save(self):
        self._stop_preview()
        clean_id = "".join(c for c in self.config.id.strip().lower().replace(" ", "_") if c.isalnum() or c == "_")
        if not clean_id:
            self._set_status("Attack needs an id before it can be saved.", False)
            return
        self.config.id = clean_id
        path = CONFIGS_DIR / f"{clean_id}.json"
        self.config.save(path)
        warnings = self.config.missing_asset_warnings(BASE_DIR / "assets")
        if warnings:
            self._set_status(f"Saved. {len(warnings)} asset warning(s) — see console.", True)
            for w in warnings:
                print(f"[attack_creator] {self.config.id}: {w}")
        else:
            self._set_status(f"Saved to {path.relative_to(BASE_DIR)}", True)

    def _load_config_from_path(self, path):
        self._stop_preview()
        self.config = load_config(path)   # archetype-agnostic — reads the JSON's own "archetype" field
        self.active_archetype = self.config.archetype
        self.active_tab = self._first_tab(self.config)
        self._build_tabs()
        self._reload_panel()
        self._set_status(f"Loaded {self.config.id}", True)

    # ── character / direction (unchanged) ───────────────────────────────
    def _prev_char(self):
        self.char_index = (self.char_index - 1) % len(self.characters)
        self._new_actor()

    def _next_char(self):
        self.char_index = (self.char_index + 1) % len(self.characters)
        self._new_actor()

    def _prev_dir(self):
        self._stop_preview()
        self.direction_index = (self.direction_index - 1) % len(DIRECTIONS)
        self.actor.set_direction(DIRECTIONS[self.direction_index])

    def _next_dir(self):
        self._stop_preview()
        self.direction_index = (self.direction_index + 1) % len(DIRECTIONS)
        self.actor.set_direction(DIRECTIONS[self.direction_index])

    # ── fire lifecycle — mirrors player.py's hold-to-charge/release-to-
    # fire/release-to-decay beam contract described in beam.py's own
    # comments, just driven by a mouse button instead of a keybind.
    # UNCHANGED from the previous version of this file. ──
    def _on_fire_press(self):
        if self.paused:
            return
        self._stop_preview()
        direction = self.actor.direction
        if self.config.charge_enabled:
            self.charge_obj = self.config.build_charge_effect(self.actor)
            self.charge_elapsed = 0.0
            self.state = self.STATE_CHARGING
        elif self.active_archetype == "ultra_volleyball":
            self._pending_ultra_volleyball = True
            self._pending_direction = direction
            self._pending_ultra_volleyball_elapsed = 0.0
            self.state = self.STATE_FIRING
        else:
            self.attack_obj = self.config.build_attack(self.actor.x, self.actor.y, direction, player=self.actor)
            self.state = self.STATE_FIRING

    def _on_fire_release(self):
        if self.paused:
            return
        if self.state == self.STATE_CHARGING:
            if getattr(self.config, "fires_on_release", False) and self.charge_obj is not None:
                self.attack_obj = self.config.build_attack(
                    self.actor.x, self.actor.y, self.actor.direction,
                    player=self.actor, charge_obj=self.charge_obj)
                self.charge_obj = None
                self.state = self.STATE_FIRING
            else:
                self._stop_preview()
        elif self.state == self.STATE_FIRING:
            if self._pending_ultra_volleyball is not None:
                pass
            elif getattr(self.attack_obj, "no_release_cancel", False):
                pass
            elif hasattr(self.attack_obj, "start_decay"):
                self.attack_obj.start_decay()
                self.state = self.STATE_DECAYING
            elif hasattr(self.attack_obj, "start_retract"):
                self.attack_obj.start_retract()
                self.state = self.STATE_DECAYING
            elif hasattr(self.attack_obj, "stop"):
                self.attack_obj.stop()
                self._stop_preview()
            else:
                self._stop_preview()
        # already decaying / idle: nothing to do, let it finish naturally

    def _stop_preview(self):
        self.state = self.STATE_IDLE
        self.charge_obj = None
        self.attack_obj = None
        self._pending_ultra_volleyball = None
        self._pending_direction = None
        self._pending_ultra_volleyball_elapsed = 0.0

    def _toggle_pause(self):
        """Freeze the fire state machine and the actor's own animation so
        the currently-visible frame holds still — meant to be hit mid-
        charge/fire/decay so the on-stage offset handle can be dragged
        precisely without everything moving out from under the mouse."""
        self.paused = not self.paused

    def _set_status(self, msg, ok):
        self.status_msg = msg
        self.status_ok = ok
        self.status_timer = 4.0

    # ── lifecycle (same contract as character_creator.CharacterCreator) ──
    def toggle(self):
        self.active = not self.active
        if not self.active:
            self._stop_preview()
            self.paused = False
            self.dragging_offset = False
            self._drag_offset_attr = None
            self._panel_scroll_dragging = False
            self._cancel_field_edit()

    # ── frame update ─────────────────────────────────────────────────
    def update(self, dt):
        if not self.active:
            uk.set_text_cursor(False)
            uk.set_hand_cursor(False)
            return
        dt_ui = min(dt, 1 / 20)
        self.cursor_blink += dt_ui

        # hover-anim lerps (UI only)
        def _lerp(cur, target):
            return cur + (target - cur) * min(1.0, dt_ui * 12.0)
        self._back_hover_anim = _lerp(self._back_hover_anim, 1.0 if self._back_hovered else 0.0)
        self._save_hover_anim = _lerp(self._save_hover_anim, 1.0 if self._save_hovered else 0.0)
        self._new_hover_anim = _lerp(self._new_hover_anim, 1.0 if self._new_hovered else 0.0)
        self._dup_hover_anim = _lerp(self._dup_hover_anim, 1.0 if self._dup_hovered else 0.0)
        self._delete_hover_anim = _lerp(self._delete_hover_anim, 1.0 if self._delete_hovered else 0.0)
        self._pause_hover_anim = _lerp(self._pause_hover_anim, 1.0 if self._pause_hovered else 0.0)
        self._fire_hover_anim = _lerp(self._fire_hover_anim, 1.0 if self._fire_hovered else 0.0)
        self._enabled_chip_hover_anim = _lerp(self._enabled_chip_hover_anim, 1.0 if self._enabled_chip_hovered else 0.0)
        for i in range(min(len(self._saved_configs()), len(self._sidebar_hover_anim))):
            target = 1.0 if i == self.sidebar_hover_index else 0.0
            self._sidebar_hover_anim[i] = _lerp(self._sidebar_hover_anim[i], target)

        if self.status_timer > 0:
            self.status_timer -= dt_ui
            if self.status_timer <= 0:
                self.status_msg = ""

        # OS cursor: I-beam over a text field, hand over anything clickable,
        # else the plain arrow. Same once-per-frame convention/priority as
        # CharacterCreator._resolve_cursor — resolved here against the rects
        # the last draw() pass registered (text_field_rects/_field_row_rects
        # etc. are rebuilt in _draw_panel), same lag the text-cursor check
        # already lived with before this call existed.
        self._resolve_cursor()

        if self.paused:
            # Deliberately don't touch state/charge_obj/attack_obj/actor at
            # all — whatever was on screen the moment Pause was hit just
            # keeps being redrawn as-is by draw(), frozen.
            return
        if self.state == self.STATE_CHARGING and self.charge_obj:
            self.charge_obj.update(dt)
            self.charge_elapsed += dt
            if self.charge_elapsed >= self.charge_obj.get_total_duration():
                self.attack_obj = self.config.build_attack(
                    self.actor.x, self.actor.y, self.actor.direction, player=self.actor)
                self.charge_obj = None
                self.state = self.STATE_FIRING
        elif self.state == self.STATE_FIRING and self._pending_ultra_volleyball is not None:
            if self._pending_ultra_volleyball is True:
                self._pending_ultra_volleyball_elapsed += dt
                if self._pending_ultra_volleyball_elapsed >= self.actor.FRAME_DURATION:
                    self._pending_ultra_volleyball = 'ready'
            if self._pending_ultra_volleyball == 'ready':
                self.attack_obj = self.config.build_attack(
                    self.actor.x, self.actor.y, self._pending_direction, player=self.actor)
                self._pending_ultra_volleyball = None
                self._pending_direction = None
                self._pending_ultra_volleyball_elapsed = 0.0
        elif self.state in (self.STATE_FIRING, self.STATE_DECAYING) and self.attack_obj:
            try:
                param_names = set(inspect.signature(self.attack_obj.update).parameters.keys())
            except (TypeError, ValueError):
                param_names = set()
            if {"player_x", "player_y"} <= param_names:
                self.attack_obj.update(dt, self.actor.x, self.actor.y)
            elif len(param_names) >= 3:
                # Matches Projectile.update(self, world_width, world_height,
                # dt=0.016)'s real positional order (see attacks/projectile.py
                # and attacks/genkidama.py's GenkidamaBlast, which shares it).
                self.attack_obj.update(PREVIEW_WORLD_BOUND, PREVIEW_WORLD_BOUND, dt)
            else:
                self.attack_obj.update(dt)
            if not self.attack_obj.active:
                self._stop_preview()

        self.actor.set_anim_state(self.state, self._anim_archetype_key())
        if self.active_archetype == "sword" and self.state in (self.STATE_FIRING, self.STATE_DECAYING) \
                and self.attack_obj is not None and hasattr(self.attack_obj, "current_octant"):
            self.actor.set_octant(self.attack_obj.current_octant())
        else:
            self.actor.set_octant(None)
        self.actor.update(dt)

    def _anim_archetype_key(self) -> str:
        if self.active_archetype != "sword":
            return self.active_archetype
        clockwise = getattr(self.attack_obj, "clockwise", None)
        if clockwise is None:
            clockwise = bool(self.config.sword.get("clockwise", True))
        return "sword_cw" if clockwise else "sword_ccw"

    # ── offset drag handle (charge / beam / chain / projectile /
    # dragon_fist / sword — archetype-generic). UNCHANGED. ──
    def _active_offset_attr(self) -> Optional[str]:
        attr_name = OFFSET_ATTR_FOR_TAB.get(self.active_tab)
        if attr_name and hasattr(self.config, attr_name):
            return attr_name
        return None

    def _offset_anchor_extra(self, attr_name: str):
        if attr_name == "dragon_fist_offsets":
            dxu, dyu = _DRAGON_FIST_DIRECTION_UNIT.get(self.actor.direction, (0, 0))
            anchor_offset = self.config.dragon_fist.get("anchor_offset", 0)
            return dxu * anchor_offset, dyu * anchor_offset
        if attr_name == "direction_offsets" and self.active_archetype == "genkidama":
            return 0, -getattr(self.actor, "height", 0) / 2
        if attr_name == "genkidama_offsets":
            dox, doy = self.config.direction_offsets.get(self.actor.direction, (0, 0))
            return dox, doy - getattr(self.actor, "height", 0) / 2
        return 0, 0

    def _offset_screen_pos(self, attr_name: str):
        from config.settings import RENDER_SCALE
        offsets = getattr(self.config, attr_name)
        ox, oy = offsets.get(self.actor.direction, (0, 0))
        ex, ey = self._offset_anchor_extra(attr_name)
        screen_x = (self.actor.x + ox + ex) * RENDER_SCALE - self.camera.x
        screen_y = (self.actor.y + oy + ey) * RENDER_SCALE - self.camera.y
        return int(screen_x), int(screen_y)

    def _drag_offset_to(self, attr_name: str, mouse_pos):
        from config.settings import RENDER_SCALE
        direction = self.actor.direction
        ex, ey = self._offset_anchor_extra(attr_name)
        world_x = (mouse_pos[0] + self.camera.x) / RENDER_SCALE - self.actor.x - ex
        world_y = (mouse_pos[1] + self.camera.y) / RENDER_SCALE - self.actor.y - ey
        new_offset = (round(world_x), round(world_y))
        offsets_dict = getattr(self.config, attr_name)
        old_offset = offsets_dict.get(direction, (0, 0))
        offsets_dict[direction] = new_offset

        if attr_name == "direction_offsets" and self.charge_obj is not None \
                and hasattr(self.charge_obj, "direction_offsets"):
            try:
                self.charge_obj.direction_offsets[direction] = new_offset
            except Exception:
                pass
        elif attr_name == "sword_offsets" and self.attack_obj is not None \
                and hasattr(self.attack_obj, "direction_offsets"):
            try:
                self.attack_obj.direction_offsets[direction] = new_offset
            except Exception:
                pass
        elif attr_name == "dragon_fist_offsets" and self.attack_obj is not None \
                and hasattr(self.attack_obj, "translate"):
            dx, dy = new_offset[0] - old_offset[0], new_offset[1] - old_offset[1]
            try:
                self.attack_obj.translate(dx, dy)
            except Exception:
                pass
        elif attr_name in ("beam_offsets", "chain_offsets", "projectile_offsets") and self.attack_obj is not None:
            ox, oy = new_offset
            try:
                self.attack_obj.x = self.actor.x + ox
                self.attack_obj.y = self.actor.y + oy
            except Exception:
                pass
        elif attr_name == "genkidama_offsets" and self.attack_obj is not None:
            ox, oy = new_offset
            dox, doy = self.config.direction_offsets.get(direction, (0, 0))
            height = getattr(self.actor, "height", 0)
            try:
                self.attack_obj.x = self.actor.x + dox + ox
                self.attack_obj.y = self.actor.y - height / 2 + doy + oy
            except Exception:
                pass
    # ── events ───────────────────────────────────────────────────────
    def handle_input(self, event):
        """Returns 'close' when the overlay was just closed (mirrors
        CharacterCreator.handle_input's contract), else None."""
        if not self.active:
            return None
        if hasattr(event, 'pos'):
            self._logical_mouse_pos = tuple(event.pos)

        # -- inline field editing (int/float/str param fields) --------------
        if self.editing_field is not None:
            consumed = self._handle_text_edit_event(event)
            if consumed:
                return None
            # fall through — the click that just committed the field may
            # also hit a button/row below

        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self._stop_preview()
            self.paused = False
            self.dragging_offset = False
            self._drag_offset_attr = None
            self.active = False
            return "back_to_dev_menu"

        # Spacebar mirrors the "Hold to Fire" button — skipped while a
        # param field has an active edit buffer so typing a space into a
        # value doesn't also trigger the stage.
        if event.type == pygame.KEYDOWN and event.key == pygame.K_SPACE and self.editing_field is None:
            if not self._fire_pressed:
                self._fire_pressed = True
                self._on_fire_press()
            return None
        if event.type == pygame.KEYUP and event.key == pygame.K_SPACE:
            if self._fire_pressed:
                self._fire_pressed = False
                self._on_fire_release()
            return None

        # offset drag handle: press near the crosshair while paused
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and self.paused \
                and self.stage_rect.collidepoint(event.pos):
            attr_name = self._active_offset_attr()
            if attr_name:
                hx, hy = self._offset_screen_pos(attr_name)
                if (event.pos[0] - hx) ** 2 + (event.pos[1] - hy) ** 2 <= 144:  # 12px grab radius
                    self.dragging_offset = True
                    self._drag_offset_attr = attr_name
                    return None

        if event.type == pygame.MOUSEMOTION and self.dragging_offset and self._drag_offset_attr:
            self._drag_offset_to(self._drag_offset_attr, event.pos)
            return None

        if event.type == pygame.MOUSEBUTTONUP and event.button == 1 and self.dragging_offset:
            self.dragging_offset = False
            self._drag_offset_attr = None
            return None

        # param-panel scrollbar thumb — click-to-jump, then drag
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and self._panel_scrollbar_track is not None:
            hit = self._panel_scrollbar_track.inflate(10, 0)
            if hit.collidepoint(event.pos) or self._panel_scrollbar_thumb.collidepoint(event.pos):
                self._panel_scroll_dragging = True
                self._scrub_panel_scroll(event.pos[1])
                return None

        if event.type == pygame.MOUSEMOTION and self._panel_scroll_dragging:
            self._scrub_panel_scroll(event.pos[1])
            return None

        if event.type == pygame.MOUSEBUTTONUP and event.button == 1 and self._panel_scroll_dragging:
            self._panel_scroll_dragging = False
            return None

        # hold-to-fire button
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and self._fire_rect.collidepoint(event.pos):
            self._fire_pressed = True
            self._on_fire_press()
            return None
        if event.type == pygame.MOUSEBUTTONUP and event.button == 1 and self._fire_pressed:
            self._fire_pressed = False
            self._on_fire_release()
            return None

        if event.type == pygame.MOUSEMOTION:
            self._update_hover(event.pos)
            return None

        if event.type == pygame.MOUSEWHEEL:
            if self._sidebar_list_rect.collidepoint(self._logical_mouse_pos):
                total = len(self._saved_configs()) * SIDEBAR_ROW_H
                max_scroll = max(0, total - self._sidebar_list_rect.h)
                self.sidebar_scroll = max(0, min(max_scroll, self.sidebar_scroll - event.y * SIDEBAR_ROW_H))
                return None
            if self._panel_list_rect.collidepoint(self._logical_mouse_pos):
                max_scroll = max(0, self._panel_content_height() - self._panel_list_rect.h)
                raw = max(0, min(max_scroll, self.panel_scroll - event.y * ROW_H))
                self.panel_scroll = self._snap_panel_scroll(raw)
                return None

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self._back_rect.collidepoint(event.pos):
                self._stop_preview()
                self.paused = False
                self.dragging_offset = False
                self._drag_offset_attr = None
                self.active = False
                return 'back_to_dev_menu'
            if self._save_rect.collidepoint(event.pos):
                self._on_save()
                return None
            if self._arch_prev_rect.collidepoint(event.pos):
                self._cycle_archetype(-1)
                return None
            if self._arch_next_rect.collidepoint(event.pos):
                self._cycle_archetype(1)
                return None
            if self._new_rect.collidepoint(event.pos):
                self._on_new()
                return None
            if self._dup_rect.collidepoint(event.pos):
                self._on_duplicate()
                return None
            if self._delete_rect.collidepoint(event.pos):
                self._on_delete()
                return None
            if self._char_prev_rect.collidepoint(event.pos):
                self._prev_char()
                return None
            if self._char_next_rect.collidepoint(event.pos):
                self._next_char()
                return None
            if self._dir_prev_rect.collidepoint(event.pos):
                self._prev_dir()
                return None
            if self._dir_next_rect.collidepoint(event.pos):
                self._next_dir()
                return None
            if self._pause_rect.collidepoint(event.pos):
                self._toggle_pause()
                return None

            if self._sidebar_list_rect.collidepoint(event.pos):
                configs = self._saved_configs()
                for i, row_rect in enumerate(self._sidebar_row_rects):
                    if row_rect.collidepoint(event.pos) and i < len(configs):
                        path, cid, name, arch = configs[i]
                        self._load_config_from_path(path)
                        return None

            for tab_name, rect in self.tab_rects.items():
                if rect.collidepoint(event.pos) and self.active_tab != tab_name:
                    self.active_tab = tab_name
                    self._build_tabs()
                    self._reload_panel()
                    return None
            if self.active_tab in self.config.OPTIONAL_SETS and self._enabled_chip_rect.collidepoint(event.pos):
                self.config.set_enabled[self.active_tab] = not self.config.set_enabled[self.active_tab]
                return None

            if self._panel_list_rect.collidepoint(event.pos):
                for key, (rect, spec, target) in self._field_row_rects.items():
                    if rect.collidepoint(event.pos):
                        self._on_field_row_click(spec, target)
                        return None

        return None

    def _update_hover(self, pos):
        self._back_hovered = self._back_rect.collidepoint(pos)
        self._save_hovered = self._save_rect.collidepoint(pos)
        self._new_hovered = self._new_rect.collidepoint(pos)
        self._dup_hovered = self._dup_rect.collidepoint(pos)
        self._delete_hovered = self._delete_rect.collidepoint(pos)
        self._pause_hovered = self._pause_rect.collidepoint(pos)
        self._fire_hovered = self._fire_rect.collidepoint(pos)
        self._arch_prev_hovered = self._arch_prev_rect.collidepoint(pos)
        self._arch_next_hovered = self._arch_next_rect.collidepoint(pos)
        self._char_prev_hovered = self._char_prev_rect.collidepoint(pos)
        self._char_next_hovered = self._char_next_rect.collidepoint(pos)
        self._dir_prev_hovered = self._dir_prev_rect.collidepoint(pos)
        self._dir_next_hovered = self._dir_next_rect.collidepoint(pos)
        self._enabled_chip_hovered = (
            self.active_tab in self.config.OPTIONAL_SETS and self._enabled_chip_rect.collidepoint(pos))
        self.sidebar_hover_index = -1
        if self._sidebar_list_rect.collidepoint(pos):
            for i, r in enumerate(self._sidebar_row_rects):
                if r.collidepoint(pos):
                    self.sidebar_hover_index = i
                    break

    def _resolve_cursor(self):
        """Switch the OS cursor to an I-beam over a text field, a hand over
        anything clickable (buttons, tabs, sidebar rows, chips, field rows,
        the panel scrollbar thumb...), or back to the plain arrow otherwise.
        I-beam wins where a field and a button happen to overlap."""
        pos = self._logical_mouse_pos
        hovering_text_field = any(r.collidepoint(pos) for r in self.text_field_rects)
        hovering_widget = False
        if not hovering_text_field:
            click_rects = (
                self._back_rect, self._save_rect, self._new_rect, self._dup_rect,
                self._delete_rect, self._pause_rect, self._fire_rect,
                self._arch_prev_rect, self._arch_next_rect,
                self._char_prev_rect, self._char_next_rect,
                self._dir_prev_rect, self._dir_next_rect,
            )
            hovering_widget = any(r.collidepoint(pos) for r in click_rects)
            if not hovering_widget and self._sidebar_list_rect.collidepoint(pos):
                hovering_widget = any(r.collidepoint(pos) for r in self._sidebar_row_rects)
            if not hovering_widget:
                hovering_widget = any(r.collidepoint(pos) for r in self.tab_rects.values())
            if not hovering_widget and self.active_tab in self.config.OPTIONAL_SETS:
                hovering_widget = self._enabled_chip_rect.collidepoint(pos)
            if not hovering_widget and self._panel_list_rect.collidepoint(pos):
                hovering_widget = any(r.collidepoint(pos) for r, spec, target in self._field_row_rects.values())
            if not hovering_widget and self._panel_scrollbar_thumb is not None:
                hovering_widget = (self._panel_scrollbar_thumb.collidepoint(pos)
                                    or self._panel_scrollbar_track.inflate(10, 0).collidepoint(pos))
        uk.set_text_cursor(hovering_text_field)
        uk.set_hand_cursor(hovering_widget)

    def _on_field_row_click(self, spec, target):
        if spec.kind == "bool":
            target[spec.key] = not bool(target.get(spec.key, spec.default))
        elif spec.kind == "choice":
            choices = list(spec.choices)
            cur = target.get(spec.key, spec.default)
            idx = (choices.index(cur) + 1) % len(choices) if cur in choices else 0
            target[spec.key] = choices[idx]
        else:
            self._begin_field_edit(spec, target)

    # ------------------------------------------------------------------ inline field-edit engine
    # Generalized version of item_creator.py's editing_field/text_input/
    # cursor state machine — single-line only (every field this tool's
    # param panel shows is a short int/float/str value), with per-
    # keystroke numeric filtering for int/float fields carried over from
    # the previous version's FieldEditor.handle_event.
    def _begin_field_edit(self, spec, target) -> None:
        value = target.get(spec.key, spec.default)
        text = "" if value is None else str(value)
        self.editing_field = (self.active_tab, spec.key)
        self._editing_spec = spec
        self._editing_target = target
        self.text_input = text
        self.cursor_pos = len(text)
        self.selection_anchor = None
        self.cursor_blink = 0.0
        self._text_max_len = FIELD_MAX_LEN

    def _clear_field_edit(self) -> None:
        self.editing_field = None
        self._editing_spec = None
        self._editing_target = None
        self.text_input = ""
        self.cursor_pos = 0
        self.selection_anchor = None

    def _commit_field_edit(self) -> None:
        spec = self._editing_spec
        target = self._editing_target
        if spec is None or target is None:
            self._clear_field_edit()
            return
        raw = self.text_input.strip()
        if raw == "" and spec.nullable:
            target[spec.key] = None
        elif spec.kind == "int":
            try:
                v = int(float(raw))
                if spec.min is not None:
                    v = max(spec.min, v)
                if spec.max is not None:
                    v = min(spec.max, v)
                target[spec.key] = v
            except ValueError:
                pass
        elif spec.kind == "float":
            try:
                v = float(raw)
                if spec.min is not None:
                    v = max(spec.min, v)
                if spec.max is not None:
                    v = min(spec.max, v)
                target[spec.key] = v
            except ValueError:
                pass
        elif spec.kind == "str":
            target[spec.key] = raw
        self._clear_field_edit()

    def _cancel_field_edit(self) -> None:
        self._clear_field_edit()

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

    def _insert_into_text_input(self, s: str, numeric: bool) -> None:
        if numeric:
            s = "".join(ch for ch in s if ch.isdigit() or ch in "-.")
        else:
            s = "".join(ch for ch in s if ch.isprintable())
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

    def _handle_text_edit_event(self, event) -> bool:
        """Returns True if the event was consumed by the text-edit engine."""
        if self.editing_field is None:
            return False
        spec = self._editing_spec
        numeric = spec is not None and spec.kind in ("int", "float")

        if event.type == pygame.KEYDOWN:
            mods = pygame.key.get_mods()
            ctrl = bool(mods & (pygame.KMOD_CTRL | pygame.KMOD_META))
            shift = bool(mods & pygame.KMOD_SHIFT)

            if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_TAB):
                self._commit_field_edit()
            elif event.key == pygame.K_ESCAPE:
                self._cancel_field_edit()
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
                self._insert_into_text_input(uk.clipboard_get_text(), numeric)
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
                if event.unicode and event.unicode.isprintable():
                    self._insert_into_text_input(event.unicode, numeric)
            self.cursor_blink = 0.0
            return True

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self._active_edit_rect is not None and self._active_edit_rect.collidepoint(event.pos):
                idx = self._text_index_from_x(event.pos[0])
                if pygame.key.get_mods() & pygame.KMOD_SHIFT:
                    if self.selection_anchor is None:
                        self.selection_anchor = self.cursor_pos
                else:
                    self.selection_anchor = idx
                self.cursor_pos = idx
                self._text_drag = True
                self.cursor_blink = 0.0
                return True
            self._commit_field_edit()
            return False  # let the click fall through to normal handling

        if event.type == pygame.MOUSEMOTION:
            if self._text_drag and self._active_edit_rect is not None:
                x = max(self._active_edit_rect.left, min(event.pos[0], self._active_edit_rect.right))
                idx = self._text_index_from_x(x)
                self.cursor_pos = idx
                self.cursor_blink = 0.0
            return True

        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            self._text_drag = False
            return True

        return True

    # ------------------------------------------------------------------ shared card/panel drawing primitives
    # Same shapes as item_creator.py's own rebuild — kept local here too
    # since neither file shares a base class.
    def _draw_card_shell(self, screen, rect, t, accent=None):
        accent = accent or uk.Theme.GOLD
        t = round(max(0.0, min(1.0, t)) * 20) / 20.0
        lift = int(round(2 * t))
        draw_rect = rect.move(0, -lift)
        base = uk.lerp_color((22, 26, 35), (28, 33, 44), t)
        border = uk.lerp_color(uk.Theme.CARD_BORDER, accent, t * 0.78)
        uk.draw_panel(screen, draw_rect, bg=(*base, 255), border=border, border_width=1, radius=10, shadow=False)
        return draw_rect

    def _draw_pill_button(self, screen, rect, t, label, accent, icon_fn=None, danger=False, enabled=True):
        col = uk.Theme.DANGER_BRIGHT if danger else accent
        dim_base = (32, 22, 22) if danger else (28, 33, 44)
        t = t if enabled else 0.0
        base = uk.lerp_color((22, 26, 35), dim_base, t)
        border = uk.lerp_color(uk.Theme.CARD_BORDER, col, t)
        uk.draw_panel(screen, rect, bg=(*base, 255), border=border, border_width=1 + round(t), radius=10, shadow=False)
        label_color = uk.lerp_color(uk.Theme.TEXT_SECONDARY, col, t) if enabled else uk.Theme.TEXT_DIM
        if icon_fn is not None:
            icon_rect = pygame.Rect(0, 0, 15, 15)
            icon_rect.midleft = (rect.x + 12, rect.centery)
            icon_fn(screen, icon_rect, label_color)
            label_surf = self.font_small.render(label, True, label_color)
            uk.blit_surface(screen, label_surf, (icon_rect.right + 7, rect.centery - label_surf.get_height() // 2),
                             transient=True)
        else:
            label_surf = self.font_small.render(label, True, label_color)
            uk.blit_surface(screen, label_surf, label_surf.get_rect(center=rect.center), transient=True)

    def _draw_icon_square_btn(self, screen, rect, t, icon_fn, accent, danger=False):
        t = round(max(0.0, min(1.0, t)) * 20) / 20.0
        dim_base = (32, 22, 22) if danger else (28, 33, 44)
        base = uk.lerp_color((22, 26, 35), dim_base, t)
        border = uk.lerp_color(uk.Theme.CARD_BORDER, accent, t)
        uk.draw_panel(screen, rect, bg=(*base, 255), border=border, border_width=1 + round(t), radius=8, shadow=False)
        if t > 0.01:
            uk.draw_soft_glow(screen, rect.center, min(rect.w, rect.h) // 2 + 4, accent, max_alpha=int(28 * t))
        icon_fn(screen, rect, accent if t > 0.01 else uk.Theme.TEXT_SECONDARY)

    def _draw_nav_btn(self, screen, rect, hovered, icon_fn):
        t = 1.0 if hovered else 0.0
        base = uk.lerp_color((20, 23, 32), (28, 33, 44), t)
        border = uk.lerp_color(uk.Theme.CARD_BORDER, uk.Theme.GOLD, t)
        uk.draw_panel(screen, rect, bg=(*base, 255), border=border, border_width=1, radius=6, shadow=False)
        icon_fn(screen, rect, uk.Theme.GOLD if hovered else uk.Theme.TEXT_SECONDARY)

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
    # ------------------------------------------------------------------ drawing
    def draw(self, screen, dt: float = 0.0):
        if not self.active:
            return
        self._draw_background(screen)
        self._draw_sidebar(screen)
        self._draw_top_bar(screen)
        self._draw_stage(screen)
        self._draw_panel(screen)
        self._draw_header(screen)   # drawn last so it sits above the columns
        self._draw_footer(screen)

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

        if self.status_msg:
            status_col = uk.Theme.GOLD_BRIGHT if self.status_ok else uk.Theme.DANGER_BRIGHT
            status_surf = self.font_small.render(self.status_msg, True, status_col)
            status_rect = status_surf.get_rect(right=self._save_rect.left - 16, centery=self.header_h // 2)
            uk.blit_surface(screen, status_surf, status_rect, transient=True)

        self._draw_icon_square_btn(screen, self._save_rect, self._save_hover_anim,
                                    self._icon_save_png, uk.Theme.GOLD)

    def _draw_back_button(self, screen):
        accent = uk.Theme.GOLD
        t = round(self._back_hover_anim * 20) / 20.0
        base = uk.lerp_color((22, 26, 35), (28, 33, 44), t)
        border = uk.lerp_color(uk.Theme.CARD_BORDER, accent, t * 0.78)
        uk.draw_panel(screen, self._back_rect, bg=(*base, 255), border=border,
                      border_width=1, radius=8, shadow=False)
        if t > 0.01:
            uk.draw_soft_glow(screen, self._back_rect.center, 22, accent, max_alpha=int(25 * t))
        if self._back_icon is not None:
            uk.blit_surface(screen, self._back_icon, self._back_icon.get_rect(center=self._back_rect.center),
                             transient=False)
        else:
            _draw_chevron_left(screen, self._back_rect, uk.Theme.TEXT_SECONDARY, width=3)

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

    def _icon_duplicate_png(self, screen, rect, color):
        if self._dup_icon is not None:
            uk.blit_surface(screen, self._dup_icon, self._dup_icon.get_rect(center=rect.center))
        else:
            _draw_duplicate_icon(screen, rect, color)

    def _draw_footer(self, screen):
        w, h = self.screen_width, self.screen_height
        y = h - self.footer_h
        uk.draw_rect_on(screen, (12, 15, 23), pygame.Rect(0, y, w, self.footer_h), 0, 0)
        uk.draw_rect_on(screen, (43, 49, 63), pygame.Rect(0, y, w, 1), 0, 0)

    # ------------------------------------------------------------------ sidebar
    def _draw_sidebar(self, screen):
        r = self.sidebar_rect
        uk.draw_panel(screen, r, bg=uk.Theme.PANEL_BG, border=uk.Theme.PANEL_BORDER, border_width=1,
                      radius=uk.Theme.RADIUS_PANEL, shadow=False)

        title_surf = self.font_large.render("ATTACKS", True, uk.Theme.TEXT_PRIMARY)
        uk.blit_surface(screen, title_surf, self._sidebar_title_pos, transient=True)

        self._draw_nav_btn(screen, self._arch_prev_rect, self._arch_prev_hovered, _draw_chevron_left)
        self._draw_nav_btn(screen, self._arch_next_rect, self._arch_next_hovered, _draw_chevron_right)
        arch_label, _cls = ARCHETYPES[self.active_archetype]
        arch_accent = ARCHETYPE_ACCENT.get(self.active_archetype, uk.Theme.GOLD)
        arch_txt = self.font_small.render(f"New: {arch_label}", True, arch_accent)
        uk.blit_surface(screen, arch_txt, arch_txt.get_rect(center=self._arch_row_rect.center), transient=True)

        self._draw_icon_square_btn(screen, self._new_rect, self._new_hover_anim, self._icon_plus_png, uk.Theme.GOLD)
        self._draw_icon_square_btn(screen, self._dup_rect, self._dup_hover_anim, self._icon_duplicate_png, uk.Theme.KI_BLUE)

        prev_clip = screen.get_clip()
        screen.set_clip(self._sidebar_list_rect)
        configs = self._saved_configs()
        self._sidebar_row_rects = []
        y = self._sidebar_list_rect.y - self.sidebar_scroll
        for i, (path, cid, name, arch) in enumerate(configs):
            row = pygame.Rect(self._sidebar_list_rect.x, y, self._sidebar_list_rect.w, SIDEBAR_ROW_H - 4)
            self._sidebar_row_rects.append(pygame.Rect(self._sidebar_list_rect.x, y,
                                                         self._sidebar_list_rect.w, SIDEBAR_ROW_H))
            if row.bottom > self._sidebar_list_rect.y and row.top < self._sidebar_list_rect.bottom:
                selected = cid == self.config.id and arch == self.config.archetype
                t = max(self._item_hover_anim(i), 1.0 if selected else 0.0)
                accent = ARCHETYPE_ACCENT.get(arch, uk.Theme.GOLD)
                base = uk.lerp_color((18, 21, 29), (26, 30, 40), t)
                border = uk.lerp_color(uk.Theme.CARD_BORDER, accent, t * 0.8)
                uk.draw_panel(screen, row, bg=(*base, 255), border=border, border_width=1, radius=7, shadow=False)
                dot = pygame.Rect(0, 0, 8, 8)
                dot.midleft = (row.x + 10, row.centery)
                uk.draw_circle_on(screen, accent, dot.center, 4, width=0)
                name_color = uk.lerp_color(uk.Theme.TEXT_SECONDARY, uk.Theme.TEXT_PRIMARY, t)
                name_surf = self.font_small.render(name, True, name_color)
                uk.blit_surface(screen, name_surf, (row.x + 24, row.y + (row.h - name_surf.get_height()) // 2),
                                 transient=True)
            y += SIDEBAR_ROW_H
        if not configs:
            empty = self.font_small.render("No saved attacks yet", True, uk.Theme.TEXT_DIM)
            uk.blit_surface(screen, empty, (self._sidebar_list_rect.x, self._sidebar_list_rect.y), transient=True)
        screen.set_clip(prev_clip)

        self._draw_pill_button(screen, self._delete_rect, self._delete_hover_anim, "Delete Selected",
                                uk.Theme.DANGER_BRIGHT, danger=True)

    def _item_hover_anim(self, index: int) -> float:
        if 0 <= index < len(self._sidebar_hover_anim):
            return self._sidebar_hover_anim[index]
        return 1.0 if index == self.sidebar_hover_index else 0.0

    # ------------------------------------------------------------------ top bar
    def _draw_top_bar(self, screen):
        self._draw_card_shell(screen, self.top_rect, 0.0)

        self._draw_nav_btn(screen, self._char_prev_rect, self._char_prev_hovered, _draw_chevron_left)
        self._draw_nav_btn(screen, self._char_next_rect, self._char_next_hovered, _draw_chevron_right)
        char_name = self.characters[self.char_index]
        char_txt = self.font_small.render(char_name, True, uk.Theme.TEXT_PRIMARY)
        uk.blit_surface(screen, char_txt, char_txt.get_rect(center=self._char_label_rect.center), transient=True)

        self._draw_nav_btn(screen, self._dir_prev_rect, self._dir_prev_hovered, _draw_chevron_left)
        self._draw_nav_btn(screen, self._dir_next_rect, self._dir_next_hovered, _draw_chevron_right)
        dir_txt = self.font_small.render(DIRECTIONS[self.direction_index].title(), True, uk.Theme.TEXT_PRIMARY)
        uk.blit_surface(screen, dir_txt, dir_txt.get_rect(center=self._dir_label_rect.center), transient=True)

    # ------------------------------------------------------------------ stage
    def _draw_stage(self, screen):
        r = self.stage_rect
        uk.draw_panel(screen, r, bg=(10, 12, 18, 255), border=uk.Theme.PANEL_BORDER, border_width=1,
                      radius=uk.Theme.RADIUS_PANEL, shadow=False)
        prev_clip = screen.get_clip()
        screen.set_clip(r)

        uk.draw_line_on(screen, (30, 33, 44), (r.x, r.bottom - 40), (r.right, r.bottom - 40), 1)

        from config.settings import RENDER_SCALE
        # Real LayerManager pass — same draw_layer/get_sort_key() sorting
        # game.py itself uses. Unchanged from the previous version.
        self.layer_manager.clear()
        self.layer_manager.add_object(self.actor)
        if self.charge_obj:
            self.layer_manager.add_object(self.charge_obj)
        if self.attack_obj:
            self.layer_manager.add_object(self.attack_obj)
        self.layer_manager.draw_all(screen, self.camera, FALLBACK_COLORS, RENDER_SCALE)

        attr_name = self._active_offset_attr()
        if attr_name:
            self._draw_offset_handle(screen, attr_name)

        screen.set_clip(prev_clip)

        state_label = {
            self.STATE_IDLE: "idle", self.STATE_CHARGING: "charging...",
            self.STATE_FIRING: "firing", self.STATE_DECAYING: "decaying...",
        }[self.state]
        if self.paused:
            state_label += "  (paused)"
        state_col = uk.Theme.GOLD_BRIGHT if self.paused else uk.Theme.TEXT_MUTED
        lbl = self.font_small.render(f"state: {state_label}", True, state_col)
        uk.blit_surface(screen, lbl, (r.x + 10, r.y + 8), transient=True)

        fire_t = 1.0 if self._fire_pressed else self._fire_hover_anim
        fire_accent = uk.Theme.DANGER_BRIGHT if self._fire_pressed else uk.Theme.GOLD
        self._draw_pill_button(screen, self._fire_rect, max(fire_t, 0.15), "Hold to Fire", fire_accent,
                                icon_fn=_draw_play_icon)
        pause_accent = uk.Theme.KI_BLUE if self.paused else uk.Theme.TEXT_SECONDARY
        self._draw_pill_button(screen, self._pause_rect, max(self._pause_hover_anim, 0.3 if self.paused else 0.0),
                                "Resume" if self.paused else "Pause", pause_accent, icon_fn=_draw_pause_icon)

    def _draw_offset_handle(self, screen, attr_name: str):
        """Crosshair for the current direction's offset on whichever tab
        is active. Bright + draggable while paused; dimmed with a hint
        otherwise. Unchanged behaviour from the previous version, restyled."""
        is_dragging = self.dragging_offset and self._drag_offset_attr == attr_name
        hx, hy = self._offset_screen_pos(attr_name)
        color = uk.Theme.GOLD_BRIGHT if self.paused else uk.Theme.TEXT_DIM
        radius = 8 if is_dragging else 6
        uk.draw_circle_on(screen, color, (hx, hy), radius, width=0 if is_dragging else 2)
        uk.draw_line_on(screen, color, (hx - 10, hy), (hx + 10, hy), 1)
        uk.draw_line_on(screen, color, (hx, hy - 10), (hx, hy + 10), 1)

        ox, oy = getattr(self.config, attr_name).get(self.actor.direction, (0, 0))
        label = self.active_tab.replace("_", " ").title()
        r = self.stage_rect
        if self.paused:
            txt = self.font_small.render(
                f"{label} offset ({self.actor.direction}): ({ox}, {oy}) — drag the crosshair", True,
                uk.Theme.TEXT_PRIMARY)
        else:
            txt = self.font_small.render(
                f"{label} offset ({self.actor.direction}): ({ox}, {oy}) — Pause to drag", True, uk.Theme.TEXT_DIM)
        uk.blit_surface(screen, txt, (r.x + 10, r.bottom - 24), transient=True)

    # ------------------------------------------------------------------ param panel
    def _draw_panel(self, screen):
        for tab_name, rect in self.tab_rects.items():
            active = self.active_tab == tab_name
            accent = uk.Theme.GOLD
            base = (26, 30, 40) if active else (18, 21, 29)
            border = accent if active else uk.Theme.CARD_BORDER
            uk.draw_panel(screen, rect, bg=(*base, 255), border=border, border_width=2 if active else 1,
                          radius=0, shadow=False)
            label = tab_name.replace("_", " ").title()
            color = uk.Theme.GOLD_BRIGHT if active else uk.Theme.TEXT_MUTED
            txt = self.font_small.render(label, True, color)
            uk.blit_surface(screen, txt, txt.get_rect(center=rect.center), transient=True)

        if self.active_tab in self.config.OPTIONAL_SETS:
            on = self.config.set_enabled[self.active_tab]
            cb = self._enabled_chip_rect
            t = max(self._enabled_chip_hover_anim, 1.0 if on else 0.0)
            base = uk.lerp_color((28, 33, 44), (24, 44, 32), t if on else 0.0)
            border = uk.lerp_color(uk.Theme.CARD_BORDER, uk.Theme.GOLD if not on else (110, 210, 140), 0.6)
            uk.draw_panel(screen, cb, bg=(*base, 255), border=border, border_width=1, radius=6, shadow=False)
            label = self.active_tab.replace("_", " ").title()
            txt = self.font_tiny.render(f"{label}: {'ON' if on else 'OFF'}", True,
                                         (150, 230, 170) if on else uk.Theme.TEXT_DIM)
            uk.blit_surface(screen, txt, txt.get_rect(center=cb.center), transient=True)

        list_rect = self._panel_list_rect
        prev_clip = screen.get_clip()
        screen.set_clip(list_rect)
        uk.draw_rect_on(screen, (14, 16, 23), list_rect, 0, 0)

        self._field_row_rects = {}
        self.text_field_rects = []
        y = list_rect.y - self.panel_scroll
        for row in self._param_rows:
            if row[0] == "header":
                section_name = row[1]
                hrect = pygame.Rect(list_rect.x, y, list_rect.w, SECTION_H)
                if hrect.bottom > list_rect.y and hrect.top < list_rect.bottom:
                    uk.draw_rect_on(screen, (20, 23, 32), hrect, 0, 0)
                    txt = self.font_small.render(section_name.upper(), True, uk.Theme.GOLD)
                    uk.blit_surface(screen, txt, (hrect.x + 10, hrect.y + (SECTION_H - txt.get_height()) // 2),
                                     transient=True)
                y += SECTION_H
            else:
                _, spec, target = row
                frect = pygame.Rect(list_rect.x + 4, y, list_rect.w - 8, ROW_H - 2)
                if frect.bottom > list_rect.y and frect.top < list_rect.bottom:
                    self._draw_param_field_row(screen, frect, spec, target)
                self._field_row_rects[id(spec)] = (pygame.Rect(list_rect.x, y, list_rect.w, ROW_H), spec, target)
                y += ROW_H

        screen.set_clip(prev_clip)

        total = self._panel_content_height()
        if total > list_rect.height:
            track = pygame.Rect(list_rect.right - 8, list_rect.y, 6, list_rect.height)
            uk.draw_rect_on(screen, (24, 27, 36), track, 0, 3)
            thumb_h = max(20, int(list_rect.height * list_rect.height / total))
            max_scroll = max(1, total - list_rect.height)
            thumb_y = list_rect.y + int(self.panel_scroll * (list_rect.height - thumb_h) / max_scroll)
            thumb = pygame.Rect(track.x, thumb_y, 6, thumb_h)
            thumb_color = uk.Theme.GOLD if self._panel_scroll_dragging else uk.Theme.CARD_BORDER
            uk.draw_rect_on(screen, thumb_color, thumb, 0, 3)
            self._panel_scrollbar_track = track
            self._panel_scrollbar_thumb = thumb
            self._panel_scrollbar_max_scroll = max_scroll
        else:
            self._panel_scrollbar_track = None
            self._panel_scrollbar_thumb = None
            self._panel_scrollbar_max_scroll = 0

    def _draw_param_field_row(self, screen, rect, spec, target):
        label_surf = self.font_small.render(spec.label, True, uk.Theme.TEXT_SECONDARY)
        uk.blit_surface(screen, label_surf,
                         (rect.x + 8, rect.y + (rect.height - label_surf.get_height()) // 2), transient=True)

        widget_rect = pygame.Rect(rect.right - FIELD_VALUE_W - 6, rect.y + 2, FIELD_VALUE_W, rect.height - 4)
        editing = self.editing_field == (self.active_tab, spec.key)

        if spec.kind == "bool":
            on = bool(target.get(spec.key, spec.default))
            bg = (24, 44, 32) if on else (24, 24, 30)
            border = (110, 210, 140) if on else uk.Theme.CARD_BORDER
            uk.draw_panel(screen, widget_rect, bg=(*bg, 255), border=border, border_width=1, radius=6, shadow=False)
            txt = self.font_tiny.render("ON" if on else "OFF", True,
                                         (150, 230, 170) if on else uk.Theme.TEXT_MUTED)
            uk.blit_surface(screen, txt, txt.get_rect(center=widget_rect.center), transient=True)
        elif spec.kind == "choice":
            uk.draw_panel(screen, widget_rect, bg=(24, 24, 30, 255), border=uk.Theme.CARD_BORDER,
                          border_width=1, radius=6, shadow=False)
            txt = self.font_tiny.render(str(target.get(spec.key, spec.default)), True, uk.Theme.TEXT_SECONDARY)
            uk.blit_surface(screen, txt, txt.get_rect(center=widget_rect.center), transient=True)
        else:
            bg = (30, 34, 46) if editing else (22, 24, 32)
            border = uk.Theme.GOLD if editing else uk.Theme.CARD_BORDER
            uk.draw_panel(screen, widget_rect, bg=(*bg, 255), border=border,
                          border_width=1 + (1 if editing else 0), radius=6, shadow=False)
            self.text_field_rects.append(widget_rect)
            if editing:
                val_surf = self.font_tiny.render(self.text_input, True, uk.Theme.TEXT_PRIMARY)
            else:
                v = target.get(spec.key, spec.default)
                shown = "auto" if v is None else str(v)
                col = uk.Theme.TEXT_DIM if v is None else uk.Theme.TEXT_SECONDARY
                val_surf = self.font_tiny.render(shown, True, col)
            val_rect = val_surf.get_rect(midleft=(widget_rect.x + 8, widget_rect.centery))
            uk.blit_surface(screen, val_surf, val_rect, transient=True)
            if editing:
                line_h = self.font_tiny.size("Ag")[1]
                caret_rect = pygame.Rect(val_rect.x, 0, val_rect.w, line_h)
                caret_rect.centery = widget_rect.centery
                self._draw_live_text_field(screen, self.font_tiny, caret_rect, widget_rect)


# ─────────────────────────────────────────────────────────────────────────
#  Standalone runner — NOT used by game.py (see the class docstring's
#  wire-up section for the real integration). Only here so this file can
#  be sanity-checked / demoed with `python dev_tools/attack_creator.py`
#  without booting the whole game.
# ─────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    pygame.init()
    _screen = pygame.display.set_mode((1280, 800))
    pygame.display.set_caption("Attack Creator (standalone)")
    _clock = pygame.time.Clock()

    _creator = AttackCreator(1280, 800)
    _creator.active = True

    _running = True
    while _running:
        _dt = _clock.tick(60) / 1000.0
        for _event in pygame.event.get():
            if _event.type == pygame.QUIT:
                pygame.quit()
                sys.exit()
            result = _creator.handle_input(_event)
            if result == "close":
                _running = False
        _creator.update(_dt)
        _creator.draw(_screen, _dt)
        pygame.display.flip()
    pygame.quit()