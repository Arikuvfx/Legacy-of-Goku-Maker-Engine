"""
dev_tools/attack_creator.py — In-engine Attack Creator
============================================================================
Lets you build an attack (currently: beam-family — final_flash /
banshee_blast / big_bang_kamehameha style — chain-family —
flame_kamehameha style — projectile-family — burning_attack style —
sword-family — energy_sword style — dragon-fist-family, or
genkidama-family — a five-power-state hold-then-throw ball) entirely from
data, preview it live on any player character, tweak every parameter
while it's charging/firing/decaying (firing/stopping for chain, or
firing/despawning for projectile and genkidama), and save it to
assets/attack_configs/{id}.json.

This is WYSIWYG on purpose: the stage doesn't simulate the attack, it
constructs and drives the REAL attacks.beam.BeamAttack and
KamehamehaChargeEffect classes (via attacks/attack_config.py) with whatever
values are currently in the form. What you see here is exactly what
game.py will render if it loads the same config.

Follows character_creator.py's conventions on purpose (same BASE_DIR
anchoring, same dark dev-tool palette, same run(screen, clock) wire-up) so
it feels like the same toolset, not a bolted-on one-off.

Wire-up (game.py) — same shape as character_creator.CharacterCreator:
------------------------------------------------------------------
    from dev_tools import attack_creator
    ...
    self.attack_creator = attack_creator.AttackCreator(SCREEN_WIDTH, SCREEN_HEIGHT)
    ...
    # event loop
    if self.attack_creator.active:
        result = self.attack_creator.handle_input(event)
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
    Left sidebar    — archetype picker (which kind [+ New] creates), then
                       saved attack configs across every archetype: click
                       to load (switches the archetype picker to match),
                       [+ New], [Duplicate], [Delete].
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
                       Click a number/text field to edit it, Enter or click
                       away to commit.
    Drag handle     — Every tab whose param set has a per-direction spawn
                       offset shows a crosshair on the stage for it (Beam/
                       Chain/Projectile/Dragon Fist's own tab, plus every
                       archetype's Charge tab). Hit [Pause] first (freezes
                       whatever's currently on screen), then drag the
                       crosshair to reposition it.
    [Save]          — writes the current config to assets/attack_configs/.

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
import copy
import inspect
from pathlib import Path
from typing import Optional

import pygame

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
    list_saved_configs, load_config, BEAM_DIRECTIONS as DIRECTIONS, FieldSpec,
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
}

# ── Palette (matches character_creator.py's dark dev-tool look) ─────────
C_BG = (14, 14, 20)
C_PANEL = (24, 24, 34)
C_PANEL_DARK = (18, 18, 26)
C_BORDER = (52, 52, 66)
C_TEXT = (225, 225, 232)
C_TEXT_DIM = (150, 150, 162)
C_ACCENT = (90, 160, 255)
C_ACCENT_DIM = (60, 100, 160)
C_GOOD = (110, 200, 130)
C_WARN = (230, 180, 90)
C_BAD = (220, 90, 90)

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
# "sword" are each archetype's own fired-attack spawn offset (attacks/
# attack_config.py's beam_offsets/chain_offsets/projectile_offsets/
# dragon_fist_offsets/sword_offsets). Two of these need extra handling
# beyond "add the offset to actor.x/y" — see _offset_anchor_extra() (
# dragon_fist's crosshair needs to sit at the real anchor point, not the
# raw player position) and _drag_offset_to()'s per-archetype live-nudge
# branches (sword's spin re-reads its offset live every frame rather than
# baking it in once at construction).
OFFSET_ATTR_FOR_TAB = {
    "charge": "direction_offsets",
    "beam": "beam_offsets",
    "chain": "chain_offsets",
    "projectile": "projectile_offsets",
    "dragon_fist": "dragon_fist_offsets",
    "sword": "sword_offsets",
    "genkidama": "genkidama_offsets",
}


# ─────────────────────────────────────────────────────────────────────────
#  Small generic widgets
# ─────────────────────────────────────────────────────────────────────────

class Button:
    def __init__(self, rect: pygame.Rect, label: str, on_click, enabled=True, style="normal"):
        self.rect = rect
        self.label = label
        self.on_click = on_click
        self.enabled = enabled
        self.style = style  # 'normal' | 'primary' | 'danger'
        self.hovered = False

    def handle_event(self, event) -> bool:
        if not self.enabled:
            return False
        if event.type == pygame.MOUSEMOTION:
            self.hovered = self.rect.collidepoint(event.pos)
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.rect.collidepoint(event.pos):
                self.on_click()
                return True
        return False

    def draw(self, surf, font):
        if not self.enabled:
            bg = C_PANEL_DARK
        elif self.style == "primary":
            bg = C_ACCENT if self.hovered else C_ACCENT_DIM
        elif self.style == "danger":
            bg = (170, 60, 60) if self.hovered else (120, 45, 45)
        else:
            bg = (54, 54, 68) if self.hovered else C_PANEL
        surf.draw_rect(bg, self.rect, border_radius=4)
        surf.draw_rect(C_BORDER, self.rect, width=1, border_radius=4)
        color = C_TEXT if self.enabled else C_TEXT_DIM
        text = font.render(self.label, True, color)
        surf.blit(text, text.get_rect(center=self.rect.center))


class HoldButton(Button):
    """Like Button, but reports press/release separately instead of a
    single click — for the stage's 'hold to fire' control, which needs to
    know exactly when the mouse goes down and up (mirrors holding Q in
    the real game) rather than a completed click."""

    def __init__(self, rect, label, on_press, on_release, enabled=True):
        super().__init__(rect, label, on_click=lambda: None, enabled=enabled)
        self.on_press = on_press
        self.on_release = on_release
        self.pressed = False

    def handle_event(self, event) -> bool:
        if event.type == pygame.MOUSEMOTION:
            self.hovered = self.rect.collidepoint(event.pos)
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and self.enabled:
            if self.rect.collidepoint(event.pos):
                self.pressed = True
                self.on_press()
                return True
        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            if self.pressed:
                self.pressed = False
                self.on_release()
                return True
        return False

    def draw(self, surf, font):
        bg = (200, 90, 60) if self.pressed else (C_ACCENT if self.hovered else C_ACCENT_DIM)
        surf.draw_rect(bg, self.rect, border_radius=4)
        surf.draw_rect(C_BORDER, self.rect, width=1, border_radius=4)
        text = font.render(self.label, True, C_TEXT)
        surf.blit(text, text.get_rect(center=self.rect.center))


class FieldEditor:
    """One editable row bound to `target[spec.key]`, its look and parsing
    driven entirely by the FieldSpec (see attacks/attack_config.py). This is
    what makes the panel generic: add a field to the schema and it shows
    up here automatically, no bespoke widget needed."""

    ROW_H = 26

    def __init__(self, spec: FieldSpec, target: dict):
        self.spec = spec
        self.target = target
        self.rect = pygame.Rect(0, 0, 0, 0)   # positioned by the panel each frame
        self.editing = False
        self.buffer = ""
        self._select_all = False   # True right after clicking in: first keystroke replaces, not appends

    def value(self):
        return self.target.get(self.spec.key, self.spec.default)

    def set_value(self, v):
        self.target[self.spec.key] = v

    def start_edit(self):
        v = self.value()
        self.buffer = "" if v is None else str(v)
        self.editing = True
        self._select_all = True

    def commit(self):
        raw = self.buffer.strip()
        spec = self.spec
        if raw == "" and spec.nullable:
            self.set_value(None)
        elif spec.kind == "int":
            try:
                v = int(float(raw))
                if spec.min is not None: v = max(spec.min, v)
                if spec.max is not None: v = min(spec.max, v)
                self.set_value(v)
            except ValueError:
                pass
        elif spec.kind == "float":
            try:
                v = float(raw)
                if spec.min is not None: v = max(spec.min, v)
                if spec.max is not None: v = min(spec.max, v)
                self.set_value(v)
            except ValueError:
                pass
        elif spec.kind == "str":
            self.set_value(raw)
        self.editing = False

    def handle_event(self, event, panel_clip: pygame.Rect):
        if not panel_clip.collidepoint(self.rect.center) and self.rect.height == 0:
            return False
        spec = self.spec
        if spec.kind == "bool":
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and self.rect.collidepoint(event.pos):
                self.set_value(not bool(self.value()))
                return True
            return False
        if spec.kind == "choice":
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and self.rect.collidepoint(event.pos):
                choices = list(spec.choices)
                cur = self.value()
                idx = (choices.index(cur) + 1) % len(choices) if cur in choices else 0
                self.set_value(choices[idx])
                return True
            return False

        # numeric / string text fields
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.rect.collidepoint(event.pos):
                if not self.editing:
                    self.start_edit()
                return True
            elif self.editing:
                self.commit()
        if self.editing and event.type == pygame.KEYDOWN:
            if event.key == pygame.K_RETURN or event.key == pygame.K_KP_ENTER or event.key == pygame.K_TAB:
                self.commit()
            elif event.key == pygame.K_ESCAPE:
                self.editing = False
            elif event.key == pygame.K_BACKSPACE:
                self.buffer = "" if self._select_all else self.buffer[:-1]
                self._select_all = False
            else:
                ch = event.unicode
                is_valid = (ch.isdigit() or ch in "-.") if spec.kind in ("int", "float") else (ch and ch.isprintable())
                if is_valid:
                    if self._select_all:
                        self.buffer = ""
                        self._select_all = False
                    self.buffer += ch
            return True
        return False

    def draw(self, surf, font, font_sm):
        spec = self.spec
        label_surf = font_sm.render(spec.label, True, C_TEXT_DIM)
        surf.blit(label_surf, (self.rect.x, self.rect.y + (self.rect.height - label_surf.get_height()) // 2))

        widget_w = 130
        widget_rect = pygame.Rect(self.rect.right - widget_w, self.rect.y + 2, widget_w, self.rect.height - 4)

        if spec.kind == "bool":
            on = bool(self.value())
            surf.draw_rect(C_GOOD if on else C_PANEL_DARK, widget_rect, border_radius=4)
            surf.draw_rect(C_BORDER, widget_rect, width=1, border_radius=4)
            txt = font_sm.render("ON" if on else "OFF", True, C_TEXT)
            surf.blit(txt, txt.get_rect(center=widget_rect.center))
        elif spec.kind == "choice":
            surf.draw_rect(C_PANEL_DARK, widget_rect, border_radius=4)
            surf.draw_rect(C_BORDER, widget_rect, width=1, border_radius=4)
            txt = font_sm.render(str(self.value()), True, C_TEXT)
            surf.blit(txt, txt.get_rect(center=widget_rect.center))
        else:
            bg = C_PANEL if self.editing else C_PANEL_DARK
            surf.draw_rect(bg, widget_rect, border_radius=4)
            surf.draw_rect(C_ACCENT if self.editing else C_BORDER, widget_rect, width=1, border_radius=4)
            if self.editing:
                shown = self.buffer + ("_" if (pygame.time.get_ticks() // 400) % 2 == 0 else "")
            else:
                v = self.value()
                shown = "auto" if v is None else str(v)
            color = C_TEXT_DIM if (not self.editing and self.value() is None) else C_TEXT
            txt = font_sm.render(shown, True, color)
            surf.blit(txt, (widget_rect.x + 6, widget_rect.y + (widget_rect.height - txt.get_height()) // 2))
        self.rect.height = self.ROW_H  # keep hit-test valid between layout passes


# ─────────────────────────────────────────────────────────────────────────
#  Parameter panel — scrollable, section-grouped, built from a schema
# ─────────────────────────────────────────────────────────────────────────

class ParamPanel:
    def __init__(self, rect: pygame.Rect):
        self.rect = rect
        self.scroll = 0
        self.editors: list = []   # flat list of (kind, payload) — 'header' or FieldEditor

    def load(self, groups, target: dict):
        self.editors = []
        for section_name, fields in groups:
            self.editors.append(("header", section_name))
            for spec in fields:
                self.editors.append(("field", FieldEditor(spec, target)))
        self.scroll = 0

    def content_height(self):
        h = 0
        for kind, payload in self.editors:
            h += 28 if kind == "header" else FieldEditor.ROW_H
        return h

    def is_editing(self):
        """True while a text/numeric FieldEditor has an active edit buffer
        open — used to keep the stage's spacebar hotkey from hijacking a
        space the user is trying to type into a field."""
        return any(kind == "field" and payload.editing for kind, payload in self.editors)

    def handle_event(self, event):
        if event.type == pygame.MOUSEWHEEL and self.rect.collidepoint(pygame.mouse.get_pos()):
            self.scroll -= event.y * 30
            max_scroll = max(0, self.content_height() - self.rect.height)
            self.scroll = max(0, min(self.scroll, max_scroll))
            return True
        for kind, payload in self.editors:
            if kind == "field":
                if payload.rect.height and self.rect.collidepoint(payload.rect.center):
                    if payload.handle_event(event, self.rect):
                        return True
                elif payload.editing and event.type == pygame.MOUSEBUTTONDOWN:
                    payload.commit()
        return False

    def draw(self, surf, font, font_sm):
        prev_clip = surf.get_clip()
        surf.set_clip(self.rect)
        surf.draw_rect(C_PANEL_DARK, self.rect)

        y = self.rect.y - self.scroll
        for kind, payload in self.editors:
            if kind == "header":
                row = pygame.Rect(self.rect.x, y, self.rect.width, 28)
                if row.bottom > self.rect.y and row.top < self.rect.bottom:
                    surf.draw_rect((34, 34, 46), row)
                    txt = font.render(payload, True, C_ACCENT)
                    surf.blit(txt, (row.x + 8, row.y + 5))
                y += 28
            else:
                editor: FieldEditor = payload
                editor.rect = pygame.Rect(self.rect.x + 12, y, self.rect.width - 24, FieldEditor.ROW_H)
                if editor.rect.bottom > self.rect.y and editor.rect.top < self.rect.bottom:
                    editor.draw(surf, font, font_sm)
                    if editor.spec.help:
                        pass  # tooltip could hook in here later (status bar shows it on hover)
                y += FieldEditor.ROW_H

        surf.set_clip(prev_clip)
        surf.draw_rect(C_BORDER, self.rect, width=1)

        # scrollbar
        total = self.content_height()
        if total > self.rect.height:
            track = pygame.Rect(self.rect.right - 6, self.rect.y, 6, self.rect.height)
            surf.draw_rect(C_PANEL, track)
            thumb_h = max(20, int(self.rect.height * self.rect.height / total))
            thumb_y = self.rect.y + int(self.scroll * (self.rect.height - thumb_h) / max(1, total - self.rect.height))
            surf.draw_rect(C_ACCENT_DIM, (track.x, thumb_y, 6, thumb_h), border_radius=3)

    def field_under_mouse(self, pos):
        for kind, payload in self.editors:
            if kind == "field" and payload.rect.height and payload.rect.collidepoint(pos):
                return payload.spec
        return None


# ─────────────────────────────────────────────────────────────────────────
#  Preview actor — a minimal stand-in for player.py's Player class.
#  KamehamehaChargeEffect only ever reads .x / .y / .height / .direction
#  off the object passed to it (see beam.py's KamehamehaChargeEffect.draw),
#  so that's all this needs to provide to drive the REAL charge-effect
#  class. It also knows how to draw a placeholder body so the stage reads
#  as "a character standing here" even before wiring in real sprite frames.
# ─────────────────────────────────────────────────────────────────────────

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
        'dragon_fist' entry below) — entries can be 2- or 3-tuples, mixed
        freely within the same map."""
        anim_map = self.ANIM_FOR_STATE_BY_ARCHETYPE.get(
            self.anim_archetype, self._BEAM_STYLE_ANIM_FOR_STATE)
        lookup_direction = self._octant_override or self.direction
        for entry in anim_map.get(self.anim_state, [("walk", None)]):
            anim_name = entry[0]
            hold_frame = entry[1] if len(entry) > 1 else None
            loop_tail = entry[2] if len(entry) > 2 else None
            frames = self._frames_for(anim_name, lookup_direction)
            if frames:
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
            surf.draw_ellipse(C_BORDER, body, width=2)
            label = pygame.font.Font(None, 16).render(self.char_id, True, C_TEXT_DIM)
            surf.blit(label, label.get_rect(midtop=(body.centerx, body.bottom + 2)))


