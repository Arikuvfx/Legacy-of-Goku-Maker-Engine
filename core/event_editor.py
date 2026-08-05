"""
core/event_editor.py

The full RPG-Maker-style Event Editor in one file: Conditions builder,
Actions builder, and the modal window that hosts both — merged from what
was previously condition_builder.py / action_sequence_builder.py /
event_editor_window.py since the three were only ever used together.

    from core.event_editor import EventEditorWindow

    self.event_editor = EventEditorWindow(flag_manager, colors=self.colors)
    self.event_editor.open(title="...", existing_conditions=[...], existing_actions=[...],
                            on_save=lambda conditions, actions: ...)
    # each frame while self.event_editor.active:
    self.event_editor.handle_input(event)
    self.event_editor.draw(screen)

ConditionBuilder and ActionSequenceBuilder are also exported individually
in case you ever want either half standalone.
"""

import copy
import json

import pygame

from core.flag_manager import (
    flag_is, flag_is_not, variable_is,
    check_item, check_stat, check_character, check_zeni, check_resource, check_skill,
    check_timer, check_boss_hp, check_bar,
)
from core.event_actions import ACTION_TYPES


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

_DEFAULT_COLORS = {
    'bg': (20, 20, 30), 'bg_transparent': (20, 20, 30, 230),
    'panel': (35, 35, 55), 'panel_light': (45, 45, 65),
    'accent': (255, 215, 0), 'accent_dim': (200, 170, 0),
    'text': (255, 255, 255), 'text_dim': (180, 180, 200), 'text_dark': (120, 120, 140),
    'grid': (60, 60, 80), 'success': (100, 255, 100),
    'delete': (255, 50, 50), 'delete_hover': (255, 100, 100),
    'input_bg': (60, 60, 75), 'input_active': (80, 80, 100),
}


# ═════════════════════════════════════════════════════════════════════════
# ConditionBuilder — the "Conditions" half
# ═════════════════════════════════════════════════════════════════════════

_DEFAULT_COLORS = {
    'bg': (20, 20, 30), 'bg_transparent': (20, 20, 30, 230),
    'panel': (35, 35, 55), 'panel_light': (45, 45, 65),
    'accent': (255, 215, 0), 'accent_dim': (200, 170, 0),
    'text': (255, 255, 255), 'text_dim': (180, 180, 200), 'text_dark': (120, 120, 140),
    'grid': (60, 60, 80), 'success': (100, 255, 100),
    'delete': (255, 50, 50), 'delete_hover': (255, 100, 100),
    'input_bg': (60, 60, 75), 'input_active': (80, 80, 100),
}

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
    'boss_hp': {
        'label': 'Boss HP',
        'fields': [('arg0', 'boss_picker', None), ('mode', 'choice', ['percent', 'value']),
                   ('cmp', 'cmp', None), ('value', 'text', None)],
        'build': lambda p: check_boss_hp(p.get('arg0', ''), p.get('cmp', '=='),
                                          _coerce(p.get('value')), mode=p.get('mode', 'percent')),
        'unbuild': _unbuild_boss_hp,
    },
}

_KIND_ORDER = ['flag', 'flag_not', 'variable', 'item', 'stat', 'character', 'zeni', 'resource', 'skill', 'timer', 'bar', 'boss_hp']


