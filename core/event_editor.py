"""
core/event_editor.py

The Event Editor: build a Conditions list ("only if ...") and an Actions list
("then do ...") for a Trigger Box / NPC / quest step / anything that fires an
event.

The interface is built on dev_tools.ui_kit, so it reads as part of the same
family as the Dev Menu, Room Editor and Character Creator: navy backdrop, flat
cards with hairline borders that light up on hover, the bitmap menu font and
vector line icons. It draws ONLY through ui_kit's dispatch helpers, so it works
on the engine's GPUScreen as well as on a plain pygame.Surface.

Nothing of the old popup UI is left. What is kept is the *functionality* and
the data model:

    * every condition kind and every action type (same dict shapes out),
    * every picker (flags, characters, skills, portraits, rooms, music, ...),
      including the row-scoped ones (skins / animations / transformations /
      map locations) and the live-data hooks the host pushes in,
    * dialogue_choice options and conditional IF / ELSE IF / ELSE branches,
      each with their own nested action lists,
    * the mouse "Set Spawn" / "Set Position" room picker.

Usage (same as before):

    from core.event_editor import EventEditorWindow

    self.event_editor = EventEditorWindow(flag_manager)
    self.event_editor.open(title="...", existing_conditions=[...], existing_actions=[...],
                           on_save=lambda conditions, actions: ...)
    # every event while self.event_editor.active (mouse motion / wheel / keys too):
    self.event_editor.handle_input(event)
    # every frame while self.event_editor.active:
    self.event_editor.draw(screen)

The editor is a full-screen tool overlay (like the other dev tools) rather than a
small popup. Save (icon / Ctrl+S) calls on_save(conditions, actions) and closes;
Back / Esc closes without saving (with a confirm if something changed).

ConditionBuilder / ActionSequenceBuilder still exist and still hold the rows and
the discovery/picker data, but they are pure data models now - all drawing and
input lives in EventEditorWindow.
"""

from __future__ import annotations

import copy
import json
import math
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pygame

try:
    import dev_tools.ui_kit as uk
except ImportError:                                  # running from a flat folder
    import ui_kit as uk

from core.flag_manager import (
    flag_is, flag_is_not, variable_is,
    check_item, check_stat, check_character, check_zeni, check_resource, check_skill,
    check_timer, check_boss_hp, check_bar, check_room_kills,
)
from core.event_actions import ACTION_TYPES

# Same anchoring rule the Character Creator uses: the .exe's own folder when
# frozen, else the project root (one level up from this package folder).
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent.parent



def _discover_character_ids():
    """Player character IDs, sourced from the character creator's own
    filesystem scan (assets/sprites/player/) so this list never drifts
    out of sync with what characters actually exist. Best-effort: the
    character creator lives in dev_tools/ and may not always be on the
    import path (e.g. headless tooling), so this quietly returns []
    rather than blowing up the event editor over it."""
    try:
        from dev_tools.character_creator import discover_characters
        return discover_characters()
    except Exception:
        pass
    try:
        from character_creator import discover_characters
        return discover_characters()
    except Exception:
        return []


def _discover_skill_ids():
    """Skill/attack ids, sourced from the character creator's own
    filesystem scan (assets/sprites/attacks/) — the same global roster
    the Attacks tab's icon picker uses to populate a character's
    cfg["attacks"]["equipped_attacks"]. Kept in sync with what actually
    exists on disk rather than a hand-typed id, same rationale as
    _discover_character_ids() above."""
    try:
        from dev_tools.character_creator import discover_attacks
        return discover_attacks()
    except Exception:
        pass
    try:
        from character_creator import discover_attacks
        return discover_attacks()
    except Exception:
        return []


def _discover_equipped_skills(character_id):
    """Skills a SPECIFIC character currently has equipped, sourced from
    that character's saved config (assets/characters/{id}.json, via
    character_creator's load_config()['attacks']['equipped_attacks']).

    Unlike _discover_skill_ids() (the global attack roster every character
    can potentially use), this scopes down to just what could actually be
    removed from character_id right now. Same best-effort fallback-import
    pattern as the other _discover_* helpers above; returns [] if
    character_id is falsy or nothing can be loaded."""
    if not character_id:
        return []
    try:
        from dev_tools.character_creator import load_config
        return list(load_config(character_id).get('attacks', {}).get('equipped_attacks', []))
    except Exception:
        pass
    try:
        from character_creator import load_config
        return list(load_config(character_id).get('attacks', {}).get('equipped_attacks', []))
    except Exception:
        return []


def _discover_costume_ids(character_id):
    """Costume/skin ids for a SPECIFIC character, sourced from the character
    creator's own filesystem scan (assets/sprites/player/{character_id}/) —
    same rationale/fallback-import pattern as the other _discover_* helpers
    above. Unlike skills/attacks, costumes are per-character (not a shared
    global roster), so this always needs a character_id to scope against —
    used to populate the skin_id picker on set_player_character/
    set_player_skin action fields. Returns [] if character_id is falsy or
    nothing can be loaded."""
    if not character_id:
        return []
    try:
        from dev_tools.character_creator import discover_costumes
        return discover_costumes(character_id)
    except Exception:
        pass
    try:
        from character_creator import discover_costumes
        return discover_costumes(character_id)
    except Exception:
        return []


def _transformation_form_id(costume_path):
    """Given a transformation entry's 'costume' field
    ("{owning_costume}/transformations/{form}", per game.py's
    _reload_attack_config()), return just the "{form}" tail — the id the
    'transformation' event action's add/remove and player.
    unlocked_transformations key off of. Returns '' if costume_path
    doesn't look like a transformation entry at all."""
    marker = '/transformations/'
    if marker not in (costume_path or ''):
        return ''
    return costume_path.split(marker, 1)[1]


def _discover_transformation_ids(character_id):
    """Transformation form ids configured anywhere on a SPECIFIC character
    (across all its costumes), sourced from that character's saved config
    (assets/characters/{id}.json, via character_creator's
    load_config()['transformations'] — the same list game.py's
    _reload_attack_config() reads to compute has_transformation). Scoped
    per-character like _discover_costume_ids() above rather than global
    like _discover_skill_ids(), since transformation forms are authored
    per costume, not shared across the roster. Same best-effort
    fallback-import pattern as the other _discover_* helpers; returns []
    if character_id is falsy or nothing can be loaded."""
    if not character_id:
        return []
    try:
        from dev_tools.character_creator import load_config
    except Exception:
        try:
            from character_creator import load_config
        except Exception:
            return []
    try:
        cfg = load_config(character_id)
        return sorted({
            _transformation_form_id(t.get('costume', ''))
            for t in cfg.get('transformations', [])
            if _transformation_form_id(t.get('costume', ''))
        })
    except Exception:
        return []


def _discover_animation_ids(character_id):
    """Base animation ids for a SPECIFIC character (e.g. 'idle', 'walk',
    'attack'), sourced from the character creator's own filesystem scan
    (assets/sprites/player/{character_id}/{form}/*.png stems, unioned
    across forms) — same rationale/fallback-import pattern as
    _discover_costume_ids() above. Used to populate the animation_id
    picker on play_character_animation action fields, scoped to whatever
    character_id is picked in that same row. Returns [] if character_id
    is falsy or nothing can be loaded."""
    if not character_id:
        return []
    try:
        from dev_tools.character_creator import discover_animation_ids
        return discover_animation_ids(character_id)
    except Exception:
        pass
    try:
        from character_creator import discover_animation_ids
        return discover_animation_ids(character_id)
    except Exception:
        return []


def _discover_enemy_ids():
    """Spawnable enemy/boss ids, sourced from the entity editor's own
    catalogue (assets/sprites/enemies + boss roster — see
    discover_enemy_ids() in entity_editor.py) so this list never drifts out
    of sync with what enemies actually exist. Used to populate the
    enemy_id picker on spawn_enemies action fields. Same best-effort
    fallback-import pattern as the other _discover_* helpers above; returns
    [] if the entity editor isn't importable rather than blowing up the
    event editor over it."""
    try:
        from dev_tools.room_editor.room_editor_tools.entity_editor import discover_enemy_ids
        return discover_enemy_ids()
    except Exception:
        pass
    try:
        from entity_editor import discover_enemy_ids
        return discover_enemy_ids()
    except Exception:
        return []


def _discover_boss_ids():
    """Boss ids for the Boss HP condition's picker — the subset of
    _discover_enemy_ids()'s catalogue that are actually bosses (i.e. have
    a boss_id, per BossEnemy/entity_editor.py's own roster split), sourced
    the same best-effort fallback-import way as the other _discover_*
    helpers above. Deliberately narrower than the plain enemy_picker list
    used by spawn_enemies — _lookup_boss_hp_percent/_lookup_boss_hp_value
    only ever match BossEnemy.boss_id, so a regular (non-boss) enemy id
    picked here would just always evaluate to None/False."""
    try:
        from dev_tools.room_editor.room_editor_tools.entity_editor import discover_boss_ids
        return discover_boss_ids()
    except Exception:
        pass
    try:
        from entity_editor import discover_boss_ids
        return discover_boss_ids()
    except Exception:
        return []


def _discover_npc_ids():
    """Spawnable NPC ids, sourced from the entity editor's own catalogue
    (see discover_npc_ids() in entity_editor.py) so this list never drifts
    out of sync with what NPCs actually exist. Used to populate the
    npc_id picker on spawn_npc action fields. Same best-effort
    fallback-import pattern as the other _discover_* helpers above; returns
    [] if the entity editor isn't importable rather than blowing up the
    event editor over it."""
    try:
        from dev_tools.room_editor.room_editor_tools.entity_editor import discover_npc_ids
        return discover_npc_ids()
    except Exception:
        pass
    try:
        from entity_editor import discover_npc_ids
        return discover_npc_ids()
    except Exception:
        return []


def _discover_cutscene_ids():
    """Playable cutscene ids, sourced from the cutscene editor's own file
    scan (see discover_cutscene_ids() in cutscene_editor.py — the .json
    filename stems in data/cutscenes/) so this list never drifts out of
    sync with what cutscenes actually exist on disk. Used to populate the
    cutscene_id picker on play_cutscene action fields. Same best-effort
    fallback-import pattern as the other _discover_* helpers above; returns
    [] if the cutscene editor isn't importable rather than blowing up the
    event editor over it."""
    try:
        from dev_tools.cutscene_editor import discover_cutscene_ids
        return discover_cutscene_ids()
    except Exception:
        pass
    try:
        from cutscene_editor import discover_cutscene_ids
        return discover_cutscene_ids()
    except Exception:
        return []


def _discover_item_ids():
    """Item ids for the item picker on the 'item' action, sourced from the
    game's own item registry (data/items.py) so the list never drifts from
    what items actually exist. Best-effort like the other _discover_*
    helpers: tries a few likely registry names/accessors, returns [] if
    nothing importable is found rather than breaking the event editor."""
    def _ids_from(obj):
        out = []
        if isinstance(obj, dict):
            out = [k for k in obj.keys() if isinstance(k, str)]
        elif isinstance(obj, (list, tuple, set)):
            for e in obj:
                if isinstance(e, str):
                    out.append(e)
                elif isinstance(e, dict):
                    v = e.get('id') or e.get('name')
                    if isinstance(v, str):
                        out.append(v)
                else:
                    v = getattr(e, 'id', None) or getattr(e, 'name', None)
                    if isinstance(v, str):
                        out.append(v)
        return out

    def _scan(mod):
        for attr in ('ITEMS', 'ITEM_DB', 'ITEM_DATA', 'ITEM_DEFS', 'ITEM_DEFINITIONS',
                     'ITEM_REGISTRY', 'ITEM_CATALOG', 'ALL_ITEMS'):
            ids = _ids_from(getattr(mod, attr, None))
            if ids:
                return ids
        for fn in ('get_all_items', 'all_items', 'list_items', 'get_item_ids', 'item_ids'):
            f = getattr(mod, fn, None)
            if callable(f):
                try:
                    ids = _ids_from(f())
                except Exception:
                    ids = []
                if ids:
                    return ids
        for attr in dir(mod):
            if attr.isupper() and 'ITEM' in attr:
                ids = _ids_from(getattr(mod, attr, None))
                if ids:
                    return ids
        return []

    for modname in ('data.items', 'items'):
        try:
            import importlib
            ids = _scan(importlib.import_module(modname))
            if ids:
                return sorted(set(ids), key=str.lower)
        except Exception:
            continue
    return []


def _discover_weather_types():
    """Weather type ids, sourced the same way cutscene_editor.py's
    weather_type field does: filenames in assets/weather/ (one PNG per
    type — rain.png, snow.png, fog.png, ...). Falls back to the same
    hardcoded trio cutscene_editor.py uses if the folder is missing/empty,
    so the picker is never blank."""
    import os, glob
    try:
        files = sorted(glob.glob(os.path.join('assets', 'weather', '*.png')))
        pool = [os.path.splitext(os.path.basename(f))[0] for f in files]
    except Exception:
        pool = []
    return pool or ['rain', 'snow', 'fog']


def _discover_music_tracks():
    """Music track names, sourced by scanning assets/audio/music/ directly
    (no dependency on objects/music_object.py — that file can be deleted
    without affecting this): filename stems in assets/audio/music/,
    matching whatever extension SoundEngine's
    AudioAssetLoader recognizes (see MUSIC_EXTENSIONS in sound_engine.py —
    .ogg/.mp3/.wav/.it/.xm/.s3m/.mod). Scanned directly from disk rather
    than read off a live SoundEngine instance, since the event editor can
    be opened as a dev tool with no SoundEngine/pygame.mixer around to ask
    — same rationale as _discover_weather_types() above. Falls back to []
    (not a hardcoded list, since track names are project-specific) if the
    folder is missing/empty; the picker shows '(no music found)' in that
    case rather than silently looking populated."""
    import os
    MUSIC_EXTENSIONS = ('.ogg', '.mp3', '.wav', '.it', '.xm', '.s3m', '.mod')
    music_path = os.path.join('assets', 'audio', 'music')
    try:
        return sorted({
            os.path.splitext(f)[0]
            for f in os.listdir(music_path)
            if f.lower().endswith(MUSIC_EXTENSIONS)
        })
    except FileNotFoundError:
        return []


def _discover_sound_effects():
    """SFX names, sourced the same way SoundEngine's AudioAssetLoader
    populates sound_engine.sound_effects (see load_from_directory() in
    sound_engine.py): walks assets/audio/sfx/ recursively for .wav files,
    keyed by bare filename stem — category subfolders (combat/, misc/,
    ...) are just organization and don't affect the id, same as at load
    time. Scanned directly from disk rather than read off a live
    SoundEngine instance, for the same reason as _discover_music_tracks()
    above. Two files in different subfolders with the same stem collapse
    to one picker entry here, same as they'd collide in
    sound_engine.sound_effects at load time (AudioAssetLoader just prints
    a warning and keeps the last one loaded)."""
    import os
    sfx_path = os.path.join('assets', 'audio', 'sfx')
    names = set()
    for root, _dirs, filenames in os.walk(sfx_path):
        for filename in filenames:
            if filename.lower().endswith('.wav'):
                names.add(os.path.splitext(filename)[0])
    return sorted(names)


def _discover_portrait_ids():
    """Portrait ids, sourced from the character creator's own filesystem
    scan (assets/portraits/) — same rationale/fallback-import pattern as
    _discover_character_ids()/_discover_skill_ids() above. Used to populate
    the portrait picker on the dialogue_box ('portrait') and set_portrait
    ('portrait_id') action fields."""
    try:
        from dev_tools.character_creator import discover_portraits
        return discover_portraits()
    except Exception:
        pass
    try:
        from character_creator import discover_portraits
        return discover_portraits()
    except Exception:
        return []


def _discover_room_names():
    """Room name ids for the change_map 'room_name' picker, sourced from
    on-disk room files (assets/rooms/*.json) — same disk-fallback
    rationale/pattern as _discover_music_tracks() above, for standalone/
    headless use where there's no live RoomManager to ask. The live game
    normally overrides this with the real, current room list via
    ActionSequenceBuilder.set_known_rooms() (see there), since the
    on-disk rooms folder won't reflect transient/generated rooms — this
    disk scan is only what's shown before set_known_rooms() has ever
    been called."""
    import os, glob
    try:
        files = sorted(glob.glob(os.path.join('assets', 'rooms', '*.json')))
        return [os.path.splitext(os.path.basename(f))[0] for f in files]
    except Exception:
        return []


# Fallback (width, height) — in the same world units as Room.width/height —
# used by the Set Spawn preview for a room whose real dimensions haven't
# been supplied via set_known_rooms(). Arbitrary but room-sized, purely so
# the preview has *something* sane to scale against.
_ROOM_PICKER_DEFAULT_DIMS = (800, 600)


def _discover_world_map_names():
    """World map ids for the world_map_location 'map_name' picker, sourced
    from on-disk map files (assets/world_maps/*.json) — same pattern as
    _discover_room_names() above. These are authored/saved by
    dev_tools/world_map_editor.py."""
    import os, glob
    try:
        files = sorted(glob.glob(os.path.join('assets', 'world_maps', '*.json')))
        return [os.path.splitext(os.path.basename(f))[0] for f in files]
    except Exception:
        return []


def _discover_world_map_location_names(map_name):
    """Location-pin names already placed on a given world map (via
    dev_tools/world_map_editor.py's WMLocation), for the
    world_map_location 'name' picker. Scoped to whichever map is picked
    in that same row's 'map_name' field — see
    _wm_location_choices_for_row()."""
    import os, json
    if not map_name:
        return []
    try:
        path = os.path.join('assets', 'world_maps', '%s.json' % map_name)
        with open(path) as f:
            data = json.load(f)
        return [loc.get('name', '') for loc in data.get('locations', []) if loc.get('name')]
    except Exception:
        return []


# Runtime stat keys, matching Player.stats in player.py (not the
# character-creator's base-config "stats" block, which uses different
# names like max_hp/power — these are the ones conditions/actions
# actually read and mutate at runtime).
_STAT_KEYS = ['strength', 'ki_power', 'vitality', 'energy', 'speed', 'defense', 'ki_regen']


_CMP_OPTIONS = ['==', '!=', '<', '<=', '>', '>=']

_ROW_H = 34
_FIELD_H = 24
_FIELD_GAP = 6


def _placeholder_for(field_name, field_kind, kind=None):
    """Friendly empty-field placeholder. Picker kinds get a human 'select X'
    hint rather than leaking the raw internal param name (e.g. 'arg0') —
    only plain text/number fields fall back to that."""
    labels = {
        'flag_picker': '<select flag>',
        'char_picker': '<select character>',
        'skill_picker': '<select skill>',
        'transformation_picker': '<select transformation>',
        'skin_picker': '<select skin>',
        'animation_picker': '<select animation>',
        'portrait_picker': '<select portrait>',
        'weather_picker': '<select weather>',
        'music_picker': '<select track>',
        'sound_picker': '<select sound>',
        'room_picker': '<select room>',
        'world_map_picker': '<select world map>',
        'wm_location_picker': '<select location>',
        'enemy_picker': '<select enemy>',
        'boss_picker': '<select boss>',
        'npc_picker': '<select npc>',
        'cutscene_picker': '<select cutscene>',
        'item_picker': '<select item>',
        'timer_picker': '<select timer>',
        'bar_picker': '<select bar>',
    }
    if field_kind in labels:
        return labels[field_kind]
    # Field-name-specific hints for the generic 'text'/'number' params that
    # still use a raw internal name like 'arg0' — several condition kinds
    # reuse 'arg0' for different things (item id, timer id, ...), so key on
    # (kind, field_name) first and fall back to the field name alone.
    hints = {
        ('item', 'arg0'): '<item id>',
        ('timer', 'arg0'): '<timer id>',
    }
    if (kind, field_name) in hints:
        return hints[(kind, field_name)]
    return hints.get(field_name, '<%s>' % field_name)