class FakeCamera:
    x = 0.0
    y = 0.0


# ─────────────────────────────────────────────────────────────────────────
#  Main tool
# ─────────────────────────────────────────────────────────────────────────

class AttackCreator:
    STATE_IDLE, STATE_CHARGING, STATE_FIRING, STATE_DECAYING = "idle", "charging", "firing", "decaying"

    def __init__(self, screen_width, screen_height):
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.active = False   # same contract as CharacterCreator: game.py gates

        self.font = pygame.font.Font(None, 20)
        self.font_sm = pygame.font.Font(None, 16)
        self.font_lg = pygame.font.Font(None, 26)

        self.active_archetype = "beam"
        self.config = self._make_config(self.active_archetype, {"id": "new_attack", "display_name": "New Attack"})
        self.active_tab = self._first_tab(self.config)   # e.g. 'beam' | 'charge', or 'chain' | 'charge'
        self.status_msg = ""
        self.status_ok = True

        self.characters = self._scan_characters()
        self.char_index = 0
        self.direction_index = 0

        self.state = self.STATE_IDLE
        self.charge_obj = None
        self.attack_obj = None
        self.charge_elapsed = 0.0

        # Real LayerManager (core/draw_layers.py) — the same one game.py
        # uses — so the stage sorts actor/charge_obj/attack_obj by their
        # actual draw_layer/get_sort_key() instead of a fixed draw order.
        # See _draw_stage().
        self.layer_manager = LayerManager()

        # Pause + drag-to-reposition for whichever offset dict the active
        # tab exposes (charge/beam/chain — see OFFSET_ATTR_FOR_TAB above).
        self.paused = False
        self.dragging_offset = False
        self._drag_offset_attr: Optional[str] = None  # which attr is being dragged, set on press

        self._build_layout()
        self._reload_panel()
        self._new_actor()

    # ── setup ────────────────────────────────────────────────────────
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
        listing assets/sprites/player/ ourselves. That's the same roster
        game.py plays from: it honors the saved menu order and — crucially —
        excludes characters that were soft-deleted via the character
        creator (their sprite folder stays on disk, but the ID is recorded
        in character_menu.json's "removed" list). Scanning the raw folder
        ourselves, as this used to do, meant deleted characters kept
        showing up here even though they're gone everywhere else."""
        names = character_creator.discover_characters()
        if names:
            return names
        return ["default"]

    def _build_layout(self):
        pad = 12
        sidebar_w = 220
        panel_w = 340
        top_h = 46

        self.sidebar_rect = pygame.Rect(pad, pad, sidebar_w, self.screen_height - 2 * pad)
        self.top_rect = pygame.Rect(self.sidebar_rect.right + pad, pad,
                                     self.screen_width - sidebar_w - panel_w - 3 * pad, top_h)
        self.stage_rect = pygame.Rect(self.top_rect.x, self.top_rect.bottom + pad,
                                       self.top_rect.width, self.screen_height - top_h - 3 * pad)
        self.panel_rect_outer = pygame.Rect(self.stage_rect.right + pad, pad,
                                             panel_w, self.screen_height - 2 * pad)

        # Tab row: fixed height regardless of archetype, but split into
        # however many tabs config.PARAM_SETS has for the current
        # archetype — see _build_tabs(), called at the end of this method
        # and again any time self.config swaps to a different archetype
        # (new/duplicate/load/archetype-cycle). Decoupled from that count
        # here since every archetype's tab row is the same fixed height,
        # only the per-tab widths vary.
        tab_h = 30
        self.panel_w = panel_w
        self.tab_h = tab_h
        save_bar_h = 40
        self.param_panel = ParamPanel(pygame.Rect(
            self.panel_rect_outer.x, self.panel_rect_outer.y + tab_h + 4,
            panel_w, self.panel_rect_outer.height - tab_h - save_bar_h - 8))
        self.save_bar_rect = pygame.Rect(self.panel_rect_outer.x, self.panel_rect_outer.bottom - save_bar_h,
                                          panel_w, save_bar_h)

        # sidebar buttons — archetype picker (governs what [+ New] makes)
        # sits above the new/duplicate row, pushing the saved-config list
        # down accordingly.
        arch_row_y = self.sidebar_rect.y + 40
        self.btn_arch_prev = Button(pygame.Rect(self.sidebar_rect.x + 8, arch_row_y, 26, 26),
                                     "<", lambda: self._cycle_archetype(-1))
        self.btn_arch_next = Button(pygame.Rect(self.sidebar_rect.right - 8 - 26, arch_row_y, 26, 26),
                                     ">", lambda: self._cycle_archetype(1))

        new_dup_row_y = arch_row_y + 32
        list_top = new_dup_row_y + 44
        self.sidebar_list_rect = pygame.Rect(self.sidebar_rect.x + 8, list_top,
                                              self.sidebar_rect.width - 16,
                                              self.sidebar_rect.bottom - list_top - 90)
        self.sidebar_scroll = 0

        bw = (self.sidebar_rect.width - 24) // 2
        self.btn_new = Button(pygame.Rect(self.sidebar_rect.x + 8, new_dup_row_y, bw, 28),
                               "+ New", self._on_new)
        self.btn_dup = Button(pygame.Rect(self.sidebar_rect.x + 16 + bw, new_dup_row_y, bw, 28),
                               "Duplicate", self._on_duplicate)
        self.btn_delete = Button(pygame.Rect(self.sidebar_rect.x + 8, self.sidebar_rect.bottom - 40,
                                              self.sidebar_rect.width - 16, 28),
                                  "Delete Selected", self._on_delete, style="danger")

        self.btn_char_prev = Button(pygame.Rect(self.top_rect.x, self.top_rect.y, 28, self.top_rect.height),
                                     "<", self._prev_char)
        self.btn_char_next = Button(pygame.Rect(self.top_rect.x + 190, self.top_rect.y, 28, self.top_rect.height),
                                     ">", self._next_char)
        self.btn_dir_prev = Button(pygame.Rect(self.top_rect.x + 240, self.top_rect.y, 28, self.top_rect.height),
                                    "<", self._prev_dir)
        self.btn_dir_next = Button(pygame.Rect(self.top_rect.x + 380, self.top_rect.y, 28, self.top_rect.height),
                                    ">", self._next_dir)

        fire_w, fire_h = 200, 44
        self.btn_fire = HoldButton(
            pygame.Rect(self.stage_rect.centerx - fire_w // 2, self.stage_rect.bottom - fire_h - 10,
                        fire_w, fire_h),
            "Hold to Fire", self._on_fire_press, self._on_fire_release)

        pause_w = 90
        self.btn_pause = Button(
            pygame.Rect(self.btn_fire.rect.x - pause_w - 10, self.btn_fire.rect.y, pause_w, fire_h),
            "Pause", self._toggle_pause)

        self.btn_save = Button(pygame.Rect(self.save_bar_rect.x + 6, self.save_bar_rect.y + 4,
                                            self.save_bar_rect.width - 12, self.save_bar_rect.height - 8),
                                "Save", self._on_save, style="primary")

        self._build_tabs()

    def _build_tabs(self):
        """(Re)build self.tab_rects — one Rect per entry in
        self.config.PARAM_SETS, evenly dividing the fixed-height tab row.
        Called from _build_layout() and again whenever self.config swaps
        to a config of a different archetype (different PARAM_SETS), since
        the tab count/labels can change even though the row's own
        position/height doesn't."""
        names = list(self.config.PARAM_SETS.keys())
        n = max(1, len(names))
        self.tab_rects = {}
        x = self.panel_rect_outer.x
        for i, name in enumerate(names):
            w = self.panel_w // n if i < n - 1 else self.panel_w - (self.panel_w // n) * (n - 1)
            self.tab_rects[name] = pygame.Rect(x, self.panel_rect_outer.y, w, self.tab_h)
            x += w

    def _reload_panel(self):
        groups = self.config.GROUPS.get(self.active_tab) or [("Parameters", self.config.PARAM_SETS[self.active_tab])]
        target = self.config.params[self.active_tab]
        self.param_panel.load(groups, target)

    def _new_actor(self):
        cx = self.stage_rect.centerx
        cy = self.stage_rect.centery + 60
        self.actor = PreviewActor(PREVIEW_WORLD_ANCHOR, PREVIEW_WORLD_ANCHOR, self.characters[self.char_index])
        self.actor.set_direction(DIRECTIONS[self.direction_index])  # keep facing on character switch
        self.camera = FakeCamera()
        # Center world (PREVIEW_WORLD_ANCHOR, PREVIEW_WORLD_ANCHOR) — the
        # actor's anchor, well clear of the corner-origin bounds check
        # fired attacks run against (see PREVIEW_WORLD_ANCHOR's own
        # comment) — at the desired screen point:
        # screen = world*scale - camera, so camera = world*scale - screen.
        from config.settings import RENDER_SCALE
        self.camera.x = PREVIEW_WORLD_ANCHOR * RENDER_SCALE - cx
        self.camera.y = PREVIEW_WORLD_ANCHOR * RENDER_SCALE - cy

    # ── sidebar actions ─────────────────────────────────────────────
    def _saved_configs(self):
        # Every archetype this tool supports shows in one flat list —
        # _draw_sidebar tags each row with its archetype so a beam config
        # and a chain config aren't visually confused with one another.
        return list_saved_configs(CONFIGS_DIR)

    def _cycle_archetype(self, step):
        """Changes what [+ New] builds. Also immediately starts a fresh
        untitled attack of the newly-selected archetype — the previously
        in-progress (unsaved) edit would otherwise be showing a param
        panel/tabs for a class that no longer matches self.active_archetype."""
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

    # ── character / direction ──────────────────────────────────────
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
    # comments, just driven by a mouse button instead of a keybind. ──
    def _on_fire_press(self):
        if self.paused:
            # Pause freezes the whole preview for dragging — a stray
            # press while paused (e.g. clicking "Hold to Fire" again by
            # habit) shouldn't restart anything out from under the drag.
            return
        self._stop_preview()
        direction = self.actor.direction
        if self.config.charge_enabled:
            self.charge_obj = self.config.build_charge_effect(self.actor)
            self.charge_elapsed = 0.0
            self.state = self.STATE_CHARGING
        else:
            self.attack_obj = self.config.build_attack(self.actor.x, self.actor.y, direction, player=self.actor)
            self.state = self.STATE_FIRING

    def _on_fire_release(self):
        if self.paused:
            # Without this, letting go of SPACE/the mouse button after
            # hitting Pause — the natural thing to do once the preview
            # visibly stops moving — would still run the normal release
            # logic below and cancel/decay whatever was just frozen for
            # dragging (e.g. STATE_CHARGING would _stop_preview() and the
            # charge effect would vanish out from under you). Pause means
            # frozen until Resume, full stop; Resume picks the state
            # machine back up exactly where update() left it.
            return
        if self.state == self.STATE_CHARGING:
            # Genkidama-only (fires_on_release=True — see AttackConfigBase):
            # unlike every other archetype, releasing MID-CHARGE is this
            # attack's actual fire trigger, not a cancel — you throw
            # whatever power state you're currently sitting in (see
            # GenkidamaChargeEffect's own docstring). Every other
            # archetype's build_attack(...) doesn't even declare a
            # charge_obj parameter; this branch is the only caller that
            # passes one, and only takes it when the active config opted in.
            if getattr(self.config, "fires_on_release", False) and self.charge_obj is not None:
                self.attack_obj = self.config.build_attack(
                    self.actor.x, self.actor.y, self.actor.direction,
                    player=self.actor, charge_obj=self.charge_obj)
                self.charge_obj = None
                self.state = self.STATE_FIRING
            else:
                self._stop_preview()
        elif self.state == self.STATE_FIRING:
            # Archetype-generic: beam-family attacks have a lengthwise
            # decay sweep (start_decay(), then let update() run it down to
            # inactive — see the STATE_DECAYING branch in update()).
            # Chain-family attacks (flame_kamehameha) have no such sweep —
            # release just ends them outright via stop(). Prefer
            # start_decay() when the built object has one; fall back to
            # stop(); if it has neither, just drop the preview.
            #
            # Sword-family attacks (EnergySwordSpinEffect) are a third
            # case: releasing early should do NOTHING — the spin is a
            # fixed, free, autoplay beat once it starts (see that class's
            # docstring) and ends itself via its own duration countdown
            # (see the STATE_FIRING/STATE_DECAYING branch in update()).
            # no_release_cancel is how it opts out of the decay/stop
            # fallback below without a hardcoded archetype check here.
            #
            # Dragon-Fist-family attacks (DragonFistAttack) are a fourth
            # case: release begins its own two-phase closing sequence
            # (start_retract() — head_end art, then the shared destruction
            # puff — see that class's start_retract() docstring), which
            # plays out and ends the attack itself, the same
            # "start*, then let update() run it down to inactive" shape
            # start_decay() already has — so it's handled the same way,
            # just via a different method name.
            if getattr(self.attack_obj, "no_release_cancel", False):
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

    def _toggle_pause(self):
        """Freeze the fire state machine and the actor's own animation so
        the currently-visible frame holds still — meant to be hit mid-
        charge/fire/decay so the on-stage offset handle (charge/beam/
        chain) can be dragged precisely without everything moving out
        from under the mouse at the same time."""
        self.paused = not self.paused
        self.btn_pause.label = "Resume" if self.paused else "Pause"

    def _set_status(self, msg, ok):
        self.status_msg = msg
        self.status_ok = ok

    # ── lifecycle (same contract as character_creator.CharacterCreator) ──
    def toggle(self):
        self.active = not self.active
        if not self.active:
            self._stop_preview()
            self.paused = False
            self.dragging_offset = False
            self._drag_offset_attr = None
            self.btn_pause.label = "Pause"

    # ── frame update ─────────────────────────────────────────────────
    def update(self, dt):
        if not self.active:
            return
        if self.paused:
            # Deliberately don't touch state/charge_obj/attack_obj/actor at
            # all — whatever was on screen the moment Pause was hit just
            # keeps being redrawn as-is by draw(), frozen, so the charge
            # offset handle can be dragged against a stable target.
            return
        if self.state == self.STATE_CHARGING and self.charge_obj:
            self.charge_obj.update(dt)
            # Tracked independently of charge_obj's own internal tick:
            # KamehamehaChargeEffect.update() is a no-op whenever no charge
            # spritesheet loaded (see beam.py — `if not self.frames_scaled:
            # return`), so tick never advances without art in place. The
            # real game can't be relying on that tick to know when to
            # auto-fire either, or an attack with no charge art yet would
            # simply charge forever — hence tracking wall-clock elapsed
            # time here against get_total_duration() instead.
            self.charge_elapsed += dt
            if self.charge_elapsed >= self.charge_obj.get_total_duration():
                # Charge animation has played through — auto-fire while
                # still held, exactly like player.py's auto-fire-on-
                # charge-complete beams.
                self.attack_obj = self.config.build_attack(
                    self.actor.x, self.actor.y, self.actor.direction, player=self.actor)
                self.charge_obj = None
                self.state = self.STATE_FIRING
        elif self.state in (self.STATE_FIRING, self.STATE_DECAYING) and self.attack_obj:
            # Projectile-family attacks (BurningAttack, inherited from
            # attacks.projectile.Projectile) take world_width/world_height
            # so the projectile knows when it's traveled off the play area
            # and should despawn — the same bounds player.py's real
            # movement/collision code passes in. Beam/chain/sword attacks
            # only take dt. Dragon Fist takes player_x/player_y instead —
            # it needs the player's *live* position every tick (the leash
            # box and anchor segment both track it — see
            # DragonFistAttack.update()), not a one-time spawn point.
            #
            # Rather than hardcode any of this per-archetype (and have it
            # silently go stale the next time a new attack shape is
            # added), inspect the actual update() signature's PARAMETER
            # NAMES — not just the count, which can't tell Dragon Fist's
            # (dt, player_x, player_y) apart from a projectile's (dt,
            # world_width, world_height); both are 3 params — and dispatch
            # accordingly. A previewed projectile despawns at
            # PREVIEW_WORLD_BOUND from the world-space corner-origin (see
            # PREVIEW_WORLD_ANCHOR's own comment on why that's not the
            # stage rect's pixel dimensions), not the whole window; a
            # previewed Dragon Fist tracks self.actor's real position,
            # same as it would track a real player's.
            try:
                param_names = set(inspect.signature(self.attack_obj.update).parameters.keys())
            except (TypeError, ValueError):
                param_names = set()
            if {"player_x", "player_y"} <= param_names:
                self.attack_obj.update(dt, self.actor.x, self.actor.y)
            elif len(param_names) >= 3:
                # Matches Projectile.update(self, world_width, world_height,
                # dt=0.016)'s real positional order (see attacks/projectile.py
                # and attacks/genkidama.py's GenkidamaBlast, which shares it)
                # — width/height first, dt last. Previously called as
                # (dt, width, height), which silently fed dt in as
                # world_width; since dt (~0.016) is smaller than almost any
                # on-stage x/y, the projectile/genkidama preview would judge
                # itself instantly out of bounds and despawn the frame after
                # it fired. Fixed here rather than worked around per-
                # archetype since every future attack sharing this same
                # 3-param (width, height, dt) shape gets it for free too.
                self.attack_obj.update(PREVIEW_WORLD_BOUND, PREVIEW_WORLD_BOUND, dt)
            else:
                self.attack_obj.update(dt)
            if not self.attack_obj.active:
                self._stop_preview()

        # Keep the preview sprite's pose/animation in sync with the fire
        # state machine (idle/charging/firing/decaying) and let it advance
        # its own frame cycle. Previously nothing drove either of these,
        # so the actor stayed frozen on whatever single frame it loaded at
        # construction regardless of state or direction changes.
        self.actor.set_anim_state(self.state, self._anim_archetype_key())
        # Sword-spin-only: the player's own body sweeps through the same
        # 8 octants the attack_obj (EnergySwordSpinEffect) is stepping
        # through — see PreviewActor.set_octant()'s own comment. Cleared
        # back to None (plain self.direction) the instant it's not the
        # live spin driving the pose, so charging/idle/every other
        # archetype are unaffected.
        if self.active_archetype == "sword" and self.state in (self.STATE_FIRING, self.STATE_DECAYING) \
                and self.attack_obj is not None and hasattr(self.attack_obj, "current_octant"):
            self.actor.set_octant(self.attack_obj.current_octant())
        else:
            self.actor.set_octant(None)
        self.actor.update(dt)

    # ── events ───────────────────────────────────────────────────────
    def handle_input(self, event):
        """Returns 'close' when the overlay was just closed (mirrors
        CharacterCreator.handle_input's contract), else None."""
        if not self.active:
            return None

        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self._stop_preview()
            self.paused = False
            self.dragging_offset = False
            self._drag_offset_attr = None
            self.btn_pause.label = "Pause"
            self.active = False
            return "close"

        for btn in (self.btn_arch_prev, self.btn_arch_next, self.btn_new, self.btn_dup, self.btn_delete,
                    self.btn_char_prev, self.btn_char_next,
                    self.btn_dir_prev, self.btn_dir_next, self.btn_save, self.btn_pause):
            if btn.handle_event(event):
                return None
        if self.btn_fire.handle_event(event):
            return None

        # Spacebar mirrors the "Hold to Fire" button — press to start
        # charging/firing, release to let go — same contract as clicking
        # btn_fire, just keyboard-driven. Skipped while a param-panel text
        # field has an active edit buffer so typing a space into a value
        # doesn't also trigger the stage. Guarded on btn_fire.pressed so a
        # spurious extra KEYDOWN (or the mouse already holding the button)
        # can't double-fire or send a release with nothing pressed.
        if event.type == pygame.KEYDOWN and event.key == pygame.K_SPACE \
                and self.btn_fire.enabled and not self.param_panel.is_editing():
            if not self.btn_fire.pressed:
                self.btn_fire.pressed = True
                self.btn_fire.on_press()
            return None
        if event.type == pygame.KEYUP and event.key == pygame.K_SPACE:
            if self.btn_fire.pressed:
                self.btn_fire.pressed = False
                self.btn_fire.on_release()
            return None

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

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            for tab_name, rect in self.tab_rects.items():
                if rect.collidepoint(event.pos) and self.active_tab != tab_name:
                    self.active_tab = tab_name
                    self._reload_panel()
                    return
            if self.active_tab in self.config.OPTIONAL_SETS:
                cb_rect = self._charge_enabled_rect()
                if cb_rect.collidepoint(event.pos):
                    self.config.set_enabled[self.active_tab] = not self.config.set_enabled[self.active_tab]
                    return
            for i, (path, cid, name, _arch) in enumerate(self._saved_configs()):
                row = self._sidebar_row_rect(i)
                if row.collidepoint(event.pos):
                    self._load_config_from_path(path)
                    return

        # Always forward to the panel rather than gating on the live OS
        # cursor position: ParamPanel/FieldEditor already check the
        # event's own coordinates internally, and gating here on
        # pygame.mouse.get_pos() made clicks miss whenever an event's
        # position didn't match wherever the OS cursor last physically
        # was (e.g. any programmatic/injected event, or simply a click
        # registered a frame after a fast mouse move).
        self.param_panel.handle_event(event)
        return None

    def _anim_archetype_key(self) -> str:
        """Which key into PreviewActor.ANIM_FOR_STATE_BY_ARCHETYPE to drive
        the preview sprite from this frame. Every other archetype maps
        1:1 onto its own name; "sword" is the one exception — it needs to
        additionally pick the cw/ccw spin variant (see PreviewActor's own
        comment on why), which isn't something a static per-archetype
        table can encode on its own since it depends on the built attack's
        own .clockwise (or, while still only charging and nothing's been
        built yet, the config's own Clockwise field as a preview of what
        it WILL be)."""
        if self.active_archetype != "sword":
            return self.active_archetype
        clockwise = getattr(self.attack_obj, "clockwise", None)
        if clockwise is None:
            clockwise = bool(self.config.sword.get("clockwise", True))
        return "sword_cw" if clockwise else "sword_ccw"

    def _sidebar_row_rect(self, index):
        return pygame.Rect(self.sidebar_list_rect.x, self.sidebar_list_rect.y + index * 26 - self.sidebar_scroll,
                            self.sidebar_list_rect.width, 24)

    def _charge_enabled_rect(self):
        return pygame.Rect(self.panel_rect_outer.x + self.panel_rect_outer.width - 90,
                            self.panel_rect_outer.y + self.tab_h + 6, 78, 22)

    # ── offset drag handle (charge / beam / chain / projectile /
    # dragon_fist / sword — archetype-generic) ──
    # Each of these is a per-direction (x, y) pixel offset applied to
    # whatever the tab builds, relative to the player — direction_offsets
    # for the charge-up effect, beam_offsets/chain_offsets/
    # projectile_offsets/dragon_fist_offsets/sword_offsets for the fired
    # attack itself (see attacks/attack_config.py). None of them fit
    # ParamPanel's flat FieldSpec model (a dict keyed by direction, not a
    # scalar), so they get this dedicated crosshair instead of a number
    # field. OFFSET_ATTR_FOR_TAB (top of file) is what makes this
    # archetype/tab-generic: a tab shows a handle whenever the active
    # config actually has the attribute that tab maps to.
    def _active_offset_attr(self) -> Optional[str]:
        attr_name = OFFSET_ATTR_FOR_TAB.get(self.active_tab)
        if attr_name and hasattr(self.config, attr_name):
            return attr_name
        return None

    def _offset_anchor_extra(self, attr_name: str):
        """Extra world-space (x, y) between the player and the point the
        crosshair should actually represent, beyond the raw offset dict
        itself. Every archetype except Dragon Fist has none (0, 0) — the
        crosshair sits at actor position + offset, full stop.

        Dragon Fist is the exception: DragonFistAttack shifts the anchor
        segment anchor_offset further out along the throw direction on
        top of whatever x/y it's given (see DragonFistAttack._update_anchor()
        and its own anchor_offset field, editable as a plain number in the
        "dragon_fist" tab). Without folding that in here, the crosshair
        would sit anchor_offset away from the anchor piece that's actually
        drawn on screen — this keeps the handle exactly on it, so dragging
        it moves the visible anchor, not some invisible point beside it.
        """
        if attr_name == "dragon_fist_offsets":
            dxu, dyu = _DRAGON_FIST_DIRECTION_UNIT.get(self.actor.direction, (0, 0))
            anchor_offset = self.config.dragon_fist.get("anchor_offset", 0)
            return dxu * anchor_offset, dyu * anchor_offset
        if attr_name == "direction_offsets" and self.active_archetype == "genkidama":
            # GenkidamaChargeEffect anchors its floating ball at
            # player.x, player.y - player.height/2 (see its own
            # _center_world_pos()) rather than the raw player position
            # every other archetype's charge effect uses as its base
            # point — fold that vertical shift in here so the crosshair
            # sits exactly on the visible ball instead of hovering
            # half the player's height below it.
            return 0, -getattr(self.actor, "height", 0) / 2
        if attr_name == "genkidama_offsets":
            # genkidama_offsets is an ADDITIVE nudge on top of
            # direction_offsets (see GenkidamaAttackConfig.build_attack) —
            # fold direction_offsets' own current-direction value in here
            # too, plus the same -height/2 shift as above, so this
            # crosshair sits at the ball's TRUE spawn point (where it was
            # floating, plus this tab's own release-time nudge) rather
            # than double-counting or ignoring the charge tab's offset.
            dox, doy = self.config.direction_offsets.get(self.actor.direction, (0, 0))
            return dox, doy - getattr(self.actor, "height", 0) / 2
        return 0, 0

    def _offset_screen_pos(self, attr_name: str):
        from config.settings import RENDER_SCALE
        offsets = getattr(self.config, attr_name)
        ox, oy = offsets.get(self.actor.direction, (0, 0))
        ex, ey = self._offset_anchor_extra(attr_name)
        # Same world->screen transform PreviewActor.draw() uses, applied
        # to (actor position + offset + anchor extra) instead of just
        # actor position.
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

        # Best-effort: also nudge whatever's already built/on-screen so a
        # drag mid-preview (while paused) moves it immediately instead of
        # only taking effect on the *next* charge/fire. This tool doesn't
        # own attacks/beam.py, attacks/flame_kamehameha.py, etc., so it
        # can't be certain these objects expose position the same way —
        # each branch below silently no-ops if the guess is wrong. The
        # config value set above is always correct regardless, and will
        # apply next time either way.
        if attr_name == "direction_offsets" and self.charge_obj is not None \
                and hasattr(self.charge_obj, "direction_offsets"):
            try:
                self.charge_obj.direction_offsets[direction] = new_offset
            except Exception:
                pass
        elif attr_name == "sword_offsets" and self.attack_obj is not None \
                and hasattr(self.attack_obj, "direction_offsets"):
            # EnergySwordSpinEffect re-reads its own direction_offsets
            # every update() tick (see that class) rather than baking a
            # position in once — same live-reread shape as
            # direction_offsets/charge_obj above, just on the fired attack
            # instead of the charge effect.
            try:
                self.attack_obj.direction_offsets[direction] = new_offset
            except Exception:
                pass
        elif attr_name == "dragon_fist_offsets" and self.attack_obj is not None \
                and hasattr(self.attack_obj, "translate"):
            # DragonFistAttack has no single self.x/self.y to overwrite —
            # it tracks head_x/head_y, origin_x/origin_y, and every body
            # segment separately (see class docstring) — so there's
            # nothing to set an absolute position on. translate(dx, dy) is
            # its own purpose-built "shift everything already in flight"
            # method (used for the opening lunge — see Player.
            # _advance_dragon_fist_lunge), so nudge by the delta from the
            # drag instead of trying to set an absolute position.
            dx, dy = new_offset[0] - old_offset[0], new_offset[1] - old_offset[1]
            try:
                self.attack_obj.translate(dx, dy)
            except Exception:
                pass
        elif attr_name in ("beam_offsets", "chain_offsets", "projectile_offsets") and self.attack_obj is not None:
            # Unlike direction_offsets (which KamehamehaChargeEffect keeps
            # re-reading every frame), build_attack() bakes actor position
            # + offset into the object's origin once at construction —
            # there's no live offset for an already-fired beam/chain/
            # projectile to re-read. Move its origin directly by the same
            # amount instead.
            ox, oy = new_offset
            try:
                self.attack_obj.x = self.actor.x + ox
                self.attack_obj.y = self.actor.y + oy
            except Exception:
                pass
        elif attr_name == "genkidama_offsets" and self.attack_obj is not None:
            # Same "bake once at construction, so nudge origin directly"
            # story as beam/chain/projectile above, just against
            # GenkidamaAttackConfig.build_attack()'s own spawn formula
            # (actor position - half height + direction_offsets +
            # genkidama_offsets) instead of a plain actor-position-plus-
            # offset one.
            ox, oy = new_offset
            dox, doy = self.config.direction_offsets.get(direction, (0, 0))
            height = getattr(self.actor, "height", 0)
            try:
                self.attack_obj.x = self.actor.x + dox + ox
                self.attack_obj.y = self.actor.y - height / 2 + doy + oy
            except Exception:
                pass

    # ── drawing ──────────────────────────────────────────────────────
    def draw(self, screen, dt: float = 0.0):
        if not self.active:
            return
        screen.fill(C_BG)
        self._draw_sidebar(screen)
        self._draw_top_bar(screen)
        self._draw_stage(screen)
        self._draw_panel(screen)
        if self.status_msg:
            color = C_GOOD if self.status_ok else C_BAD
            txt = self.font_sm.render(self.status_msg, True, color)
            screen.blit(txt, (self.sidebar_rect.x, self.screen_height - 22))

    def _draw_sidebar(self, screen):
        r = self.sidebar_rect
        screen.draw_rect(C_PANEL, r)
        screen.draw_rect(C_BORDER, r, width=1)
        title = self.font_lg.render("Attacks", True, C_TEXT)
        screen.blit(title, (r.x + 8, r.y + 6))

        self.btn_arch_prev.draw(screen, self.font_sm)
        self.btn_arch_next.draw(screen, self.font_sm)
        arch_label, _cls = ARCHETYPES[self.active_archetype]
        arch_txt = self.font_sm.render(f"New: {arch_label}", True, C_TEXT_DIM)
        screen.blit(arch_txt, arch_txt.get_rect(center=(r.centerx, self.btn_arch_prev.rect.centery)))

        self.btn_new.draw(screen, self.font_sm)
        self.btn_dup.draw(screen, self.font_sm)

        prev_clip = screen.get_clip()
        screen.set_clip(self.sidebar_list_rect)
        for i, (path, cid, name, arch) in enumerate(self._saved_configs()):
            row = self._sidebar_row_rect(i)
            selected = cid == self.config.id and arch == self.config.archetype
            if selected:
                screen.draw_rect(C_ACCENT_DIM, row, border_radius=3)
            arch_label = ARCHETYPES.get(arch, (arch,))[0]
            txt = self.font_sm.render(f"[{arch_label}] {name}", True, C_TEXT if selected else C_TEXT_DIM)
            screen.blit(txt, (row.x + 6, row.y + 4))
        screen.set_clip(prev_clip)

        self.btn_delete.draw(screen, self.font_sm)

    def _draw_top_bar(self, screen):
        screen.draw_rect(C_PANEL, self.top_rect, border_radius=4)
        screen.draw_rect(C_BORDER, self.top_rect, width=1, border_radius=4)
        self.btn_char_prev.draw(screen, self.font)
        self.btn_char_next.draw(screen, self.font)
        char_name = self.characters[self.char_index]
        txt = self.font_sm.render(char_name, True, C_TEXT)
        screen.blit(txt, txt.get_rect(center=(self.top_rect.x + 110, self.top_rect.centery)))

        self.btn_dir_prev.draw(screen, self.font)
        self.btn_dir_next.draw(screen, self.font)
        dtxt = self.font_sm.render(DIRECTIONS[self.direction_index], True, C_TEXT)
        screen.blit(dtxt, dtxt.get_rect(center=(self.top_rect.x + 310, self.top_rect.centery)))

        id_txt = self.font_sm.render(f"Editing: {self.config.display_name}  ({self.config.id})", True, C_TEXT_DIM)
        screen.blit(id_txt, (self.top_rect.x + 440, self.top_rect.centery - 8))

    def _draw_stage(self, screen):
        r = self.stage_rect
        screen.draw_rect((10, 10, 15), r)
        screen.draw_rect(C_BORDER, r, width=1)
        prev_clip = screen.get_clip()
        screen.set_clip(r)

        # simple ground line so travel direction reads clearly
        screen.draw_line((30, 30, 40), (r.x, r.bottom - 40), (r.right, r.bottom - 40), 1)

        from config.settings import RENDER_SCALE
        # Real LayerManager pass — same draw_layer/get_sort_key() sorting
        # game.py itself uses (see core/draw_layers.py), rather than a
        # fixed charge-then-actor-then-attack draw order. That fixed order
        # happened to be right for down/left/right (both charge and beam
        # use EFFECTS_FRONT there) but was silently wrong for 'up', where
        # the real beam/charge draw BEHIND the player — see
        # get_beam_layer()'s and KamehamehaChargeEffect.__init__'s own
        # comments in beam.py/draw_layers.py. Routing through the actual
        # LayerManager means this stage is correct for every direction
        # automatically, and stays correct if an archetype's own layering
        # logic changes later, with nothing to update here.
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
        lbl = self.font_sm.render(f"state: {state_label}", True, C_WARN if self.paused else C_TEXT_DIM)
        screen.blit(lbl, (r.x + 8, r.y + 6))

        self.btn_fire.draw(screen, self.font)
        self.btn_pause.draw(screen, self.font)

    def _draw_offset_handle(self, screen, attr_name: str):
        """Crosshair for the current direction's offset on whichever tab
        is active (charge/beam/chain). Bright + draggable while paused;
        dimmed with a hint otherwise, so it's clear dragging needs Pause
        first rather than just not responding to clicks for no visible
        reason."""
        is_dragging = self.dragging_offset and self._drag_offset_attr == attr_name
        hx, hy = self._offset_screen_pos(attr_name)
        color = C_ACCENT if self.paused else C_TEXT_DIM
        radius = 8 if is_dragging else 6
        screen.draw_circle(color, (hx, hy), radius, width=0 if is_dragging else 2)
        screen.draw_line(color, (hx - 10, hy), (hx + 10, hy), 1)
        screen.draw_line(color, (hx, hy - 10), (hx, hy + 10), 1)

        ox, oy = getattr(self.config, attr_name).get(self.actor.direction, (0, 0))
        label = self.active_tab.replace("_", " ").title()
        r = self.stage_rect
        if self.paused:
            txt = self.font_sm.render(
                f"{label} offset ({self.actor.direction}): ({ox}, {oy}) — drag the crosshair", True, C_TEXT)
        else:
            txt = self.font_sm.render(
                f"{label} offset ({self.actor.direction}): ({ox}, {oy}) — Pause to drag", True, C_TEXT_DIM)
        screen.blit(txt, (r.x + 8, r.bottom - 22))

    def _draw_panel(self, screen):
        for tab_name, rect in self.tab_rects.items():
            active = self.active_tab == tab_name
            screen.draw_rect(C_ACCENT_DIM if active else C_PANEL, rect)
            screen.draw_rect(C_BORDER, rect, width=1)
            label = tab_name.replace("_", " ").title()
            txt = self.font.render(label, True, C_TEXT)
            screen.blit(txt, txt.get_rect(center=rect.center))

        if self.active_tab in self.config.OPTIONAL_SETS:
            cb = self._charge_enabled_rect()
            on = self.config.set_enabled[self.active_tab]
            label = self.active_tab.replace("_", " ").title()
            screen.draw_rect(C_GOOD if on else C_PANEL_DARK, cb, border_radius=4)
            screen.draw_rect(C_BORDER, cb, width=1, border_radius=4)
            txt = self.font_sm.render(f"{label}: ON" if on else f"{label}: OFF", True, C_TEXT)
            screen.blit(txt, txt.get_rect(center=cb.center))

        self.param_panel.draw(screen, self.font, self.font_sm)
        self.btn_save.draw(screen, self.font)


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