class ConditionBuilder:
    """Row-based UI for building a flat, implicitly-ANDed condition list."""

    def __init__(self, flag_manager, colors=None):
        self.flag_manager = flag_manager
        self.colors = colors or _DEFAULT_COLORS
        self.font_small = pygame.font.Font(None, 16)
        self.font_medium = pygame.font.Font(None, 20)

        self.rows = []          # list of {'kind': str, 'params': {field_name: str}}
        self.scroll_offset = 0
        self._known_flags = []
        self._known_characters = []
        self._known_skills = []
        self._known_bosses = []
        self._known_timers = []
        self._known_bars = []

        self._active_field = None      # (row_index, field_name)
        self._active_text = ""

        self._open_kind_dropdown_row = None   # row index whose "kind" dropdown is open
        self._open_flag_dropdown = None       # (row_index, field_name) whose flag picker is open
        self._open_char_dropdown = None       # (row_index, field_name) whose character picker is open
        self._open_skill_dropdown = None      # (row_index, field_name) whose skill picker is open
        self._open_boss_dropdown = None       # (row_index, field_name) whose boss picker is open
        self._open_timer_dropdown = None      # (row_index, field_name) whose timer picker is open
        self._open_bar_dropdown = None        # (row_index, field_name) whose bar picker is open
        self._add_picker_open = False         # True while the full kind grid (from "+ Add Condition") is open

        self._rects = {}        # populated by draw(), read by handle_input()

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def refresh(self, existing_conditions=None):
        """Call when opening the popup. Refreshes the known-flags list and,
        if given, rebuilds rows from a previously-saved condition list so
        editing round-trips instead of starting blank every time."""
        self._known_flags = sorted(getattr(self.flag_manager, 'flags', {}).keys())
        self._known_characters = _discover_character_ids()
        self._known_skills = _discover_skill_ids()
        self._known_bosses = _discover_boss_ids()
        self._known_timers = sorted(self.flag_manager.get_condition_names().get('timer_names', []))
        self._known_bars = sorted(self.flag_manager.get_condition_names().get('bar_names', []))
        self._active_field = None
        self._active_text = ""
        self._open_kind_dropdown_row = None
        self._open_flag_dropdown = None
        self._open_char_dropdown = None
        self._open_skill_dropdown = None
        self._open_boss_dropdown = None
        self._open_timer_dropdown = None
        self._open_bar_dropdown = None
        self._add_picker_open = False
        self.scroll_offset = 0

        if existing_conditions is None:
            return

        rows = []
        for cond in existing_conditions:
            matched = False
            for kind in _KIND_ORDER:
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
                continue  # malformed row — skip rather than crash the popup
        return result

    # ── Row management ──────────────────────────────────────────────────────

    def _add_row(self, kind='flag'):
        defaults = {}
        for field_name, field_kind, extra in CONDITION_KINDS[kind]['fields']:
            if field_kind == 'cmp':
                defaults[field_name] = '=='
            elif field_kind == 'choice':
                defaults[field_name] = extra[0]
            else:
                defaults[field_name] = ''
        self.rows.append({'kind': kind, 'params': defaults})

    def _remove_row(self, index):
        if 0 <= index < len(self.rows):
            self.rows.pop(index)
        if self._active_field and self._active_field[0] == index:
            self._active_field = None

    def _set_row_kind(self, index, kind):
        if 0 <= index < len(self.rows) and kind in CONDITION_KINDS:
            defaults = {}
            for field_name, field_kind, extra in CONDITION_KINDS[kind]['fields']:
                defaults[field_name] = '==' if field_kind == 'cmp' else (extra[0] if field_kind == 'choice' else '')
            self.rows[index] = {'kind': kind, 'params': defaults}

    # ── Input ────────────────────────────────────────────────────────────────

    def handle_input(self, event, x, y):
        if event.type == pygame.KEYDOWN:
            if self._active_field is not None:
                row_index, field_name = self._active_field
                if event.key == pygame.K_RETURN or event.key == pygame.K_ESCAPE:
                    if 0 <= row_index < len(self.rows):
                        self.rows[row_index]['params'][field_name] = self._active_text
                    self._active_field = None
                elif event.key == pygame.K_BACKSPACE:
                    self._active_text = self._active_text[:-1]
                elif event.unicode and event.unicode.isprintable():
                    if len(self._active_text) < 60:
                        self._active_text += event.unicode
                return

        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return
        mouse_pos = event.pos

        # Add-kind grid open — clicking an option adds it and stays open,
        # so multiple conditions can be added back-to-back. Any other click
        # closes it (consistent with how the other dropdowns dismiss).
        if self._add_picker_open:
            for rect, kind in self._rects.get('add_kind_grid_items', []):
                if rect.collidepoint(mouse_pos):
                    self._add_row(kind)
                    return
            self._add_picker_open = False
            return

        # Kind dropdown open — item list takes priority over everything else
        if self._open_kind_dropdown_row is not None:
            for rect, kind in self._rects.get('kind_dropdown_items', []):
                if rect.collidepoint(mouse_pos):
                    self._set_row_kind(self._open_kind_dropdown_row, kind)
                    self._open_kind_dropdown_row = None
                    return
            self._open_kind_dropdown_row = None
            return

        # Flag picker dropdown open
        if self._open_flag_dropdown is not None:
            for rect, name in self._rects.get('flag_dropdown_items', []):
                if rect.collidepoint(mouse_pos):
                    row_index, field_name = self._open_flag_dropdown
                    if 0 <= row_index < len(self.rows):
                        self.rows[row_index]['params'][field_name] = name
                    self._open_flag_dropdown = None
                    return
            self._open_flag_dropdown = None
            return

        # Character picker dropdown open
        if self._open_char_dropdown is not None:
            for rect, name in self._rects.get('char_dropdown_items', []):
                if rect.collidepoint(mouse_pos):
                    row_index, field_name = self._open_char_dropdown
                    if 0 <= row_index < len(self.rows):
                        self.rows[row_index]['params'][field_name] = name
                    self._open_char_dropdown = None
                    return
            self._open_char_dropdown = None
            return

        # Skill picker dropdown open
        if self._open_skill_dropdown is not None:
            for rect, name in self._rects.get('skill_dropdown_items', []):
                if rect.collidepoint(mouse_pos):
                    row_index, field_name = self._open_skill_dropdown
                    if 0 <= row_index < len(self.rows):
                        self.rows[row_index]['params'][field_name] = name
                    self._open_skill_dropdown = None
                    return
            self._open_skill_dropdown = None
            return

        # Boss picker dropdown open
        if self._open_boss_dropdown is not None:
            for rect, name in self._rects.get('boss_dropdown_items', []):
                if rect.collidepoint(mouse_pos):
                    row_index, field_name = self._open_boss_dropdown
                    if 0 <= row_index < len(self.rows):
                        self.rows[row_index]['params'][field_name] = name
                    self._open_boss_dropdown = None
                    return
            self._open_boss_dropdown = None
            return

        # Timer picker dropdown open
        if self._open_timer_dropdown is not None:
            for rect, name in self._rects.get('timer_dropdown_items', []):
                if rect.collidepoint(mouse_pos):
                    row_index, field_name = self._open_timer_dropdown
                    if 0 <= row_index < len(self.rows):
                        self.rows[row_index]['params'][field_name] = name
                    self._open_timer_dropdown = None
                    return
            self._open_timer_dropdown = None
            return

        # Bar picker dropdown open
        if self._open_bar_dropdown is not None:
            for rect, name in self._rects.get('bar_dropdown_items', []):
                if rect.collidepoint(mouse_pos):
                    row_index, field_name = self._open_bar_dropdown
                    if 0 <= row_index < len(self.rows):
                        self.rows[row_index]['params'][field_name] = name
                    self._open_bar_dropdown = None
                    return
            self._open_bar_dropdown = None
            return

        # Commit any open text field if the click lands elsewhere
        if self._active_field is not None:
            row_index, field_name = self._active_field
            if 0 <= row_index < len(self.rows):
                self.rows[row_index]['params'][field_name] = self._active_text
            self._active_field = None

        # "+ Add Condition" button — toggles the full kind grid
        add_rect = self._rects.get('add_condition_btn')
        if add_rect and add_rect.collidepoint(mouse_pos):
            self._add_picker_open = not self._add_picker_open
            return

        # Per-row hit testing
        for row_index, row_rects in self._rects.get('rows', []):
            kind_rect = row_rects.get('kind')
            if kind_rect and kind_rect.collidepoint(mouse_pos):
                self._open_kind_dropdown_row = row_index
                return

            delete_rect = row_rects.get('delete')
            if delete_rect and delete_rect.collidepoint(mouse_pos):
                self._remove_row(row_index)
                return

            for field_name, field_rect, field_kind, extra in row_rects.get('fields', []):
                if not field_rect.collidepoint(mouse_pos):
                    continue
                if field_kind == 'cmp':
                    row = self.rows[row_index]
                    cur = row['params'].get(field_name, '==')
                    nxt = _CMP_OPTIONS[(_CMP_OPTIONS.index(cur) + 1) % len(_CMP_OPTIONS)] \
                        if cur in _CMP_OPTIONS else '=='
                    row['params'][field_name] = nxt
                elif field_kind == 'choice':
                    row = self.rows[row_index]
                    cur = row['params'].get(field_name, extra[0])
                    nxt = extra[(extra.index(cur) + 1) % len(extra)] if cur in extra else extra[0]
                    row['params'][field_name] = nxt
                elif field_kind == 'flag_picker':
                    picker_btn = row_rects.get('flag_dropdown_btn_' + field_name)
                    if picker_btn and picker_btn.collidepoint(mouse_pos):
                        self._open_flag_dropdown = (row_index, field_name)
                    else:
                        self._active_field = (row_index, field_name)
                        self._active_text = self.rows[row_index]['params'].get(field_name, '')
                elif field_kind == 'char_picker':
                    picker_btn = row_rects.get('char_dropdown_btn_' + field_name)
                    if picker_btn and picker_btn.collidepoint(mouse_pos):
                        self._open_char_dropdown = (row_index, field_name)
                    else:
                        self._active_field = (row_index, field_name)
                        self._active_text = self.rows[row_index]['params'].get(field_name, '')
                elif field_kind == 'skill_picker':
                    picker_btn = row_rects.get('skill_dropdown_btn_' + field_name)
                    if picker_btn and picker_btn.collidepoint(mouse_pos):
                        self._open_skill_dropdown = (row_index, field_name)
                    else:
                        self._active_field = (row_index, field_name)
                        self._active_text = self.rows[row_index]['params'].get(field_name, '')
                elif field_kind == 'boss_picker':
                    picker_btn = row_rects.get('boss_dropdown_btn_' + field_name)
                    if picker_btn and picker_btn.collidepoint(mouse_pos):
                        self._open_boss_dropdown = (row_index, field_name)
                    else:
                        self._active_field = (row_index, field_name)
                        self._active_text = self.rows[row_index]['params'].get(field_name, '')
                elif field_kind == 'timer_picker':
                    picker_btn = row_rects.get('timer_dropdown_btn_' + field_name)
                    if picker_btn and picker_btn.collidepoint(mouse_pos):
                        self._open_timer_dropdown = (row_index, field_name)
                    else:
                        self._active_field = (row_index, field_name)
                        self._active_text = self.rows[row_index]['params'].get(field_name, '')
                elif field_kind == 'bar_picker':
                    picker_btn = row_rects.get('bar_dropdown_btn_' + field_name)
                    if picker_btn and picker_btn.collidepoint(mouse_pos):
                        self._open_bar_dropdown = (row_index, field_name)
                    else:
                        self._active_field = (row_index, field_name)
                        self._active_text = self.rows[row_index]['params'].get(field_name, '')
                else:  # text / number
                    self._active_field = (row_index, field_name)
                    self._active_text = self.rows[row_index]['params'].get(field_name, '')
                return

    # ── Draw ─────────────────────────────────────────────────────────────────

    def draw(self, screen, x, y, w):
        colors = self.colors
        self._rects = {'rows': []}

        header = self.font_medium.render(
            "Conditions (all must be true)" if self.rows else "Conditions (none — always true)",
            True, colors['text'])
        screen.blit(header, (x, y))
        cur_y = y + 26

        for row_index, row in enumerate(self.rows):
            row_rects = {'fields': []}
            row_y = cur_y
            row_x = x

            if row['kind'] == '__raw__':
                label = self.font_small.render("(unrecognized condition — kept as-is)", True, colors['text_dim'])
                screen.blit(label, (row_x, row_y + 6))
            else:
                spec = CONDITION_KINDS[row['kind']]

                kind_rect = pygame.Rect(row_x, row_y, 150, _FIELD_H)
                pygame.draw.rect(screen, colors['input_bg'], kind_rect, border_radius=4)
                pygame.draw.rect(screen, colors['accent'], kind_rect, 1, border_radius=4)
                kind_label = self.font_small.render(spec['label'], True, colors['text'])
                screen.blit(kind_label, (kind_rect.x + 6, kind_rect.y + 5))
                row_rects['kind'] = kind_rect

                field_x = kind_rect.right + _FIELD_GAP
                max_x = x + w - 30 - _FIELD_GAP

                # Text fields soak up whatever width is left in the row
                # instead of sitting at a fixed 90px with dead space out
                # to the delete button.
                fixed_total = sum(
                    (50 if fk == 'cmp' else 90) + _FIELD_GAP for _, fk, _ in spec['fields'])
                stretch_names = [fn for fn, fk, _ in spec['fields'] if fk == 'text']
                leftover = max_x - field_x - fixed_total
                stretch_bonus = max(0, min(280, leftover) // len(stretch_names)) if stretch_names else 0

                for field_name, field_kind, extra in spec['fields']:
                    field_w = 50 if field_kind == 'cmp' else 90
                    if field_kind == 'text':
                        field_w += stretch_bonus
                    field_rect = pygame.Rect(field_x, row_y, field_w, _FIELD_H)

                    active = self._active_field == (row_index, field_name)
                    bg = colors['input_active'] if active else colors['input_bg']
                    pygame.draw.rect(screen, bg, field_rect, border_radius=4)
                    pygame.draw.rect(screen, colors['grid'], field_rect, 1, border_radius=4)

                    if field_kind == 'flag_picker':
                        btn_rect = pygame.Rect(field_rect.right - 18, field_rect.y, 18, _FIELD_H)
                        row_rects['flag_dropdown_btn_' + field_name] = btn_rect
                        pygame.draw.rect(screen, colors['panel_light'], btn_rect, border_radius=3)
                        arrow = self.font_small.render('v', True, colors['text_dim'])
                        screen.blit(arrow, (btn_rect.x + 5, btn_rect.y + 4))
                    elif field_kind == 'char_picker':
                        btn_rect = pygame.Rect(field_rect.right - 18, field_rect.y, 18, _FIELD_H)
                        row_rects['char_dropdown_btn_' + field_name] = btn_rect
                        pygame.draw.rect(screen, colors['panel_light'], btn_rect, border_radius=3)
                        arrow = self.font_small.render('v', True, colors['text_dim'])
                        screen.blit(arrow, (btn_rect.x + 5, btn_rect.y + 4))
                    elif field_kind == 'skill_picker':
                        btn_rect = pygame.Rect(field_rect.right - 18, field_rect.y, 18, _FIELD_H)
                        row_rects['skill_dropdown_btn_' + field_name] = btn_rect
                        pygame.draw.rect(screen, colors['panel_light'], btn_rect, border_radius=3)
                        arrow = self.font_small.render('v', True, colors['text_dim'])
                        screen.blit(arrow, (btn_rect.x + 5, btn_rect.y + 4))
                    elif field_kind == 'boss_picker':
                        btn_rect = pygame.Rect(field_rect.right - 18, field_rect.y, 18, _FIELD_H)
                        row_rects['boss_dropdown_btn_' + field_name] = btn_rect
                        pygame.draw.rect(screen, colors['panel_light'], btn_rect, border_radius=3)
                        arrow = self.font_small.render('v', True, colors['text_dim'])
                        screen.blit(arrow, (btn_rect.x + 5, btn_rect.y + 4))
                    elif field_kind == 'timer_picker':
                        btn_rect = pygame.Rect(field_rect.right - 18, field_rect.y, 18, _FIELD_H)
                        row_rects['timer_dropdown_btn_' + field_name] = btn_rect
                        pygame.draw.rect(screen, colors['panel_light'], btn_rect, border_radius=3)
                        arrow = self.font_small.render('v', True, colors['text_dim'])
                        screen.blit(arrow, (btn_rect.x + 5, btn_rect.y + 4))
                    elif field_kind == 'bar_picker':
                        btn_rect = pygame.Rect(field_rect.right - 18, field_rect.y, 18, _FIELD_H)
                        row_rects['bar_dropdown_btn_' + field_name] = btn_rect
                        pygame.draw.rect(screen, colors['panel_light'], btn_rect, border_radius=3)
                        arrow = self.font_small.render('v', True, colors['text_dim'])
                        screen.blit(arrow, (btn_rect.x + 5, btn_rect.y + 4))

                    display = self._active_text if active else row['params'].get(field_name, '')
                    if not active and not display:
                        display = _placeholder_for(field_name, field_kind, row['kind'])
                    clip = pygame.Rect(field_rect.x + 4, field_rect.y, field_rect.w - 8, field_rect.h)
                    screen.set_clip(clip)
                    text_surf = self.font_small.render(str(display), True, colors['text'])
                    screen.blit(text_surf, (field_rect.x + 4, field_rect.y + 5))
                    screen.set_clip(None)

                    row_rects['fields'].append((field_name, field_rect, field_kind, extra))
                    field_x = field_rect.right + _FIELD_GAP

            delete_rect = pygame.Rect(x + w - 26, row_y, 20, _FIELD_H)
            pygame.draw.rect(screen, colors['delete'], delete_rect, border_radius=4)
            x_label = self.font_small.render('X', True, colors['text'])
            screen.blit(x_label, x_label.get_rect(center=delete_rect.center))
            row_rects['delete'] = delete_rect

            self._rects['rows'].append((row_index, row_rects))
            cur_y += _ROW_H

            # Open dropdown slots in right after its own row, pushing
            # everything below (later rows, Add button) down to fit —
            # rather than floating below the Add button at the bottom.
            if self._open_kind_dropdown_row == row_index:
                self._draw_kind_dropdown(screen, x, cur_y)
                cur_y += len(_KIND_ORDER) * 22
            elif self._open_flag_dropdown is not None and self._open_flag_dropdown[0] == row_index:
                names = self._known_flags or ['(no flags used yet)']
                self._draw_flag_dropdown(screen, x, cur_y)
                cur_y += len(names[:8]) * 22
            elif self._open_char_dropdown is not None and self._open_char_dropdown[0] == row_index:
                names = self._known_characters or ['(no characters found)']
                self._draw_char_dropdown(screen, x, cur_y)
                cur_y += len(names[:8]) * 22
            elif self._open_skill_dropdown is not None and self._open_skill_dropdown[0] == row_index:
                names = self._known_skills or ['(no skills found)']
                self._draw_skill_dropdown(screen, x, cur_y)
                cur_y += len(names[:8]) * 22
            elif self._open_boss_dropdown is not None and self._open_boss_dropdown[0] == row_index:
                names = self._known_bosses or ['(no bosses found)']
                self._draw_boss_dropdown(screen, x, cur_y)
                cur_y += len(names[:8]) * 22
            elif self._open_timer_dropdown is not None and self._open_timer_dropdown[0] == row_index:
                names = self._known_timers or ['(no timers made yet)']
                self._draw_timer_dropdown(screen, x, cur_y)
                cur_y += len(names[:8]) * 22
            elif self._open_bar_dropdown is not None and self._open_bar_dropdown[0] == row_index:
                names = self._known_bars or ['(no bars made yet)']
                self._draw_bar_dropdown(screen, x, cur_y)
                cur_y += len(names[:8]) * 22

        if self._add_picker_open:
            cur_y += self._draw_add_kind_grid(screen, x, cur_y, w)

        add_rect = pygame.Rect(x, cur_y, 160, _FIELD_H)
        pygame.draw.rect(screen, colors['input_bg'], add_rect, border_radius=4)
        pygame.draw.rect(screen, colors['success'], add_rect, 1, border_radius=4)
        add_label = self.font_small.render("+ Add Condition", True, colors['success'])
        screen.blit(add_label, (add_rect.x + 8, add_rect.y + 5))
        self._rects['add_condition_btn'] = add_rect
        cur_y += _ROW_H

        self._content_height = cur_y - y

    def _draw_add_kind_grid(self, screen, x, y, w):
        colors = self.colors
        item_w, item_h, gap = 170, 26, 6
        cols = max(1, (w + gap) // (item_w + gap))
        items = []
        for i, kind in enumerate(_KIND_ORDER):
            col, row = i % cols, i // cols
            rect = pygame.Rect(x + col * (item_w + gap), y + row * (item_h + gap), item_w, item_h)
            pygame.draw.rect(screen, colors['panel_light'], rect, border_radius=4)
            pygame.draw.rect(screen, colors['accent'], rect, 1, border_radius=4)
            clip = pygame.Rect(rect.x + 4, rect.y, rect.w - 8, rect.h)
            screen.set_clip(clip)
            label = self.font_small.render(CONDITION_KINDS[kind]['label'], True, colors['text'])
            screen.blit(label, (rect.x + 6, rect.y + 5))
            screen.set_clip(None)
            items.append((rect, kind))
        self._rects['add_kind_grid_items'] = items
        rows_used = (len(_KIND_ORDER) + cols - 1) // cols
        return rows_used * (item_h + gap)

    def _draw_kind_dropdown(self, screen, x, list_y):
        colors = self.colors
        items = []
        list_w = 180
        list_rect = pygame.Rect(x, list_y, list_w, len(_KIND_ORDER) * 22)
        pygame.draw.rect(screen, colors['panel_light'], list_rect)
        pygame.draw.rect(screen, colors['accent'], list_rect, 2)
        for i, kind in enumerate(_KIND_ORDER):
            item_rect = pygame.Rect(x, list_y + i * 22, list_w, 22)
            label = self.font_small.render(CONDITION_KINDS[kind]['label'], True, colors['text'])
            screen.blit(label, (item_rect.x + 6, item_rect.y + 4))
            items.append((item_rect, kind))
        self._rects['kind_dropdown_items'] = items

    def _draw_flag_dropdown(self, screen, x, list_y):
        colors = self.colors
        items = []
        list_w = 180
        names = self._known_flags or ['(no flags used yet)']
        visible = names[:8]
        list_rect = pygame.Rect(x, list_y, list_w, len(visible) * 22)
        pygame.draw.rect(screen, colors['panel_light'], list_rect)
        pygame.draw.rect(screen, colors['accent'], list_rect, 2)
        for i, name in enumerate(visible):
            item_rect = pygame.Rect(x, list_y + i * 22, list_w, 22)
            label = self.font_small.render(name, True, colors['text'])
            screen.blit(label, (item_rect.x + 6, item_rect.y + 4))
            if self._known_flags:
                items.append((item_rect, name))
        self._rects['flag_dropdown_items'] = items

    def _draw_char_dropdown(self, screen, x, list_y):
        colors = self.colors
        items = []
        list_w = 180
        names = self._known_characters or ['(no characters found)']
        visible = names[:8]
        list_rect = pygame.Rect(x, list_y, list_w, len(visible) * 22)
        pygame.draw.rect(screen, colors['panel_light'], list_rect)
        pygame.draw.rect(screen, colors['accent'], list_rect, 2)
        for i, name in enumerate(visible):
            item_rect = pygame.Rect(x, list_y + i * 22, list_w, 22)
            label = self.font_small.render(name, True, colors['text'])
            screen.blit(label, (item_rect.x + 6, item_rect.y + 4))
            if self._known_characters:
                items.append((item_rect, name))
        self._rects['char_dropdown_items'] = items

    def _draw_skill_dropdown(self, screen, x, list_y):
        colors = self.colors
        items = []
        list_w = 180
        names = self._known_skills or ['(no skills found)']
        visible = names[:8]
        list_rect = pygame.Rect(x, list_y, list_w, len(visible) * 22)
        pygame.draw.rect(screen, colors['panel_light'], list_rect)
        pygame.draw.rect(screen, colors['accent'], list_rect, 2)
        for i, name in enumerate(visible):
            item_rect = pygame.Rect(x, list_y + i * 22, list_w, 22)
            label = self.font_small.render(name, True, colors['text'])
            screen.blit(label, (item_rect.x + 6, item_rect.y + 4))
            if self._known_skills:
                items.append((item_rect, name))
        self._rects['skill_dropdown_items'] = items

    def _draw_boss_dropdown(self, screen, x, list_y):
        colors = self.colors
        items = []
        list_w = 180
        names = self._known_bosses or ['(no bosses found)']
        visible = names[:8]
        list_rect = pygame.Rect(x, list_y, list_w, len(visible) * 22)
        pygame.draw.rect(screen, colors['panel_light'], list_rect)
        pygame.draw.rect(screen, colors['accent'], list_rect, 2)
        for i, name in enumerate(visible):
            item_rect = pygame.Rect(x, list_y + i * 22, list_w, 22)
            label = self.font_small.render(name, True, colors['text'])
            screen.blit(label, (item_rect.x + 6, item_rect.y + 4))
            if self._known_bosses:
                items.append((item_rect, name))
        self._rects['boss_dropdown_items'] = items

    def _draw_timer_dropdown(self, screen, x, list_y):
        colors = self.colors
        items = []
        list_w = 180
        names = self._known_timers or ['(no timers made yet)']
        visible = names[:8]
        list_rect = pygame.Rect(x, list_y, list_w, len(visible) * 22)
        pygame.draw.rect(screen, colors['panel_light'], list_rect)
        pygame.draw.rect(screen, colors['accent'], list_rect, 2)
        for i, name in enumerate(visible):
            item_rect = pygame.Rect(x, list_y + i * 22, list_w, 22)
            label = self.font_small.render(name, True, colors['text'])
            screen.blit(label, (item_rect.x + 6, item_rect.y + 4))
            if self._known_timers:
                items.append((item_rect, name))
        self._rects['timer_dropdown_items'] = items

    def _draw_bar_dropdown(self, screen, x, list_y):
        colors = self.colors
        items = []
        list_w = 180
        names = self._known_bars or ['(no bars made yet)']
        visible = names[:8]
        list_rect = pygame.Rect(x, list_y, list_w, len(visible) * 22)
        pygame.draw.rect(screen, colors['panel_light'], list_rect)
        pygame.draw.rect(screen, colors['accent'], list_rect, 2)
        for i, name in enumerate(visible):
            item_rect = pygame.Rect(x, list_y + i * 22, list_w, 22)
            label = self.font_small.render(name, True, colors['text'])
            screen.blit(label, (item_rect.x + 6, item_rect.y + 4))
            if self._known_bars:
                items.append((item_rect, name))
        self._rects['bar_dropdown_items'] = items

    def content_height(self):
        """Total drawn height from the last draw() call, for the caller to
        size the popup window around."""
        return getattr(self, '_content_height', 40)

# ═════════════════════════════════════════════════════════════════════════
# ActionSequenceBuilder — the "Command list" half
# ═════════════════════════════════════════════════════════════════════════

_FIELD_H = 24
_FIELD_GAP = 6
_ROW_GAP = 6
_TYPE_DROPDOWN_VISIBLE = 10
_MUSIC_DROPDOWN_VISIBLE = 8
_SOUND_DROPDOWN_VISIBLE = 8

# ─────────────────────────────────────────────────────────────────────────────
# Per-action-type field schema. field_kind: 'text' | 'number' | 'bool' |
# 'choice' (extra=options list) | 'json' (raw JSON text, for list/dict params)
# Params not listed here (e.g. 'blocking', which some builders set from a
# 'wait' flag) are left out of the row editor and just passed through as
# whatever the builder function defaults to.
# ─────────────────────────────────────────────────────────────────────────────

ACTION_SCHEMA = {
    'dialogue_box': [('speaker_type', 'choice', ['character', 'narrator', 'info']),
                      ('speaker_name', 'text', None), ('text', 'text', None),
                      ('portrait', 'portrait_picker', None)],
    'set_portrait': [('character_name', 'text', None), ('portrait_id', 'portrait_picker', None)],
    'dialogue_choice': [('prompt', 'text', None)],
    'timer_start': [('timer_id', 'text', None), ('duration', 'number', None)],
    'timer_pause': [('timer_id', 'text', None)],
    'timer_stop': [('timer_id', 'text', None)],
    'zeni': [('mode', 'choice', ['set', 'add', 'remove']), ('amount', 'number', None)],
    'item': [('mode', 'choice', ['add', 'remove']), ('item_id', 'text', None), ('quantity', 'number', None)],
    'level': [('mode', 'choice', ['set', 'add', 'remove']), ('amount', 'number', None), ('character_id', 'char_picker', None)],
    'exp': [('mode', 'choice', ['set', 'add', 'remove']), ('amount', 'number', None), ('character_id', 'char_picker', None)],
    'stat': [('mode', 'choice', ['set', 'add', 'remove']), ('stat_name', 'choice', _STAT_KEYS),
             ('amount', 'number', None), ('character_id', 'char_picker', None)],
    'resource': [('mode', 'choice', ['set', 'add', 'remove']),
                 ('resource_name', 'choice', ['health', 'energy', 'transformation_gauge']),
                 ('amount', 'number', None)],
    'skill': [('mode', 'choice', ['add', 'remove']), ('skill_id', 'skill_picker', None)],
    'transformation': [('mode', 'choice', ['add', 'remove']), ('form_id', 'transformation_picker', None)],
    'set_player_character': [('character_id', 'char_picker', None), ('skin_id', 'skin_picker', None)],
    'set_player_skin': [('skin_id', 'skin_picker', None)],
    'character_list': [('mode', 'choice', ['add', 'remove']), ('character_id', 'char_picker', None)],
    'screen_fade': [('direction', 'choice', ['in', 'out']), ('duration', 'number', None)],
    'screen_shake': [('intensity', 'number', None), ('duration', 'number', None)],
    'spam_qte': [('qte_id', 'text', None), ('fill_per_press', 'number', None),
                 ('drain_rate', 'number', None), ('start_progress', 'number', None)],
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


class ActionSequenceBuilder:
    """Row-based UI for building an ordered action list."""

    def __init__(self, colors=None):
        self.colors = colors or _DEFAULT_COLORS
        self.font_small = pygame.font.Font(None, 16)
        self.font_medium = pygame.font.Font(None, 20)

        self.rows = []   # list of {'type': action_type, 'params': {field_name: str}}
                          # dialogue_choice rows also carry '_options':
                          # [{'text': str, 'actions': [...]}]

        self._active_field = None      # (row_index, field_name)
        self._active_text = ""

        self._active_option_field = None   # (row_index, option_index) — editing an option's label
        self._active_option_text = ""

        self._open_type_dropdown_row = None
        self._type_dropdown_scroll = 0
        self._open_portrait_dropdown = None   # (row_index, field_name) whose portrait picker is open
        self._known_portraits = []
        self._open_char_dropdown = None       # (row_index, field_name) whose character picker is open
        self._known_characters = []
        self._open_enemy_dropdown = None      # (row_index, field_name) whose enemy picker is open
        self._known_enemies = []
        self._open_npc_dropdown = None        # (row_index, field_name) whose npc picker is open
        self._known_npcs = []
        self._open_cutscene_dropdown = None   # (row_index, field_name) whose cutscene picker is open
        self._known_cutscenes = []
        self._open_skill_dropdown = None      # (row_index, field_name) whose skill picker is open
        self._known_skills = []
        self._open_transformation_dropdown = None  # (row_index, field_name) whose transformation picker is open
        # Which character's equipped-skill list backs the skill_id picker's
        # 'add'/'remove' options — set externally via set_current_character()
        # (e.g. by the room editor, from self.player.character) since this
        # builder has no live game state of its own to read it from.
        self._current_character_id = None
        # Optional callable returning the character's LIVE equipped-skill
        # list (e.g. lambda: self.player.equipped_attacks), also supplied via
        # set_current_character(). Runtime 'skill' actions mutate the live
        # player object directly and are never written back to the
        # character-creator's saved config, so falling back to
        # _discover_equipped_skills() (which reads that saved config from
        # disk) would show stale data — skills added at runtime wouldn't
        # show up as equipped, and the 'add' filter wouldn't exclude them.
        self._get_equipped_skills = None
        # Same idea as _get_equipped_skills, but for the transformation
        # picker's 'add'/'remove' options — a callable returning the LIVE
        # unlocked-forms list (e.g. lambda: self.player.
        # unlocked_transformations), also supplied via
        # set_current_character(). Falls back to treating every configured
        # form as unlocked (see _transformation_choices_for_row()) when
        # unset, matching the default-fully-unlocked behavior in
        # game.py's _reload_attack_config().
        self._get_unlocked_transformations = None
        # Which room the 'x' position_picker for set_player_location should
        # preview/place against — set externally via set_current_room()
        # (e.g. by the room editor/trigger box editor, from the room the
        # event/trigger box actually lives in), since unlike change_map's
        # spawn point this action has no room_name field of its own to
        # read a target room from — it always affects the current room.
        self._current_room_name = None
        self._open_skin_dropdown = None       # (row_index, field_name) whose skin/costume picker is open
        self._open_animation_dropdown = None  # (row_index, field_name) whose animation picker is open
        self._known_weather_types = []
        self._open_weather_dropdown = None    # (row_index, field_name) whose weather picker is open
        self._known_music_tracks = []
        self._open_music_dropdown = None      # (row_index, field_name) whose music picker is open
        self._music_dropdown_scroll = 0
        self._known_sound_effects = []
        self._open_sound_dropdown = None      # (row_index, field_name) whose sound picker is open
        self._sound_dropdown_scroll = 0
        self._open_room_dropdown = None       # (row_index, field_name) whose room picker is open
        self._known_rooms = []
        self._open_world_map_dropdown = None  # (row_index, field_name) whose world_map_location map_name picker is open
        self._known_world_maps = []
        self._open_wm_location_dropdown = None  # (row_index, field_name) whose world_map_location name picker is open — choices are row-scoped, see _wm_location_choices_for_row()
        # room_name -> (width, height) in world units, set externally via
        # set_known_rooms() (e.g. by the room editor, from the live
        # RoomManager) — used to scale the Set Spawn preview canvas.
        # Rooms missing here fall back to _ROOM_PICKER_DEFAULT_DIMS.
        self._known_room_dims = {}
        # Optional callable (room_name -> pygame.Surface | None) for
        # rendering an actual tile preview in the Set Spawn overlay — set
        # externally via set_room_preview_provider(). Falls back to a
        # plain grid rectangle when unset/returns None.
        self._room_preview_provider = None
        # Active "Set Spawn" mouse-picking overlay state (see
        # _open_spawn_picker()/_draw_spawn_picker() below), or None when
        # closed — same shape as _option_editor one level down.
        self._spawn_picker = None
        self._add_picker_open = False   # True while the full type grid (from "+ Add Command") is open

        # Nested action-list editor for a single dialogue_choice option:
        # {'row_index':, 'option_index':, 'builder': ActionSequenceBuilder,
        #  'origin': (x, y), 'save_rect':, 'cancel_rect':} while open, else None.
        self._option_editor = None

        self._rects = {}

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

    def refresh(self, existing_actions=None):
        self._active_field = None
        self._active_text = ""
        self._active_option_field = None
        self._active_option_text = ""
        self._open_type_dropdown_row = None
        self._open_portrait_dropdown = None
        self._known_portraits = _discover_portrait_ids()
        self._open_char_dropdown = None
        self._known_characters = _discover_character_ids()
        self._open_enemy_dropdown = None
        self._known_enemies = _discover_enemy_ids()
        self._open_npc_dropdown = None
        self._known_npcs = _discover_npc_ids()
        self._open_cutscene_dropdown = None
        self._known_cutscenes = _discover_cutscene_ids()
        self._open_skill_dropdown = None
        self._known_skills = _discover_skill_ids()
        self._open_transformation_dropdown = None
        self._open_skin_dropdown = None
        self._open_animation_dropdown = None
        self._known_weather_types = _discover_weather_types()
        self._open_weather_dropdown = None
        self._known_music_tracks = _discover_music_tracks()
        self._open_music_dropdown = None
        self._music_dropdown_scroll = 0
        self._known_sound_effects = _discover_sound_effects()
        self._open_sound_dropdown = None
        self._sound_dropdown_scroll = 0
        self._open_room_dropdown = None
        # Unlike _known_characters/_known_skills above (which re-scan disk
        # fresh every refresh() since that scan IS their source of truth),
        # _known_rooms' real source of truth is the live push from
        # set_known_rooms() (see there) — refresh() runs every time this
        # popup opens, which is *after* the host's sync call, so
        # unconditionally overwriting here would wipe out that live list
        # right before it's needed. Only fall back to the disk scan if
        # nothing's ever been pushed.
        if not self._known_rooms:
            self._known_rooms = _discover_room_names()
        self._open_world_map_dropdown = None
        self._known_world_maps = _discover_world_map_names()
        self._open_wm_location_dropdown = None
        self._spawn_picker = None
        self._add_picker_open = False
        self._option_editor = None

        if existing_actions is None:
            return

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
                # 'spawn_picker' field) but has no schema entry of its own
                # — see the ACTION_SCHEMA comment on 'change_map'.
                raw_spawn_y = action.get('spawn_y')
                params['spawn_y'] = '' if raw_spawn_y is None else str(raw_spawn_y)
            if action_type == 'set_player_location':
                # y rides along with x (the schema-registered
                # 'position_picker' field) — see the ACTION_SCHEMA comment
                # on 'set_player_location'.
                raw_y = action.get('y')
                params['y'] = '' if raw_y is None else str(raw_y)
            if action_type == 'dialogue_choice':
                row['_options'] = _clone_options(action.get('options'))
            rows.append(row)
        self.rows = rows

    def get_action_list(self):
        result = []
        for row in self.rows:
            if row['type'] == '__raw__':
                result.append(row.get('_raw'))
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
                    # spawn_y's companion to the spawn_x 'spawn_picker'
                    # field above — see the ACTION_SCHEMA comment.
                    action['spawn_y'] = _coerce_number(row['params'].get('spawn_y', ''))
                if row['type'] == 'set_player_location':
                    # y's companion to the x 'position_picker' field above
                    # — see the ACTION_SCHEMA comment.
                    action['y'] = _coerce_number(row['params'].get('y', ''))
                if row['type'] == 'dialogue_choice':
                    action['options'] = _clone_options(row.get('_options'))
                result.append(action)
            except Exception:
                continue  # malformed row (usually bad JSON) — skip rather than crash
        return result

    # ── Row management ──────────────────────────────────────────────────────

    def _add_row(self, action_type='dialogue_box'):
        row = {'type': action_type, 'params': self._defaults_for(action_type)}
        if action_type == 'dialogue_choice':
            row['_options'] = []
        self.rows.append(row)

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

    def _remove_row(self, index):
        if 0 <= index < len(self.rows):
            self.rows.pop(index)
        if self._active_field and self._active_field[0] == index:
            self._active_field = None

    def _move_row(self, index, delta):
        new_index = index + delta
        if 0 <= index < len(self.rows) and 0 <= new_index < len(self.rows):
            self.rows[index], self.rows[new_index] = self.rows[new_index], self.rows[index]

    def _set_row_type(self, index, action_type):
        if 0 <= index < len(self.rows):
            row = {'type': action_type, 'params': self._defaults_for(action_type)}
            if action_type == 'dialogue_choice':
                row['_options'] = []
            self.rows[index] = row

    # ── dialogue_choice option management ────────────────────────────────────

    def _add_option(self, row_index):
        if 0 <= row_index < len(self.rows):
            self.rows[row_index].setdefault('_options', []).append({'text': '', 'actions': []})

    def _remove_option(self, row_index, option_index):
        if 0 <= row_index < len(self.rows):
            options = self.rows[row_index].get('_options', [])
            if 0 <= option_index < len(options):
                options.pop(option_index)
        if self._active_option_field == (row_index, option_index):
            self._active_option_field = None

    def _move_option(self, row_index, option_index, delta):
        if 0 <= row_index < len(self.rows):
            options = self.rows[row_index].get('_options', [])
            new_index = option_index + delta
            if 0 <= option_index < len(options) and 0 <= new_index < len(options):
                options[option_index], options[new_index] = options[new_index], options[option_index]

    def _open_option_editor(self, row_index, option_index):
        if not (0 <= row_index < len(self.rows)):
            return
        options = self.rows[row_index].get('_options', [])
        if not (0 <= option_index < len(options)):
            return
        builder = ActionSequenceBuilder(colors=self.colors)
        builder.refresh(options[option_index].get('actions', []))
        self._option_editor = {'row_index': row_index, 'option_index': option_index, 'builder': builder}

    def _close_option_editor(self, save):
        editor = self._option_editor
        if editor is None:
            return
        if save:
            builder = editor['builder']
            active = getattr(builder, '_active_field', None)
            if active is not None:
                r, f = active
                if 0 <= r < len(builder.rows):
                    builder.rows[r]['params'][f] = builder._active_text
                builder._active_field = None
            row_index, option_index = editor['row_index'], editor['option_index']
            if 0 <= row_index < len(self.rows):
                options = self.rows[row_index].get('_options', [])
                if 0 <= option_index < len(options):
                    options[option_index]['actions'] = builder.get_action_list()
        self._option_editor = None

    def _handle_option_editor_input(self, event):
        """The nested per-option action editor owns all input while open —
        same shape as EventEditorWindow.handle_input's own Escape/Save/Cancel
        handling, one level down."""
        editor = self._option_editor

        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self._close_option_editor(save=False)
            return

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            save_rect = editor.get('save_rect')
            if save_rect and save_rect.collidepoint(event.pos):
                self._close_option_editor(save=True)
                return
            cancel_rect = editor.get('cancel_rect')
            if cancel_rect and cancel_rect.collidepoint(event.pos):
                self._close_option_editor(save=False)
                return

        editor['builder'].handle_input(event, *editor.get('origin', (0, 0)))

    # ── Input ────────────────────────────────────────────────────────────────

    def handle_input(self, event, x, y):
        if self._spawn_picker is not None:
            self._handle_spawn_picker_input(event)
            return

        if self._option_editor is not None:
            self._handle_option_editor_input(event)
            return

        if event.type == pygame.KEYDOWN:
            if self._active_option_field is not None:
                row_index, option_index = self._active_option_field
                if event.key == pygame.K_RETURN or event.key == pygame.K_ESCAPE:
                    options = self.rows[row_index].get('_options', []) if 0 <= row_index < len(self.rows) else []
                    if 0 <= option_index < len(options):
                        options[option_index]['text'] = self._active_option_text
                    self._active_option_field = None
                elif event.key == pygame.K_BACKSPACE:
                    self._active_option_text = self._active_option_text[:-1]
                elif event.unicode and event.unicode.isprintable():
                    if len(self._active_option_text) < 120:
                        self._active_option_text += event.unicode
                return

            if self._active_field is not None:
                row_index, field_name = self._active_field
                if event.key == pygame.K_RETURN or event.key == pygame.K_ESCAPE:
                    if 0 <= row_index < len(self.rows):
                        self.rows[row_index]['params'][field_name] = self._active_text
                    self._active_field = None
                elif event.key == pygame.K_BACKSPACE:
                    self._active_text = self._active_text[:-1]
                elif event.unicode and event.unicode.isprintable():
                    if len(self._active_text) < 300:
                        self._active_text += event.unicode
            return

        if event.type == pygame.MOUSEWHEEL and self._open_type_dropdown_row is not None:
            max_scroll = max(0, len(ACTION_TYPES) - _TYPE_DROPDOWN_VISIBLE)
            self._type_dropdown_scroll = max(0, min(self._type_dropdown_scroll - event.y, max_scroll))
            return

        if event.type == pygame.MOUSEWHEEL and self._open_music_dropdown is not None:
            max_scroll = max(0, len(self._known_music_tracks) - _MUSIC_DROPDOWN_VISIBLE)
            self._music_dropdown_scroll = max(0, min(self._music_dropdown_scroll - event.y, max_scroll))
            return

        if event.type == pygame.MOUSEWHEEL and self._open_sound_dropdown is not None:
            max_scroll = max(0, len(self._known_sound_effects) - _SOUND_DROPDOWN_VISIBLE)
            self._sound_dropdown_scroll = max(0, min(self._sound_dropdown_scroll - event.y, max_scroll))
            return

        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return
        mouse_pos = event.pos

        # Add-type grid open — clicking an option adds it and stays open,
        # so multiple actions can be added back-to-back.
        if self._add_picker_open:
            for rect, action_type in self._rects.get('add_type_grid_items', []):
                if rect.collidepoint(mouse_pos):
                    self._add_row(action_type)
                    return
            self._add_picker_open = False
            return

        if self._open_type_dropdown_row is not None:
            for rect, action_type in self._rects.get('type_dropdown_items', []):
                if rect.collidepoint(mouse_pos):
                    self._set_row_type(self._open_type_dropdown_row, action_type)
                    self._open_type_dropdown_row = None
                    self._type_dropdown_scroll = 0
                    return
            self._open_type_dropdown_row = None
            self._type_dropdown_scroll = 0
            return

        # Portrait picker dropdown open
        if self._open_portrait_dropdown is not None:
            for rect, name in self._rects.get('portrait_dropdown_items', []):
                if rect.collidepoint(mouse_pos):
                    row_index, field_name = self._open_portrait_dropdown
                    if 0 <= row_index < len(self.rows):
                        self.rows[row_index]['params'][field_name] = name
                    self._open_portrait_dropdown = None
                    return
            self._open_portrait_dropdown = None
            return

        # Character picker dropdown open
        if self._open_char_dropdown is not None:
            for rect, name in self._rects.get('char_dropdown_items', []):
                if rect.collidepoint(mouse_pos):
                    row_index, field_name = self._open_char_dropdown
                    if 0 <= row_index < len(self.rows):
                        self.rows[row_index]['params'][field_name] = name
                    self._open_char_dropdown = None
                    return
            self._open_char_dropdown = None
            return

        # Enemy picker dropdown open
        if self._open_enemy_dropdown is not None:
            for rect, name in self._rects.get('enemy_dropdown_items', []):
                if rect.collidepoint(mouse_pos):
                    row_index, field_name = self._open_enemy_dropdown
                    if 0 <= row_index < len(self.rows):
                        self.rows[row_index]['params'][field_name] = name
                    self._open_enemy_dropdown = None
                    return
            self._open_enemy_dropdown = None
            return

        # NPC picker dropdown open
        if self._open_npc_dropdown is not None:
            for rect, name in self._rects.get('npc_dropdown_items', []):
                if rect.collidepoint(mouse_pos):
                    row_index, field_name = self._open_npc_dropdown
                    if 0 <= row_index < len(self.rows):
                        self.rows[row_index]['params'][field_name] = name
                    self._open_npc_dropdown = None
                    return
            self._open_npc_dropdown = None
            return

        # Cutscene picker dropdown open
        if self._open_cutscene_dropdown is not None:
            for rect, name in self._rects.get('cutscene_dropdown_items', []):
                if rect.collidepoint(mouse_pos):
                    row_index, field_name = self._open_cutscene_dropdown
                    if 0 <= row_index < len(self.rows):
                        self.rows[row_index]['params'][field_name] = name
                    self._open_cutscene_dropdown = None
                    return
            self._open_cutscene_dropdown = None
            return

        # Skill picker dropdown open
        if self._open_skill_dropdown is not None:
            for rect, name in self._rects.get('skill_dropdown_items', []):
                if rect.collidepoint(mouse_pos):
                    row_index, field_name = self._open_skill_dropdown
                    if 0 <= row_index < len(self.rows):
                        self.rows[row_index]['params'][field_name] = name
                    self._open_skill_dropdown = None
                    return
            self._open_skill_dropdown = None
            return

        # Transformation picker dropdown open
        if self._open_transformation_dropdown is not None:
            for rect, name in self._rects.get('transformation_dropdown_items', []):
                if rect.collidepoint(mouse_pos):
                    row_index, field_name = self._open_transformation_dropdown
                    if 0 <= row_index < len(self.rows):
                        self.rows[row_index]['params'][field_name] = name
                    self._open_transformation_dropdown = None
                    return
            self._open_transformation_dropdown = None
            return

        # Skin/costume picker dropdown open
        if self._open_skin_dropdown is not None:
            for rect, name in self._rects.get('skin_dropdown_items', []):
                if rect.collidepoint(mouse_pos):
                    row_index, field_name = self._open_skin_dropdown
                    if 0 <= row_index < len(self.rows):
                        self.rows[row_index]['params'][field_name] = name
                    self._open_skin_dropdown = None
                    return
            self._open_skin_dropdown = None
            return

        # Animation picker dropdown open
        if self._open_animation_dropdown is not None:
            for rect, name in self._rects.get('animation_dropdown_items', []):
                if rect.collidepoint(mouse_pos):
                    row_index, field_name = self._open_animation_dropdown
                    if 0 <= row_index < len(self.rows):
                        self.rows[row_index]['params'][field_name] = name
                    self._open_animation_dropdown = None
                    return
            self._open_animation_dropdown = None
            return

        # Weather picker dropdown open
        if self._open_weather_dropdown is not None:
            for rect, name in self._rects.get('weather_dropdown_items', []):
                if rect.collidepoint(mouse_pos):
                    row_index, field_name = self._open_weather_dropdown
                    if 0 <= row_index < len(self.rows):
                        self.rows[row_index]['params'][field_name] = name
                    self._open_weather_dropdown = None
                    return
            self._open_weather_dropdown = None
            return

        # Music picker dropdown open
        if self._open_music_dropdown is not None:
            for rect, name in self._rects.get('music_dropdown_items', []):
                if rect.collidepoint(mouse_pos):
                    row_index, field_name = self._open_music_dropdown
                    if 0 <= row_index < len(self.rows):
                        self.rows[row_index]['params'][field_name] = name
                    self._open_music_dropdown = None
                    self._music_dropdown_scroll = 0
                    return
            self._open_music_dropdown = None
            self._music_dropdown_scroll = 0
            return

        # Sound picker dropdown open
        if self._open_sound_dropdown is not None:
            for rect, name in self._rects.get('sound_dropdown_items', []):
                if rect.collidepoint(mouse_pos):
                    row_index, field_name = self._open_sound_dropdown
                    if 0 <= row_index < len(self.rows):
                        self.rows[row_index]['params'][field_name] = name
                    self._open_sound_dropdown = None
                    self._sound_dropdown_scroll = 0
                    return
            self._open_sound_dropdown = None
            self._sound_dropdown_scroll = 0
            return

        # Room picker dropdown open
        if self._open_room_dropdown is not None:
            for rect, name in self._rects.get('room_dropdown_items', []):
                if rect.collidepoint(mouse_pos):
                    row_index, field_name = self._open_room_dropdown
                    if 0 <= row_index < len(self.rows):
                        self.rows[row_index]['params'][field_name] = name
                    self._open_room_dropdown = None
                    return
            self._open_room_dropdown = None
            return

        # World map picker dropdown open (world_map_location's map_name)
        if self._open_world_map_dropdown is not None:
            for rect, name in self._rects.get('world_map_dropdown_items', []):
                if rect.collidepoint(mouse_pos):
                    row_index, field_name = self._open_world_map_dropdown
                    if 0 <= row_index < len(self.rows):
                        row = self.rows[row_index]
                        if row['params'].get(field_name) != name:
                            # Map changed — the old 'name' pick almost
                            # certainly doesn't exist on the new map, so
                            # clear it rather than leave a stale/invalid
                            # location name behind (same reasoning as the
                            # skin picker clearing skin_id on character
                            # change — see set_current_character() notes).
                            row['params']['name'] = ''
                        row['params'][field_name] = name
                    self._open_world_map_dropdown = None
                    return
            self._open_world_map_dropdown = None
            return

        # World map location-name picker dropdown open (row-scoped to that
        # row's own map_name — see _wm_location_choices_for_row())
        if self._open_wm_location_dropdown is not None:
            for rect, name in self._rects.get('wm_location_dropdown_items', []):
                if rect.collidepoint(mouse_pos):
                    row_index, field_name = self._open_wm_location_dropdown
                    if 0 <= row_index < len(self.rows):
                        self.rows[row_index]['params'][field_name] = name
                    self._open_wm_location_dropdown = None
                    return
            self._open_wm_location_dropdown = None
            return

        if self._active_option_field is not None:
            row_index, option_index = self._active_option_field
            options = self.rows[row_index].get('_options', []) if 0 <= row_index < len(self.rows) else []
            if 0 <= option_index < len(options):
                options[option_index]['text'] = self._active_option_text
            self._active_option_field = None

        if self._active_field is not None:
            row_index, field_name = self._active_field
            if 0 <= row_index < len(self.rows):
                self.rows[row_index]['params'][field_name] = self._active_text
            self._active_field = None

        add_rect = self._rects.get('add_action_btn')
        if add_rect and add_rect.collidepoint(mouse_pos):
            self._add_picker_open = not self._add_picker_open
            return

        for row_index, row_rects in self._rects.get('rows', []):
            type_rect = row_rects.get('type')
            if type_rect and type_rect.collidepoint(mouse_pos):
                self._open_type_dropdown_row = row_index
                self._open_portrait_dropdown = None
                self._open_char_dropdown = None
                self._open_enemy_dropdown = None
                self._open_npc_dropdown = None
                self._open_cutscene_dropdown = None
                self._open_skill_dropdown = None
                self._open_transformation_dropdown = None
                self._open_skin_dropdown = None
                self._open_animation_dropdown = None
                self._open_weather_dropdown = None
                self._open_music_dropdown = None
                self._open_sound_dropdown = None
                self._open_room_dropdown = None
                self._open_world_map_dropdown = None
                self._open_wm_location_dropdown = None
                return

            up_rect = row_rects.get('up')
            if up_rect and up_rect.collidepoint(mouse_pos):
                self._move_row(row_index, -1)
                return

            down_rect = row_rects.get('down')
            if down_rect and down_rect.collidepoint(mouse_pos):
                self._move_row(row_index, 1)
                return

            delete_rect = row_rects.get('delete')
            if delete_rect and delete_rect.collidepoint(mouse_pos):
                self._remove_row(row_index)
                return

            add_option_rect = row_rects.get('add_option_btn')
            if add_option_rect and add_option_rect.collidepoint(mouse_pos):
                self._add_option(row_index)
                return

            for option_index, opt_rects in row_rects.get('options', []):
                if opt_rects['edit'].collidepoint(mouse_pos):
                    self._open_option_editor(row_index, option_index)
                    return
                if opt_rects['up'].collidepoint(mouse_pos):
                    self._move_option(row_index, option_index, -1)
                    return
                if opt_rects['down'].collidepoint(mouse_pos):
                    self._move_option(row_index, option_index, 1)
                    return
                if opt_rects['delete'].collidepoint(mouse_pos):
                    self._remove_option(row_index, option_index)
                    return
                if opt_rects['text'].collidepoint(mouse_pos):
                    self._active_option_field = (row_index, option_index)
                    options = self.rows[row_index].get('_options', [])
                    self._active_option_text = (options[option_index].get('text', '')
                                                 if 0 <= option_index < len(options) else '')
                    return

            for field_name, field_rect, field_kind, extra in row_rects.get('fields', []):
                if not field_rect.collidepoint(mouse_pos):
                    continue
                if field_kind == 'choice':
                    row = self.rows[row_index]
                    cur = row['params'].get(field_name, extra[0])
                    row['params'][field_name] = extra[(extra.index(cur) + 1) % len(extra)] if cur in extra else extra[0]
                elif field_kind == 'bool':
                    row = self.rows[row_index]
                    cur = row['params'].get(field_name, 'false')
                    row['params'][field_name] = 'false' if cur == 'true' else 'true'
                elif field_kind == 'portrait_picker':
                    picker_btn = row_rects.get('portrait_dropdown_btn_' + field_name)
                    if picker_btn and picker_btn.collidepoint(mouse_pos):
                        self._open_portrait_dropdown = (row_index, field_name)
                        self._open_type_dropdown_row = None
                    else:
                        self._active_field = (row_index, field_name)
                        self._active_text = self.rows[row_index]['params'].get(field_name, '')
                elif field_kind == 'char_picker':
                    picker_btn = row_rects.get('char_dropdown_btn_' + field_name)
                    if picker_btn and picker_btn.collidepoint(mouse_pos):
                        self._open_char_dropdown = (row_index, field_name)
                        self._open_type_dropdown_row = None
                    else:
                        self._active_field = (row_index, field_name)
                        self._active_text = self.rows[row_index]['params'].get(field_name, '')
                elif field_kind == 'enemy_picker':
                    picker_btn = row_rects.get('enemy_dropdown_btn_' + field_name)
                    if picker_btn and picker_btn.collidepoint(mouse_pos):
                        self._open_enemy_dropdown = (row_index, field_name)
                        self._open_type_dropdown_row = None
                    else:
                        self._active_field = (row_index, field_name)
                        self._active_text = self.rows[row_index]['params'].get(field_name, '')
                elif field_kind == 'npc_picker':
                    picker_btn = row_rects.get('npc_dropdown_btn_' + field_name)
                    if picker_btn and picker_btn.collidepoint(mouse_pos):
                        self._open_npc_dropdown = (row_index, field_name)
                        self._open_type_dropdown_row = None
                    else:
                        self._active_field = (row_index, field_name)
                        self._active_text = self.rows[row_index]['params'].get(field_name, '')
                elif field_kind == 'cutscene_picker':
                    picker_btn = row_rects.get('cutscene_dropdown_btn_' + field_name)
                    if picker_btn and picker_btn.collidepoint(mouse_pos):
                        self._open_cutscene_dropdown = (row_index, field_name)
                        self._open_type_dropdown_row = None
                    else:
                        self._active_field = (row_index, field_name)
                        self._active_text = self.rows[row_index]['params'].get(field_name, '')
                elif field_kind == 'skill_picker':
                    picker_btn = row_rects.get('skill_dropdown_btn_' + field_name)
                    if picker_btn and picker_btn.collidepoint(mouse_pos):
                        self._open_skill_dropdown = (row_index, field_name)
                        self._open_type_dropdown_row = None
                    else:
                        self._active_field = (row_index, field_name)
                        self._active_text = self.rows[row_index]['params'].get(field_name, '')
                elif field_kind == 'transformation_picker':
                    picker_btn = row_rects.get('transformation_dropdown_btn_' + field_name)
                    if picker_btn and picker_btn.collidepoint(mouse_pos):
                        self._open_transformation_dropdown = (row_index, field_name)
                        self._open_type_dropdown_row = None
                    else:
                        self._active_field = (row_index, field_name)
                        self._active_text = self.rows[row_index]['params'].get(field_name, '')
                elif field_kind == 'skin_picker':
                    picker_btn = row_rects.get('skin_dropdown_btn_' + field_name)
                    if picker_btn and picker_btn.collidepoint(mouse_pos):
                        self._open_skin_dropdown = (row_index, field_name)
                        self._open_type_dropdown_row = None
                    else:
                        self._active_field = (row_index, field_name)
                        self._active_text = self.rows[row_index]['params'].get(field_name, '')
                elif field_kind == 'animation_picker':
                    picker_btn = row_rects.get('animation_dropdown_btn_' + field_name)
                    if picker_btn and picker_btn.collidepoint(mouse_pos):
                        self._open_animation_dropdown = (row_index, field_name)
                        self._open_type_dropdown_row = None
                    else:
                        self._active_field = (row_index, field_name)
                        self._active_text = self.rows[row_index]['params'].get(field_name, '')
                elif field_kind == 'weather_picker':
                    picker_btn = row_rects.get('weather_dropdown_btn_' + field_name)
                    if picker_btn and picker_btn.collidepoint(mouse_pos):
                        self._open_weather_dropdown = (row_index, field_name)
                        self._open_type_dropdown_row = None
                    else:
                        self._active_field = (row_index, field_name)
                        self._active_text = self.rows[row_index]['params'].get(field_name, '')
                elif field_kind == 'music_picker':
                    picker_btn = row_rects.get('music_dropdown_btn_' + field_name)
                    if picker_btn and picker_btn.collidepoint(mouse_pos):
                        self._open_music_dropdown = (row_index, field_name)
                        self._music_dropdown_scroll = 0
                        self._open_type_dropdown_row = None
                    else:
                        self._active_field = (row_index, field_name)
                        self._active_text = self.rows[row_index]['params'].get(field_name, '')
                elif field_kind == 'sound_picker':
                    picker_btn = row_rects.get('sound_dropdown_btn_' + field_name)
                    if picker_btn and picker_btn.collidepoint(mouse_pos):
                        self._open_sound_dropdown = (row_index, field_name)
                        self._sound_dropdown_scroll = 0
                        self._open_type_dropdown_row = None
                    else:
                        self._active_field = (row_index, field_name)
                        self._active_text = self.rows[row_index]['params'].get(field_name, '')
                elif field_kind == 'room_picker':
                    picker_btn = row_rects.get('room_dropdown_btn_' + field_name)
                    if picker_btn and picker_btn.collidepoint(mouse_pos):
                        self._open_room_dropdown = (row_index, field_name)
                        self._open_type_dropdown_row = None
                    else:
                        self._active_field = (row_index, field_name)
                        self._active_text = self.rows[row_index]['params'].get(field_name, '')
                elif field_kind == 'world_map_picker':
                    picker_btn = row_rects.get('world_map_dropdown_btn_' + field_name)
                    if picker_btn and picker_btn.collidepoint(mouse_pos):
                        self._open_world_map_dropdown = (row_index, field_name)
                        self._open_type_dropdown_row = None
                    else:
                        self._active_field = (row_index, field_name)
                        self._active_text = self.rows[row_index]['params'].get(field_name, '')
                elif field_kind == 'wm_location_picker':
                    picker_btn = row_rects.get('wm_location_dropdown_btn_' + field_name)
                    if picker_btn and picker_btn.collidepoint(mouse_pos):
                        self._open_wm_location_dropdown = (row_index, field_name)
                        self._open_type_dropdown_row = None
                    else:
                        self._active_field = (row_index, field_name)
                        self._active_text = self.rows[row_index]['params'].get(field_name, '')
                elif field_kind in ('spawn_picker', 'position_picker'):
                    # No text-entry fallback here (unlike the other
                    # pickers) — the whole field IS the "Set Spawn"/"Set
                    # Position" button.
                    self._open_spawn_picker(row_index, field_name)
                else:  # text / number / json
                    self._active_field = (row_index, field_name)
                    self._active_text = self.rows[row_index]['params'].get(field_name, '')
                return

    # ── Draw ─────────────────────────────────────────────────────────────────

    def draw(self, screen, x, y, w):
        colors = self.colors
        self._rects = {'rows': []}

        header = self.font_medium.render(
            "Actions (run in order)" if self.rows else "Actions (none)", True, colors['text'])
        screen.blit(header, (x, y))
        cur_y = y + 26

        for row_index, row in enumerate(self.rows):
            row_rects = {'fields': []}
            row_x = x

            if row['type'] == '__raw__':
                label = self.font_small.render("(unrecognized action — kept as-is)", True, colors['text_dim'])
                screen.blit(label, (row_x, cur_y + 6))
                row_h = _FIELD_H
            else:
                schema = ACTION_SCHEMA.get(row['type'], [])

                type_rect = pygame.Rect(row_x, cur_y, 160, _FIELD_H)
                pygame.draw.rect(screen, colors['input_bg'], type_rect, border_radius=4)
                pygame.draw.rect(screen, colors['accent'], type_rect, 1, border_radius=4)
                clip = pygame.Rect(type_rect.x + 4, type_rect.y, type_rect.w - 8, type_rect.h)
                screen.set_clip(clip)
                type_label = self.font_small.render(row['type'], True, colors['text'])
                screen.blit(type_label, (type_rect.x + 6, type_rect.y + 5))
                screen.set_clip(None)
                row_rects['type'] = type_rect

                # Fields wrap onto additional lines under the type box when
                # they'd overflow the panel width, so long rows (dialogue_box
                # with 4 fields) don't run off the edge.
                field_x = row_x
                field_y = cur_y + _FIELD_H + 4
                line_h = _FIELD_H
                max_x = x + w - 30

                visible_schema = _row_visible_fields(row['type'], row['params'], schema)
                for field_name, field_kind, extra in visible_schema:
                    field_w = _FIELD_WIDTH.get(field_kind, 90)
                    if field_kind in _WIDE_FIELDS:
                        field_w = min(220, max_x - field_x) if max_x - field_x > 60 else field_w
                    if field_x + field_w > max_x:
                        field_x = row_x
                        field_y += _FIELD_H + 4
                        line_h += _FIELD_H + 4

                    field_rect = pygame.Rect(field_x, field_y, field_w, _FIELD_H)
                    active = self._active_field == (row_index, field_name)
                    bg = colors['input_active'] if active else colors['input_bg']
                    pygame.draw.rect(screen, bg, field_rect, border_radius=4)
                    pygame.draw.rect(screen, colors['grid'], field_rect, 1, border_radius=4)

                    if field_kind == 'portrait_picker':
                        btn_rect = pygame.Rect(field_rect.right - 18, field_rect.y, 18, _FIELD_H)
                        row_rects['portrait_dropdown_btn_' + field_name] = btn_rect
                        pygame.draw.rect(screen, colors['panel_light'], btn_rect, border_radius=3)
                        arrow = self.font_small.render('v', True, colors['text_dim'])
                        screen.blit(arrow, (btn_rect.x + 5, btn_rect.y + 4))
                    elif field_kind == 'char_picker':
                        btn_rect = pygame.Rect(field_rect.right - 18, field_rect.y, 18, _FIELD_H)
                        row_rects['char_dropdown_btn_' + field_name] = btn_rect
                        pygame.draw.rect(screen, colors['panel_light'], btn_rect, border_radius=3)
                        arrow = self.font_small.render('v', True, colors['text_dim'])
                        screen.blit(arrow, (btn_rect.x + 5, btn_rect.y + 4))
                    elif field_kind == 'enemy_picker':
                        btn_rect = pygame.Rect(field_rect.right - 18, field_rect.y, 18, _FIELD_H)
                        row_rects['enemy_dropdown_btn_' + field_name] = btn_rect
                        pygame.draw.rect(screen, colors['panel_light'], btn_rect, border_radius=3)
                        arrow = self.font_small.render('v', True, colors['text_dim'])
                        screen.blit(arrow, (btn_rect.x + 5, btn_rect.y + 4))
                    elif field_kind == 'npc_picker':
                        btn_rect = pygame.Rect(field_rect.right - 18, field_rect.y, 18, _FIELD_H)
                        row_rects['npc_dropdown_btn_' + field_name] = btn_rect
                        pygame.draw.rect(screen, colors['panel_light'], btn_rect, border_radius=3)
                        arrow = self.font_small.render('v', True, colors['text_dim'])
                        screen.blit(arrow, (btn_rect.x + 5, btn_rect.y + 4))
                    elif field_kind == 'cutscene_picker':
                        btn_rect = pygame.Rect(field_rect.right - 18, field_rect.y, 18, _FIELD_H)
                        row_rects['cutscene_dropdown_btn_' + field_name] = btn_rect
                        pygame.draw.rect(screen, colors['panel_light'], btn_rect, border_radius=3)
                        arrow = self.font_small.render('v', True, colors['text_dim'])
                        screen.blit(arrow, (btn_rect.x + 5, btn_rect.y + 4))
                    elif field_kind == 'skill_picker':
                        btn_rect = pygame.Rect(field_rect.right - 18, field_rect.y, 18, _FIELD_H)
                        row_rects['skill_dropdown_btn_' + field_name] = btn_rect
                        pygame.draw.rect(screen, colors['panel_light'], btn_rect, border_radius=3)
                        arrow = self.font_small.render('v', True, colors['text_dim'])
                        screen.blit(arrow, (btn_rect.x + 5, btn_rect.y + 4))
                    elif field_kind == 'transformation_picker':
                        btn_rect = pygame.Rect(field_rect.right - 18, field_rect.y, 18, _FIELD_H)
                        row_rects['transformation_dropdown_btn_' + field_name] = btn_rect
                        pygame.draw.rect(screen, colors['panel_light'], btn_rect, border_radius=3)
                        arrow = self.font_small.render('v', True, colors['text_dim'])
                        screen.blit(arrow, (btn_rect.x + 5, btn_rect.y + 4))
                    elif field_kind == 'skin_picker':
                        btn_rect = pygame.Rect(field_rect.right - 18, field_rect.y, 18, _FIELD_H)
                        row_rects['skin_dropdown_btn_' + field_name] = btn_rect
                        pygame.draw.rect(screen, colors['panel_light'], btn_rect, border_radius=3)
                        arrow = self.font_small.render('v', True, colors['text_dim'])
                        screen.blit(arrow, (btn_rect.x + 5, btn_rect.y + 4))
                    elif field_kind == 'animation_picker':
                        btn_rect = pygame.Rect(field_rect.right - 18, field_rect.y, 18, _FIELD_H)
                        row_rects['animation_dropdown_btn_' + field_name] = btn_rect
                        pygame.draw.rect(screen, colors['panel_light'], btn_rect, border_radius=3)
                        arrow = self.font_small.render('v', True, colors['text_dim'])
                        screen.blit(arrow, (btn_rect.x + 5, btn_rect.y + 4))
                    elif field_kind == 'weather_picker':
                        btn_rect = pygame.Rect(field_rect.right - 18, field_rect.y, 18, _FIELD_H)
                        row_rects['weather_dropdown_btn_' + field_name] = btn_rect
                        pygame.draw.rect(screen, colors['panel_light'], btn_rect, border_radius=3)
                        arrow = self.font_small.render('v', True, colors['text_dim'])
                        screen.blit(arrow, (btn_rect.x + 5, btn_rect.y + 4))
                    elif field_kind == 'music_picker':
                        btn_rect = pygame.Rect(field_rect.right - 18, field_rect.y, 18, _FIELD_H)
                        row_rects['music_dropdown_btn_' + field_name] = btn_rect
                        pygame.draw.rect(screen, colors['panel_light'], btn_rect, border_radius=3)
                        arrow = self.font_small.render('v', True, colors['text_dim'])
                        screen.blit(arrow, (btn_rect.x + 5, btn_rect.y + 4))
                    elif field_kind == 'sound_picker':
                        btn_rect = pygame.Rect(field_rect.right - 18, field_rect.y, 18, _FIELD_H)
                        row_rects['sound_dropdown_btn_' + field_name] = btn_rect
                        pygame.draw.rect(screen, colors['panel_light'], btn_rect, border_radius=3)
                        arrow = self.font_small.render('v', True, colors['text_dim'])
                        screen.blit(arrow, (btn_rect.x + 5, btn_rect.y + 4))
                    elif field_kind == 'room_picker':
                        btn_rect = pygame.Rect(field_rect.right - 18, field_rect.y, 18, _FIELD_H)
                        row_rects['room_dropdown_btn_' + field_name] = btn_rect
                        pygame.draw.rect(screen, colors['panel_light'], btn_rect, border_radius=3)
                        arrow = self.font_small.render('v', True, colors['text_dim'])
                        screen.blit(arrow, (btn_rect.x + 5, btn_rect.y + 4))
                    elif field_kind == 'world_map_picker':
                        btn_rect = pygame.Rect(field_rect.right - 18, field_rect.y, 18, _FIELD_H)
                        row_rects['world_map_dropdown_btn_' + field_name] = btn_rect
                        pygame.draw.rect(screen, colors['panel_light'], btn_rect, border_radius=3)
                        arrow = self.font_small.render('v', True, colors['text_dim'])
                        screen.blit(arrow, (btn_rect.x + 5, btn_rect.y + 4))
                    elif field_kind == 'wm_location_picker':
                        btn_rect = pygame.Rect(field_rect.right - 18, field_rect.y, 18, _FIELD_H)
                        row_rects['wm_location_dropdown_btn_' + field_name] = btn_rect
                        pygame.draw.rect(screen, colors['panel_light'], btn_rect, border_radius=3)
                        arrow = self.font_small.render('v', True, colors['text_dim'])
                        screen.blit(arrow, (btn_rect.x + 5, btn_rect.y + 4))

                    if field_kind == 'spawn_picker':
                        # The whole field renders as a "Set Spawn" button —
                        # no dropdown arrow, and its label reflects both
                        # spawn_x AND its schema-less companion spawn_y
                        # rather than the field's own raw param value.
                        sx = row['params'].get('spawn_x', '')
                        sy = row['params'].get('spawn_y', '')
                        room_picked = bool(row['params'].get('room_name', ''))
                        if sx != '' and sy != '':
                            display = 'Spawn: (%s, %s)' % (sx, sy)
                        elif room_picked:
                            display = 'Set Spawn...'
                        else:
                            display = 'Set Spawn (pick room first)'
                    elif field_kind == 'position_picker':
                        # Same "whole field is a button" treatment as
                        # spawn_picker, but for x/y in the current room —
                        # no room_name field to gate on, since it's always
                        # the room this event/trigger box lives in (see
                        # _current_room_name / set_current_room()).
                        px = row['params'].get('x', '')
                        py = row['params'].get('y', '')
                        if px != '' and py != '':
                            display = 'Position: (%s, %s)' % (px, py)
                        else:
                            display = 'Set Position...'
                    else:
                        display = self._active_text if active else row['params'].get(field_name, '')
                        if not active and display == '':
                            display = _placeholder_for(field_name, field_kind) \
                                if field_kind in ('portrait_picker', 'char_picker', 'enemy_picker', 'npc_picker', 'cutscene_picker', 'skill_picker', 'transformation_picker', 'skin_picker', 'animation_picker', 'weather_picker', 'music_picker', 'sound_picker', 'room_picker', 'world_map_picker', 'wm_location_picker') else '<%s>' % field_name
                    fclip = pygame.Rect(field_rect.x + 4, field_rect.y, field_rect.w - 8, field_rect.h)
                    screen.set_clip(fclip)
                    text_surf = self.font_small.render(str(display), True, colors['text'])
                    screen.blit(text_surf, (field_rect.x + 4, field_rect.y + 5))
                    screen.set_clip(None)

                    row_rects['fields'].append((field_name, field_rect, field_kind, extra))
                    field_x = field_rect.right + _FIELD_GAP

                row_h = _FIELD_H + line_h + 4

                if row['type'] == 'dialogue_choice':
                    row_h += self._draw_dialogue_choice_options(
                        screen, row_index, row, row_x, cur_y + row_h, w, row_rects)

            # Reorder + delete controls, aligned to the row's top line.
            up_rect = pygame.Rect(x + w - 78, cur_y, 20, _FIELD_H)
            down_rect = pygame.Rect(x + w - 54, cur_y, 20, _FIELD_H)
            delete_rect = pygame.Rect(x + w - 26, cur_y, 20, _FIELD_H)
            for rect, label, color in ((up_rect, '^', colors['text_dim']),
                                        (down_rect, 'v', colors['text_dim']),
                                        (delete_rect, 'X', colors['text'])):
                fill = colors['delete'] if rect is delete_rect else colors['panel_light']
                pygame.draw.rect(screen, fill, rect, border_radius=4)
                lbl = self.font_small.render(label, True, color)
                screen.blit(lbl, lbl.get_rect(center=rect.center))
            row_rects['up'] = up_rect
            row_rects['down'] = down_rect
            row_rects['delete'] = delete_rect

            self._rects['rows'].append((row_index, row_rects))
            cur_y += row_h + _ROW_GAP

            if self._open_type_dropdown_row == row_index:
                self._draw_type_dropdown(screen, x, cur_y)
                shown = min(_TYPE_DROPDOWN_VISIBLE, len(ACTION_TYPES))
                dropdown_h = shown * 20
                if len(ACTION_TYPES) > _TYPE_DROPDOWN_VISIBLE:
                    dropdown_h += 18
                cur_y += dropdown_h
            elif self._open_portrait_dropdown is not None and self._open_portrait_dropdown[0] == row_index:
                names = self._known_portraits or ['(no portraits found)']
                self._draw_portrait_dropdown(screen, x, cur_y)
                cur_y += len(names[:8]) * 22
            elif self._open_char_dropdown is not None and self._open_char_dropdown[0] == row_index:
                names = self._known_characters or ['(no characters found)']
                self._draw_char_dropdown(screen, x, cur_y)
                cur_y += len(names[:8]) * 22
            elif self._open_enemy_dropdown is not None and self._open_enemy_dropdown[0] == row_index:
                names = self._known_enemies or ['(no enemies found)']
                self._draw_enemy_dropdown(screen, x, cur_y)
                cur_y += len(names[:8]) * 22
            elif self._open_npc_dropdown is not None and self._open_npc_dropdown[0] == row_index:
                names = self._known_npcs or ['(no npcs found)']
                self._draw_npc_dropdown(screen, x, cur_y)
                cur_y += len(names[:8]) * 22
            elif self._open_cutscene_dropdown is not None and self._open_cutscene_dropdown[0] == row_index:
                names = self._known_cutscenes or ['(no cutscenes found)']
                self._draw_cutscene_dropdown(screen, x, cur_y)
                cur_y += len(names[:8]) * 22
            elif self._open_skill_dropdown is not None and self._open_skill_dropdown[0] == row_index:
                real, placeholder = self._skill_choices_for_row(row_index)
                names = real or [placeholder]
                self._draw_skill_dropdown(screen, x, cur_y, row_index)
                cur_y += len(names[:8]) * 22
            elif self._open_transformation_dropdown is not None and self._open_transformation_dropdown[0] == row_index:
                real, placeholder = self._transformation_choices_for_row(row_index)
                names = real or [placeholder]
                self._draw_transformation_dropdown(screen, x, cur_y, row_index)
                cur_y += len(names[:8]) * 22
            elif self._open_skin_dropdown is not None and self._open_skin_dropdown[0] == row_index:
                real, placeholder = self._costume_choices_for_row(row_index)
                names = real or [placeholder]
                self._draw_skin_dropdown(screen, x, cur_y, row_index)
                cur_y += len(names[:8]) * 22
            elif self._open_animation_dropdown is not None and self._open_animation_dropdown[0] == row_index:
                real, placeholder = self._animation_choices_for_row(row_index)
                names = real or [placeholder]
                self._draw_animation_dropdown(screen, x, cur_y, row_index)
                cur_y += len(names[:8]) * 22
            elif self._open_weather_dropdown is not None and self._open_weather_dropdown[0] == row_index:
                names = self._known_weather_types or ['(no weather found)']
                self._draw_weather_dropdown(screen, x, cur_y)
                cur_y += len(names[:8]) * 22
            elif self._open_music_dropdown is not None and self._open_music_dropdown[0] == row_index:
                names = self._known_music_tracks or ['(no music found)']
                self._draw_music_dropdown(screen, x, cur_y)
                shown = min(_MUSIC_DROPDOWN_VISIBLE, len(names) - self._music_dropdown_scroll)
                cur_y += shown * 22
                if len(names) > _MUSIC_DROPDOWN_VISIBLE:
                    cur_y += 18
            elif self._open_sound_dropdown is not None and self._open_sound_dropdown[0] == row_index:
                names = self._known_sound_effects or ['(no sfx found)']
                self._draw_sound_dropdown(screen, x, cur_y)
                shown = min(_SOUND_DROPDOWN_VISIBLE, len(names) - self._sound_dropdown_scroll)
                cur_y += shown * 22
                if len(names) > _SOUND_DROPDOWN_VISIBLE:
                    cur_y += 18
            elif self._open_room_dropdown is not None and self._open_room_dropdown[0] == row_index:
                names = self._known_rooms or ['(no rooms found)']
                self._draw_room_dropdown(screen, x, cur_y)
                cur_y += len(names[:8]) * 22
            elif self._open_world_map_dropdown is not None and self._open_world_map_dropdown[0] == row_index:
                names = self._known_world_maps or ['(no world maps found)']
                self._draw_world_map_dropdown(screen, x, cur_y)
                cur_y += len(names[:8]) * 22
            elif self._open_wm_location_dropdown is not None and self._open_wm_location_dropdown[0] == row_index:
                real, placeholder = self._wm_location_choices_for_row(row_index)
                names = real or [placeholder]
                self._draw_wm_location_dropdown(screen, x, cur_y, row_index)
                cur_y += len(names[:8]) * 22

        if self._add_picker_open:
            cur_y += self._draw_add_type_grid(screen, x, cur_y, w)

        add_rect = pygame.Rect(x, cur_y, 160, _FIELD_H)
        pygame.draw.rect(screen, colors['input_bg'], add_rect, border_radius=4)
        pygame.draw.rect(screen, colors['success'], add_rect, 1, border_radius=4)
        add_label = self.font_small.render("+ Add Command", True, colors['success'])
        screen.blit(add_label, (add_rect.x + 8, add_rect.y + 5))
        self._rects['add_action_btn'] = add_rect
        cur_y += _FIELD_H

        self._content_height = cur_y - y

        if self._option_editor is not None:
            self._draw_option_editor(screen)
        if self._spawn_picker is not None:
            self._draw_spawn_picker(screen)

    def _draw_dialogue_choice_options(self, screen, row_index, row, x, y, w, row_rects):
        """Draws the option list for a dialogue_choice row: one row per
        option (text field, Edit Actions button, reorder/delete) plus an
        Add Option button. Returns the extra height consumed so the caller
        can fold it into row_h."""
        colors = self.colors
        options = row.get('_options', [])
        cur_y = y + 4

        label = self.font_small.render("Options:", True, colors['text_dim'])
        screen.blit(label, (x, cur_y))
        cur_y += 18

        text_w = max(60, min(180, w - 210))
        opt_rects_list = []
        for option_index, option in enumerate(options):
            text_rect = pygame.Rect(x, cur_y, text_w, _FIELD_H)
            active = self._active_option_field == (row_index, option_index)
            bg = colors['input_active'] if active else colors['input_bg']
            pygame.draw.rect(screen, bg, text_rect, border_radius=4)
            pygame.draw.rect(screen, colors['grid'], text_rect, 1, border_radius=4)

            display = self._active_option_text if active else option.get('text', '')
            if not active and display == '':
                display = '<option text>'
            fclip = pygame.Rect(text_rect.x + 4, text_rect.y, text_rect.w - 8, text_rect.h)
            screen.set_clip(fclip)
            text_surf = self.font_small.render(str(display), True, colors['text'])
            screen.blit(text_surf, (text_rect.x + 4, text_rect.y + 5))
            screen.set_clip(None)

            edit_rect = pygame.Rect(text_rect.right + _FIELD_GAP, cur_y, 130, _FIELD_H)
            n_actions = len(option.get('actions', []))
            pygame.draw.rect(screen, colors['panel_light'], edit_rect, border_radius=4)
            pygame.draw.rect(screen, colors['accent'], edit_rect, 1, border_radius=4)
            edit_label = self.font_small.render("Edit Actions (%d)" % n_actions, True, colors['text'])
            screen.blit(edit_label, (edit_rect.x + 6, edit_rect.y + 5))

            up_rect = pygame.Rect(edit_rect.right + _FIELD_GAP, cur_y, 20, _FIELD_H)
            down_rect = pygame.Rect(up_rect.right + 2, cur_y, 20, _FIELD_H)
            delete_rect = pygame.Rect(down_rect.right + 2, cur_y, 20, _FIELD_H)
            for rect, lbl, color in ((up_rect, '^', colors['text_dim']),
                                      (down_rect, 'v', colors['text_dim']),
                                      (delete_rect, 'X', colors['text'])):
                fill = colors['delete'] if rect is delete_rect else colors['panel_light']
                pygame.draw.rect(screen, fill, rect, border_radius=4)
                lb = self.font_small.render(lbl, True, color)
                screen.blit(lb, lb.get_rect(center=rect.center))

            opt_rects_list.append((option_index, {
                'text': text_rect, 'edit': edit_rect,
                'up': up_rect, 'down': down_rect, 'delete': delete_rect,
            }))
            cur_y += _FIELD_H + 4

        row_rects['options'] = opt_rects_list

        add_option_rect = pygame.Rect(x, cur_y, 130, _FIELD_H)
        pygame.draw.rect(screen, colors['input_bg'], add_option_rect, border_radius=4)
        pygame.draw.rect(screen, colors['success'], add_option_rect, 1, border_radius=4)
        add_label = self.font_small.render("+ Add Option", True, colors['success'])
        screen.blit(add_label, (add_option_rect.x + 8, add_option_rect.y + 5))
        row_rects['add_option_btn'] = add_option_rect
        cur_y += _FIELD_H + 4

        return cur_y - y

    def _draw_option_editor(self, screen):
        """Full-panel overlay hosting a nested ActionSequenceBuilder for
        whichever dialogue_choice option is currently being edited — same
        Save/Cancel/Esc shape as EventEditorWindow itself, one level down."""
        colors = self.colors
        sw, sh = screen.get_size()

        overlay = pygame.Surface((sw, sh), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 180))
        screen.blit(overlay, (0, 0))

        margin = 60
        panel = pygame.Rect(margin, margin, sw - margin * 2, sh - margin * 2)
        panel_surf = pygame.Surface((panel.width, panel.height), pygame.SRCALPHA)
        panel_surf.fill(colors.get('bg_transparent', (20, 20, 20, 235)))
        screen.blit(panel_surf, panel.topleft)
        pygame.draw.rect(screen, colors['accent'], panel, 2)

        title = self.font_medium.render("Option Actions", True, colors['text'])
        screen.blit(title, (panel.x + 12, panel.y + 10))
        hint = self.font_small.render("Esc to cancel", True, colors['text_dim'])
        screen.blit(hint, (panel.right - 12 - hint.get_width(), panel.y + 16))

        content_x, content_y = panel.x + 12, panel.y + 40
        content_w = panel.width - 24
        clip_rect = pygame.Rect(panel.x, content_y, panel.width, panel.height - 90)
        screen.set_clip(clip_rect)
        self._option_editor['builder'].draw(screen, content_x, content_y, content_w)
        screen.set_clip(None)
        self._option_editor['origin'] = (content_x, content_y)

        btn_w = 100
        save_rect = pygame.Rect(panel.right - 12 - btn_w, panel.bottom - 12 - _FIELD_H - 6, btn_w, _FIELD_H + 6)
        cancel_rect = pygame.Rect(save_rect.x - btn_w - 10, save_rect.y, btn_w, _FIELD_H + 6)

        pygame.draw.rect(screen, colors['success'], save_rect, border_radius=5)
        save_label = self.font_small.render("Save", True, colors['bg'])
        screen.blit(save_label, save_label.get_rect(center=save_rect.center))

        pygame.draw.rect(screen, colors['panel_light'], cancel_rect, border_radius=5)
        pygame.draw.rect(screen, colors['grid'], cancel_rect, 1, border_radius=5)
        cancel_label = self.font_small.render("Cancel", True, colors['text'])
        screen.blit(cancel_label, cancel_label.get_rect(center=cancel_rect.center))

        self._option_editor['save_rect'] = save_rect
        self._option_editor['cancel_rect'] = cancel_rect

    def _draw_add_type_grid(self, screen, x, y, w):
        colors = self.colors
        item_w, item_h, gap = 190, 26, 6
        cols = max(1, (w + gap) // (item_w + gap))
        items = []
        for i, action_type in enumerate(ACTION_TYPES):
            col, row = i % cols, i // cols
            rect = pygame.Rect(x + col * (item_w + gap), y + row * (item_h + gap), item_w, item_h)
            pygame.draw.rect(screen, colors['panel_light'], rect, border_radius=4)
            pygame.draw.rect(screen, colors['accent'], rect, 1, border_radius=4)
            clip = pygame.Rect(rect.x + 4, rect.y, rect.w - 8, rect.h)
            screen.set_clip(clip)
            label = self.font_small.render(action_type, True, colors['text'])
            screen.blit(label, (rect.x + 6, rect.y + 5))
            screen.set_clip(None)
            items.append((rect, action_type))
        self._rects['add_type_grid_items'] = items
        rows_used = (len(ACTION_TYPES) + cols - 1) // cols
        return rows_used * (item_h + gap)

    def _draw_type_dropdown(self, screen, x, list_y):
        colors = self.colors
        items = []
        list_w = 200
        row_h = 20
        max_scroll = max(0, len(ACTION_TYPES) - _TYPE_DROPDOWN_VISIBLE)
        self._type_dropdown_scroll = max(0, min(self._type_dropdown_scroll, max_scroll))
        start = self._type_dropdown_scroll
        shown = ACTION_TYPES[start:start + _TYPE_DROPDOWN_VISIBLE]

        list_rect = pygame.Rect(x, list_y, list_w, len(shown) * row_h)
        pygame.draw.rect(screen, colors['panel_light'], list_rect)
        pygame.draw.rect(screen, colors['accent'], list_rect, 2)
        for i, action_type in enumerate(shown):
            item_rect = pygame.Rect(x, list_y + i * row_h, list_w, row_h)
            label = self.font_small.render(action_type, True, colors['text'])
            screen.blit(label, (item_rect.x + 6, item_rect.y + 3))
            items.append((item_rect, action_type))
        self._rects['type_dropdown_items'] = items

        if len(ACTION_TYPES) > _TYPE_DROPDOWN_VISIBLE:
            hint = self.font_small.render(
                "%d/%d — scroll for more" % (start + len(shown), len(ACTION_TYPES)),
                True, colors['text_dim'])
            screen.blit(hint, (x, list_rect.bottom + 2))

    def _draw_portrait_dropdown(self, screen, x, list_y):
        colors = self.colors
        items = []
        list_w = 180
        names = self._known_portraits or ['(no portraits found)']
        visible = names[:8]
        list_rect = pygame.Rect(x, list_y, list_w, len(visible) * 22)
        pygame.draw.rect(screen, colors['panel_light'], list_rect)
        pygame.draw.rect(screen, colors['accent'], list_rect, 2)
        for i, name in enumerate(visible):
            item_rect = pygame.Rect(x, list_y + i * 22, list_w, 22)
            label = self.font_small.render(name, True, colors['text'])
            screen.blit(label, (item_rect.x + 6, item_rect.y + 4))
            if self._known_portraits:
                items.append((item_rect, name))
        self._rects['portrait_dropdown_items'] = items

    def _draw_char_dropdown(self, screen, x, list_y):
        colors = self.colors
        items = []
        list_w = 180
        names = self._known_characters or ['(no characters found)']
        visible = names[:8]
        list_rect = pygame.Rect(x, list_y, list_w, len(visible) * 22)
        pygame.draw.rect(screen, colors['panel_light'], list_rect)
        pygame.draw.rect(screen, colors['accent'], list_rect, 2)
        for i, name in enumerate(visible):
            item_rect = pygame.Rect(x, list_y + i * 22, list_w, 22)
            label = self.font_small.render(name, True, colors['text'])
            screen.blit(label, (item_rect.x + 6, item_rect.y + 4))
            if self._known_characters:
                items.append((item_rect, name))
        self._rects['char_dropdown_items'] = items

    def _draw_enemy_dropdown(self, screen, x, list_y):
        colors = self.colors
        items = []
        list_w = 180
        names = self._known_enemies or ['(no enemies found)']
        visible = names[:8]
        list_rect = pygame.Rect(x, list_y, list_w, len(visible) * 22)
        pygame.draw.rect(screen, colors['panel_light'], list_rect)
        pygame.draw.rect(screen, colors['accent'], list_rect, 2)
        for i, name in enumerate(visible):
            item_rect = pygame.Rect(x, list_y + i * 22, list_w, 22)
            label = self.font_small.render(name, True, colors['text'])
            screen.blit(label, (item_rect.x + 6, item_rect.y + 4))
            if self._known_enemies:
                items.append((item_rect, name))
        self._rects['enemy_dropdown_items'] = items

    def _draw_npc_dropdown(self, screen, x, list_y):
        colors = self.colors
        items = []
        list_w = 180
        names = self._known_npcs or ['(no npcs found)']
        visible = names[:8]
        list_rect = pygame.Rect(x, list_y, list_w, len(visible) * 22)
        pygame.draw.rect(screen, colors['panel_light'], list_rect)
        pygame.draw.rect(screen, colors['accent'], list_rect, 2)
        for i, name in enumerate(visible):
            item_rect = pygame.Rect(x, list_y + i * 22, list_w, 22)
            label = self.font_small.render(name, True, colors['text'])
            screen.blit(label, (item_rect.x + 6, item_rect.y + 4))
            if self._known_npcs:
                items.append((item_rect, name))
        self._rects['npc_dropdown_items'] = items

    def _draw_cutscene_dropdown(self, screen, x, list_y):
        colors = self.colors
        items = []
        list_w = 180
        names = self._known_cutscenes or ['(no cutscenes found)']
        visible = names[:8]
        list_rect = pygame.Rect(x, list_y, list_w, len(visible) * 22)
        pygame.draw.rect(screen, colors['panel_light'], list_rect)
        pygame.draw.rect(screen, colors['accent'], list_rect, 2)
        for i, name in enumerate(visible):
            item_rect = pygame.Rect(x, list_y + i * 22, list_w, 22)
            label = self.font_small.render(name, True, colors['text'])
            screen.blit(label, (item_rect.x + 6, item_rect.y + 4))
            if self._known_cutscenes:
                items.append((item_rect, name))
        self._rects['cutscene_dropdown_items'] = items

    def _draw_weather_dropdown(self, screen, x, list_y):
        colors = self.colors
        items = []
        list_w = 180
        names = self._known_weather_types or ['(no weather found)']
        visible = names[:8]
        list_rect = pygame.Rect(x, list_y, list_w, len(visible) * 22)
        pygame.draw.rect(screen, colors['panel_light'], list_rect)
        pygame.draw.rect(screen, colors['accent'], list_rect, 2)
        for i, name in enumerate(visible):
            item_rect = pygame.Rect(x, list_y + i * 22, list_w, 22)
            label = self.font_small.render(name, True, colors['text'])
            screen.blit(label, (item_rect.x + 6, item_rect.y + 4))
            if self._known_weather_types:
                items.append((item_rect, name))
        self._rects['weather_dropdown_items'] = items

    def _draw_music_dropdown(self, screen, x, list_y):
        colors = self.colors
        items = []
        list_w = 180
        row_h = 22
        names = self._known_music_tracks or ['(no music found)']
        max_scroll = max(0, len(names) - _MUSIC_DROPDOWN_VISIBLE)
        self._music_dropdown_scroll = max(0, min(self._music_dropdown_scroll, max_scroll))
        start = self._music_dropdown_scroll
        visible = names[start:start + _MUSIC_DROPDOWN_VISIBLE]

        list_rect = pygame.Rect(x, list_y, list_w, len(visible) * row_h)
        pygame.draw.rect(screen, colors['panel_light'], list_rect)
        pygame.draw.rect(screen, colors['accent'], list_rect, 2)
        for i, name in enumerate(visible):
            item_rect = pygame.Rect(x, list_y + i * row_h, list_w, row_h)
            label = self.font_small.render(name, True, colors['text'])
            screen.blit(label, (item_rect.x + 6, item_rect.y + 4))
            if self._known_music_tracks:
                items.append((item_rect, name))
        self._rects['music_dropdown_items'] = items

        if len(names) > _MUSIC_DROPDOWN_VISIBLE:
            hint = self.font_small.render(
                "%d/%d — scroll for more" % (start + len(visible), len(names)),
                True, colors['text_dim'])
            screen.blit(hint, (x, list_rect.bottom + 2))

    def _draw_sound_dropdown(self, screen, x, list_y):
        colors = self.colors
        items = []
        list_w = 180
        row_h = 22
        names = self._known_sound_effects or ['(no sfx found)']
        max_scroll = max(0, len(names) - _SOUND_DROPDOWN_VISIBLE)
        self._sound_dropdown_scroll = max(0, min(self._sound_dropdown_scroll, max_scroll))
        start = self._sound_dropdown_scroll
        visible = names[start:start + _SOUND_DROPDOWN_VISIBLE]

        list_rect = pygame.Rect(x, list_y, list_w, len(visible) * row_h)
        pygame.draw.rect(screen, colors['panel_light'], list_rect)
        pygame.draw.rect(screen, colors['accent'], list_rect, 2)
        for i, name in enumerate(visible):
            item_rect = pygame.Rect(x, list_y + i * row_h, list_w, row_h)
            label = self.font_small.render(name, True, colors['text'])
            screen.blit(label, (item_rect.x + 6, item_rect.y + 4))
            if self._known_sound_effects:
                items.append((item_rect, name))
        self._rects['sound_dropdown_items'] = items

        if len(names) > _SOUND_DROPDOWN_VISIBLE:
            hint = self.font_small.render(
                "%d/%d — scroll for more" % (start + len(visible), len(names)),
                True, colors['text_dim'])
            screen.blit(hint, (x, list_rect.bottom + 2))

    def _draw_room_dropdown(self, screen, x, list_y):
        colors = self.colors
        items = []
        list_w = 180
        names = self._known_rooms or ['(no rooms found)']
        visible = names[:8]
        list_rect = pygame.Rect(x, list_y, list_w, len(visible) * 22)
        pygame.draw.rect(screen, colors['panel_light'], list_rect)
        pygame.draw.rect(screen, colors['accent'], list_rect, 2)
        for i, name in enumerate(visible):
            item_rect = pygame.Rect(x, list_y + i * 22, list_w, 22)
            label = self.font_small.render(name, True, colors['text'])
            screen.blit(label, (item_rect.x + 6, item_rect.y + 4))
            if self._known_rooms:
                items.append((item_rect, name))
        self._rects['room_dropdown_items'] = items

    def _draw_world_map_dropdown(self, screen, x, list_y):
        """map_name picker for world_map_location — static list of every
        map saved by dev_tools/world_map_editor.py, same shape as
        _draw_room_dropdown() above."""
        colors = self.colors
        items = []
        list_w = 180
        names = self._known_world_maps or ['(no world maps found)']
        visible = names[:8]
        list_rect = pygame.Rect(x, list_y, list_w, len(visible) * 22)
        pygame.draw.rect(screen, colors['panel_light'], list_rect)
        pygame.draw.rect(screen, colors['accent'], list_rect, 2)
        for i, name in enumerate(visible):
            item_rect = pygame.Rect(x, list_y + i * 22, list_w, 22)
            label = self.font_small.render(name, True, colors['text'])
            screen.blit(label, (item_rect.x + 6, item_rect.y + 4))
            if self._known_world_maps:
                items.append((item_rect, name))
        self._rects['world_map_dropdown_items'] = items

    def _draw_wm_location_dropdown(self, screen, x, list_y, row_index):
        """name picker for world_map_location — the pins already placed on
        whichever map is picked in that same row's map_name field, via
        _wm_location_choices_for_row(). Row-scoped like
        _draw_animation_dropdown() above."""
        colors = self.colors
        items = []
        list_w = 180
        real, placeholder = self._wm_location_choices_for_row(row_index)
        names = real or [placeholder]
        visible = names[:8]
        list_rect = pygame.Rect(x, list_y, list_w, len(visible) * 22)
        pygame.draw.rect(screen, colors['panel_light'], list_rect)
        pygame.draw.rect(screen, colors['accent'], list_rect, 2)
        for i, name in enumerate(visible):
            item_rect = pygame.Rect(x, list_y + i * 22, list_w, 22)
            label = self.font_small.render(name, True, colors['text'])
            screen.blit(label, (item_rect.x + 6, item_rect.y + 4))
            if real:
                items.append((item_rect, name))
        self._rects['wm_location_dropdown_items'] = items

    # ── Set Spawn / Set Position overlay ────────────────────────────────────
    # A full-panel mouse-picking overlay shared by change_map's spawn point
    # (the 'spawn_picker' field) and set_player_location's position (the
    # 'position_picker' field) — same Escape/Done/Cancel shape as the
    # dialogue_choice option editor above, one level down. See
    # _spawn_picker in __init__ for the state shape.

    def _open_spawn_picker(self, row_index, field_name):
        """No-ops if there's no room to preview a point against yet.
        field_name is either 'spawn_x' (change_map — the schema-registered
        half of its x/y pair, room comes from that same row's 'room_name'
        field) or 'x' (set_player_location — room is always whatever
        set_current_room() last supplied, since that action has no
        room_name field of its own)."""
        if not (0 <= row_index < len(self.rows)):
            return
        row = self.rows[row_index]

        if field_name == 'spawn_x':
            room_name = row['params'].get('room_name', '')
            y_field, title = 'spawn_y', 'Set Spawn'
        else:  # 'x' — set_player_location
            room_name = self._current_room_name or ''
            y_field, title = 'y', 'Set Position'

        if not room_name:
            return

        width, height = self._known_room_dims.get(room_name, _ROOM_PICKER_DEFAULT_DIMS)
        try:
            sx = float(row['params'].get(field_name, ''))
            sy = float(row['params'].get(y_field, ''))
        except (TypeError, ValueError):
            sx = sy = None

        preview_surface = None
        if self._room_preview_provider is not None:
            try:
                preview_surface = self._room_preview_provider(room_name)
            except Exception:
                preview_surface = None  # best-effort — falls back to the plain grid

        self._spawn_picker = {
            'row_index': row_index,
            'field_name': field_name,
            'y_field': y_field,
            'title': title,
            'room_name': room_name,
            'width': width,
            'height': height,
            'known_dims': room_name in self._known_room_dims,
            'x': sx,
            'y': sy,
            'preview_surface': preview_surface,   # raw, full-room-size — scaled per-frame in _draw_spawn_picker
            '_scaled_preview': None,               # (surface, size) cache, see _draw_spawn_picker
        }

    def _handle_spawn_picker_input(self, event):
        picker = self._spawn_picker
        if picker is None:
            return

        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self._spawn_picker = None
            return

        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return
        mouse_pos = event.pos

        done_rect = picker.get('done_rect')
        if done_rect and done_rect.collidepoint(mouse_pos):
            if picker['x'] is not None and picker['y'] is not None:
                row_index = picker['row_index']
                if 0 <= row_index < len(self.rows):
                    self.rows[row_index]['params'][picker['field_name']] = str(int(round(picker['x'])))
                    self.rows[row_index]['params'][picker['y_field']] = str(int(round(picker['y'])))
                self._spawn_picker = None
            return

        cancel_rect = picker.get('cancel_rect')
        if cancel_rect and cancel_rect.collidepoint(mouse_pos):
            self._spawn_picker = None
            return

        canvas_rect = picker.get('canvas_rect')
        scale = picker.get('scale')
        if canvas_rect and scale and canvas_rect.collidepoint(mouse_pos):
            rel_x = mouse_pos[0] - canvas_rect.x
            rel_y = mouse_pos[1] - canvas_rect.y
            picker['x'] = max(0, min(picker['width'], rel_x / scale))
            picker['y'] = max(0, min(picker['height'], rel_y / scale))

    def _draw_spawn_picker(self, screen):
        """Purely a to-scale rectangle + grid standing in for the room —
        this builder has no live tile/background renderer of its own to
        draw a real room preview with (see set_known_rooms() for how a
        host can at least supply accurate room dimensions so the scale,
        if not the art, is right)."""
        colors = self.colors
        picker = self._spawn_picker
        sw, sh = screen.get_size()

        overlay = pygame.Surface((sw, sh), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 180))
        screen.blit(overlay, (0, 0))

        margin = 60
        panel = pygame.Rect(margin, margin, sw - margin * 2, sh - margin * 2)
        panel_surf = pygame.Surface((panel.width, panel.height), pygame.SRCALPHA)
        panel_surf.fill(colors.get('bg_transparent', (20, 20, 20, 235)))
        screen.blit(panel_surf, panel.topleft)
        pygame.draw.rect(screen, colors['accent'], panel, 2)

        title = self.font_medium.render("%s — %s" % (picker['title'], picker['room_name']), True, colors['text'])
        screen.blit(title, (panel.x + 12, panel.y + 10))
        hint = self.font_small.render(
            "Left-click in the room to place the point — Esc to cancel", True, colors['text_dim'])
        screen.blit(hint, (panel.x + 12, panel.y + 34))

        top_offset = 58
        if not picker['known_dims']:
            note = self.font_small.render(
                "(exact room size unknown — showing a %dx%d placeholder; "
                "wire set_known_rooms() for the real size)" % (picker['width'], picker['height']),
                True, colors['text_dim'])
            screen.blit(note, (panel.x + 12, panel.y + 52))
            top_offset = 74
        elif picker['preview_surface'] is None:
            note = self.font_small.render(
                "(no tile preview available — wire set_room_preview_provider() to see the room)",
                True, colors['text_dim'])
            screen.blit(note, (panel.x + 12, panel.y + 52))
            top_offset = 74

        avail_top = panel.y + top_offset
        avail_w = panel.width - 24
        avail_h = panel.bottom - 70 - avail_top
        scale = max(0.01, min(avail_w / picker['width'], avail_h / picker['height']))
        canvas_w = max(1, int(picker['width'] * scale))
        canvas_h = max(1, int(picker['height'] * scale))
        canvas_rect = pygame.Rect(
            panel.x + 12 + (avail_w - canvas_w) // 2,
            avail_top + max(0, (avail_h - canvas_h) // 2),
            canvas_w, canvas_h)

        pygame.draw.rect(screen, colors['panel_light'], canvas_rect)
        if picker['preview_surface'] is not None:
            cache = picker.get('_scaled_preview')
            if cache is None or cache[1] != (canvas_w, canvas_h):
                scaled = pygame.transform.smoothscale(picker['preview_surface'], (canvas_w, canvas_h))
                picker['_scaled_preview'] = (scaled, (canvas_w, canvas_h))
            else:
                scaled = cache[0]
            screen.blit(scaled, canvas_rect.topleft)
        else:
            grid_step = max(16, int(64 * scale))
            for gx in range(canvas_rect.x, canvas_rect.right, grid_step):
                pygame.draw.line(screen, colors['grid'], (gx, canvas_rect.y), (gx, canvas_rect.bottom), 1)
            for gy in range(canvas_rect.y, canvas_rect.bottom, grid_step):
                pygame.draw.line(screen, colors['grid'], (canvas_rect.x, gy), (canvas_rect.right, gy), 1)
        pygame.draw.rect(screen, colors['accent'], canvas_rect, 2)

        if picker['x'] is not None and picker['y'] is not None:
            mx = canvas_rect.x + int(picker['x'] * scale)
            my = canvas_rect.y + int(picker['y'] * scale)
            pygame.draw.circle(screen, colors['success'], (mx, my), 6)
            pygame.draw.circle(screen, colors['bg'], (mx, my), 6, 1)
            coord_label = self.font_small.render(
                "(%d, %d)" % (picker['x'], picker['y']), True, colors['text'])
            label_x = min(mx + 10, panel.right - 12 - coord_label.get_width())
            screen.blit(coord_label, (label_x, my - 18))

        picker['canvas_rect'] = canvas_rect
        picker['scale'] = scale

        btn_w = 100
        done_rect = pygame.Rect(panel.right - 12 - btn_w, panel.bottom - 12 - _FIELD_H - 6, btn_w, _FIELD_H + 6)
        cancel_rect = pygame.Rect(done_rect.x - btn_w - 10, done_rect.y, btn_w, _FIELD_H + 6)

        done_enabled = picker['x'] is not None and picker['y'] is not None
        pygame.draw.rect(screen, colors['success'] if done_enabled else colors['panel_light'],
                          done_rect, border_radius=5)
        done_label = self.font_small.render("Done", True, colors['bg'] if done_enabled else colors['text_dim'])
        screen.blit(done_label, done_label.get_rect(center=done_rect.center))

        pygame.draw.rect(screen, colors['panel_light'], cancel_rect, border_radius=5)
        pygame.draw.rect(screen, colors['grid'], cancel_rect, 1, border_radius=5)
        cancel_label = self.font_small.render("Cancel", True, colors['text'])
        screen.blit(cancel_label, cancel_label.get_rect(center=cancel_rect.center))

        picker['done_rect'] = done_rect
        picker['cancel_rect'] = cancel_rect

    def _draw_skill_dropdown(self, screen, x, list_y, row_index):
        """Skill list depends on the row's own 'mode', via
        _skill_choices_for_row(): 'remove' shows only what
        self._current_character_id actually has equipped (so you can't pick
        a skill to remove that was never equipped); 'add' shows only skills
        from the global roster that character doesn't already have (so you
        can't pick one that's already equipped)."""
        colors = self.colors
        items = []
        list_w = 180
        real, placeholder = self._skill_choices_for_row(row_index)
        names = real or [placeholder]
        visible = names[:8]
        list_rect = pygame.Rect(x, list_y, list_w, len(visible) * 22)
        pygame.draw.rect(screen, colors['panel_light'], list_rect)
        pygame.draw.rect(screen, colors['accent'], list_rect, 2)
        for i, name in enumerate(visible):
            item_rect = pygame.Rect(x, list_y + i * 22, list_w, 22)
            label = self.font_small.render(name, True, colors['text'])
            screen.blit(label, (item_rect.x + 6, item_rect.y + 4))
            if real:
                items.append((item_rect, name))
        self._rects['skill_dropdown_items'] = items

    def _draw_transformation_dropdown(self, screen, x, list_y, row_index):
        """Transformation form list depends on the row's own 'mode', via
        _transformation_choices_for_row(): 'remove' shows only what
        self._current_character_id actually has unlocked (so you can't
        pick a form to remove that was never unlocked); 'add' shows only
        forms configured on that character that aren't unlocked yet (so
        you can't pick one that's already unlocked)."""
        colors = self.colors
        items = []
        list_w = 180
        real, placeholder = self._transformation_choices_for_row(row_index)
        names = real or [placeholder]
        visible = names[:8]
        list_rect = pygame.Rect(x, list_y, list_w, len(visible) * 22)
        pygame.draw.rect(screen, colors['panel_light'], list_rect)
        pygame.draw.rect(screen, colors['accent'], list_rect, 2)
        for i, name in enumerate(visible):
            item_rect = pygame.Rect(x, list_y + i * 22, list_w, 22)
            label = self.font_small.render(name, True, colors['text'])
            screen.blit(label, (item_rect.x + 6, item_rect.y + 4))
            if real:
                items.append((item_rect, name))
        self._rects['transformation_dropdown_items'] = items

    def _draw_skin_dropdown(self, screen, x, list_y, row_index):
        """Skin/costume list depends on which character it's scoped to, via
        _costume_choices_for_row(): the row's own 'character_id' field for
        set_player_character rows (skinning the character being switched
        TO), or self._current_character_id for set_player_skin rows
        (skinning whoever's currently played)."""
        colors = self.colors
        items = []
        list_w = 180
        real, placeholder = self._costume_choices_for_row(row_index)
        names = real or [placeholder]
        visible = names[:8]
        list_rect = pygame.Rect(x, list_y, list_w, len(visible) * 22)
        pygame.draw.rect(screen, colors['panel_light'], list_rect)
        pygame.draw.rect(screen, colors['accent'], list_rect, 2)
        for i, name in enumerate(visible):
            item_rect = pygame.Rect(x, list_y + i * 22, list_w, 22)
            label = self.font_small.render(name, True, colors['text'])
            screen.blit(label, (item_rect.x + 6, item_rect.y + 4))
            if real:
                items.append((item_rect, name))
        self._rects['skin_dropdown_items'] = items

    def _draw_animation_dropdown(self, screen, x, list_y, row_index):
        """Animation list depends on the row's own 'character_id' field,
        via _animation_choices_for_row() — play_character_animation always
        carries its own character_id (no "currently played" fallback like
        the skin picker's set_player_skin case), so this is always scoped
        to whatever's picked in that same row."""
        colors = self.colors
        items = []
        list_w = 180
        real, placeholder = self._animation_choices_for_row(row_index)
        names = real or [placeholder]
        visible = names[:8]
        list_rect = pygame.Rect(x, list_y, list_w, len(visible) * 22)
        pygame.draw.rect(screen, colors['panel_light'], list_rect)
        pygame.draw.rect(screen, colors['accent'], list_rect, 2)
        for i, name in enumerate(visible):
            item_rect = pygame.Rect(x, list_y + i * 22, list_w, 22)
            label = self.font_small.render(name, True, colors['text'])
            screen.blit(label, (item_rect.x + 6, item_rect.y + 4))
            if real:
                items.append((item_rect, name))
        self._rects['animation_dropdown_items'] = items

    def content_height(self):
        return getattr(self, '_content_height', 40)

# ═════════════════════════════════════════════════════════════════════════
# EventEditorWindow — combines both into one modal popup
# ═════════════════════════════════════════════════════════════════════════

import pygame

_MARGIN = 40
_PADDING = 20
_DIVIDER_GAP = 16
_BUTTON_H = 32


class EventEditorWindow:
    """Modal popup combining ConditionBuilder + ActionSequenceBuilder."""

    def __init__(self, flag_manager, colors=None):
        self.colors = colors or _DEFAULT_COLORS
        self.flag_manager = flag_manager

        self.condition_builder = ConditionBuilder(flag_manager, colors=self.colors)
        self.action_builder = ActionSequenceBuilder(colors=self.colors)

        self.font_title = pygame.font.Font(None, 24)
        self.font_small = pygame.font.Font(None, 16)

        self.active = False
        self.title = "Edit Event"
        self._on_save = None

        self.last_conditions = []
        self.last_actions = []

        self._rects = {}
        self._scroll_offset = 0

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def open(self, title="Edit Event", existing_conditions=None, existing_actions=None, on_save=None):
        self.title = title
        self._on_save = on_save
        self.condition_builder.refresh(existing_conditions)
        self.action_builder.refresh(existing_actions)
        self._scroll_offset = 0
        self.active = True

    def set_current_character(self, character_id, get_equipped_skills=None):
        """Forwarded to the action builder — see ActionSequenceBuilder.set_current_character()."""
        self.action_builder.set_current_character(character_id, get_equipped_skills)

    def set_current_room(self, room_name):
        """Forwarded to the action builder — see ActionSequenceBuilder.set_current_room()."""
        self.action_builder.set_current_room(room_name)

    def set_known_rooms(self, room_names, room_dims=None):
        """Forwarded to the action builder — see ActionSequenceBuilder.set_known_rooms()."""
        self.action_builder.set_known_rooms(room_names, room_dims)

    def set_room_preview_provider(self, provider):
        """Forwarded to the action builder — see ActionSequenceBuilder.set_room_preview_provider()."""
        self.action_builder.set_room_preview_provider(provider)

    def _commit_active_fields(self):
        """Flush whatever text field either builder currently has focused
        into its row's params. Needed because clicking Save goes straight to
        close() without ever passing that click through to the builders'
        own handle_input() — which is normally what commits _active_text on
        an outside click — so a field being typed into when Save is pressed
        would otherwise be silently discarded."""
        for builder in (self.condition_builder, self.action_builder):
            active = getattr(builder, '_active_field', None)
            if active is not None:
                row_index, field_name = active
                if 0 <= row_index < len(builder.rows):
                    builder.rows[row_index]['params'][field_name] = builder._active_text
                builder._active_field = None

    def close(self, save):
        if save:
            self._commit_active_fields()
            self.last_conditions = self.condition_builder.get_condition_list()
            self.last_actions = self.action_builder.get_action_list()
            if self._on_save:
                self._on_save(self.last_conditions, self.last_actions)
        self.active = False
        self._on_save = None

    # ── Layout ───────────────────────────────────────────────────────────────

    def _window_rect(self, screen):
        sw, sh = screen.get_size()
        return pygame.Rect(_MARGIN, _MARGIN, sw - _MARGIN * 2, sh - _MARGIN * 2)

    def _content_origin(self, screen):
        win = self._window_rect(screen)
        content_x = win.x + _PADDING
        content_y = win.y + 50
        content_w = win.width - _PADDING * 2

        left_w = max(300, min(460, int(content_w * 0.34)))
        right_x = content_x + left_w + _DIVIDER_GAP
        right_w = content_w - left_w - _DIVIDER_GAP
        return content_x, content_y, left_w, right_x, right_w

    # ── Input ────────────────────────────────────────────────────────────────

    def handle_input(self, event):
        if not self.active:
            return

        if self.action_builder._option_editor is not None or self.action_builder._spawn_picker is not None:
            self.action_builder.handle_input(event, *self._rects.get('action_origin', (0, 0)))
            return

        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self.close(save=False)
            return

        if event.type == pygame.MOUSEWHEEL:
            # An open action-type, music, or sound dropdown owns the wheel
            # while it's open — otherwise scrolling to reach an item would
            # also scroll the popup out from under it.
            if (self.action_builder._open_type_dropdown_row is not None
                    or self.action_builder._open_music_dropdown is not None
                    or self.action_builder._open_sound_dropdown is not None):
                self.action_builder.handle_input(event, *self._rects.get('action_origin', (0, 0)))
                return
            max_scroll = max(0, getattr(self, '_total_content_height', 0) - getattr(self, '_viewport_height', 0))
            self._scroll_offset -= event.y * 25
            self._scroll_offset = max(0, min(self._scroll_offset, max_scroll))
            return

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            save_rect = self._rects.get('save_btn')
            if save_rect and save_rect.collidepoint(event.pos):
                self.close(save=True)
                return
            cancel_rect = self._rects.get('cancel_btn')
            if cancel_rect and cancel_rect.collidepoint(event.pos):
                self.close(save=False)
                return

        # Both builders get every event and independently no-op unless it
        # falls on one of their own last-drawn rects. That's also what makes
        # cross-builder focus switching work for free: any click anywhere
        # (even one meant for the other builder) makes each builder commit
        # and clear its own active text field first, before checking for a
        # new one to activate.
        cond_origin = self._rects.get('condition_origin')
        action_origin = self._rects.get('action_origin')
        if cond_origin:
            self.condition_builder.handle_input(event, *cond_origin)
        if action_origin:
            self.action_builder.handle_input(event, *action_origin)

    # ── Draw ─────────────────────────────────────────────────────────────────

    def draw(self, screen):
        if not self.active:
            return

        colors = self.colors
        sw, sh = screen.get_size()

        overlay = pygame.Surface((sw, sh), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 160))
        screen.blit(overlay, (0, 0))

        win = self._window_rect(screen)
        win_surf = pygame.Surface((win.width, win.height), pygame.SRCALPHA)
        win_surf.fill(colors['bg_transparent'])
        screen.blit(win_surf, win.topleft)
        pygame.draw.rect(screen, colors['accent'], win, 2)

        title_surf = self.font_title.render(self.title, True, colors['text'])
        screen.blit(title_surf, (win.x + _PADDING, win.y + 12))

        hint = self.font_small.render("Esc to cancel", True, colors['text_dim'])
        screen.blit(hint, (win.right - _PADDING - hint.get_width(), win.y + 18))

        content_x, content_y, left_w, right_x, right_w = self._content_origin(screen)
        content_bottom = win.bottom - _BUTTON_H - _PADDING * 2

        clip_rect = pygame.Rect(win.x, content_y, win.width, content_bottom - content_y)
        screen.set_clip(clip_rect)

        draw_y = content_y - self._scroll_offset
        cond_origin = (content_x, draw_y)
        action_origin = (right_x, draw_y)
        self.condition_builder.draw(screen, content_x, draw_y, left_w)
        self.action_builder.draw(screen, right_x, draw_y, right_w)

        col_height = max(self.condition_builder.content_height(), self.action_builder.content_height())
        divider_x = right_x - _DIVIDER_GAP // 2
        pygame.draw.line(screen, colors['grid'], (divider_x, draw_y), (divider_x, draw_y + col_height), 1)

        screen.set_clip(None)

        self._viewport_height = content_bottom - content_y
        self._total_content_height = col_height

        self._rects['condition_origin'] = cond_origin
        self._rects['action_origin'] = action_origin

        # Save / Cancel
        btn_w = 100
        save_rect = pygame.Rect(win.right - _PADDING - btn_w, win.bottom - _PADDING - _BUTTON_H, btn_w, _BUTTON_H)
        cancel_rect = pygame.Rect(save_rect.x - btn_w - 10, save_rect.y, btn_w, _BUTTON_H)

        pygame.draw.rect(screen, colors['success'], save_rect, border_radius=5)
        save_label = self.font_small.render("Save", True, colors['bg'])
        screen.blit(save_label, save_label.get_rect(center=save_rect.center))

        pygame.draw.rect(screen, colors['panel_light'], cancel_rect, border_radius=5)
        pygame.draw.rect(screen, colors['grid'], cancel_rect, 1, border_radius=5)
        cancel_label = self.font_small.render("Cancel", True, colors['text'])
        screen.blit(cancel_label, cancel_label.get_rect(center=cancel_rect.center))

        self._rects['save_btn'] = save_rect
        self._rects['cancel_btn'] = cancel_rect