def _coerce(text):
    """Best-effort turn a typed string into bool/int/float, else leave as str."""
    if text is None:
        return None
    low = text.strip().lower()
    if low in ('true', 'false'):
        return low == 'true'
    try:
        return int(text)
    except (TypeError, ValueError):
        pass
    try:
        return float(text)
    except (TypeError, ValueError):
        pass
    return text


# ─────────────────────────────────────────────────────────────────────────────
# Condition kind registry — one entry per row type the UI can build.
# fields: list of (param_name, field_kind, extra)
#   field_kind: 'flag_picker' | 'text' | 'number' | 'cmp' | 'choice' (extra=options list)
# build(params) -> condition dict (params values already coerced)
# unbuild(condition) -> params dict, or None if this condition doesn't match this kind
# ─────────────────────────────────────────────────────────────────────────────

def _unbuild_flag(cond):
    if cond.get('type') == 'flag':
        return {'flag_id': cond.get('id', '')}
    return None


def _unbuild_flag_not(cond):
    if cond.get('type') == 'not':
        child = cond.get('child') or {}
        if child.get('type') == 'flag':
            return {'flag_id': child.get('id', '')}
    return None


def _unbuild_variable(cond):
    if cond.get('type') == 'variable':
        return {'name': cond.get('name', ''), 'cmp': cond.get('cmp', '=='),
                'value': '' if cond.get('value') is None else str(cond.get('value'))}
    return None


def _unbuild_live(lookup_name, arg_index=None):
    def _unbuild(cond):
        if cond.get('type') != 'live' or cond.get('lookup') != lookup_name:
            return None
        args = cond.get('args') or []
        params = {'cmp': cond.get('cmp', '=='),
                   'value': '' if cond.get('value') is None else str(cond.get('value'))}
        if arg_index is not None:
            params['arg0'] = args[arg_index] if len(args) > arg_index else ''
        return params
    return _unbuild


def _unbuild_room_kills(cond):
    if cond.get('type') == 'variable' and cond.get('name') == 'room_kill_count':
        return {'cmp': cond.get('cmp', '=='),
                'value': '' if cond.get('value') is None else str(cond.get('value'))}
    return None


def _unbuild_boss_hp(cond):
    """boss_hp is built from one of two live lookups (percent vs. raw
    value — see check_boss_hp's `mode` param), so it needs its own
    unbuild rather than the generic _unbuild_live single-lookup-name
    helper, to round-trip the mode field correctly."""
    if cond.get('type') != 'live' or cond.get('lookup') not in ('boss_hp_lookup', 'boss_hp_value_lookup'):
        return None
    args = cond.get('args') or []
    return {
        'arg0': args[0] if args else '',
        'mode': 'percent' if cond.get('lookup') == 'boss_hp_lookup' else 'value',
        'cmp': cond.get('cmp', '=='),
        'value': '' if cond.get('value') is None else str(cond.get('value')),
    }


CONDITION_KINDS = {
    'flag': {
        'label': 'Flag Is Set',
        'fields': [('flag_id', 'flag_picker', None)],
        'build': lambda p: flag_is(p.get('flag_id', '')),
        'unbuild': _unbuild_flag,
    },
    'flag_not': {
        'label': 'Flag Is NOT Set',
        'fields': [('flag_id', 'flag_picker', None)],
        'build': lambda p: flag_is_not(p.get('flag_id', '')),
        'unbuild': _unbuild_flag_not,
    },
    'variable': {
        'label': 'Custom Variable',
        'fields': [('name', 'text', None), ('cmp', 'cmp', None), ('value', 'text', None)],
        'build': lambda p: variable_is(p.get('name', ''), p.get('cmp', '=='), _coerce(p.get('value'))),
        'unbuild': _unbuild_variable,
    },
    'item': {
        'label': 'Has Item',
        'fields': [('arg0', 'text', None), ('value', 'number', None)],
        'build': lambda p: check_item(p.get('arg0', ''), int(_coerce(p.get('value')) or 1)),
        'unbuild': _unbuild_live('player_has_item', 0),
    },
    'stat': {
        'label': 'Stat',
        'fields': [('arg0', 'choice', _STAT_KEYS), ('cmp', 'cmp', None), ('value', 'text', None)],
        'build': lambda p: check_stat(p.get('arg0', ''), p.get('cmp', '=='), _coerce(p.get('value'))),
        'unbuild': _unbuild_live('player_stat', 0),
    },
    'character': {
        'label': 'Current Character',
        'fields': [('arg0', 'char_picker', None)],
        'build': lambda p: check_character(p.get('arg0', '')),
        'unbuild': _unbuild_live('player_character'),
    },
    'zeni': {
        'label': 'Zeni',
        'fields': [('cmp', 'cmp', None), ('value', 'text', None)],
        'build': lambda p: check_zeni(p.get('cmp', '=='), _coerce(p.get('value'))),
        'unbuild': _unbuild_live('player_zeni'),
    },
    'resource': {
        'label': 'Resource',
        'fields': [('arg0', 'choice', ['health', 'energy', 'transformation_gauge']),
                   ('cmp', 'cmp', None), ('value', 'text', None)],
        'build': lambda p: check_resource(p.get('arg0', 'health'), p.get('cmp', '=='), _coerce(p.get('value'))),
        'unbuild': _unbuild_live('player_resource', 0),
    },
    'skill': {
        'label': 'Has Skill',
        'fields': [('arg0', 'skill_picker', None)],
        'build': lambda p: check_skill(p.get('arg0', '')),
        'unbuild': _unbuild_live('player_has_skill', 0),
    },
    'timer': {
        'label': 'Timer',
        'fields': [('arg0', 'timer_picker', None), ('cmp', 'cmp', None), ('value', 'text', None)],
        'build': lambda p: check_timer(p.get('arg0', ''), p.get('cmp', '=='), _coerce(p.get('value'))),
        'unbuild': _unbuild_live('player_timer_remaining', 0),
    },
    'bar': {
        'label': 'Spam/Timing Bar',
        'fields': [('arg0', 'bar_picker', None), ('cmp', 'cmp', None), ('value', 'text', None)],
        'build': lambda p: check_bar(p.get('arg0', ''), p.get('cmp', '=='), _coerce(p.get('value'))),
        'unbuild': _unbuild_live('player_bar_percent', 0),
    },
    'room_kills': {
        'label': 'Enemies Killed (this room)',
        'fields': [('cmp', 'cmp', None), ('value', 'text', None)],
        'build': lambda p: check_room_kills(p.get('cmp', '>='), _coerce(p.get('value'))),
        'unbuild': _unbuild_room_kills,
    },
    'boss_hp': {
        'label': 'Boss HP',
        'fields': [('arg0', 'boss_picker', None), ('mode', 'choice', ['percent', 'value']),
                   ('cmp', 'cmp', None), ('value', 'text', None)],
        'build': lambda p: check_boss_hp(p.get('arg0', ''), p.get('cmp', '=='),
                                          _coerce(p.get('value')), mode=p.get('mode', 'percent')),
        'unbuild': _unbuild_boss_hp,
    },
}

_KIND_ORDER = ['flag', 'flag_not', 'variable', 'item', 'stat', 'character', 'zeni', 'resource', 'skill', 'timer', 'bar', 'room_kills', 'boss_hp']


ACTION_SCHEMA = {
    'dialogue_box': [('speaker_type', 'choice', ['character', 'narrator', 'info']),
                      ('speaker_name', 'text', None), ('text', 'text', None),
                      ('portrait', 'portrait_picker', None)],
    'set_portrait': [('character_name', 'text', None), ('portrait_id', 'portrait_picker', None)],
    'dialogue_choice': [('prompt', 'text', None)],
    # Advanced branching action. Its UI is handled by the dedicated branch
    # editor below rather than ordinary row fields.
    'conditional': [],
    'timer_start': [('timer_id', 'text', None), ('duration', 'number', None)],
    'timer_pause': [('timer_id', 'text', None)],
    'timer_stop': [('timer_id', 'text', None)],
    'zeni': [('mode', 'choice', ['set', 'add', 'remove']), ('amount', 'number', None)],
    'item': [('mode', 'choice', ['add', 'remove']), ('item_id', 'item_picker', None), ('quantity', 'number', None)],
    'level': [('mode', 'choice', ['set', 'add', 'remove']), ('amount', 'number', None), ('character_id', 'char_picker', None)],
    'exp': [('mode', 'choice', ['set', 'add', 'remove']), ('amount', 'number', None), ('character_id', 'char_picker', None)],
    'stat': [('mode', 'choice', ['set', 'add', 'remove']), ('stat_name', 'choice', _STAT_KEYS),
             ('amount', 'number', None), ('character_id', 'char_picker', None)],
    'resource': [('mode', 'choice', ['set', 'add', 'remove']),
                 ('resource_name', 'choice', ['health', 'energy', 'transformation_gauge']),
                 ('amount', 'number', None)],
    'skill': [('mode', 'choice', ['add', 'remove']), ('skill_id', 'skill_picker', None)],
    'transformation': [('mode', 'choice', ['add', 'remove']), ('form_id', 'transformation_picker', None)],
    'charged_melee': [('mode', 'choice', ['add', 'remove'])],
    'set_player_character': [('character_id', 'char_picker', None), ('skin_id', 'skin_picker', None)],
    'set_player_skin': [('skin_id', 'skin_picker', None)],
    'character_list': [('mode', 'choice', ['add', 'remove']), ('character_id', 'char_picker', None)],
    'screen_fade': [('direction', 'choice', ['in', 'out']), ('duration', 'number', None)],
    'screen_shake': [('intensity', 'number', None), ('duration', 'number', None)],
    'spam_qte': [('qte_id', 'text', None), ('mode', 'choice', ['spam', 'timing']),
                 ('fill_per_press', 'number', None), ('drain_rate', 'number', None),
                 ('start_progress', 'number', None),
                 # timing-mode only (ignored in spam mode):
                 ('sweep_speed', 'number', None), ('max_attempts', 'number', None),
                 ('zones', 'json', None)],
    'weather': [('mode', 'choice', ['set', 'stop']), ('weather_type', 'weather_picker', None)],
    'room_music': [('mode', 'choice', ['set', 'stop']), ('track', 'music_picker', None)],
    'play_sound': [('sound_id', 'sound_picker', None)],
    'play_character_animation': [('character_id', 'char_picker', None), ('animation_id', 'animation_picker', None),
                                  ('wait', 'bool', None)],
    'save_game': [('save_id', 'text', None)],
    # spawn_x is the schema-registered half of the spawn point (field_kind
    # 'spawn_picker' draws it as a "Set Spawn" button, not a text box); its
    # companion spawn_y rides along in the same row's params without its
    # own schema entry — see the 'change_map' special-casing in
    # _defaults_for()/refresh()/get_action_list() below.
    'change_map': [('room_name', 'room_picker', None), ('spawn_x', 'spawn_picker', None),
                    ('wait', 'bool', None)],
    'set_player_location': [('x', 'position_picker', None)],
    # y rides along with x (the schema-registered 'position_picker' field)
    # without its own schema entry — same pattern as change_map's spawn_y
    # above, just always targeting the current room instead of a chosen one.
    # One enemy per action (same shape as spawn_npc below, minus
    # 'animation') — spawn a wave by adding several spawn_enemies rows.
    # enemy_id is a dropdown (field_kind 'enemy_picker') sourced from the
    # entity editor's own catalogue via _discover_enemy_ids(), so it can
    # only ever point at an enemy that actually exists.
    'spawn_enemies': [('enemy_id', 'enemy_picker', None), ('x', 'number', None), ('y', 'number', None)],
    'spawn_npc': [('npc_id', 'npc_picker', None), ('x', 'number', None), ('y', 'number', None), ('animation', 'text', None)],
    'play_cutscene': [('cutscene_id', 'cutscene_picker', None)],
    'quest': [('mode', 'choice', ['add', 'remove']), ('quest_id', 'text', None)],
    'modify_quest_variable': [('quest_id', 'text', None), ('variable_name', 'text', None),
                               ('mode', 'choice', ['set', 'add', 'remove']), ('value', 'text', None)],
    'set_custom_variable': [('var_name', 'text', None), ('mode', 'choice', ['set', 'add', 'remove']),
                             ('value', 'text', None)],
    'world_map_location': [('mode', 'choice', ['add', 'remove']), ('map_name', 'world_map_picker', None),
                            ('name', 'wm_location_picker', None)],
    # pad_id is the label shown on the pad itself in the dev-mode path
    # preview overlay (objects/flying_pad.py auto-assigns one, like
    # 'room_1_pad_1', when the pad is placed) — plain text field, same
    # pattern as timer_id above, since pads aren't tied to a fixed catalogue
    # the way skills/npcs/enemies are.
    'mission': [('mode', 'choice', ['start', 'complete', 'fail', 'reset']), ('mission_id', 'text', None)],
    'toggle_flying_pad': [('pad_id', 'text', None), ('mode', 'choice', ['enable', 'disable'])],
    # Show / hide / despawn an entity that already exists in the room (typically
    # fired by a Trigger Box). 'show' / 'hide' only toggle visibility + interaction
    # (the entity stays in the room and can be shown again); 'despawn' removes it
    # from the room for good. Ids come from the entity editor's catalogue pickers.
    'npc_state': [('npc_id', 'npc_picker', None), ('mode', 'choice', ['show', 'hide', 'despawn'])],
    'enemy_state': [('enemy_id', 'enemy_picker', None), ('mode', 'choice', ['show', 'hide', 'despawn'])],
    'boss_state': [('boss_id', 'boss_picker', None), ('mode', 'choice', ['show', 'hide', 'despawn'])],
}

# For action types whose schema pairs a 'set'/'stop' mode choice with a
# picker field (weather_type, track, ...), 'stop' means stop — it doesn't
# target anything specific, so the picker field is meaningless once 'stop'
# is selected. This maps action_type -> the field name to hide from the
# editor UI while mode == 'stop'. See _row_visible_fields() below.
_STOP_MODE_HIDES_FIELD = {
    'weather': 'weather_type',
    'room_music': 'track',
}


def _row_visible_fields(row_type, row_params, schema):
    """Schema fields to actually draw/click for a row, filtering out the
    mode-irrelevant picker field (see _STOP_MODE_HIDES_FIELD) when the row's
    mode is currently 'stop'."""
    hidden_field = _STOP_MODE_HIDES_FIELD.get(row_type)
    if hidden_field is not None and row_params.get('mode') == 'stop':
        return [f for f in schema if f[0] != hidden_field]
    return schema

# Default width per field kind, so long text fields (dialogue text, JSON) get
# more room than a mode toggle.
_FIELD_WIDTH = {'text': 110, 'number': 70, 'bool': 50, 'choice': 90, 'json': 160,
                 'room_picker': 130, 'spawn_picker': 150, 'position_picker': 150,
                 'world_map_picker': 130, 'wm_location_picker': 130}
_WIDE_FIELDS = {'text', 'json'}


def _coerce_number(text):
    try:
        if '.' in text:
            return float(text)
        return int(text)
    except (TypeError, ValueError):
        try:
            return float(text)
        except (TypeError, ValueError):
            return 0


def _clone_options(options):
    """Deep-copy a dialogue_choice options list ([{'text':str,'actions':[...]}])
    so editor rows never end up aliasing the same nested action lists."""
    return [{'text': o.get('text', ''), 'actions': copy.deepcopy(o.get('actions') or [])}
            for o in (options or [])]


def _clone_branches(branches):
    """Deep-copy conditional branches so nested editor state never aliases saved data."""
    result = []
    for b in (branches or []):
        result.append({
            'is_else': bool(b.get('is_else', False)),
            'conditions': copy.deepcopy(b.get('conditions') or []),
            'actions': copy.deepcopy(b.get('actions') or []),
        })
    return result


class _NullFlagManager:
    def __init__(self):
        self.flags = {}
        self.variables = {}
    def get_condition_names(self):
        return {}
    def evaluate_conditions(self, conditions, player=None):
        return False





# ═════════════════════════════════════════════════════════════════════════
# Data models — rows + discovery data, no drawing, no input
# ═════════════════════════════════════════════════════════════════════════

class _RowListMixin:
    """Row-list housekeeping shared by the condition and action models."""

    def _remove_row(self, index):
        if 0 <= index < len(self.rows):
            self.rows.pop(index)

    def _move_row(self, index, delta):
        self._move_row_to(index, index + delta)

    def _move_row_to(self, index, target):
        if 0 <= index < len(self.rows) and 0 <= target < len(self.rows) and index != target:
            self.rows.insert(target, self.rows.pop(index))

    def _duplicate_row(self, index):
        if 0 <= index < len(self.rows):
            self.rows.insert(index + 1, copy.deepcopy(self.rows[index]))


class ConditionBuilder(_RowListMixin):
    """Rows of a flat, implicitly-ANDed condition list (+ the picker data)."""

    def __init__(self, flag_manager, colors=None):
        self.flag_manager = flag_manager
        self.rows = []          # list of {'kind': str, 'params': {field_name: str}}
        self._known_flags = []
        self._known_characters = []
        self._known_skills = []
        self._known_bosses = []
        self._known_timers = []
        self._known_bars = []

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def inherit(self, other):
        """Borrow another builder's already-scanned picker lists (used by the
        nested branch editors so they don't rescan the disk)."""
        self._known_flags = list(other._known_flags)
        self._known_characters = list(other._known_characters)
        self._known_skills = list(other._known_skills)
        self._known_bosses = list(other._known_bosses)
        self._known_timers = list(other._known_timers)
        self._known_bars = list(other._known_bars)

    def refresh(self, existing_conditions=None, parent=None):
        """Refresh the picker lists and, if given, rebuild the rows from a
        previously-saved condition list so editing round-trips."""
        if parent is not None:
            self.inherit(parent)
        else:
            self._known_flags = sorted(getattr(self.flag_manager, 'flags', {}).keys())
            self._known_characters = _discover_character_ids()
            self._known_skills = _discover_skill_ids()
            self._known_bosses = _discover_boss_ids()
            try:
                names = self.flag_manager.get_condition_names() or {}
            except Exception:
                names = {}
            self._known_timers = sorted(names.get('timer_names', []))
            self._known_bars = sorted(names.get('bar_names', []))

        if existing_conditions is None:
            return

        rows = []
        for cond in existing_conditions:
            matched = False
            # room_kills is a specially-named 'variable' condition, so it has to
            # be tried before the generic Custom Variable kind or it would
            # always reload as one.
            for kind in ('room_kills',) + tuple(k for k in _KIND_ORDER if k != 'room_kills'):
                params = CONDITION_KINDS[kind]['unbuild'](cond)
                if params is not None:
                    rows.append({'kind': kind, 'params': {k: str(v) for k, v in params.items()}})
                    matched = True
                    break
            if not matched:
                # Unknown/unsupported condition shape — keep it as an opaque
                # passthrough row so re-saving doesn't silently drop it.
                rows.append({'kind': '__raw__', 'params': {}, '_raw': cond})
        self.rows = rows

    def get_condition_list(self):
        """Build the actual condition dicts from current row state."""
        result = []
        for row in self.rows:
            if row['kind'] == '__raw__':
                result.append(row.get('_raw'))
                continue
            spec = CONDITION_KINDS.get(row['kind'])
            if spec is None:
                continue
            try:
                result.append(spec['build'](row['params']))
            except Exception:
                continue  # malformed row — skip rather than crash
        return result

    # ── Row management ──────────────────────────────────────────────────────

    @staticmethod
    def _defaults_for(kind):
        defaults = {}
        for field_name, field_kind, extra in CONDITION_KINDS[kind]['fields']:
            if field_kind == 'cmp':
                defaults[field_name] = '=='
            elif field_kind == 'choice':
                defaults[field_name] = extra[0]
            else:
                defaults[field_name] = ''
        return defaults

    def _add_row(self, kind='flag'):
        if kind in CONDITION_KINDS:
            self.rows.append({'kind': kind, 'params': self._defaults_for(kind)})

    def _set_row_kind(self, index, kind):
        if 0 <= index < len(self.rows) and kind in CONDITION_KINDS:
            self.rows[index] = {'kind': kind, 'params': self._defaults_for(kind)}

    # ── Picker data ─────────────────────────────────────────────────────────

    def choices(self, field_kind):
        """(names, empty-placeholder) for a picker field kind."""
        table = {
            'flag_picker': (self._known_flags, '(no flags used yet)'),
            'char_picker': (self._known_characters, '(no characters found)'),
            'skill_picker': (self._known_skills, '(no skills found)'),
            'boss_picker': (self._known_bosses, '(no bosses found)'),
            'timer_picker': (self._known_timers, '(no timers made yet)'),
            'bar_picker': (self._known_bars, '(no bars made yet)'),
        }
        return table.get(field_kind, ([], ''))




