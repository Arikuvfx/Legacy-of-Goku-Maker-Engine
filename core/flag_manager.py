"""
core/flag_manager.py — Progression flag system.

Mirrors MissionManager's shape (snapshot()/restore(), block_saves, to_dict/
from_dict) so it drops into Game the same way mission_manager already does.

Concepts
--------
Flags        Persistent booleans, keyed by a string id like "chest_opened:c17".
             Set once via trigger()/mark_*() convenience methods, checked
             forever after (used for unlocks, locks, "have I done X yet").

Live values   Non-persistent numbers/bools read fresh every check via a
             registered lookup callback (enemy hp %, "enemy sees player",
             a bar's current %). Registered with register_live_lookup().

Conditions   Small serializable dict trees combining flags + live values
             with AND / OR / NOT, e.g. a locked gate that needs
             flag "switch_triggered:s1" AND enemy_hp_percent("boss1") <= 50.
             This is what any lock/unlock/dev-tool condition builder reads.

Usage from Game
----------------
    self.flag_manager = FlagManager()
    ...
    self.flag_manager.register_live_lookup('boss_hp_lookup', self._lookup_boss_hp_percent)
    self.flag_manager.load(self.save_manager.current_flag_data())   # or however your save file hands data in

Everywhere else in the engine, just call the matching mark_*()/trigger()
method when the thing happens (see the bottom of this file for the full
list mapped to your trigger-source spec).
"""

import copy
import json


# ─────────────────────────────────────────────────────────────────────────────
# Condition tree — small, serializable, editor-friendly
# ─────────────────────────────────────────────────────────────────────────────

_COMPARATORS = {
    '==': lambda a, b: a == b,
    '!=': lambda a, b: a != b,
    '<':  lambda a, b: a is not None and a < b,
    '<=': lambda a, b: a is not None and a <= b,
    '>':  lambda a, b: a is not None and a > b,
    '>=': lambda a, b: a is not None and a >= b,
}


def flag_is(flag_id):
    """Leaf condition: the given flag has been triggered."""
    return {'type': 'flag', 'id': flag_id}


def flag_is_not(flag_id):
    return {'type': 'not', 'child': flag_is(flag_id)}


def live_check(lookup_name, cmp, value, args=None):
    """Leaf condition comparing a live lookup's current value against `value`.

    lookup_name must have been registered with register_live_lookup().
    `args` (list) is passed through to the lookup callable, e.g. an enemy id.
    cmp is one of '==','!=','<','<=','>','>='.
    """
    return {'type': 'live', 'lookup': lookup_name, 'args': args or [], 'cmp': cmp, 'value': value}


def all_of(*conditions):
    return {'type': 'and', 'children': list(conditions)}


def any_of(*conditions):
    return {'type': 'or', 'children': list(conditions)}


def negate(condition):
    return {'type': 'not', 'child': condition}


def variable_is(var_name, cmp, value):
    """Leaf condition reading a FlagManager custom variable directly (no
    lookup registration needed — FlagManager owns these). E.g. the classic
    "if Var_01 is true do ..." check: variable_is('Var_01', '==', True)."""
    return {'type': 'variable', 'name': var_name, 'cmp': cmp, 'value': value}


# The remaining "Check ..." condition types from the spec all read player-side
# state FlagManager doesn't own, so they're thin convenience wrappers around
# live_check() — Game must register the matching lookup (see the docstring
# at the bottom of this file for the full list of expected lookup names).

def check_item(item_id, min_quantity=1):
    return live_check('player_has_item', '>=', min_quantity, args=[item_id])


def check_stat(stat_name, cmp, value):
    return live_check('player_stat', cmp, value, args=[stat_name])


def check_character(character_id):
    return live_check('player_character', '==', character_id)


def check_zeni(cmp, value):
    return live_check('player_zeni', cmp, value)


def check_resource(resource, cmp, value):
    """resource: 'health' | 'energy' | 'transformation_gauge'."""
    return live_check('player_resource', cmp, value, args=[resource])


def check_skill(skill_id):
    return live_check('player_has_skill', '==', True, args=[skill_id])


def check_timer(timer_id, cmp, value):
    """Leaf condition comparing a timer's remaining seconds against `value`.
    e.g. check_timer('boss_intro', '<=', 5) — true once 5 seconds or less
    remain. False (via the comparator's None-guard) if the timer isn't
    running/doesn't exist."""
    return live_check('player_timer_remaining', cmp, value, args=[timer_id])


def check_bar(bar_id, cmp, value):
    """Leaf condition comparing a spam/timing bar's current percent (0-100)
    against `value` — e.g. check_bar('wake_up_mash', '>=', 50). Reads
    straight off FlagManager.bar_values (kept current by set_bar_percent(),
    which the 'spam_qte' event action's handler calls every frame it runs —
    see Game._update_spam_qte), not a Game-owned lookup like check_timer/
    check_boss_hp above. Register 'player_bar_percent' ->
    self.flag_manager.get_bar_percent once, same call shape as any other
    live lookup (see the bottom of this file). 0 for a bar_id that's never
    been reported yet (get_bar_percent()'s own default), same as a fresh
    bar sitting empty.

    For a one-shot "has this bar ever reached X" check instead of "is it
    at X right now", use flag_is(f'bar_reached:{bar_id}:{threshold}')
    instead — set_bar_percent() latches those at 0/50/100 automatically."""
    return live_check('player_bar_percent', cmp, value, args=[bar_id])


def check_boss_hp(boss_id, cmp, value, mode='percent'):
    """Leaf condition comparing a specific boss's HP against `value`.

    boss_id  — matches BossEnemy.boss_id, identifying which boss in the
               room to check (not "any boss" — a specific one).
    mode     — 'percent': value is compared against 0-100 (current/max*100).
               'value':   value is compared against the raw current HP.
    False (via the comparator's None-guard, or the lookup's own
    unregistered-lookup fail-closed behavior) if that boss isn't currently
    spawned/active in the room.

    Game must register both 'boss_hp_lookup' (percent) and
    'boss_hp_value_lookup' (raw) live lookups — see the bottom of this file.
    """
    lookup_name = 'boss_hp_lookup' if mode == 'percent' else 'boss_hp_value_lookup'
    return live_check(lookup_name, cmp, value, args=[boss_id])


# ─────────────────────────────────────────────────────────────────────────────
# FlagManager
# ─────────────────────────────────────────────────────────────────────────────