_PICKER_KINDS = (
    'portrait_picker', 'char_picker', 'enemy_picker', 'npc_picker', 'item_picker',
    'cutscene_picker', 'skill_picker', 'transformation_picker', 'skin_picker',
    'animation_picker', 'weather_picker', 'music_picker', 'sound_picker',
    'room_picker', 'world_map_picker', 'wm_location_picker',
    # condition-side pickers
    'flag_picker', 'boss_picker', 'timer_picker', 'bar_picker',
)


def _new_branch(is_else=False):
    return {'is_else': is_else, 'conditions': [], 'actions': []}


class ActionSequenceBuilder(_RowListMixin):
    """Rows of an ordered action list (+ the picker data and the live-game
    context the host pushes in)."""

    def __init__(self, colors=None):
        self.rows = []   # list of {'type': action_type, 'params': {field_name: str}}
                         # dialogue_choice rows also carry '_options':
                         #   [{'text': str, 'actions': [...]}]
                         # conditional rows carry '_branches':
                         #   [{'is_else': bool, 'conditions': [...], 'actions': [...]}]
        self._known_portraits = []
        self._known_characters = []
        self._known_enemies = []
        self._known_bosses = []
        self._known_npcs = []
        self._known_cutscenes = []
        self._known_items = []
        self._known_skills = []
        self._known_weather_types = []
        self._known_music_tracks = []
        self._known_sound_effects = []
        self._known_rooms = []
        self._known_world_maps = []
        # Which character's equipped-skill list backs the skill picker's
        # add/remove options — set via set_current_character().
        self._current_character_id = None
        # Optional callables returning LIVE state (see set_current_character()).
        self._get_equipped_skills = None
        self._get_unlocked_transformations = None
        # Room the set_player_location position picker previews/places against.
        self._current_room_name = None
        # room_name -> (width, height) in world units (set_known_rooms()).
        self._known_room_dims = {}
        # Optional callable room_name -> pygame.Surface | None
        # (set_room_preview_provider()).
        self._room_preview_provider = None
        self._condition_flag_manager = None

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def set_current_character(self, character_id, get_equipped_skills=None,
                               get_unlocked_transformations=None):
        """Tell this builder which character's equipped-skill list should
        back the skill_id picker's 'add'/'remove' options — see
        _current_character_id/_get_equipped_skills in __init__ and
        _skill_choices_for_row() below. Call this (e.g. with
        self.player.character and lambda: self.player.equipped_attacks)
        whenever the host knows who it's authoring for; safe to call every
        frame the event editor is open, not just once, since equipped
        skills can change live while it's open (e.g. from a triggered
        event testing itself).

        get_unlocked_transformations is the same idea for the
        transformation_picker — pass e.g. lambda: self.player.
        unlocked_transformations so 'remove' rows only offer forms
        actually unlocked right now, same rationale as
        get_equipped_skills. See _transformation_choices_for_row()."""
        self._current_character_id = character_id
        self._get_equipped_skills = get_equipped_skills
        self._get_unlocked_transformations = get_unlocked_transformations

    def set_current_room(self, room_name):
        """Tell this builder which room set_player_location's 'x' position
        picker should target — see _current_room_name in __init__. Call
        this (e.g. with the name of the room the trigger box/event being
        edited actually lives in) whenever the host knows it; safe to call
        every frame the event editor is open, same as
        set_current_character()."""
        self._current_room_name = room_name

    def set_known_rooms(self, room_names, room_dims=None):
        """Tell this builder which rooms actually exist right now — the
        live-data equivalent of _discover_room_names() above, same
        rationale/pattern as set_current_character(). Populates the
        change_map 'room_name' dropdown and, via room_dims, the Set Spawn
        preview's canvas size.

        room_dims is optional: {room_name: (width, height)} in the same
        world units as Room.width/height. Rooms missing from it still show
        up in the dropdown but fall back to _ROOM_PICKER_DEFAULT_DIMS in
        the Set Spawn preview (with a note that the size is a guess).
        Safe to call every frame the event editor is open, same as
        set_current_character() — e.g. from the room manager's live room
        list, so a room created/renamed while the editor is open shows up
        without needing to reopen it."""
        self._known_rooms = list(room_names or [])
        if room_dims:
            self._known_room_dims.update(room_dims)

    def set_room_preview_provider(self, provider):
        """Give the Set Spawn overlay a way to render an actual room
        preview (tiles, not just a blank grid) — provider is a callable
        room_name -> pygame.Surface (sized to that room's full world
        extent at RENDER_SCALE) or None if the room has no tiles/doesn't
        exist. This builder has no tile-rendering code of its own (and
        shouldn't — that's TilesetEditor's job), so the host wires this up
        with something like:

            def _room_preview(room_name):
                room = room_manager.get_room_by_name(room_name)
                if room is None:
                    return None
                return tileset_editor.render_room_to_surface(room)

        and calls set_room_preview_provider(_room_preview) once, same
        pattern as set_known_rooms(). Safe to leave unset — the overlay
        just falls back to its plain grid rectangle."""
        self._room_preview_provider = provider

    def _skill_choices_for_row(self, row_index):
        """Return (names, placeholder) for the skill_id picker on a given
        row, scoped to that row's 'mode':
          - 'remove' → only skills the current character actually has
            equipped (so you can't pick one to remove that was never
            equipped).
          - 'add'    → only skills from the global roster that the current
            character does NOT already have (so picking one can't
            silently no-op because it's already equipped — that no-op is
            what made it look like "adding a skill does nothing").
        Both modes need to know the current character to filter correctly;
        if none is set, that's surfaced via the placeholder rather than
        silently falling back to the unfiltered global list. Prefers the
        live get_equipped_skills() callback over _discover_equipped_skills()
        (which reads the on-disk character-creator config) since that
        on-disk data doesn't reflect skills a runtime 'skill' action has
        already granted/removed this session.
        """
        row = self.rows[row_index] if 0 <= row_index < len(self.rows) else None
        mode = row['params'].get('mode') if row else None

        if not self._current_character_id:
            return [], '(no character set)'

        if self._get_equipped_skills is not None:
            equipped = list(self._get_equipped_skills() or [])
        else:
            equipped = _discover_equipped_skills(self._current_character_id)

        if mode == 'remove':
            return equipped, '(no skills equipped)'

        # mode == 'add' (or unset/default)
        available = [s for s in self._known_skills if s not in equipped]
        return available, '(all skills equipped)'

    def _transformation_choices_for_row(self, row_index):
        """Return (names, placeholder) for the transformation form_id
        picker on a given row, scoped to that row's 'mode' — same shape
        as _skill_choices_for_row() above, but forms are per-character
        (like costumes) rather than a shared global roster (like skills),
        so both branches start from _discover_transformation_ids(current
        character) instead of a cached self._known_* list:
          - 'remove' → only forms currently unlocked for
            self._current_character_id (prefers the live
            get_unlocked_transformations() callback over "every
            configured form", since a runtime 'transformation' action's
            grants/revokes are never written back to the character
            creator's saved config — same rationale as
            _get_equipped_skills. Falls back to treating every configured
            form as unlocked when no callback is set, matching
            game.py's default-fully-unlocked behavior).
          - 'add'    → configured forms the character does NOT already
            have unlocked (so picking one can't silently no-op).
        Needs the current character to filter correctly; if none is set,
        that's surfaced via the placeholder rather than silently falling
        back to an unscoped list (there isn't one to fall back to)."""
        row = self.rows[row_index] if 0 <= row_index < len(self.rows) else None
        mode = row['params'].get('mode') if row else None

        if not self._current_character_id:
            return [], '(no character set)'

        configured = _discover_transformation_ids(self._current_character_id)

        if self._get_unlocked_transformations is not None:
            unlocked = list(self._get_unlocked_transformations() or [])
        else:
            unlocked = configured

        if mode == 'remove':
            return unlocked, '(no transformations unlocked)'

        # mode == 'add' (or unset/default)
        available = [f for f in configured if f not in unlocked]
        return available, '(all transformations unlocked)'

    def _costume_choices_for_row(self, row_index):
        """Return (names, placeholder) for the skin_id picker on a given
        row. Costumes are per-character, so unlike the skill picker there's
        no single global roster to filter down — the picker needs to know
        WHICH character's costumes to list, and that differs by action:
          - 'set_player_character' rows pick a skin for whatever character
            was just chosen in that same row's own 'character_id' field
            (the character being switched TO), not the one currently played.
          - 'set_player_skin' rows have no character_id field of their own
            — they change the currently-played character's costume, so they
            fall back to self._current_character_id (see
            set_current_character()).
        """
        row = self.rows[row_index] if 0 <= row_index < len(self.rows) else None
        params = row['params'] if row else {}

        if 'character_id' in params:
            # set_player_character row — scoped to whatever's picked in
            # this row's own character_id field, regardless of who's
            # currently played.
            target_character_id = params.get('character_id')
            no_target_placeholder = '(pick a character first)'
        else:
            # set_player_skin (or anything else without its own
            # character_id field) — scoped to whoever's currently played.
            target_character_id = self._current_character_id
            no_target_placeholder = '(no character set)'

        if not target_character_id:
            return [], no_target_placeholder

        costumes = _discover_costume_ids(target_character_id)
        return costumes, '(no skins found)'

    def _animation_choices_for_row(self, row_index):
        """Return (names, placeholder) for the animation_id picker on a
        play_character_animation row. Unlike the skin picker, this action
        always carries its own 'character_id' field (there's no
        "currently played character" fallback), so the animation list is
        always scoped to whatever's picked in that same row."""
        row = self.rows[row_index] if 0 <= row_index < len(self.rows) else None
        character_id = row['params'].get('character_id') if row else None

        if not character_id:
            return [], '(pick a character first)'

        animations = _discover_animation_ids(character_id)
        return animations, '(no animations found)'

    def _wm_location_choices_for_row(self, row_index):
        """Return (names, placeholder) for the world_map_location 'name'
        picker on a given row — the list of pins already placed on
        whichever map is picked in that same row's 'map_name' field (via
        the World Map Editor). Scoped the same way _animation_choices_for_row()
        scopes to a row's own character_id: nothing to show until the
        row's own map_name is set."""
        row = self.rows[row_index] if 0 <= row_index < len(self.rows) else None
        map_name = row['params'].get('map_name') if row else None

        if not map_name:
            return [], '(pick a map first)'

        names = _discover_world_map_location_names(map_name)
        return names, '(no locations found on this map)'


    def inherit(self, other):
        """Borrow another builder's scanned picker lists and live-game context
        (used by the nested option / branch editors, so a nested Set Position
        or skill picker still knows the current room / character)."""
        for name in ('_known_portraits', '_known_characters', '_known_enemies', '_known_npcs',
                     '_known_bosses', '_known_cutscenes', '_known_items', '_known_skills', '_known_weather_types',
                     '_known_music_tracks', '_known_sound_effects', '_known_rooms',
                     '_known_world_maps'):
            setattr(self, name, list(getattr(other, name)))
        self._current_character_id = other._current_character_id
        self._get_equipped_skills = other._get_equipped_skills
        self._get_unlocked_transformations = other._get_unlocked_transformations
        self._current_room_name = other._current_room_name
        self._known_room_dims = dict(other._known_room_dims)
        self._room_preview_provider = other._room_preview_provider
        self._condition_flag_manager = other._condition_flag_manager

    def refresh(self, existing_actions=None, parent=None):
        if parent is not None:
            self.inherit(parent)
        else:
            self._known_portraits = _discover_portrait_ids()
            self._known_characters = _discover_character_ids()
            self._known_enemies = _discover_enemy_ids()
            self._known_bosses = _discover_boss_ids()
            self._known_npcs = _discover_npc_ids()
            self._known_cutscenes = _discover_cutscene_ids()
            self._known_items = _discover_item_ids()
            self._known_skills = _discover_skill_ids()
            self._known_weather_types = _discover_weather_types()
            self._known_music_tracks = _discover_music_tracks()
            self._known_sound_effects = _discover_sound_effects()
            # _known_rooms' real source of truth is the live push from
            # set_known_rooms(); refresh() runs after the host's sync call, so
            # only fall back to the disk scan if nothing was ever pushed.
            if not self._known_rooms:
                self._known_rooms = _discover_room_names()
            self._known_world_maps = _discover_world_map_names()

        if existing_actions is None:
            return
        self.rows = self._rows_from_actions(existing_actions)

    @staticmethod
    def _rows_from_actions(existing_actions):
        rows = []
        for action in existing_actions:
            action_type = action.get('type')
            schema = ACTION_SCHEMA.get(action_type)
            if schema is None:
                rows.append({'type': '__raw__', 'params': {}, '_raw': action})
                continue
            params = {}
            for field_name, field_kind, extra in schema:
                value = action.get(field_name)
                if field_kind == 'json':
                    params[field_name] = json.dumps(value) if value is not None else ''
                elif field_kind == 'bool':
                    params[field_name] = 'true' if value else 'false'
                else:
                    params[field_name] = '' if value is None else str(value)
            row = {'type': action_type, 'params': params}
            if action_type == 'change_map':
                # spawn_y rides along with spawn_x (the schema-registered
                # 'spawn_picker' field) but has no schema entry of its own.
                raw_spawn_y = action.get('spawn_y')
                params['spawn_y'] = '' if raw_spawn_y is None else str(raw_spawn_y)
            if action_type == 'set_player_location':
                raw_y = action.get('y')
                params['y'] = '' if raw_y is None else str(raw_y)
            if action_type == 'dialogue_choice':
                row['_options'] = _clone_options(action.get('options'))
            if action_type == 'conditional':
                row['_branches'] = _clone_branches(action.get('branches'))
            rows.append(row)
        return rows

    def get_action_list(self):
        result = []
        for row in self.rows:
            if row['type'] == '__raw__':
                result.append(row.get('_raw'))
                continue
            if row['type'] == 'conditional':
                result.append({'type': 'conditional', 'branches': _clone_branches(row.get('_branches'))})
                continue
            schema = ACTION_SCHEMA.get(row['type'])
            if schema is None:
                continue
            action = {'type': row['type']}
            hidden_field = _STOP_MODE_HIDES_FIELD.get(row['type'])
            is_stop_mode = hidden_field is not None and row['params'].get('mode') == 'stop'
            try:
                for field_name, field_kind, extra in schema:
                    if is_stop_mode and field_name == hidden_field:
                        action[field_name] = None
                        continue
                    raw = row['params'].get(field_name, '')
                    if field_kind in ('number', 'spawn_picker', 'position_picker'):
                        action[field_name] = _coerce_number(raw)
                    elif field_kind == 'bool':
                        action[field_name] = raw.strip().lower() == 'true'
                    elif field_kind == 'json':
                        action[field_name] = json.loads(raw) if raw.strip() else None
                    else:
                        action[field_name] = raw
                if row['type'] == 'change_map':
                    action['spawn_y'] = _coerce_number(row['params'].get('spawn_y', ''))
                if row['type'] == 'set_player_location':
                    action['y'] = _coerce_number(row['params'].get('y', ''))
                if row['type'] == 'dialogue_choice':
                    action['options'] = _clone_options(row.get('_options'))
                result.append(action)
            except Exception:
                continue  # malformed row (usually bad JSON) — skip rather than crash
        return result

    # ── Picker data ─────────────────────────────────────────────────────────

    def choices(self, row_index, field_kind):
        """(names, empty-placeholder) for a picker field kind on a given row."""
        simple = {
            'portrait_picker': (self._known_portraits, '(no portraits found)'),
            'char_picker': (self._known_characters, '(no characters found)'),
            'enemy_picker': (self._known_enemies, '(no enemies found)'),
            'boss_picker': (self._known_bosses, '(no bosses found)'),
            'npc_picker': (self._known_npcs, '(no npcs found)'),
            'item_picker': (self._known_items, '(no items found)'),
            'cutscene_picker': (self._known_cutscenes, '(no cutscenes found)'),
            'weather_picker': (self._known_weather_types, '(no weather found)'),
            'music_picker': (self._known_music_tracks, '(no music found)'),
            'sound_picker': (self._known_sound_effects, '(no sfx found)'),
            'room_picker': (self._known_rooms, '(no rooms found)'),
            'world_map_picker': (self._known_world_maps, '(no world maps found)'),
        }
        if field_kind in simple:
            return simple[field_kind]
        if field_kind == 'skill_picker':
            return self._skill_choices_for_row(row_index)
        if field_kind == 'transformation_picker':
            return self._transformation_choices_for_row(row_index)
        if field_kind == 'skin_picker':
            return self._costume_choices_for_row(row_index)
        if field_kind == 'animation_picker':
            return self._animation_choices_for_row(row_index)
        if field_kind == 'wm_location_picker':
            return self._wm_location_choices_for_row(row_index)
        return [], ''

    # ── Row management ──────────────────────────────────────────────────────

    def _new_row(self, action_type):
        row = {'type': action_type, 'params': self._defaults_for(action_type)}
        if action_type == 'dialogue_choice':
            row['_options'] = []
        if action_type == 'conditional':
            row['_branches'] = [_new_branch(False), _new_branch(True)]
        return row

    def _add_row(self, action_type='dialogue_box'):
        self.rows.append(self._new_row(action_type))

    def _defaults_for(self, action_type):
        defaults = {}
        for field_name, field_kind, extra in ACTION_SCHEMA.get(action_type, []):
            if field_kind == 'choice':
                defaults[field_name] = extra[0]
            elif field_kind == 'bool':
                defaults[field_name] = 'false'
            else:
                defaults[field_name] = ''
        if action_type == 'change_map':
            defaults['spawn_y'] = ''  # companion to spawn_x's 'spawn_picker' field
        if action_type == 'set_player_location':
            defaults['y'] = ''  # companion to x's 'position_picker' field
        return defaults

    def _set_row_type(self, index, action_type):
        if 0 <= index < len(self.rows):
            self.rows[index] = self._new_row(action_type)

    # ── conditional branch management ───────────────────────────────────────

    def _add_conditional_branch(self, row_index, is_else=False):
        if 0 <= row_index < len(self.rows) and self.rows[row_index]['type'] == 'conditional':
            branches = self.rows[row_index].setdefault('_branches', [])
            if is_else:
                if any(b.get('is_else') for b in branches):
                    return
                branches.append(_new_branch(True))
            else:
                else_index = next((i for i, b in enumerate(branches) if b.get('is_else')), len(branches))
                branches.insert(else_index, _new_branch(False))

    def _remove_conditional_branch(self, row_index, branch_index):
        if 0 <= row_index < len(self.rows) and self.rows[row_index]['type'] == 'conditional':
            branches = self.rows[row_index].get('_branches', [])
            if 0 <= branch_index < len(branches):
                branches.pop(branch_index)
                if not branches:
                    branches.append(_new_branch(False))

    # ── dialogue_choice option management ────────────────────────────────────

    def _add_option(self, row_index):
        if 0 <= row_index < len(self.rows):
            self.rows[row_index].setdefault('_options', []).append({'text': '', 'actions': []})

    def _remove_option(self, row_index, option_index):
        if 0 <= row_index < len(self.rows):
            options = self.rows[row_index].get('_options', [])
            if 0 <= option_index < len(options):
                options.pop(option_index)

    def _move_option(self, row_index, option_index, delta):
        if 0 <= row_index < len(self.rows):
            options = self.rows[row_index].get('_options', [])
            new_index = option_index + delta
            if 0 <= option_index < len(options) and 0 <= new_index < len(options):
                options[option_index], options[new_index] = options[new_index], options[option_index]



# ═════════════════════════════════════════════════════════════════════════
# UI layer — built on dev_tools.ui_kit (same family as the Character Creator)
# ═════════════════════════════════════════════════════════════════════════

_T = uk.Theme

# Same flat two-tone backdrop / bar colours DevMenu, RoomEditor and the
# Character Creator use.
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

COND_ACCENT = _T.KI_BLUE      # conditions are "ki blue"
ACT_ACCENT = _T.GOLD          # actions are gold

_PAD = 14                     # card padding
_FIELD_GAP = 12
_ROW_GAP = 10
_STRIPE = 6                   # room left of the card content for the accent stripe
_UNDO_LIMIT = 60

_CMP_LABELS = {'==': 'equals', '!=': 'not equal', '<': 'less than',
               '<=': 'at most', '>': 'greater than', '>=': 'at least'}

_ACRONYMS = {'qte': 'QTE', 'id': 'ID', 'npc': 'NPC', 'hp': 'HP', 'exp': 'EXP', 'xp': 'XP',
             'sfx': 'SFX', 'ui': 'UI'}

# (kind, field) -> label for the small caps caption above a field.
_FIELD_LABELS = {
    ('flag', 'flag_id'): 'Flag', ('flag_not', 'flag_id'): 'Flag',
    ('variable', 'name'): 'Variable',
    ('item', 'arg0'): 'Item', ('item', 'value'): 'Quantity',
    ('stat', 'arg0'): 'Stat', ('character', 'arg0'): 'Character',
    ('resource', 'arg0'): 'Resource', ('skill', 'arg0'): 'Skill',
    ('timer', 'arg0'): 'Timer', ('bar', 'arg0'): 'Bar',
    ('boss_hp', 'arg0'): 'Boss', ('boss_hp', 'mode'): 'Measure',
}

_COND_CATEGORIES = [
    ('Flags and variables', ['flag', 'flag_not', 'variable']),
    ('Player', ['item', 'stat', 'character', 'zeni', 'resource', 'skill']),
    ('World', ['timer', 'bar', 'room_kills', 'boss_hp']),
]

_COND_DESCS = {
    'flag': "A flag has been set.",
    'flag_not': "A flag has not been set.",
    'variable': "Compare a custom variable.",
    'item': "Player owns enough of an item.",
    'stat': "Compare a player stat.",
    'character': "A specific character is played.",
    'zeni': "Compare the zeni amount.",
    'resource': "Compare health, energy or gauge.",
    'skill': "Player has a skill equipped.",
    'timer': "Compare a timer's time left.",
    'bar': "Compare a QTE bar's fill.",
    'room_kills': "Enemies defeated in this room.",
    'boss_hp': "Compare a boss's health.",
}

_ACTION_CATEGORIES = [
    ('Dialogue and flow', ['dialogue_box', 'set_portrait', 'dialogue_choice', 'conditional',
                           'play_cutscene']),
    ('Timers', ['timer_start', 'timer_pause', 'timer_stop']),
    ('Player', ['zeni', 'item', 'level', 'exp', 'stat', 'resource', 'skill', 'transformation',
                'charged_melee', 'set_player_character', 'set_player_skin', 'character_list',
                'play_character_animation', 'set_player_location']),
    ('Screen and audio', ['screen_fade', 'screen_shake', 'spam_qte', 'weather', 'room_music',
                          'play_sound']),
    ('World', ['change_map', 'spawn_enemies', 'spawn_npc', 'npc_state', 'enemy_state',
               'boss_state', 'toggle_flying_pad', 'world_map_location']),
    ('Story and data', ['quest', 'mission', 'modify_quest_variable', 'set_custom_variable',
                        'save_game']),
]

_ACTION_DESCS = {
    'dialogue_box': "Show a line of dialogue.",
    'set_portrait': "Change a speaker's portrait.",
    'dialogue_choice': "Ask a question with options.",
    'conditional': "IF / ELSE IF / ELSE branching.",
    'timer_start': "Start a named countdown timer.",
    'timer_pause': "Pause a running timer.",
    'timer_stop': "Stop and clear a timer.",
    'zeni': "Set, add or remove zeni.",
    'item': "Give or take an item.",
    'level': "Change a character's level.",
    'exp': "Change a character's experience.",
    'stat': "Change a character stat.",
    'resource': "Change health, energy or gauge.",
    'skill': "Equip or remove a skill.",
    'transformation': "Unlock or lock a form.",
    'charged_melee': "Grant or remove charged melee.",
    'set_player_character': "Switch the playable character.",
    'set_player_skin': "Change the current costume.",
    'character_list': "Add or remove roster members.",
    'screen_fade': "Fade the screen in or out.",
    'screen_shake': "Shake the screen.",
    'spam_qte': "Start a button-mash meter or timing bar.",
    'weather': "Start or stop weather.",
    'room_music': "Set or stop the room music.",
    'play_sound': "Play a sound effect.",
    'play_character_animation': "Play an animation on a character.",
    'save_game': "Save the game.",
    'change_map': "Move to another room.",
    'set_player_location': "Teleport within this room.",
    'spawn_enemies': "Spawn an enemy at a position.",
    'spawn_npc': "Spawn an NPC at a position.",
    'play_cutscene': "Run a cutscene.",
    'quest': "Add or remove a quest.",
    'modify_quest_variable': "Change a quest variable.",
    'set_custom_variable': "Set a custom variable.",
    'world_map_location': "Show or hide a map location.",
    'mission': "Start, complete or fail a mission.",
    'toggle_flying_pad': "Enable or disable a flying pad.",
    'npc_state': "Show, hide or despawn an NPC.",
    'enemy_state': "Show, hide or despawn an enemy.",
    'boss_state': "Show, hide or despawn a boss.",
}


def _humanize(name):
    words = str(name).replace('_', ' ').split()
    return ' '.join(_ACRONYMS.get(w.lower(), w.capitalize()) for w in words)


def _opt_label(opt):
    return _CMP_LABELS.get(opt, str(opt).replace('_', ' '))


def _num_ok(ch):
    return ch in '0123456789.-'


def _plural(n, word):
    return "%d %s%s" % (n, word, '' if n == 1 else ('es' if word.endswith('ch') else 's'))


def _action_type_list():
    """Every action type the editor can build: ACTION_TYPES order, plus any
    schema-only extras."""
    seen = list(ACTION_TYPES)
    for t in ACTION_SCHEMA:
        if t not in seen:
            seen.append(t)
    return seen


# ── Vector icons (same fn(surface, rect, color[, width]) shape as ui_kit) ──

def _ic_x(surface, rect, color, width=3):
    cx, cy = rect.center
    s = min(rect.w, rect.h) * 0.26
    uk.draw_line_on(surface, color, (cx - s, cy - s), (cx + s, cy + s), width)
    uk.draw_line_on(surface, color, (cx - s, cy + s), (cx + s, cy - s), width)


def _ic_copy(surface, rect, color, width=2):
    cx, cy = rect.center
    s = min(rect.w, rect.h)
    a = pygame.Rect(0, 0, int(s * 0.34), int(s * 0.40))
    b = a.copy()
    a.center = (cx - int(s * 0.08), cy - int(s * 0.08))
    b.center = (cx + int(s * 0.08), cy + int(s * 0.08))
    uk.draw_rect_on(surface, color, a, width, 2)
    uk.draw_rect_on(surface, color, b, width, 2)


def _ic_grip(surface, rect, color, width=3):
    cx, cy = rect.center
    s = min(rect.w, rect.h)
    for col in (-1, 1):
        for row in (-1, 0, 1):
            uk.draw_circle_on(surface, color, (int(cx + col * s * 0.16), int(cy + row * s * 0.24)), 2)


def _ic_search(surface, rect, color, width=2):
    cx, cy = rect.center
    s = min(rect.w, rect.h)
    r = max(3, int(s * 0.20))
    c = (int(cx - s * 0.06), int(cy - s * 0.06))
    uk.draw_circle_on(surface, color, c, r, width)
    uk.draw_line_on(surface, color, (c[0] + r * 0.7, c[1] + r * 0.7),
                    (cx + s * 0.26, cy + s * 0.26), width)


def _ic_target(surface, rect, color, width=2):
    cx, cy = rect.center
    s = min(rect.w, rect.h)
    uk.draw_circle_on(surface, color, (cx, cy), max(3, int(s * 0.20)), width)
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        uk.draw_line_on(surface, color, (cx + dx * s * 0.28, cy + dy * s * 0.28),
                        (cx + dx * s * 0.42, cy + dy * s * 0.42), width)


def _ic_edit(surface, rect, color, width=2):
    cx, cy = rect.center
    s = min(rect.w, rect.h)
    uk.draw_line_on(surface, color, (cx - s * 0.26, cy + s * 0.26), (cx + s * 0.24, cy - s * 0.24), width + 1)
    uk.draw_line_on(surface, color, (cx - s * 0.30, cy + s * 0.30), (cx - s * 0.16, cy + s * 0.28), width)
    uk.draw_line_on(surface, color, (cx - s * 0.30, cy + s * 0.30), (cx - s * 0.28, cy + s * 0.16), width)


def _make_curve_arrow(flip):
    """Undo (flip=False) / redo (flip=True) arrow: a 3/4 arc with a head."""
    def draw(surface, rect, color, width=2):
        cx, cy = rect.center
        r = min(rect.w, rect.h) * 0.26
        sgn = -1 if flip else 1
        pts = []
        for i in range(0, 11):
            a = math.radians(200 - i * 22)
            pts.append((cx + sgn * math.cos(a) * r, cy + 1 - math.sin(a) * r))
        for a, b in zip(pts, pts[1:]):
            uk.draw_line_on(surface, color, a, b, width)
        tip = pts[0]
        uk.draw_line_on(surface, color, tip, (tip[0] + sgn * r * 0.75, tip[1] - r * 0.05), width)
        uk.draw_line_on(surface, color, tip, (tip[0] + sgn * r * 0.10, tip[1] + r * 0.80), width)
    return draw


_ic_undo = _make_curve_arrow(False)
_ic_redo = _make_curve_arrow(True)



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
        self.anchor = None      # a click leaves a zero-width anchor; typing must not turn it into a selection
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




# ═════════════════════════════════════════════════════════════════════════
# EventEditorWindow — the full-screen editor
# ═════════════════════════════════════════════════════════════════════════