class FlagManager:
    """Owns every progression flag plus the live-value lookups used to
    evaluate lock/unlock conditions built against them."""

    def __init__(self):
        self.flags = {}              # flag_id -> True (absence == False/unset)
        self.bar_values = {}          # bar_id  -> float 0-100, last-known % for spam/timing bars
        self.variables = {}           # custom variable name -> any JSON-safe value (bool/int/float/str)
        self.block_saves = False      # set True during test-mode play (mirrors mission_manager)
        self._live_lookups = {}       # name -> callable(*args) -> value
        self._names_refresh_callback = None   # FlagEditor dropdown hook, see set_names_refresh_callback

        # Death-override registrations: enemy_id -> callback(player, enemy) -> bool.
        # If the callback returns True, Game should play the registered cutscene
        # instead of the normal death sequence.
        self._death_overrides = {}

    # ── Registration ─────────────────────────────────────────────────────────

    def register_live_lookup(self, name, callback):
        """Register a callable used by live_check() conditions, e.g.
        register_live_lookup('boss_hp_lookup', self._lookup_boss_hp_percent)."""
        self._live_lookups[name] = callback

    def set_names_refresh_callback(self, callback):
        """Wire up FlagEditor's dropdown-name source, e.g.
        flag_manager.set_names_refresh_callback(self._get_flag_condition_names)."""
        self._names_refresh_callback = callback

    def get_condition_names(self):
        """Used by the FlagEditor UI to populate its dropdowns."""
        if self._names_refresh_callback:
            return self._names_refresh_callback()
        return {}

    def register_death_override(self, enemy_id, callback):
        """Register a cutscene hook so a specific enemy's killing blow doesn't
        end the game — Game should call should_override_death() from its
        normal player-death handling and branch into the cutscene instead."""
        self._death_overrides[enemy_id] = callback

    def should_override_death(self, player, enemy):
        enemy_id = getattr(enemy, 'boss_id', None) or getattr(enemy, 'enemy_type', None)
        callback = self._death_overrides.get(enemy_id)
        if callback:
            return bool(callback(player, enemy))
        return False

    # ── Core flag storage ────────────────────────────────────────────────────

    def trigger(self, flag_id):
        """Set a flag true. Safe to call repeatedly (idempotent)."""
        self.flags[flag_id] = True

    def is_set(self, flag_id):
        return bool(self.flags.get(flag_id, False))

    def clear(self, flag_id):
        self.flags.pop(flag_id, None)

    def clear_all(self):
        self.flags.clear()
        self.bar_values.clear()
        self.variables.clear()

    # ── Trigger-source convenience methods ───────────────────────────────────
    # One method per box/source from the spec. All just call trigger() with a
    # namespaced id, so condition authors get consistent, predictable ids.

    # Trigger Box
    def mark_box_triggered(self, box_id):
        self.trigger(f'box:{box_id}')

    # Room Transition
    def mark_room_visited(self, room_name):
        self.trigger(f'room_entered:{room_name}')

    def mark_room_exited(self, room_name):
        self.trigger(f'room_exited:{room_name}')

    def has_visited_room(self, room_name):
        return self.is_set(f'room_entered:{room_name}')

    # Destructible Stone / Gate
    def mark_stone_destroyed(self, stone_id):
        self.trigger(f'stone_destroyed:{stone_id}')

    def mark_gate_destroyed(self, gate_id):
        self.trigger(f'gate_destroyed:{gate_id}')

    # Flying Pad
    def mark_pad_taken(self, pad_id, pad_index):
        """pad_index: 1 for first pad, 2 for second pad, matching the spec."""
        self.trigger(f'pad_taken:{pad_id}:{pad_index}')

    # Save Pad
    def mark_player_saved(self, save_id):
        self.trigger(f'player_saved:{save_id}')

    def mark_character_selected(self, save_id, character_id):
        self.trigger(f'character_selected:{save_id}:{character_id}')

    # World Map
    def mark_zone_entered(self, zone_id):
        self.trigger(f'zone_entered:{zone_id}')

    # Chest
    def mark_chest_opened(self, chest_id):
        self.trigger(f'chest_opened:{chest_id}')

    # Switch
    def mark_switch_triggered(self, switch_id):
        self.trigger(f'switch_triggered:{switch_id}')

    # NPC
    def mark_npc_talked(self, npc_id):
        self.trigger(f'npc_talked:{npc_id}')

    # Enemies & Boss (the persistent ones — "sees player" and "hp %" are live, see below)
    def mark_enemy_defeated(self, enemy_id):
        self.trigger(f'enemy_defeated:{enemy_id}')

    def is_enemy_defeated(self, enemy_id):
        return self.is_set(f'enemy_defeated:{enemy_id}')

    def mark_player_killed_by(self, enemy_id):
        self.trigger(f'player_killed_by:{enemy_id}')

    # Items
    def mark_item_picked_up(self, item_id):
        self.trigger(f'item_picked_up:{item_id}')

    # Spam Bar / Timing Bar — live-checked via live_check(), but we also latch
    # threshold flags so "reached 50% at least once" can be a permanent unlock.
    def set_bar_percent(self, bar_id, percent):
        self.bar_values[bar_id] = percent
        for threshold in (0, 50, 100):
            if percent >= threshold:
                self.trigger(f'bar_reached:{bar_id}:{threshold}')

    def get_bar_percent(self, bar_id):
        return self.bar_values.get(bar_id, 0.0)

    # Quest
    def mark_quest_finished(self, quest_id):
        self.trigger(f'quest_finished:{quest_id}')

    # Timer — the spec asks for a flag trigger on start/pause/end, in
    # addition to the Timer start/pause/stop *action* (see event_actions.py).
    def mark_timer_started(self, timer_id):
        self.trigger(f'timer_started:{timer_id}')

    def mark_timer_paused(self, timer_id):
        self.trigger(f'timer_paused:{timer_id}')

    def mark_timer_ended(self, timer_id):
        self.trigger(f'timer_ended:{timer_id}')

    # ── Custom variables ─────────────────────────────────────────────────────
    # Freeform named values ("Var_01") distinct from boolean flags — used by
    # "Make Custom Variables" / "Check Custom Variables" and by
    # ModifyQuestVariable-style actions.

    def get_variable(self, name, default=None):
        return self.variables.get(name, default)

    def set_variable(self, name, value):
        self.variables[name] = value

    def add_variable(self, name, amount, default=0):
        """Numeric add-in-place, e.g. quest counters. Creates the variable
        at `default` first if it doesn't exist yet."""
        self.variables[name] = self.variables.get(name, default) + amount

    def remove_variable(self, name):
        self.variables.pop(name, None)

    # ── Condition evaluation ─────────────────────────────────────────────────

    def evaluate(self, condition):
        """Evaluate a condition tree (see flag_is/live_check/all_of/any_of/
        negate above) against current flag + live state. None/{} == True
        (an unset condition never blocks anything)."""
        if not condition:
            return True

        node_type = condition.get('type')

        if node_type == 'flag':
            return self.is_set(condition['id'])

        if node_type == 'not':
            return not self.evaluate(condition['child'])

        if node_type == 'and':
            return all(self.evaluate(c) for c in condition.get('children', []))

        if node_type == 'or':
            return any(self.evaluate(c) for c in condition.get('children', []))

        if node_type == 'variable':
            comparator = _COMPARATORS.get(condition['cmp'], _COMPARATORS['=='])
            return comparator(self.variables.get(condition['name']), condition['value'])

        if node_type == 'live':
            lookup = self._live_lookups.get(condition['lookup'])
            if lookup is None:
                return False  # unregistered lookup — fail closed, don't unlock by accident
            value = lookup(*condition.get('args', []))
            comparator = _COMPARATORS.get(condition['cmp'], _COMPARATORS['=='])
            return comparator(value, condition['value'])

        return False

    def evaluate_conditions(self, conditions, player=None):
        """AND together a list of condition rows (what ConditionBuilder saves
        onto a box/NPC/etc — 'all must be true'). `player` is accepted for
        callers like TriggerBox.should_fire() but unused here: live checks
        already reach the player through their registered lookup closures."""
        if not conditions:
            return True
        return all(self.evaluate(c) for c in conditions)

    # ── Save / load ──────────────────────────────────────────────────────────

    def to_dict(self):
        return {
            'flags': dict(self.flags),
            'bar_values': dict(self.bar_values),
            'variables': dict(self.variables),
        }

    def from_dict(self, data):
        data = data or {}
        self.flags = dict(data.get('flags', {}))
        self.bar_values = dict(data.get('bar_values', {}))
        self.variables = dict(data.get('variables', {}))

    def save(self, path=None):
        """Persist to disk if a path is given, and always return the dict so
        callers folding this into a larger save-file payload can use that
        instead. Respects block_saves (set during test mode) exactly like
        mission_manager.block_saves does."""
        if self.block_saves:
            return self.to_dict()
        data = self.to_dict()
        if path:
            with open(path, 'w') as f:
                json.dump(data, f, indent=2)
        return data

    def load(self, path_or_data=None):
        if path_or_data is None:
            return
        if isinstance(path_or_data, dict):
            self.from_dict(path_or_data)
            return
        try:
            with open(path_or_data, 'r') as f:
                self.from_dict(json.load(f))
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    # ── Test-mode snapshot / restore (matches mission_manager pattern) ───────

    def snapshot(self):
        return copy.deepcopy(self.to_dict())

    def restore(self, snapshot):
        if snapshot is not None:
            self.from_dict(snapshot)