class EventEditorWindow:
    """Full-screen Conditions + Actions editor, in the Dev Menu / Room Editor /
    Character Creator style.

    Layout: header (back / title / save) · breadcrumb + undo bar · a Conditions
    panel on the left and an Actions panel on the right (each scrolls on its
    own) · footer with tooltips and status.

    Dialogue-choice options and conditional branches open as nested pages
    (crumbs in the breadcrumb bar), instead of the old stacked popups.
    """

    def __init__(self, flag_manager, colors=None):
        self.flag_manager = flag_manager if flag_manager is not None else _NullFlagManager()
        # `colors` is only accepted so old call sites keep working; the look
        # now comes from ui_kit.Theme like every other dev tool.
        self.colors = colors

        self.condition_builder = ConditionBuilder(self.flag_manager)
        self.action_builder = ActionSequenceBuilder()
        self.action_builder._condition_flag_manager = self.flag_manager

        self.active = False
        self.title = "Edit Event"
        self._on_save = None
        self.last_conditions = []
        self.last_actions = []

        self.screen_width = 0
        self.screen_height = 0
        self._fonts_ready = False
        self._laid_out = False
        self._reset_ui_state()

    def _reset_ui_state(self):
        self.pages = []
        self._rev = 0
        self._rev_seen = -1
        self._dirty_cache = False
        self.status_msg = ""
        self.status_ok = True
        self.status_timer = 0.0
        self.dialog = None          # confirm dialog
        self.popup = None           # dropdown list / add-type grid
        self.spawn = None           # Set Spawn / Set Position room picker
        self._mouse = tuple(pygame.mouse.get_pos()) if pygame.get_init() else (0, 0)
        self._hm = self._mouse
        self._dt = 1 / 60
        self._pulse = 0.0
        self._updated = False
        self._last_ticks = None
        self._hits = []
        self._hv = {}
        self._focus = None
        self._focus_before = None
        self._drag = None
        self._drag_before = None
        self._reorder = None
        self._tscroll = {}
        self._text_rects = []
        self._text_rects_new = []
        self._ml_info = {}
        self._ml_info_new = {}
        self._tip = None
        self._vp = None
        self._blocked = False
        self._modal_start = 0
        self._row_rects = {'c': [], 'a': []}
        self._list_vp = {}
        self._panel_rects = {}
        self._repeat_on = False

    # ── Host-facing context hooks (forwarded to every open builder) ─────────

    def _all_action_builders(self):
        seen = [self.action_builder]
        for page in self.pages[1:]:
            seen.append(page['act'])
        return seen

    def set_current_character(self, character_id, get_equipped_skills=None,
                              get_unlocked_transformations=None):
        """See ActionSequenceBuilder.set_current_character()."""
        for b in self._all_action_builders():
            b.set_current_character(character_id, get_equipped_skills, get_unlocked_transformations)

    def set_current_room(self, room_name):
        """See ActionSequenceBuilder.set_current_room()."""
        for b in self._all_action_builders():
            b.set_current_room(room_name)

    def set_known_rooms(self, room_names, room_dims=None):
        """See ActionSequenceBuilder.set_known_rooms()."""
        for b in self._all_action_builders():
            b.set_known_rooms(room_names, room_dims)

    def set_room_preview_provider(self, provider):
        """See ActionSequenceBuilder.set_room_preview_provider()."""
        for b in self._all_action_builders():
            b.set_room_preview_provider(provider)

    # ── Assets / fonts ──────────────────────────────────────────────────────

    @staticmethod
    def _font_root():
        anchored = BASE_DIR / "assets" / "ui" / "fonts"
        return str(anchored) if anchored.exists() else os.path.join("assets", "ui", "fonts")

    @staticmethod
    def _load_png_icon(name, box):
        """Same crop + integer-blow-up + point-sample path the Character Creator
        uses for its header icons. None when the file is missing (the caller
        falls back to a vector icon)."""
        path = os.path.join(str(BASE_DIR), "assets", "ui", "dev_menu", "icons", name)
        if not os.path.exists(path):
            path = os.path.join("assets", "ui", "dev_menu", "icons", name)
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

    def _ensure_fonts(self):
        if self._fonts_ready:
            return
        pygame.font.init()
        root = self._font_root()
        menu = uk.BitmapFont(root, letter_spacing=1)
        title = uk.BitmapFont(root, letter_spacing=1)
        title.uppercase_dir = os.path.join(root, "uppercase")
        title.lowercase_dir = os.path.join(root, "lowercase")
        self.f_title = _Font(title, 32)
        self.f_lg = _Font(menu, 20)
        self.f_md = _Font(menu, 16)
        self.f_sm = _Font(menu, 12)
        self._back_icon = self._load_png_icon("back.png", 34)
        self._save_icon = self._load_png_icon("save.png", 26)
        self._plus_icon = self._load_png_icon("plus.png", 24)
        self._trash_icon = self._load_png_icon("trash.png", 24)
        # Duplicate-row icon: the user's own PNG (first name found wins), else the vector one.
        self._dup_icon = None
        for _n in ("duplicate.png", "copy.png", "dup.png"):
            self._dup_icon = self._load_png_icon(_n, 24)
            if self._dup_icon is not None:
                break
        self._fonts_ready = True

    def _ensure_layout(self, screen):
        w, h = screen.get_size()
        if self._laid_out and (w, h) == (self.screen_width, self.screen_height):
            return
        self.screen_width, self.screen_height = int(w), int(h)
        self._laid_out = True
        self.header_h = max(86, round(h * 0.12))
        self.footer_h = max(42, round(h * 0.065))
        m = 32
        md = self.f_md
        self.m_field_h = max(40, md.line_h + 20)
        self.m_pill_h = max(44, md.line_h + 22)
        self.m_btn = 30                     # small icon buttons on rows
        back = max(40, round(self.header_h * 0.55))
        self.back_rect = pygame.Rect(m, (self.header_h - back) // 2, back, back)
        self.save_rect = pygame.Rect(w - m - back, (self.header_h - back) // 2, back, back)
        self.ctx_rect = pygame.Rect(m, self.header_h + 14, w - 2 * m, 44)
        top = self.ctx_rect.bottom + 12
        bottom = h - self.footer_h - 20
        self.area_rect = pygame.Rect(m, top, w - 2 * m, max(200, bottom - top))

    def resize(self, width, height):
        """Optional: force a re-layout for a new draw-target size."""
        self._laid_out = False

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def open(self, title="Edit Event", existing_conditions=None, existing_actions=None, on_save=None):
        self._set_key_repeat(False)
        self._reset_ui_state()
        self.title = title or "Edit Event"
        self._on_save = on_save
        self.condition_builder.refresh(copy.deepcopy(list(existing_conditions or [])))
        self.action_builder.refresh(copy.deepcopy(list(existing_actions or [])))
        self.pages = [self._make_page('root', 'Event', self.condition_builder, self.action_builder)]
        self.active = True

    def close(self, save):
        if save:
            self.last_conditions = self.condition_builder.get_condition_list()
            self.last_actions = self.action_builder.get_action_list()
            if self._on_save:
                self._on_save(self.last_conditions, self.last_actions)
        self._focus = None
        self._focus_before = None
        self.popup = None
        self.spawn = None
        self.dialog = None
        self._drag = None
        self._reorder = None
        self.active = False
        self._on_save = None
        uk.set_text_cursor(False)
        uk.set_hand_cursor(False)
        self._set_key_repeat(False)

    def _set_key_repeat(self, on):
        if on and not self._repeat_on:
            pygame.key.set_repeat(400, 50)
            self._repeat_on = True
        elif not on and self._repeat_on:
            pygame.key.set_repeat(0)
            self._repeat_on = False

    def _set_status(self, msg, ok=True):
        self.status_msg = msg
        self.status_ok = ok
        self.status_timer = 2.4

    # ── Pages (root event + nested option / branch editors) ─────────────────

    def _make_page(self, kind, crumb, cond, act, apply=None, cond_locked=False, note=None):
        page = {'kind': kind, 'crumb': crumb, 'cond': cond, 'act': act, 'apply': apply,
                'cond_locked': cond_locked, 'note': note,
                'scroll': {'c': 0.0, 'a': 0.0}, 'content_h': {'c': 0, 'a': 0},
                'undo': [], 'redo': [], 'collapsed': set()}
        page['snapshot'] = self._page_sig(page)
        return page

    @property
    def page(self):
        return self.pages[-1]

    @staticmethod
    def _page_sig(page):
        conds = page['cond'].get_condition_list() if page['cond'] is not None else []
        acts = page['act'].get_action_list()
        return json.dumps([conds, acts], sort_keys=True, default=str)

    def _is_dirty(self):
        if not self.pages:
            return False
        if self._rev != self._rev_seen:
            self._dirty_cache = self._page_sig(self.page) != self.page['snapshot']
            self._rev_seen = self._rev
        return self._dirty_cache

    def _nested_actions(self, actions, parent_model):
        builder = ActionSequenceBuilder()
        builder.refresh(actions, parent=parent_model)
        builder._condition_flag_manager = self.flag_manager
        return builder

    def _open_option_page(self, row_index, option_index):
        model = self.page['act']
        if not (0 <= row_index < len(model.rows)):
            return
        options = model.rows[row_index].get('_options', [])
        if not (0 <= option_index < len(options)):
            return
        opt = options[option_index]
        builder = self._nested_actions(opt.get('actions', []), model)

        def apply(conds, acts, opt=opt):
            opt['actions'] = acts

        self._push_page(self._make_page(
            'option', "Option %d" % (option_index + 1), None, builder, apply,
            note="These actions run when the player picks this option."))

    def _open_branch_page(self, row_index, branch_index):
        model = self.page['act']
        if not (0 <= row_index < len(model.rows)):
            return
        row = model.rows[row_index]
        if row.get('type') != 'conditional':
            return
        branches = row.get('_branches', [])
        if not (0 <= branch_index < len(branches)):
            return
        branch = branches[branch_index]
        is_else = bool(branch.get('is_else'))
        label = 'ELSE' if is_else else ('IF' if branch_index == 0 else 'ELSE IF')
        cond = None
        if not is_else:
            cond = ConditionBuilder(self.flag_manager)
            cond.refresh(branch.get('conditions', []), parent=self.condition_builder)
        builder = self._nested_actions(branch.get('actions', []), model)

        def apply(conds, acts, branch=branch):
            if conds is not None:
                branch['conditions'] = conds
            branch['actions'] = acts

        self._push_page(self._make_page(
            'branch', "%s branch" % label, cond, builder, apply, cond_locked=is_else,
            note="ELSE runs when none of the branches above matched, so it has no conditions."))

    def _push_page(self, page):
        self._blur()
        self.popup = None
        self.pages.append(page)
        self._rev += 1

    def _apply_page(self, page):
        apply = page.get('apply')
        if apply is None:
            return
        conds = page['cond'].get_condition_list() if page['cond'] is not None else None
        apply(conds, page['act'].get_action_list())

    def _pop_page(self, apply):
        if len(self.pages) <= 1:
            return
        self._blur()
        self.popup = None
        child, parent = self.pages[-1], self.pages[-2]
        if apply:
            before = self._state_of(parent)
            self._apply_page(child)
            self._note_change(parent, before)
        self.pages.pop()
        self._rev += 1

    def _pop_to(self, level):
        """Return to page `level` (0 = the event), keeping what was edited."""
        while len(self.pages) - 1 > level:
            self._pop_page(True)

    # ── Undo / redo (per page) ──────────────────────────────────────────────

    @staticmethod
    def _state_of(page):
        return (copy.deepcopy(page['cond'].rows) if page['cond'] is not None else None,
                copy.deepcopy(page['act'].rows))

    def _note_change(self, page, before):
        """Record `before` as an undo step if the page's rows changed since."""
        if self._state_of(page) != before:
            page['undo'].append(before)
            del page['undo'][:-_UNDO_LIMIT]
            page['redo'].clear()
            self._rev += 1

    def _tracked(self, fn, *args):
        page = self.page
        before = self._state_of(page)
        fn(*args)
        self._note_change(page, before)

    def _restore(self, page, state):
        if page['cond'] is not None and state[0] is not None:
            page['cond'].rows = copy.deepcopy(state[0])
        page['act'].rows = copy.deepcopy(state[1])
        page['collapsed'].clear()

    def _undo(self):
        page = self.page
        if not page['undo']:
            return
        self._blur()
        self.popup = None
        page['redo'].append(self._state_of(page))
        self._restore(page, page['undo'].pop())
        self._rev += 1
        self._set_status("Undid the last change")

    def _redo(self):
        page = self.page
        if not page['redo']:
            return
        self._blur()
        self.popup = None
        page['undo'].append(self._state_of(page))
        self._restore(page, page['redo'].pop())
        self._rev += 1
        self._set_status("Redid the change")

    # ── Close / save / discard flows ────────────────────────────────────────

    def _request_cancel(self):
        if self._is_dirty():
            self._open_confirm("Discard changes?",
                               "This event has unsaved changes. Close the editor without saving them?",
                               lambda: self.close(False), "Discard", True)
        else:
            self.close(False)

    def _discard_level(self):
        if len(self.pages) <= 1:
            self._request_cancel()
            return
        if self._is_dirty():
            self._open_confirm("Discard changes?",
                               "Throw away what you changed on this page and go back?",
                               lambda: self._pop_page(False), "Discard", True)
        else:
            self._pop_page(False)

    def _request_save(self):
        """Apply every open nested page, then save the whole event."""
        self._blur()
        while len(self.pages) > 1:
            self._pop_page(True)
        self.close(True)

    def _escape(self):
        if self.spawn is not None:
            self.spawn = None
        elif len(self.pages) > 1:
            self._discard_level()
        else:
            self._request_cancel()

    # ══════════════════════════════════════════════════════════════════════
    #  Input
    # ══════════════════════════════════════════════════════════════════════

    def handle_input(self, event):
        """Feed every pygame event here while `active` (mouse motion, buttons,
        wheel and keys — hover / drag / scroll need the non-click ones too)."""
        if not self.active:
            return
        et = event.type
        if et in (pygame.MOUSEMOTION, pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP) and hasattr(event, "pos"):
            self._mouse = tuple(event.pos)

        if self.dialog is not None:
            self._dialog_event(event)
            return

        if et == pygame.KEYDOWN:
            mods = getattr(event, "mod", 0) | pygame.key.get_mods()
            ctrl = bool(mods & (pygame.KMOD_CTRL | pygame.KMOD_META))
            shift = bool(mods & pygame.KMOD_SHIFT)
            if ctrl and event.key == pygame.K_s:
                self._request_save()
                return
            if ctrl and event.key == pygame.K_z and self.popup is None and self.spawn is None:
                self._redo() if shift else self._undo()
                return
            if ctrl and event.key == pygame.K_y and self.popup is None and self.spawn is None:
                self._redo()
                return
            if self.popup is not None:
                self._popup_key(event)
                return
            if self._focus is not None:
                res = self._focus.edit.key(event)
                if res == "changed":
                    self._focus.set(self._focus.edit.value)
                    self._rev += 1
                elif res in ("commit", "cancel"):
                    self._blur()
                return
            if self.spawn is not None:
                if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                    self._spawn_done()
                elif event.key == pygame.K_ESCAPE:
                    self.spawn = None
                return
            if event.key == pygame.K_ESCAPE:
                self._escape()
        elif et == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self._mouse_down(event.pos)
        elif et == pygame.MOUSEMOTION:
            if self._drag is not None and self._drag.get("drag"):
                self._drag["drag"](event.pos)
        elif et == pygame.MOUSEBUTTONUP and event.button == 1:
            d, self._drag = self._drag, None
            if d is not None and d.get("up"):
                d["up"](event.pos)
            if self._drag_before is not None:
                page, before = self._drag_before
                self._note_change(page, before)
            self._drag_before = None
            self._reorder = None
        elif et == pygame.MOUSEWHEEL:
            self._wheel(event.y)

    def _hit_at(self, pos):
        for hit in reversed(self._hits):
            if hit["rect"].collidepoint(pos):
                return hit
        return None

    def _mouse_down(self, pos):
        hit = self._hit_at(pos)
        if self._focus is not None and (hit is None or hit["key"] != self._focus.key):
            self._blur()
        if hit is None:
            return
        page = self.page
        before = self._state_of(page)
        if hit["down"]:
            hit["down"](pos)
        if hit["drag"] or hit["up"]:
            self._drag = hit
            self._drag_before = (page, before)
        else:
            self._note_change(page, before)
        self._rev += 1

    def _max_scroll(self, which):
        vp = self._list_vp.get(which)
        if vp is None:
            return 0
        return max(0, self.page['content_h'][which] - vp.h)

    def _wheel(self, dy):
        pos = self._mouse
        if self.popup is not None:
            self._popup_wheel(dy)
            return
        if self.spawn is not None:
            return
        for key, (rect, total, rows) in self._ml_info.items():
            if rect.collidepoint(pos) and total > rows:
                self._tscroll[key] = _clamp(self._tscroll.get(key, 0) - dy, 0, total - rows)
                return
        for which, rect in self._panel_rects.items():
            if rect.collidepoint(pos):
                sc = self.page['scroll']
                sc[which] = _clamp(sc[which] - dy * 64, 0, self._max_scroll(which))
                return

    # ── text focus ──────────────────────────────────────────────────────────

    def _focus_text(self, key, get, set_, multiline=False, max_len=600, allowed=None):
        edit = _TextEdit(get() or "", multiline=multiline, max_len=max_len, allowed=allowed)
        self._focus = SimpleNamespace(key=key, edit=edit, set=set_)
        page = self.page
        self._focus_before = (page, self._state_of(page))
        return edit

    def _blur(self):
        if self._focus is not None and self._focus_before is not None:
            page, before = self._focus_before
            self._focus = None
            self._focus_before = None
            self._note_change(page, before)
        self._focus = None
        self._focus_before = None

    # ── confirm dialog ──────────────────────────────────────────────────────

    def _open_confirm(self, title, message, on_confirm, confirm_label="Confirm", danger=True):
        self._blur()
        self.popup = None
        self.dialog = {"title": title, "message": message, "on_confirm": on_confirm,
                       "confirm_label": confirm_label, "danger": danger}

    def _close_dialog(self):
        self.dialog = None

    def _dialog_rects(self):
        d = self.dialog
        sw, sh = self.screen_width, self.screen_height
        W, pad = 460, 28
        lines = self.f_md.wrap(d["message"], W - pad * 2)
        line_h = self.f_md.line_h + 6
        y = pad + self.f_lg.line_h + 14
        msg_y = y
        y += len(lines) * line_h + 22
        btn_y = y
        H = btn_y + self.m_pill_h + pad
        panel = pygame.Rect(0, 0, W, H)
        panel.center = (sw // 2, sh // 2)
        bw = (W - pad * 2 - 14) // 2
        return {
            "panel": panel, "lines": lines, "line_h": line_h, "pad": pad,
            "msg_y": panel.y + msg_y,
            "ok": pygame.Rect(panel.x + pad, panel.y + btn_y, bw, self.m_pill_h),
            "cancel": pygame.Rect(panel.right - pad - bw, panel.y + btn_y, bw, self.m_pill_h),
        }

    def _dialog_submit(self):
        cb = self.dialog["on_confirm"]
        self._close_dialog()
        cb()

    def _dialog_event(self, event):
        r = self._dialog_rects()
        if event.type == pygame.KEYDOWN:
            if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self._dialog_submit()
            elif event.key == pygame.K_ESCAPE:
                self._close_dialog()
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if r["ok"].collidepoint(event.pos):
                self._dialog_submit()
            elif r["cancel"].collidepoint(event.pos):
                self._close_dialog()

    # ══════════════════════════════════════════════════════════════════════
    #  Update / draw plumbing
    # ══════════════════════════════════════════════════════════════════════

    def update(self, dt, mouse_pos=None):
        """Optional: the host may call this each frame with its dt (and the
        logical mouse position). If it never does, draw() times itself."""
        if not self.active:
            uk.set_text_cursor(False)
            uk.set_hand_cursor(False)
            return
        if mouse_pos is not None:
            self._mouse = tuple(mouse_pos)
        self._tick(dt)
        self._updated = True

    def _tick(self, dt):
        dt = min(dt, 1 / 20)
        self._dt = max(dt, 1 / 240)
        self._pulse += dt
        if self.status_timer > 0:
            self.status_timer -= dt
        if self._focus is not None:
            self._focus.edit.blink += dt
        if self.popup is not None and self.popup.get("edit") is not None:
            self.popup["edit"].blink += dt

    def draw(self, screen, dt=0.0):
        """`screen` may be the engine's GPUScreen or a plain pygame.Surface."""
        if not self.active:
            return
        self._ensure_fonts()
        self._ensure_layout(screen)
        if not self._updated:
            now = pygame.time.get_ticks()
            if dt <= 0:
                dt = 1 / 60 if self._last_ticks is None else (now - self._last_ticks) / 1000.0
            self._last_ticks = now
            self._tick(dt)
        self._updated = False
        self._set_key_repeat(self._focus is not None or self.popup is not None)

        self._hits = []
        self._text_rects_new = []
        self._ml_info_new = {}
        self._row_rects = {'c': [], 'a': []}
        self._panel_rects = {}
        self._tip = None
        self._vp = None
        modal = self.dialog is not None or self.popup is not None or self.spawn is not None
        self._blocked = modal
        self._hm = _OFFSCREEN if modal else self._mouse

        w, h = self.screen_width, self.screen_height
        uk.draw_rect_on(screen, _BG, pygame.Rect(0, 0, w, h), 0, 0)
        uk.draw_rect_on(screen, _BAND, pygame.Rect(0, self.header_h, w, h - self.header_h - self.footer_h), 0, 0)

        self._prune_collapsed()
        self._draw_context_bar(screen)
        self._draw_panels(screen)
        self._draw_header(screen)
        self._draw_footer(screen)

        # Modal layers, lowest first. Each one starts with a full-screen
        # backdrop hit so clicks never reach the widgets underneath.
        self._blocked = False
        self._hm = self._mouse
        self._vp = None
        self._modal_start = len(self._hits)
        if self.spawn is not None:
            self._draw_spawn(screen)
        if self.popup is not None:
            self._draw_popup(screen)
        if self.dialog is not None:
            self._draw_dialog(screen)

        self._text_rects = self._text_rects_new
        self._ml_info = self._ml_info_new
        self._resolve_cursor()

    def _prune_collapsed(self):
        page = self.page
        live = {id(r) for m in (page['cond'], page['act']) if m is not None for r in m.rows}
        page['collapsed'] &= live

    def _resolve_cursor(self):
        """I-beam over a text field, hand over anything clickable, arrow
        otherwise (I-beam wins where the two overlap). While a modal layer is
        up only that layer's own widgets count."""
        if self.dialog is not None:
            r = self._dialog_rects()
            hover_text = False
            hover_widget = r["ok"].collidepoint(self._mouse) or r["cancel"].collidepoint(self._mouse)
        else:
            hits = self._hits[self._modal_start:] if (self.popup or self.spawn) else self._hits
            hover_text = any(rect.collidepoint(self._mouse) for rect in self._text_rects)
            hover_widget = (not hover_text and any(
                hit["rect"].collidepoint(self._mouse) for hit in hits if hit["key"] != "backdrop"))
        uk.set_text_cursor(hover_text)
        uk.set_hand_cursor(hover_widget)

    # ── hover / hit plumbing ────────────────────────────────────────────────

    def _anim(self, key, on):
        """Eased 0..1 hover amount for `key`, quantised to 20 steps so the
        bitmap-font / rounded-rect caches don't fill with near-duplicates."""
        v = self._hv.get(key, 0.0)
        target = 1.0 if on else 0.0
        v += (target - v) * min(1.0, self._dt * 14.0)
        if abs(target - v) < 0.01:
            v = target
        self._hv[key] = v
        return round(v * 20) / 20

    def _hov(self, rect):
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

    # ── clipping ────────────────────────────────────────────────────────────

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
    def _pop_clip(screen, old):
        screen.set_clip(old)

    # ── text ────────────────────────────────────────────────────────────────

    def _put(self, screen, font, text, color, x, base_y, anchor="l", dyn=False):
        if not text:
            return 0
        surf, desc = font.render(text, color)
        tw, th = surf.get_size()
        if anchor == "c":
            x -= tw // 2
        elif anchor == "r":
            x -= tw
        px, py = int(x), int(base_y - (th - desc))
        vp = self._vp
        if vp is not None:
            # Inside a scrolling area: crop the text to the viewport ourselves, because
            # the dispatch blit does not always honour the screen clip (this is what let
            # option names bleed over the popup's search bar).
            vis = pygame.Rect(px, py, tw, th).clip(vp)
            if vis.w <= 0 or vis.h <= 0:
                return tw
            if vis.size != (tw, th):
                surf = surf.subsurface(pygame.Rect(vis.x - px, vis.y - py, vis.w, vis.h)).copy()
                px, py = vis.x, vis.y
        uk.blit_surface(screen, surf, (px, py), transient=dyn)
        return tw

    def _text_top(self, screen, font, text, color, x, y, anchor="l", dyn=False):
        """Draw with the top of the capital letters at y."""
        return self._put(screen, font, text, color, x, y + font.cap_h, anchor, dyn)

    def _text_mid(self, screen, font, text, color, x, cy, anchor="l", dyn=False, max_w=None):
        """Draw vertically centred on cy (by cap-height, so 'no' and 'go' align)."""
        if max_w is not None:
            text = font.fit(text, max_w)
        return self._put(screen, font, text, color, x, cy + (font.cap_h + 1) // 2, anchor, dyn)

    # ── primitives ──────────────────────────────────────────────────────────

    @staticmethod
    def _panel(screen, rect, bg, border, bw=1, radius=10):
        if len(bg) == 3:
            bg = (*bg, 255)
        uk.draw_panel(screen, rect, bg=bg, border=border, border_width=bw, radius=radius, shadow=False)

    def _icon_png(self, surf, fallback):
        def draw(screen, rect, color, width=3):
            if surf is not None:
                uk.blit_surface(screen, surf, surf.get_rect(center=rect.center))
            else:
                fallback(screen, rect, color, width)
        return draw

    def _pill(self, screen, key, rect, label, accent, icon=None, danger=False, enabled=True,
              on_click=None, tip=None):
        if danger:
            accent = _T.DANGER_BRIGHT
        hov = enabled and self._hov(rect)
        t = self._anim(key, hov)
        if enabled:
            self._panel(screen, rect, uk.lerp_color(_CARD, _CARD_HI, t),
                        uk.lerp_color(_T.CARD_BORDER, accent, 0.5 + 0.5 * t))
            fg = uk.lerp_color(_T.TEXT_SECONDARY, accent, 0.55 + 0.45 * t)
            is_add = isinstance(key, tuple) and key and key[0] in ("add", "add_end", "oadd", "bradd", "brelse")
            if t > 0 and not is_add:        # the "add ..." buttons get no hover glow
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

    def _icon_btn(self, screen, key, rect, icon, on_click, danger=False, enabled=True, tip=None,
                  accent=None, size=28):
        accent = _T.DANGER_BRIGHT if danger else (accent or _T.GOLD)
        hov = enabled and self._hov(rect)
        t = self._anim(key, hov)
        if enabled:
            self._panel(screen, rect, uk.lerp_color(_CARD, _CARD_HI, t),
                        uk.lerp_color(_T.CARD_BORDER, accent, 0.78 * t), 1, 8 if rect.h < 40 else 10)
            fg = uk.lerp_color(_T.TEXT_SECONDARY, accent, t)
        else:
            self._panel(screen, rect, _CARD, _T.CARD_BORDER, 1, 8 if rect.h < 40 else 10)
            fg = _T.TEXT_DIM
        box = pygame.Rect(0, 0, size, size)
        box.center = rect.center
        icon(screen, box, fg, 3 if size >= 26 else 2)
        if enabled and on_click:
            self._add_hit(rect, key=key, down=lambda p, cb=on_click: cb(), tip=tip)

    def _select_box(self, screen, key, rect, text, on_click, muted=False, tip=None, accent=None):
        """A field-looking button with a chevron; on_click(rect) opens a list."""
        hov = self._hov(rect)
        t = self._anim(key, hov)
        accent = accent or _T.GOLD
        self._panel(screen, rect, uk.lerp_color(_FIELD, _FIELD_HI, t),
                    uk.lerp_color(_T.CARD_BORDER, accent, 0.6 * t))
        fg = _T.TEXT_DIM if muted else (_T.TEXT_PRIMARY if hov else _T.TEXT_SECONDARY)
        self._text_mid(screen, self.f_md, text, fg, rect.x + 14, rect.centery, dyn=True,
                       max_w=rect.w - 14 - 38)
        chev = pygame.Rect(rect.right - 34, rect.y, 34, rect.h)
        _ic_down(screen, chev, uk.lerp_color(_T.TEXT_MUTED, accent, t), 2)
        self._add_hit(rect, key=key, down=lambda p, r=pygame.Rect(rect): on_click(r), tip=tip)

    def _checkbox(self, screen, key, x, y, w, label, checked, on_toggle):
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

    def _segmented(self, screen, key, x, y, w, options, current, on_select):
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

    def _caption(self, screen, text, x, y, w, right=None):
        """Section heading: small caps label with a hairline out to the right."""
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

    def _note(self, screen, x, y, w, text, color=None, font=None):
        font = font or self.f_sm
        color = color or _T.TEXT_DIM
        lh = font.line_h + 4
        lines = font.wrap(text, w)
        for i, line in enumerate(lines):
            self._text_top(screen, font, line, color, x, y + i * lh)
        return len(lines) * lh

    def _chip(self, screen, rect, text, fg, bg=(26, 30, 40), border=(55, 61, 76), font=None):
        self._panel(screen, rect, bg, border, 1, rect.h // 2)
        self._text_mid(screen, font or self.f_sm, text, fg, rect.centerx, rect.centery, "c", dyn=True)

    # ── text fields ─────────────────────────────────────────────────────────

    def _text_field(self, screen, key, rect, get, set_, placeholder="", multiline=False, wrap=False,
                    max_len=600, allowed=None, pad_r=0):
        """Single-line field, or (wrap=True) a wrapped multi-row field. With
        multiline=True Enter inserts newlines; with wrap-only, Enter commits
        and the value stays one line (dialogue text is stored single-line)."""
        md = self.f_md
        wrap = wrap or multiline
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
        if not self._blocked:
            tr = pygame.Rect(rect.x, rect.y, rect.w - pad_r, rect.h)
            self._text_rects_new.append(tr.clip(self._vp) if self._vp is not None else tr)

        if not wrap:
            inner = pygame.Rect(rect.x + pad, rect.y + 2, rect.w - pad - max(pad, pad_r), rect.h - 4)
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
                self._text_mid(screen, md, placeholder, _T.TEXT_DIM, inner.x, cy, max_w=inner.w)
            elif focus:
                if edit.has_sel():
                    s, e = edit.sel_range()
                    sx = inner.x - scroll + md.width(value[:s])
                    ex = inner.x - scroll + md.width(value[:e])
                    uk.draw_rect_on(screen, (*_T.GOLD, 70),
                                    pygame.Rect(sx, cy - md.cap_h // 2 - 4, ex - sx, md.line_h + 8), 0, 3)
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
            inner = pygame.Rect(rect.x + pad, rect.y + 10, rect.w - pad - max(pad, pad_r), rect.h - 20)
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

        hit_rect = pygame.Rect(rect.x, rect.y, rect.w - pad_r, rect.h) if pad_r else rect
        self._add_hit(hit_rect, key=key, down=click, drag=drag)

    # ══════════════════════════════════════════════════════════════════════
    #  Drawing: header / footer / breadcrumb bar
    # ══════════════════════════════════════════════════════════════════════

    def _draw_header(self, screen):
        w, hh = self.screen_width, self.header_h
        uk.draw_rect_on(screen, _BAR, pygame.Rect(0, 0, w, hh), 0, 0)
        uk.draw_line_on(screen, _HAIR, (0, hh - 1), (w, hh - 1), 1)

        nested = len(self.pages) > 1
        r = self.back_rect
        t = self._anim("back", self._hov(r))
        self._panel(screen, r, uk.lerp_color(_CARD, _CARD_HI, t), uk.lerp_color(_T.CARD_BORDER, _T.GOLD, 0.78 * t))
        if t > 0:
            uk.draw_soft_glow(screen, r.center, int(r.w * 0.8), _T.GOLD, max_alpha=int(28 * t))
        if self._back_icon is not None:
            uk.blit_surface(screen, self._back_icon, self._back_icon.get_rect(center=r.center))
        else:
            _ic_left(screen, r, uk.lerp_color(_T.TEXT_SECONDARY, _T.GOLD, t), 3)
        if nested:
            self._add_hit(r, key="back", down=lambda p: self._pop_page(True),
                          tip="Keep these changes and go back one level")
        else:
            self._add_hit(r, key="back", down=lambda p: self._request_cancel(),
                          tip="Close the editor without saving  (Esc)")

        cx = w // 2
        self._text_mid(screen, self.f_title, "EVENT EDITOR", _T.TEXT_PRIMARY, cx, hh // 2, "c")

        self._icon_btn(screen, "save", self.save_rect, self._icon_png(self._save_icon, _ic_check),
                       self._request_save, tip="Save the whole event and close  (Ctrl+S)")
        if self._is_dirty() or (nested and self.pages[0].get('snapshot') is not None and self._root_dirty()):
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

    def _root_dirty(self):
        return False    # nested edits only reach the root on Back; covered by _is_dirty()

    def _draw_footer(self, screen):
        w, h = self.screen_width, self.screen_height
        fy = h - self.footer_h
        uk.draw_rect_on(screen, _BAR, pygame.Rect(0, fy, w, self.footer_h), 0, 0)
        uk.draw_line_on(screen, _HAIR, (0, fy), (w, fy), 1)
        cy = fy + self.footer_h // 2
        x = 32
        room = w - 64
        if self.status_timer > 0 and self.status_msg:
            col = _T.KI_BLUE if self.status_ok else _T.DANGER_BRIGHT
            uk.draw_circle_on(screen, col, (x + 4, cy), 4)
            self._text_mid(screen, self.f_sm, self.status_msg, col, x + 18, cy, dyn=True, max_w=room)
        elif self._tip:
            self._text_mid(screen, self.f_sm, self._tip, _T.TEXT_MUTED, x, cy, dyn=True, max_w=room)

    def _draw_context_bar(self, screen):
        r = self.ctx_rect
        cy = r.centery
        page = self.page
        right = r.right
        ur = pygame.Rect(0, 0, 40, 40)
        rr = pygame.Rect(right - 40, cy - 20, 40, 40)
        ur = pygame.Rect(rr.left - 8 - 40, cy - 20, 40, 40)
        self._icon_btn(screen, "redo", rr, _ic_redo, self._redo, enabled=bool(page['redo']),
                       tip="Redo  (Ctrl+Shift+Z)", size=26)
        self._icon_btn(screen, "undo", ur, _ic_undo, self._undo, enabled=bool(page['undo']),
                       tip="Undo  (Ctrl+Z)", size=26)
        right = ur.left - 14
        if len(self.pages) > 1:
            dr = pygame.Rect(right - 150, cy - 20, 150, 40)
            self._pill(screen, "discard", dr, "Discard", _T.DANGER_BRIGHT, icon=_ic_x, danger=True,
                       on_click=self._discard_level, tip="Throw away this page's changes and go back")
            right = dr.left - 14

        x = r.x
        n = len(self.pages)
        for i, pg in enumerate(self.pages):
            label = self.title if i == 0 else pg['crumb']
            last = i == n - 1
            font = self.f_md
            cw = min(300, font.width(label) + 34)
            if x + cw > right - 30 and not last:
                cw = max(60, min(cw, 90))
            chip = pygame.Rect(x, cy - 18, cw, 36)
            t = self._anim(("crumb", i), self._hov(chip) and not last)
            base = _SEL if last else uk.lerp_color(_CARD, _CARD_HI, t)
            border = _T.GOLD if last else uk.lerp_color(_T.CARD_BORDER, _T.GOLD, 0.78 * t)
            self._panel(screen, chip, base, border, 1, 10)
            fg = _T.GOLD_BRIGHT if last else uk.lerp_color(_T.TEXT_SECONDARY, _T.TEXT_PRIMARY, t)
            self._text_mid(screen, font, label, fg, chip.centerx, chip.centery, "c", dyn=True, max_w=cw - 24)
            if not last:
                self._add_hit(chip, key=("crumb", i), down=lambda p, i=i: self._pop_to(i),
                              tip="Go back to this level")
                _ic_right(screen, pygame.Rect(chip.right + 2, cy - 12, 24, 24), _T.TEXT_DIM, 2)
            x = chip.right + 28

    # ══════════════════════════════════════════════════════════════════════
    #  Drawing: the two list panels
    # ══════════════════════════════════════════════════════════════════════

    def _draw_panels(self, screen):
        page = self.page
        ar = self.area_rect
        if page['cond'] is None:
            rects = {'a': pygame.Rect(ar)}
        else:
            gap = 20
            lw = int((ar.w - gap) * 0.42)
            rects = {'c': pygame.Rect(ar.x, ar.y, lw, ar.h),
                     'a': pygame.Rect(ar.x + lw + gap, ar.y, ar.w - lw - gap, ar.h)}
        for which, rect in rects.items():
            self._draw_list_panel(screen, which, rect)

    def _draw_list_panel(self, screen, which, rect):
        page = self.page
        model = page['cond'] if which == 'c' else page['act']
        accent = COND_ACCENT if which == 'c' else ACT_ACCENT
        locked = which == 'c' and page['cond_locked']
        self._panel(screen, rect, _T.PANEL_BG, _T.PANEL_BORDER, 1, 12)

        title = "CONDITIONS" if which == 'c' else "ACTIONS"
        uk.draw_soft_glow(screen, (rect.x + 26, rect.y + 31), 14, accent, max_alpha=46)
        uk.draw_circle_on(screen, accent, (rect.x + 26, rect.y + 31), 5)
        tw = self._text_mid(screen, self.f_lg, title, _T.TEXT_PRIMARY, rect.x + 44, rect.y + 31)
        count = "-" if locked else str(len(model.rows))
        self._text_mid(screen, self.f_sm, count, _T.TEXT_DIM, rect.x + 44 + tw + 14, rect.y + 31, dyn=True)

        if not locked:
            label = "Add condition" if which == 'c' else "Add action"
            bw = min(rect.w - 44 - tw - 60, 210)
            if bw >= 46:
                br = pygame.Rect(rect.right - 16 - bw, rect.y + 12, bw, 38)
                self._pill(screen, ("add", which), br, label if bw >= 150 else "",
                           accent, icon=self._icon_png(self._plus_icon, _ic_plus),
                           on_click=lambda w=which: self._open_add_popup(w),
                           tip="Add a condition" if which == 'c' else "Add an action")

        vp = pygame.Rect(rect.x + 3, rect.y + 62, rect.w - 6, rect.h - 62 - 4)
        self._panel_rects[which] = rect
        self._list_vp[which] = vp
        sc = page['scroll']
        sc[which] = _clamp(sc[which], 0, self._max_scroll(which))

        old_vp = self._vp
        self._vp = vp
        old = self._push_clip(screen, vp)
        x = rect.x + 16
        w = rect.w - 32 - 10
        y = vp.y + 6 - int(sc[which])
        used = self._draw_list_content(screen, which, model, x, y, w, locked)
        self._pop_clip(screen, old)
        self._vp = old_vp
        page['content_h'][which] = used + 12

        max_scroll = self._max_scroll(which)
        if max_scroll > 0:
            track = pygame.Rect(rect.right - 12, vp.y + 4, 5, vp.h - 8)
            th = max(30, int(track.h * vp.h / page['content_h'][which]))
            frac = sc[which] / max_scroll
            thumb = pygame.Rect(track.x, track.y + int((track.h - th) * frac), track.w, th)
            key = ("scrollbar", which)
            grab = self._drag is not None and self._drag.get("key") == key
            t = self._anim(key, self._hov(track.inflate(10, 0)) or grab)
            uk.draw_rect_on(screen, (24, 28, 38), track, 0, 2)
            uk.draw_rect_on(screen, uk.lerp_color(_T.CHIP_BORDER, _T.GOLD, t), thumb, 0, 2)

            def scrub(pos, track=track, th=th, ms=max_scroll, which=which):
                f = _clamp((pos[1] - track.y - th / 2) / max(1, track.h - th), 0.0, 1.0)
                self.page['scroll'][which] = f * ms

            self._add_hit(track.inflate(12, 0), key=key, down=scrub, drag=scrub)

    def _draw_list_content(self, screen, which, model, x, y, w, locked):
        page = self.page
        y0 = y
        vp = self._list_vp[which]
        if locked:
            y += self._note(screen, x, y, w, page.get('note') or "This branch has no conditions.")
            return y - y0

        if which == 'c':
            msg = ("Every condition below must be true before the event fires."
                   if model.rows else "No conditions. The event always fires when it is triggered.")
        else:
            msg = ("These run from top to bottom once the event fires."
                   if model.rows else "No actions yet. Add one to decide what happens.")
        if which == 'a' and page.get('note'):
            y += self._note(screen, x, y, w, page['note'], color=_T.TEXT_MUTED) + 10
        else:
            y += self._note(screen, x, y, w, msg) + 10

        rects = []
        for i, row in enumerate(model.rows):
            h = self._card(screen, which, model, i, row, x, y, w, False)
            rects.append((i, pygame.Rect(x, y, w, h)))
            if y + h >= vp.y and y <= vp.bottom:
                self._card(screen, which, model, i, row, x, y, w, True)
            y += h + _ROW_GAP
        self._row_rects[which] = rects

        # Big add-button at the end of the list, for mouse convenience.
        ar = pygame.Rect(x, y, w, 46)
        if ar.bottom >= vp.y and ar.y <= vp.bottom:
            label = "Add condition" if which == 'c' else "Add action"
            self._pill(screen, ("add_end", which), ar, label,
                       COND_ACCENT if which == 'c' else ACT_ACCENT,
                       icon=self._icon_png(self._plus_icon, _ic_plus),
                       on_click=lambda w=which: self._open_add_popup(w))
        y += 46
        return y - y0

    # ══════════════════════════════════════════════════════════════════════
    #  Row cards
    # ══════════════════════════════════════════════════════════════════════

    _WRAP_FIELDS = {('dialogue_box', 'text'), ('dialogue_choice', 'prompt')}
    _CARD_HEAD_H = 46

    def _row_kind_key(self, which):
        return 'kind' if which == 'c' else 'type'

    def _row_label(self, which, row):
        k = row.get(self._row_kind_key(which))
        if which == 'c':
            return CONDITION_KINDS[k]['label'] if k in CONDITION_KINDS else _humanize(k)
        return _humanize(k)

    def _row_fields(self, which, row):
        if which == 'c':
            return CONDITION_KINDS[row['kind']]['fields']
        t = row['type']
        if t == 'conditional':
            return []
        return _row_visible_fields(t, row['params'], ACTION_SCHEMA.get(t, []))

    def _row_summary(self, which, row):
        if row.get(self._row_kind_key(which)) == '__raw__':
            return ""
        t = row.get('type')
        if t == 'conditional':
            return "%s" % _plural(len(row.get('_branches', [])), 'branch').replace('branchs', 'branches')
        if t == 'dialogue_choice':
            bits = [row['params'].get('prompt', '')] + ["%s" % _plural(len(row.get('_options', [])), 'option')]
            return "   ".join(b for b in bits if b)
        bits = []
        for name, fk, extra in self._row_fields(which, row):
            v = str(row['params'].get(name, '')).strip()
            if fk == 'bool':
                if v == 'true':
                    bits.append(_humanize(name))
                continue
            if fk in ('spawn_picker', 'position_picker'):
                a, b = ('spawn_x', 'spawn_y') if fk == 'spawn_picker' else ('x', 'y')
                if row['params'].get(a, '') != '':
                    bits.append("%s, %s" % (row['params'].get(a), row['params'].get(b)))
                continue
            if v:
                bits.append(_opt_label(v) if fk in ('cmp', 'choice') else v)
        return "   ".join(bits)

    def _card(self, screen, which, model, idx, row, x, y, w, draw):
        """Lay out (draw=False, returns the height) or draw one row card."""
        page = self.page
        kk = self._row_kind_key(which)
        raw = row.get(kk) == '__raw__'
        collapsed = id(row) in page['collapsed']
        cw = w - _STRIPE - _PAD * 2
        cx = x + _STRIPE + _PAD
        plan = None
        body_h = 0
        if not raw and not collapsed:
            plan = self._body_plan(which, row, cw)
            body_h = plan['h']
        h = 8 + self._CARD_HEAD_H + (body_h + 10 if body_h else 0) + 8
        if not draw:
            return h

        accent = COND_ACCENT if which == 'c' else ACT_ACCENT
        if raw:
            accent = _T.TEXT_DIM
        rect = pygame.Rect(x, y, w, h)
        t = self._anim(('card', which, id(row)), self._hov(rect))
        dragging = self._reorder is not None and self._reorder['row'] is row
        self._panel(screen, rect, uk.lerp_color(_CARD, _CARD_HI, 1.0 if dragging else t),
                    _T.GOLD if dragging else uk.lerp_color(_T.CARD_BORDER, accent, 0.45 * t), 1, 10)
        uk.draw_rect_on(screen, accent, pygame.Rect(x + 8, y + 12, 3, h - 24), 0, 1)

        hy = y + 8
        cy = hy + self._CARD_HEAD_H // 2
        bx = cx
        grip = pygame.Rect(bx, cy - 15, 22, 30)
        gh = self._hov(grip) or dragging
        _ic_grip(screen, grip, _T.GOLD if gh else _T.TEXT_DIM, 3)
        self._add_hit(grip, key=('grip', which, id(row)),
                      down=lambda p, w=which, r=row: self._start_reorder(w, r),
                      drag=lambda p: self._drag_reorder(p), tip="Drag to reorder")
        sx = bx + 26
        if not raw:
            chev = pygame.Rect(sx, cy - 15, 28, 30)
            ct = self._anim(('chev', which, id(row)), self._hov(chev))
            (_ic_right if collapsed else _ic_down)(screen, chev, uk.lerp_color(_T.TEXT_MUTED, _T.GOLD, ct), 3)
            self._add_hit(chev, key=('chev', which, id(row)),
                          down=lambda p, r=row: self._toggle_collapse(r),
                          tip="Show the fields" if collapsed else "Collapse this row")
            sx += 32

        btn_w, btn_gap = 30, 6
        btns_w = 4 * btn_w + 3 * btn_gap
        btn_x = cx + cw - btns_w
        fh = self.m_field_h
        sel_w = int(_clamp(cw - (sx - cx) - btns_w - 14, 120, 320))
        sel = pygame.Rect(sx, cy - fh // 2, sel_w, fh)
        if raw:
            what = "condition" if which == 'c' else "action"
            self._panel(screen, pygame.Rect(sx, cy - fh // 2, cx + cw - btns_w - 14 - sx, fh), _INSET, _T.CARD_BORDER)
            self._text_mid(screen, self.f_md, "Unrecognized %s, kept as-is" % what, _T.TEXT_MUTED,
                           sx + 14, cy, max_w=cx + cw - btns_w - 14 - sx - 28)
        else:
            self._select_box(screen, ('kind', which, id(row)), sel, self._row_label(which, row),
                             lambda r, w=which, rw=row: self._open_type_popup(w, rw, r),
                             tip="Change the type of this row", accent=accent)
            if collapsed:
                summ = self._row_summary(which, row)
                if summ:
                    self._text_mid(screen, self.f_md, summ, _T.TEXT_DIM, sel.right + 16, cy, dyn=True,
                                   max_w=btn_x - 14 - (sel.right + 16))

        bs = [(_ic_up, "Move up", idx > 0, lambda: model._move_row(idx, -1), False),
              (_ic_down, "Move down", idx < len(model.rows) - 1, lambda: model._move_row(idx, 1), False),
              (self._icon_png(self._dup_icon, _ic_copy), "Duplicate this row", True, lambda: model._duplicate_row(idx), False),
              (self._icon_png(self._trash_icon, _ic_trash), "Delete this row", True,
               lambda: model._remove_row(idx), True)]
        for i, (ic, tip, en, cb, danger) in enumerate(bs):
            br = pygame.Rect(btn_x + i * (btn_w + btn_gap), cy - btn_w // 2, btn_w, btn_w)
            self._icon_btn(screen, ('rb', which, id(row), i), br, ic, cb, danger=danger, enabled=en,
                           tip=tip, size=22)

        if plan is not None and plan['h']:
            self._draw_body(screen, which, model, idx, row, plan, cx, hy + self._CARD_HEAD_H + 10, cw)
        return h

    def _toggle_collapse(self, row):
        c = self.page['collapsed']
        if id(row) in c:
            c.discard(id(row))
        else:
            c.add(id(row))

    def _start_reorder(self, which, row):
        self._reorder = {'which': which, 'row': row}

    def _drag_reorder(self, pos):
        ro = self._reorder
        if ro is None:
            return
        which, row = ro['which'], ro['row']
        model = self.page['cond'] if which == 'c' else self.page['act']
        if model is None:
            return
        cur = next((i for i, r in enumerate(model.rows) if r is row), None)
        rects = self._row_rects.get(which) or []
        if cur is None or not rects:
            return
        target = None
        if pos[1] < rects[0][1].top:
            target = 0
        elif pos[1] > rects[-1][1].bottom:
            target = len(rects) - 1
        else:
            for i, r in rects:
                if r.top <= pos[1] <= r.bottom:
                    if i > cur and pos[1] > r.top + min(r.h, 60):
                        target = i
                    elif i < cur and pos[1] < r.bottom - min(r.h, 60):
                        target = i
                    break
        if target is not None and target != cur:
            model._move_row_to(cur, target)

    # ── field layout ────────────────────────────────────────────────────────

    def _field_caption(self, which, row, name, fk):
        if which == 'c':
            cap = _FIELD_LABELS.get((row['kind'], name))
            if cap:
                return cap
            if name == 'cmp':
                return "Compare"
            if name in ('value', 'arg0'):
                return "Value"
            return _humanize(name)
        if fk == 'spawn_picker':
            return "Spawn point"
        if fk == 'position_picker':
            return "Position"
        return _humanize(name)

    def _bool_label(self, name):
        return {'wait': "Wait until it finishes"}.get(name, _humanize(name))

    def _fields_layout(self, which, row, fields, w):
        md = self.f_md
        cap_h = self.f_sm.cap_h + 8
        gap = _FIELD_GAP
        items = []
        for name, fk, extra in fields:
            it = {'name': name, 'kind': fk, 'extra': extra, 'fh': self.m_field_h,
                  'minw': 240, 'flex': True, 'full': False, 'cap': True, 'seg': False}
            if fk == 'choice':
                total = sum(md.width(_opt_label(o)) + 40 for o in extra)
                if len(extra) <= 3 and total <= w:
                    it.update(seg=True, minw=total, flex=False)
                else:
                    it.update(minw=200)
            elif fk == 'cmp':
                it.update(minw=180, flex=False)
            elif fk == 'number':
                it.update(minw=150, flex=False)
            elif fk == 'bool':
                it.update(minw=24 + 14 + md.width(self._bool_label(name)) + 16, flex=False,
                          cap=False, fh=32)
            elif fk == 'text':
                if (row.get('type'), name) in self._WRAP_FIELDS:
                    lh = md.line_h + 6
                    it.update(full=True, fh=3 * lh + 20)
                else:
                    it.update(minw=220)
            elif fk == 'json':
                it.update(minw=280)
            elif fk in ('spawn_picker', 'position_picker'):
                it.update(minw=280)
            items.append(it)

        lines, cur, curw = [], [], 0
        for it in items:
            if it['full']:
                if cur:
                    lines.append(cur)
                    cur, curw = [], 0
                lines.append([it])
                continue
            need = it['minw'] + (gap if cur else 0)
            if cur and curw + need > w:
                lines.append(cur)
                cur, curw, need = [], 0, it['minw']
            cur.append(it)
            curw += need
        if cur:
            lines.append(cur)

        ly = 0
        for line in lines:
            line_cap = cap_h if any(i['cap'] for i in line) else 0
            field_area = max(i['fh'] for i in line)
            if any(i['kind'] == 'bool' for i in line):
                field_area = max(field_area, self.m_field_h)
            gaps = gap * (len(line) - 1)
            leftover = w - sum(i['minw'] for i in line) - gaps
            flex_n = sum(1 for i in line if i['flex'])
            share = leftover // flex_n if (flex_n and leftover > 0) else 0
            lx = 0
            for it in line:
                it['x'] = lx
                it['w'] = it['minw'] + (share if it['flex'] else 0)
                if it['full']:
                    it['w'] = w
                it['cy'] = ly
                it['wy'] = ly + line_cap + ((field_area - it['fh']) // 2 if it['kind'] == 'bool' else 0)
                lx += it['w'] + gap
            ly += line_cap + field_area + gap
        return {'items': items, 'h': max(0, ly - gap) if lines else 0}

    def _body_plan(self, which, row, cw):
        fields = self._row_fields(which, row)
        lay = self._fields_layout(which, row, fields, cw)
        h = lay['h']
        extra = 0
        if which == 'a' and row.get('type') == 'dialogue_choice':
            n = len(row.get('_options', []))
            extra = self.f_sm.cap_h + 18 + n * (self.m_field_h + 8) + 38
        elif which == 'a' and row.get('type') == 'conditional':
            n = len(row.get('_branches', []))
            extra = self.f_sm.cap_h + 18 + n * (44 + 8) + 38
        if h and extra:
            h += 14
        return {'lay': lay, 'h': h + extra, 'fields_h': h if not extra else lay['h']}

    def _draw_body(self, screen, which, model, idx, row, plan, x, y, w):
        lay = plan['lay']
        for it in lay['items']:
            if it['cap']:
                self._text_top(screen, self.f_sm, self._field_caption(which, row, it['name'], it['kind']).upper(),
                               _T.TEXT_MUTED, x + it['x'], y + it['cy'])
            rect = pygame.Rect(x + it['x'], y + it['wy'], it['w'], it['fh'])
            self._field_widget(screen, which, model, idx, row, it, rect)
        y2 = y + lay['h'] + (14 if lay['h'] else 0)
        if which == 'a' and row.get('type') == 'dialogue_choice':
            self._draw_options(screen, model, idx, row, x, y2, w)
        elif which == 'a' and row.get('type') == 'conditional':
            self._draw_branches(screen, model, idx, row, x, y2, w)

    # ── the field widgets ───────────────────────────────────────────────────

    def _field_widget(self, screen, which, model, idx, row, it, rect):
        name, fk, extra = it['name'], it['kind'], it['extra']
        params = row['params']
        key = (which, id(row), name)

        def setp(v, p=params, n=name):
            p[n] = v

        if fk in ('text', 'number', 'json'):
            wrap = (row.get('type'), name) in self._WRAP_FIELDS
            ph = "0" if fk == 'number' else ("Type the %s..." % _humanize(name).lower())
            self._text_field(screen, key, rect, lambda p=params, n=name: p.get(n, ''), setp, ph,
                             wrap=wrap, max_len=400 if wrap else 300,
                             allowed=_num_ok if fk == 'number' else None)
        elif fk == 'cmp':
            cur = params.get(name, '==')
            self._select_box(screen, key, rect, _opt_label(cur),
                             lambda r, p=params, n=name, cur=cur: self._open_list_popup(
                                 "Compare", [(o, _opt_label(o)) for o in _CMP_OPTIONS], cur,
                                 lambda v: p.__setitem__(n, v), r, search=False))
        elif fk == 'choice':
            cur = params.get(name, extra[0])
            if it['seg']:
                self._segmented(screen, key, rect.x, rect.y, rect.w,
                                [(o, _opt_label(o)) for o in extra], cur, setp)
            else:
                self._select_box(screen, key, rect, _opt_label(cur),
                                 lambda r, p=params, n=name, cur=cur, ex=extra: self._open_list_popup(
                                     _humanize(n), [(o, _opt_label(o)) for o in ex], cur,
                                     lambda v: p.__setitem__(n, v), r, search=False))
        elif fk == 'bool':
            self._checkbox(screen, key, rect.x, rect.y, rect.w, self._bool_label(name),
                           params.get(name, 'false') == 'true',
                           lambda p=params, n=name: p.__setitem__(n, 'false' if p.get(n) == 'true' else 'true'))
        elif fk in ('spawn_picker', 'position_picker'):
            if fk == 'spawn_picker':
                a, b = 'spawn_x', 'spawn_y'
                ok = bool(params.get('room_name', ''))
                nice, empty = "Spawn", "Set spawn point"
                blocked = "Set spawn (pick a room first)"
            else:
                a, b = 'x', 'y'
                ok = bool(model._current_room_name)
                nice, empty = "Position", "Set position"
                blocked = "Set position (no room set)"
            if params.get(a, '') != '' and params.get(b, '') != '':
                text = "%s: %s, %s" % (nice, params.get(a), params.get(b))
            else:
                text = empty if ok else blocked
            self._pill(screen, key, rect, text, ACT_ACCENT, icon=_ic_target, enabled=ok,
                       on_click=lambda m=model, r=row, n=name: self._open_spawn(m, r, n),
                       tip="Click the room to place the point" if ok else "Nothing to preview yet")
        elif fk in _PICKER_KINDS:
            cur = params.get(name, '')
            if which == 'c':
                names, placeholder = model.choices(fk)
            else:
                names, placeholder = model.choices(idx, fk)
            ph = _placeholder_for(name, fk, row.get('kind')).strip('<>')
            ph = ph[:1].upper() + ph[1:]
            self._select_box(screen, key, rect, cur or ph, lambda r, m=model, rw=row, n=name, f=fk, i=idx, w=which:
                             self._open_picker(w, m, i, rw, n, f, r), muted=not cur,
                             tip="Pick from the list, or type your own value")
        else:
            self._text_field(screen, key, rect, lambda p=params, n=name: p.get(n, ''), setp, "", max_len=300)

    def _open_picker(self, which, model, idx, row, name, fk, anchor):
        if which == 'c':
            names, placeholder = model.choices(fk)
        else:
            names, placeholder = model.choices(idx, fk)
        params = row['params']

        def pick(v, p=params, n=name, f=fk):
            if f == 'world_map_picker' and p.get(n) != v:
                p['name'] = ''      # the old location name won't exist on the new map
            p[n] = v

        self._open_list_popup(_humanize(name), [(n_, n_) for n_ in names], params.get(name, ''), pick,
                              anchor, allow_custom=True, placeholder=placeholder)

    # ── dialogue_choice options ─────────────────────────────────────────────

    def _draw_options(self, screen, model, idx, row, x, y, w):
        options = row.get('_options', [])
        fh = self.m_field_h
        y += self._caption(screen, "Options", x, y, w, right=_plural(len(options), 'option'))
        for oi, opt in enumerate(options):
            ry = y + oi * (fh + 8)
            bw = 34
            right = x + w
            for i, (ic, tip, en, cb, danger) in enumerate((
                    (self._icon_png(self._trash_icon, _ic_trash), "Delete this option", True,
                     lambda oi=oi: model._remove_option(idx, oi), True),
                    (_ic_down, "Move option down", oi < len(options) - 1,
                     lambda oi=oi: model._move_option(idx, oi, 1), False),
                    (_ic_up, "Move option up", oi > 0,
                     lambda oi=oi: model._move_option(idx, oi, -1), False))):
                br = pygame.Rect(right - bw, ry + (fh - bw) // 2, bw, bw)
                self._icon_btn(screen, ('ob', id(row), id(opt), i), br, ic, cb, danger=danger,
                               enabled=en, tip=tip, size=22)
                right = br.left - 6
            ew = 190
            er = pygame.Rect(right - ew, ry, ew, fh)
            self._pill(screen, ('oe', id(row), id(opt)), er, "Actions (%d)" % len(opt.get('actions', [])),
                       ACT_ACCENT, icon=_ic_edit,
                       on_click=lambda oi=oi: self._open_option_page(idx, oi),
                       tip="Edit what happens when this option is picked")
            tr = pygame.Rect(x, ry, er.left - 10 - x, fh)
            self._text_field(screen, ('opt', id(row), id(opt)), tr, lambda o=opt: o.get('text', ''),
                             lambda v, o=opt: o.__setitem__('text', v), "Option text...", max_len=120)
        y += len(options) * (fh + 8)
        self._pill(screen, ('oadd', id(row)), pygame.Rect(x, y, 190, 38), "Add option", ACT_ACCENT,
                   icon=self._icon_png(self._plus_icon, _ic_plus),
                   on_click=lambda: model._add_option(idx), tip="Add another answer")

    # ── conditional branches ────────────────────────────────────────────────

    def _draw_branches(self, screen, model, idx, row, x, y, w):
        branches = row.get('_branches', [])
        y += self._caption(screen, "Branches", x, y, w, right=_plural(len(branches), 'branch').replace('branchs', 'branches'))
        for bi, br in enumerate(branches):
            is_else = bool(br.get('is_else'))
            label = 'ELSE' if is_else else ('IF' if bi == 0 else 'ELSE IF')
            rr = pygame.Rect(x, y + bi * 52, w, 44)
            t = self._anim(('br', id(row), id(br)), self._hov(rr))
            self._panel(screen, rr, uk.lerp_color(_FIELD, _FIELD_HI, t),
                        uk.lerp_color(_T.CARD_BORDER, COND_ACCENT if not is_else else _T.TEXT_MUTED, 0.78 * t), 1, 9)
            chip = pygame.Rect(rr.x + 8, rr.y + 8, 84, 28)
            self._chip(screen, chip, label, _T.TEXT_MUTED if is_else else COND_ACCENT, bg=_INSET, border=_HAIR)
            nc, na = len(br.get('conditions') or []), len(br.get('actions') or [])
            summary = _plural(na, 'action') if is_else else "%s, %s" % (_plural(nc, 'condition'), _plural(na, 'action'))
            dw = 34
            dele = pygame.Rect(rr.right - 8 - dw, rr.y + 5, dw, 34)
            ed = pygame.Rect(dele.left - 8 - 110, rr.y + 5, 110, 34)
            self._text_mid(screen, self.f_md, summary, _T.TEXT_SECONDARY, chip.right + 14, rr.centery,
                           dyn=True, max_w=ed.left - 14 - (chip.right + 14))
            self._add_hit(rr, key=('brrow', id(row), id(br)),
                          down=lambda p, r=idx, b=bi: self._open_branch_page(r, b),
                          tip="Edit this branch")
            self._pill(screen, ('bre', id(row), id(br)), ed, "Edit", ACT_ACCENT, icon=_ic_edit,
                       on_click=lambda r=idx, b=bi: self._open_branch_page(r, b))
            self._icon_btn(screen, ('brd', id(row), id(br)), dele, self._icon_png(self._trash_icon, _ic_trash),
                           lambda b=bi: model._remove_conditional_branch(idx, b), danger=True,
                           tip="Delete this branch", size=22)
        y += len(branches) * 52
        self._pill(screen, ('bradd', id(row)), pygame.Rect(x, y, 170, 38), "Else if", COND_ACCENT,
                   icon=self._icon_png(self._plus_icon, _ic_plus),
                   on_click=lambda: model._add_conditional_branch(idx, False), tip="Add an ELSE IF branch")
        if not any(b.get('is_else') for b in branches):
            self._pill(screen, ('brelse', id(row)), pygame.Rect(x + 180, y, 130, 38), "Else", COND_ACCENT,
                       icon=self._icon_png(self._plus_icon, _ic_plus),
                       on_click=lambda: model._add_conditional_branch(idx, True), tip="Add the ELSE branch")

    # ══════════════════════════════════════════════════════════════════════
    #  Popups: searchable dropdown list + the add / change-type grid
    # ══════════════════════════════════════════════════════════════════════

    def _attach_popup_search(self, popup):
        edit = _TextEdit("", multiline=False, max_len=60)
        popup['edit'] = edit
        popup['last_q'] = ""
        popup['scroll'] = 0
        popup['hi'] = 0
        self._focus = SimpleNamespace(key='popsearch', edit=edit, set=lambda v: None)
        self._focus_before = None

    def _close_popup(self):
        self.popup = None
        if self._focus is not None and self._focus.key == 'popsearch':
            self._focus = None
            self._focus_before = None

    def _open_list_popup(self, title, items, current, on_pick, anchor=None, allow_custom=False,
                         placeholder="", search=None):
        """items: [(value, label)]. on_pick(value) runs inside the click that
        chose it (so it is recorded for undo like every other edit)."""
        self._blur()
        if search is None:
            search = allow_custom or len(items) > 8
        self.popup = {'kind': 'list', 'title': title, 'items': list(items), 'current': current,
                      'on_pick': on_pick, 'anchor': pygame.Rect(anchor) if anchor is not None else None,
                      'allow_custom': allow_custom, 'placeholder': placeholder, 'search': search}
        self._attach_popup_search(self.popup)
        if not search:
            self._focus = None

    def _open_add_popup(self, which):
        model = self.page['cond'] if which == 'c' else self.page['act']
        if model is None:
            return
        self._blur()

        def pick(value, m=model, w=which):
            m._add_row(value)
            self._set_status("Added %s" % (CONDITION_KINDS[value]['label'] if w == 'c' else _humanize(value)))
            page = self.page
            page['scroll'][w] = 1e9          # jump to the new row (clamped on draw)

        self.popup = {'kind': 'grid', 'which': which, 'mode': 'add', 'on_pick': pick, 'multi': True,
                      'title': "Add condition" if which == 'c' else "Add action", 'current': None}
        self._attach_popup_search(self.popup)

    def _open_type_popup(self, which, row, anchor=None):
        model = self.page['cond'] if which == 'c' else self.page['act']
        self._blur()

        def pick(value, m=model, r=row):
            idx = next((i for i, x in enumerate(m.rows) if x is r), None)
            if idx is not None:
                (m._set_row_kind if which == 'c' else m._set_row_type)(idx, value)

        self.popup = {'kind': 'grid', 'which': which, 'mode': 'change', 'on_pick': pick, 'multi': False,
                      'title': "Change condition type" if which == 'c' else "Change action type",
                      'current': row.get(self._row_kind_key(which))}
        self._attach_popup_search(self.popup)

    def _popup_entries(self, p):
        q = p['edit'].value.strip().lower()
        if p['kind'] == 'list':
            out = [(v, l) for v, l in p['items'] if not q or q in l.lower() or q in str(v).lower()]
            typed = p['edit'].value.strip()
            if p['allow_custom'] and typed and not any(str(v) == typed for v, _ in p['items']):
                out.append((typed, "Use: " + typed))
            return out
        return []

    def _popup_pick(self, value):
        p = self.popup
        if p is None:
            return
        cb = p['on_pick']
        if not p.get('multi'):
            self._close_popup()
        cb(value)

    def _popup_key(self, event):
        p = self.popup
        k = event.key
        if k == pygame.K_ESCAPE:
            self._close_popup()
            return
        if p['kind'] == 'list':
            entries = self._popup_entries(p)
            if k in (pygame.K_DOWN, pygame.K_UP):
                step = 1 if k == pygame.K_DOWN else -1
                p['hi'] = int(_clamp(p['hi'] + step, 0, max(0, len(entries) - 1)))
                vis = p.get('vis_rows', 8)
                if p['hi'] < p['scroll']:
                    p['scroll'] = p['hi']
                elif p['hi'] >= p['scroll'] + vis:
                    p['scroll'] = p['hi'] - vis + 1
                return
            if k in (pygame.K_RETURN, pygame.K_KP_ENTER):
                if entries:
                    value = entries[int(_clamp(p['hi'], 0, len(entries) - 1))][0]
                    self._tracked(self._popup_pick, value)
                return
        else:
            if k in (pygame.K_RETURN, pygame.K_KP_ENTER):
                tiles = p.get('tiles') or []
                if len(tiles) == 1:
                    self._tracked(self._popup_pick, tiles[0])
                return
        if p.get('edit') is not None and self._focus is not None and self._focus.key == 'popsearch':
            self._focus.edit.key(event)

    def _popup_wheel(self, dy):
        p = self.popup
        if p['kind'] == 'list':
            total = len(self._popup_entries(p))
            vis = p.get('vis_rows', 8)
            p['scroll'] = int(_clamp(p['scroll'] - dy, 0, max(0, total - vis)))
        else:
            p['scroll'] = max(0, p['scroll'] - dy * 70)

    def _draw_popup(self, screen):
        p = self.popup
        # keep the search box focused while the popup is up
        if p.get('search', True) and (self._focus is None or self._focus.key != 'popsearch'):
            self._focus = SimpleNamespace(key='popsearch', edit=p['edit'], set=lambda v: None)
            self._focus_before = None
        if p['edit'].value != p['last_q']:
            p['last_q'] = p['edit'].value
            p['hi'] = 0
            p['scroll'] = 0
        full = pygame.Rect(0, 0, self.screen_width, self.screen_height)
        uk.draw_rect_on(screen, (0, 0, 0, 110 if p['kind'] == 'list' else 175), full, 0, 0)
        self._add_hit(full, key='backdrop', down=lambda pos: self._close_popup())
        if p['kind'] == 'list':
            self._draw_list_popup(screen, p)
        else:
            self._draw_grid_popup(screen, p)

    def _draw_list_popup(self, screen, p):
        sw, sh = self.screen_width, self.screen_height
        entries = self._popup_entries(p)
        md = self.f_md
        row_h, pad = 38, 10
        search_h = self.m_field_h if p['search'] else 0
        anchor = p['anchor']
        pw = max(300, min(520, (anchor.w if anchor else 340)))
        vis_max = 9
        n = max(1, len(entries))
        vis = min(vis_max, n)
        p['vis_rows'] = vis
        ph = pad + (search_h + 8 if search_h else 0) + vis * row_h + pad
        if anchor is not None:
            px = int(_clamp(anchor.x, 16, sw - pw - 16))
            py = anchor.bottom + 6
            if py + ph > sh - 16:
                py = max(16, anchor.y - 6 - ph)
        else:
            px, py = (sw - pw) // 2, (sh - ph) // 2
        panel = pygame.Rect(px, py, pw, ph)
        uk.draw_panel(screen, panel, bg=_T.PANEL_BG, border=_T.GOLD, border_width=1, radius=12)
        self._add_hit(panel, key='popanel')

        y = panel.y + pad
        if search_h:
            sr = pygame.Rect(panel.x + pad, y, panel.w - 2 * pad, search_h)
            self._text_field(screen, 'popsearch', sr, lambda: p['edit'].value, lambda v: None,
                             "Search or type a value...", max_len=60)
            y += search_h + 8
        p['scroll'] = int(_clamp(p['scroll'], 0, max(0, len(entries) - vis)))
        rows_area = pygame.Rect(panel.x + 6, y, panel.w - 12, vis * row_h)
        old_vp, self._vp = self._vp, rows_area
        old = self._push_clip(screen, rows_area)
        if not entries:
            msg = p['placeholder'] or "Nothing matches"
            self._text_mid(screen, md, msg, _T.TEXT_DIM, rows_area.x + 14, rows_area.y + row_h // 2,
                           max_w=rows_area.w - 28)
        for i in range(p['scroll'], min(len(entries), p['scroll'] + vis)):
            value, label = entries[i]
            rr = pygame.Rect(rows_area.x, rows_area.y + (i - p['scroll']) * row_h, rows_area.w - 8, row_h - 2)
            cur = str(value) == str(p['current']) and not label.startswith("Use: ")
            hov = self._hov(rr)
            if hov:
                p['hi'] = i
            on = i == p['hi']
            t = self._anim(('pop', i), on)
            if cur:
                self._panel(screen, rr, (48, 39, 19), _T.GOLD, 1, 8)
            elif t > 0:
                self._panel(screen, rr, uk.lerp_color(_FIELD, _CARD_HI, t),
                            uk.lerp_color(_T.CARD_BORDER, _T.GOLD, 0.6 * t), 1, 8)
            fg = _T.GOLD_BRIGHT if cur else uk.lerp_color(_T.TEXT_SECONDARY, _T.TEXT_PRIMARY, t)
            self._text_mid(screen, md, label, fg, rr.x + 14, rr.centery, dyn=True, max_w=rr.w - 28)
            self._add_hit(rr, key=('popitem', i), down=lambda pos, v=value: self._popup_pick(v))
        self._pop_clip(screen, old)
        self._vp = old_vp
        if len(entries) > vis:
            frac = p['scroll'] / max(1, len(entries) - vis)
            th = max(24, int(rows_area.h * vis / len(entries)))
            ty = rows_area.y + int((rows_area.h - th) * frac)
            uk.draw_rect_on(screen, _T.CHIP_BORDER, pygame.Rect(panel.right - 8, ty, 3, th), 0, 1)

    def _draw_grid_popup(self, screen, p):
        sw, sh = self.screen_width, self.screen_height
        which = p['which']
        accent = COND_ACCENT if which == 'c' else ACT_ACCENT
        pw = min(sw - 120, 1000)
        ph = min(sh - 90, 700)
        panel = pygame.Rect(0, 0, pw, ph)
        panel.center = (sw // 2, sh // 2)
        uk.draw_panel(screen, panel, bg=_T.PANEL_BG, border=accent, border_width=2, radius=14)
        self._add_hit(panel, key='popanel')
        pad = 24
        self._text_mid(screen, self.f_lg, p['title'].upper(), _T.TEXT_PRIMARY, panel.x + pad, panel.y + 34)
        close = pygame.Rect(panel.right - pad - 40, panel.y + 14, 40, 40)
        self._icon_btn(screen, 'grid_close', close, _ic_x, self._close_popup, tip="Close  (Esc)", size=26)
        if p.get('multi'):
            hint = "Click as many as you like"
            self._text_mid(screen, self.f_sm, hint, _T.TEXT_DIM, close.left - 18, panel.y + 34, "r")

        sr = pygame.Rect(panel.x + pad, panel.y + 66, panel.w - 2 * pad, self.m_field_h)
        self._text_field(screen, 'popsearch', sr, lambda: p['edit'].value, lambda v: None,
                         "Search...", max_len=60)
        vp = pygame.Rect(panel.x + 10, sr.bottom + 12, panel.w - 20, panel.bottom - (sr.bottom + 12) - 12)

        q = p['edit'].value.strip().lower()
        if which == 'c':
            cats = [(n, list(ks)) for n, ks in _COND_CATEGORIES]
            label_of = lambda k: CONDITION_KINDS[k]['label']
            desc_of = lambda k: _COND_DESCS.get(k, "")
        else:
            cats = [(n, list(ks)) for n, ks in _ACTION_CATEGORIES]
            listed = {k for _, ks in cats for k in ks}
            extra = [t for t in _action_type_list() if t not in listed]
            if extra:
                cats.append(("Other", extra))
            label_of = _humanize
            desc_of = lambda k: _ACTION_DESCS.get(k, "")
        valid = set(CONDITION_KINDS) if which == 'c' else set(ACTION_SCHEMA)
        cols = 3 if vp.w >= 760 else (2 if vp.w >= 500 else 1)
        gap = 12
        inner_w = vp.w - 24 - 8
        tw = (inner_w - gap * (cols - 1)) // cols
        th = max(66, self.f_md.cap_h + self.f_sm.cap_h + 38)

        layout = []      # ('cap', y, text) / ('tile', Rect(rel), key)
        y = 0
        tiles_found = []
        for name, keys in cats:
            keys = [k for k in keys if k in valid and
                    (not q or q in label_of(k).lower() or q in k.lower() or q in desc_of(k).lower())]
            if not keys:
                continue
            layout.append(('cap', y, name))
            y += self.f_sm.cap_h + 18
            for i, k in enumerate(keys):
                r, c = divmod(i, cols)
                layout.append(('tile', pygame.Rect(c * (tw + gap), y + r * (th + gap), tw, th), k))
                tiles_found.append(k)
            y += ((len(keys) + cols - 1) // cols) * (th + gap) + 10
        p['tiles'] = tiles_found
        total = y
        max_sc = max(0, total + 12 - vp.h)
        p['scroll'] = int(_clamp(p['scroll'], 0, max_sc))
        sc = p['scroll']

        old_vp, self._vp = self._vp, vp
        old = self._push_clip(screen, vp)
        ox, oy = vp.x + 12, vp.y + 6 - sc
        if not layout:
            self._text_mid(screen, self.f_md, "Nothing matches your search", _T.TEXT_DIM,
                           vp.centerx, vp.y + 50, "c")
        for item in layout:
            if item[0] == 'cap':
                self._caption(screen, item[2], ox, oy + item[1], inner_w)
                continue
            _, rel, key = item
            r = rel.move(ox, oy)
            if r.bottom < vp.y or r.y > vp.bottom:
                continue
            cur = key == p.get('current')
            t = self._anim(('tile', p['which'], key), self._hov(r))
            self._panel(screen, r, (48, 39, 19) if cur else uk.lerp_color(_CARD, _CARD_HI, t),
                        _T.GOLD if cur else uk.lerp_color(_T.CARD_BORDER, accent, 0.78 * t), 1, 10)
            if t > 0:
                uk.draw_soft_glow(screen, r.center, int(r.w * 0.45), accent, max_alpha=int(18 * t))
            fg = _T.GOLD_BRIGHT if cur else uk.lerp_color(_T.TEXT_PRIMARY, accent, 0.5 * t)
            self._text_top(screen, self.f_md, self.f_md.fit(label_of(key), r.w - 28), fg, r.x + 14, r.y + 14, dyn=True)
            d = desc_of(key)
            if d:
                self._text_top(screen, self.f_sm, self.f_sm.fit(d, r.w - 28), _T.TEXT_DIM, r.x + 14,
                               r.y + 14 + self.f_md.cap_h + 12)
            self._add_hit(r, key=('tile', p['which'], key), down=lambda pos, k=key: self._popup_pick(k))
        self._pop_clip(screen, old)
        self._vp = old_vp

        if max_sc > 0:
            track = pygame.Rect(vp.right - 8, vp.y + 4, 4, vp.h - 8)
            thh = max(30, int(track.h * vp.h / (total + 12)))
            frac = sc / max_sc
            uk.draw_rect_on(screen, (24, 28, 38), track, 0, 2)
            uk.draw_rect_on(screen, _T.CHIP_BORDER,
                            pygame.Rect(track.x, track.y + int((track.h - thh) * frac), track.w, thh), 0, 2)

    # ══════════════════════════════════════════════════════════════════════
    #  Set Spawn / Set Position — click the room to place the point
    # ══════════════════════════════════════════════════════════════════════

    def _open_spawn(self, model, row, field_name):
        """No-ops if there's no room to preview a point against yet.
        field_name is 'spawn_x' (change_map: room from the row's room_name)
        or 'x' (set_player_location: room from set_current_room())."""
        self._blur()
        if field_name == 'spawn_x':
            room_name = row['params'].get('room_name', '')
            y_field, title = 'spawn_y', 'Set spawn'
        else:
            room_name = model._current_room_name or ''
            y_field, title = 'y', 'Set position'
        if not room_name:
            return
        width, height = model._known_room_dims.get(room_name, _ROOM_PICKER_DEFAULT_DIMS)
        try:
            sx = float(row['params'].get(field_name, ''))
            sy = float(row['params'].get(y_field, ''))
        except (TypeError, ValueError):
            sx = sy = None
        preview = None
        if model._room_preview_provider is not None:
            try:
                preview = model._room_preview_provider(room_name)
            except Exception:
                preview = None
        self.spawn = {'row': row, 'field': field_name, 'y_field': y_field, 'title': title,
                      'room': room_name, 'width': width, 'height': height,
                      'known_dims': room_name in model._known_room_dims,
                      'x': sx, 'y': sy, 'preview': preview, '_scaled': None,
                      'canvas': None, 'scale': None}

    def _spawn_done(self):
        sp = self.spawn
        if sp is None or sp['x'] is None or sp['y'] is None:
            return
        params = sp['row']['params']
        params[sp['field']] = str(int(round(sp['x'])))
        params[sp['y_field']] = str(int(round(sp['y'])))
        self.spawn = None
        if self._focus is not None and str(self._focus.key).startswith('spawn_'):
            self._focus = None

    def _spawn_cancel(self):
        self.spawn = None
        if self._focus is not None and str(self._focus.key).startswith('spawn_'):
            self._focus = None

    def _draw_spawn(self, screen):
        sp = self.spawn
        sw, sh = self.screen_width, self.screen_height
        full = pygame.Rect(0, 0, sw, sh)
        uk.draw_rect_on(screen, (0, 0, 0, 185), full, 0, 0)
        self._add_hit(full, key='backdrop')
        panel = pygame.Rect(40, 34, sw - 80, sh - 68)
        uk.draw_panel(screen, panel, bg=_T.PANEL_BG, border=_T.GOLD, border_width=2, radius=14)
        self._add_hit(panel, key='popanel')
        pad = 24
        self._text_mid(screen, self.f_lg, ("%s: %s" % (sp['title'], sp['room'])).upper(), _T.TEXT_PRIMARY,
                       panel.x + pad, panel.y + 34, dyn=True, max_w=panel.w - 2 * pad - 60)
        close = pygame.Rect(panel.right - pad - 40, panel.y + 14, 40, 40)
        self._icon_btn(screen, 'spawn_close', close, _ic_x, self._spawn_cancel, tip="Cancel  (Esc)", size=26)
        self._text_top(screen, self.f_sm, "Click or drag inside the room to place the point.",
                       _T.TEXT_MUTED, panel.x + pad, panel.y + 62)
        top = panel.y + 86
        if not sp['known_dims']:
            top += self._note(screen, panel.x + pad, top, panel.w - 2 * pad,
                              "Exact room size unknown, showing a %dx%d placeholder." % (sp['width'], sp['height']))
        elif sp['preview'] is None:
            top += self._note(screen, panel.x + pad, top, panel.w - 2 * pad,
                              "No tile preview available for this room, showing a grid.")
        top += 6
        bar_h = self.m_pill_h + 8
        avail = pygame.Rect(panel.x + pad, top, panel.w - 2 * pad, panel.bottom - bar_h - 20 - top)
        scale = max(0.01, min(avail.w / sp['width'], avail.h / sp['height']))
        cw_, ch_ = max(1, int(sp['width'] * scale)), max(1, int(sp['height'] * scale))
        canvas = pygame.Rect(avail.x + (avail.w - cw_) // 2, avail.y + max(0, (avail.h - ch_) // 2), cw_, ch_)
        sp['canvas'], sp['scale'] = canvas, scale

        uk.draw_rect_on(screen, _INSET, canvas, 0, 0)
        if sp['preview'] is not None:
            cache = sp['_scaled']
            if cache is None or cache[1] != (cw_, ch_):
                try:
                    scaled = pygame.transform.smoothscale(sp['preview'], (cw_, ch_))
                except Exception:
                    scaled = pygame.transform.scale(sp['preview'], (cw_, ch_))
                sp['_scaled'] = (scaled, (cw_, ch_))
            else:
                scaled = cache[0]
            uk.blit_surface(screen, scaled, canvas.topleft, transient=False)
        else:
            step = max(16, int(64 * scale))
            for gx in range(canvas.x, canvas.right, step):
                uk.draw_line_on(screen, _HAIR, (gx, canvas.y), (gx, canvas.bottom), 1)
            for gy in range(canvas.y, canvas.bottom, step):
                uk.draw_line_on(screen, _HAIR, (canvas.x, gy), (canvas.right, gy), 1)
        uk.draw_rect_on(screen, _T.GOLD, canvas, 2, 0)

        def place(pos, canvas=canvas, scale=scale):
            sp['x'] = _clamp((pos[0] - canvas.x) / scale, 0, sp['width'])
            sp['y'] = _clamp((pos[1] - canvas.y) / scale, 0, sp['height'])

        self._add_hit(canvas, key='spawn_canvas', down=place, drag=place)
        mx, my = self._mouse
        if canvas.collidepoint((mx, my)) and self.dialog is None:
            uk.draw_line_on(screen, (*_T.KI_BLUE, 120), (canvas.x, my), (canvas.right, my), 1)
            uk.draw_line_on(screen, (*_T.KI_BLUE, 120), (mx, canvas.y), (mx, canvas.bottom), 1)
            hx, hy = (mx - canvas.x) / scale, (my - canvas.y) / scale
            self._text_top(screen, self.f_sm, "%d, %d" % (hx, hy), _T.KI_BLUE,
                           min(mx + 12, canvas.right - 90), max(canvas.y + 4, my - 22), dyn=True)
        if sp['x'] is not None and sp['y'] is not None:
            px, py = canvas.x + int(sp['x'] * scale), canvas.y + int(sp['y'] * scale)
            uk.draw_soft_glow(screen, (px, py), 26, _T.GOLD, max_alpha=70)
            uk.draw_circle_on(screen, _T.GOLD, (px, py), 7)
            uk.draw_circle_on(screen, (0, 0, 0), (px, py), 7, 2)
            lab = "%d, %d" % (sp['x'], sp['y'])
            self._text_top(screen, self.f_sm, lab, _T.GOLD_BRIGHT,
                           min(px + 12, canvas.right - self.f_sm.width(lab) - 6), max(canvas.y + 4, py - 24), dyn=True)

        # bottom bar: numeric X / Y fields + Cancel / Done
        by = panel.bottom - 20 - self.m_pill_h
        x = panel.x + pad
        for axis, label in (('x', 'X'), ('y', 'Y')):
            self._text_mid(screen, self.f_md, label, _T.TEXT_MUTED, x, by + self.m_pill_h // 2)
            fr = pygame.Rect(x + 26, by, 130, self.m_pill_h)

            def getv(a=axis):
                return '' if sp[a] is None else str(int(round(sp[a])))

            def setv(v, a=axis):
                v = v.strip()
                if v in ('', '-', '.'):
                    sp[a] = None if v == '' else sp[a]
                    return
                try:
                    lim = sp['width'] if a == 'x' else sp['height']
                    sp[a] = _clamp(float(v), 0, lim)
                except ValueError:
                    pass

            self._text_field(screen, 'spawn_' + axis, fr, getv, setv, "0", max_len=8, allowed=_num_ok)
            x = fr.right + 22
        bw = 150
        done = pygame.Rect(panel.right - pad - bw, by, bw, self.m_pill_h)
        cancel = pygame.Rect(done.x - bw - 12, by, bw, self.m_pill_h)
        ok = sp['x'] is not None and sp['y'] is not None
        self._pill(screen, 'spawn_done', done, "Done", _T.GOLD, icon=_ic_check, enabled=ok,
                   on_click=self._spawn_done, tip="Use this point  (Enter)")
        self._pill(screen, 'spawn_cancel', cancel, "Cancel", _T.TEXT_SECONDARY, on_click=self._spawn_cancel)

    # ══════════════════════════════════════════════════════════════════════
    #  Confirm dialog
    # ══════════════════════════════════════════════════════════════════════

    def _draw_dialog(self, screen):
        d = self.dialog
        r = self._dialog_rects()
        uk.draw_rect_on(screen, (0, 0, 0, 170), pygame.Rect(0, 0, self.screen_width, self.screen_height), 0, 0)
        accent = _T.DANGER_BRIGHT if d.get("danger") else _T.GOLD
        pn = r["panel"]
        uk.draw_panel(screen, pn, bg=_T.PANEL_BG, border=accent, border_width=2, radius=14)
        self._text_top(screen, self.f_lg, d["title"], _T.TEXT_PRIMARY, pn.x + r["pad"], pn.y + r["pad"])
        for i, line in enumerate(r["lines"]):
            self._text_top(screen, self.f_md, line, _T.TEXT_SECONDARY, pn.x + r["pad"], r["msg_y"] + i * r["line_h"])
        for key, rect, label, acc, icon in (
                ("d_ok", r["ok"], d["confirm_label"], accent, self._icon_png(self._trash_icon, _ic_trash) if d.get("danger") else _ic_check),
                ("d_cancel", r["cancel"], "Cancel", _T.TEXT_SECONDARY, None)):
            t = self._anim(key, rect.collidepoint(self._mouse))
            self._panel(screen, rect, uk.lerp_color(_CARD, _CARD_HI, t), uk.lerp_color(_T.CARD_BORDER, acc, 0.55 + 0.45 * t))
            fg = uk.lerp_color(_T.TEXT_SECONDARY, acc, 0.55 + 0.45 * t)
            ic = 20 if icon else 0
            tw = self.f_md.width(label)
            x = rect.centerx - (tw + (ic + 10 if icon else 0)) // 2
            if icon:
                icon(screen, pygame.Rect(x, rect.centery - 10, 20, 20), fg, 3)
                x += 30
            self._text_mid(screen, self.f_md, label, fg, x, rect.centery)


__all__ = ['EventEditorWindow', 'ConditionBuilder', 'ActionSequenceBuilder',
           'CONDITION_KINDS', 'ACTION_SCHEMA']