# ─────────────────────────────────────────────────────────────────────────────
# Live lookups Game should register for the "Check ..." condition builders
# above to work (call these once, wherever _lookup_boss_hp_percent is wired):
#
#   'boss_hp_lookup'      (boss_id)              -> float 0-100 or None
#   'boss_hp_value_lookup' (boss_id)             -> float raw current HP, or None
#   'player_has_item'     (item_id)              -> int quantity owned
#   'player_stat'         (stat_name)            -> current numeric stat value
#   'player_character'    ()                     -> currently active character_id
#   'player_zeni'         ()                     -> int current zeni
#   'player_resource'     (resource_name)        -> float, resource_name in
#                                                    ('health','energy','transformation_gauge')
#   'player_has_skill'    (skill_id)             -> bool
#   'player_timer_remaining' (timer_id)          -> float seconds remaining, or None if not running
#   'player_bar_percent'  (bar_id)               -> float 0-100 current/last-known spam/timing
#                                                    bar percent (see set_bar_percent/check_bar).
#                                                    Just self.flag_manager.get_bar_percent —
#                                                    FlagManager already owns this data itself.
#
# e.g. self.flag_manager.register_live_lookup('player_zeni', lambda: self.player.zeni)
# ─────────────────────────────────────────────────────────────────────────────