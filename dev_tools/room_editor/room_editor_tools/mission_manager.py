"""
MissionManager
──────────────
Central authority for all mission state. Pure data — no pygame dependency.

REVAMP NOTE
───────────
This used to be a self-contained system: five hardcoded objective types,
each wired to a bespoke Game hook (on_enemy_killed/on_room_entered/
on_item_acquired/on_npc_talked), plus a hand-rolled reward-application
path (Game._apply_mission_rewards) and a hand-rolled dialogue-phase state
machine (Game._advance_mission_dialogue). All of that duplicated things
core.flag_manager / core.event_actions already do generically for every
other system in the game (trigger boxes, cutscenes, locks).

Missions now plug into that same pipeline instead of running their own:

  * Each objective is a FlagManager condition tree (see flag_manager.py's
    flag_is/variable_is/check_item/... builders) — evaluated the exact
    same way a TriggerBox's conditions are. No more Game -> MissionManager
    event hooks: Game just needs to keep a handful of *generic* flags/
    variables current (kill_count:<enemy_id>, item_count:<item_id>,
    room_entered:<room> via flag_manager.mark_room_visited(), npc_talked:
    <id> via flag_manager.mark_npc_talked() — several of these already
    existed on FlagManager unused), and call
    MissionManager.evaluate_active_missions(flag_manager) once a frame.
    Anything else that can already express a condition (boss HP, a spam
    bar, a custom variable) is a valid objective for free.

  * Each mission phase (offer/active/completed/rewarded) is a
    core.event_actions action list — typically a dialogue_box or
    dialogue_choice ending in a mission(mode='start'/'complete', ...)
    action — run through the same EventRunner a trigger box's actions run
    through. Rewards are just zeni()/item()/exp()/skill()/... actions
    under 'reward_actions', fired via EventRunner when the mission
    action's 'complete' mode runs.

Old-format mission dicts (string 'dialogues' + a flat 'rewards' dict, the
shape this file used to save) are transparently upgraded to the new
shape the first time they're registered — see _migrate_legacy_dialogues/
_migrate_legacy_rewards below — so existing save/room data doesn't break.

Mission dict schema
───────────────────
{
    'id':                str,   # stable ID (set to the giver NPC's instance_id)
    'giver_instance_id': str,   # instance_id of the NPC who hands out this quest
    'state':             str,   # 'inactive' | 'active' | 'completed' | 'failed'
    'sequential':        bool,  # True → objectives must complete in order
    'objectives': [
        {
            'id':          str,
            'type':        str,   # kill | reach_room | bring_item | collect_item | talk_to_npc | custom
            'description': str,   # human-readable summary (editor/HUD)
            'params':      dict,  # type-specific, only used to (re)build 'condition' in the editor
            'condition':   dict,  # FlagManager condition tree — this is what actually gets evaluated
            'completed':   bool,
        }, ...
    ],
    'reward_actions': [ ... ],   # core.event_actions action list, run on 'complete'
    'dialogue_actions': {
        'offer':     [ ... ],    # action list shown when state == 'inactive'
        'active':    [ ... ],    # action list shown while active, objectives not all done
        'completed': [ ... ],    # action list shown once all objectives are done, not yet claimed
        'rewarded':  [ ... ],    # action list shown after the mission is claimed
    },
}

Objective params by type (still used by the editor to render type-specific
fields and to regenerate 'condition' + 'description' via make_objective()/
update_objective_params() — the runtime never looks at 'type'/'params'
directly, only at 'condition')
────────────────────────
kill:         {'enemy_id': str,  'count': int, 'room': str}   (empty = any)
reach_room:   {'room_name': str}
bring_item:   {'item_id': str,   'count': int}
collect_item: {'item_id': str,   'count': int}
talk_to_npc:  {'npc_instance_id': str}
"""

import json
import os

from core.flag_manager import flag_is, variable_is, check_item

SAVE_PATH = 'saves/missions.json'

# ── Objective type metadata (used by the editor) ─────────────────────────────
OBJECTIVE_TYPES = ['kill', 'reach_room', 'bring_item', 'collect_item', 'talk_to_npc']

OBJECTIVE_DEFAULTS = {
    'kill':         {'enemy_id': '', 'count': 1, 'room': ''},
    'reach_room':   {'room_name': ''},
    'bring_item':   {'item_id': '', 'count': 1},
    'collect_item': {'item_id': '', 'count': 1},
    'talk_to_npc':  {'npc_instance_id': ''},
}

# (param_key, display_label, pixel_width) — used by the editor for field layout
OBJECTIVE_PARAM_FIELDS = {
    'kill':         [('enemy_id', 'Enemy (blank=any)', 140), ('count', 'Count', 48), ('room', 'Room (blank=any)', 120)],
    'reach_room':   [('room_name', 'Room Name', 230)],
    'bring_item':   [('item_id', 'Item ID', 170), ('count', 'Count', 48)],
    'collect_item': [('item_id', 'Item ID', 170), ('count', 'Count', 48)],
    'talk_to_npc':  [('npc_instance_id', 'NPC Instance ID', 270)],
}


def _kill_count_var(enemy_id, room=None):
    """Variable name Game bumps by 1 every kill (see game.py's enemy-death
    handler) — room-scoped only when a specific room was asked for, so a
    "kill 5x goblin anywhere" objective and a "kill 5x goblin in cave_01"
    objective don't fight over the same counter."""
    return f'kill_count:{enemy_id}:{room}' if room else f'kill_count:{enemy_id}'


def _item_count_var(item_id):
    """Variable Game bumps whenever an item is actually picked up off the
    ground (see game.py's item-pickup completion). Distinct from
    check_item()/'player_has_item', which reads current inventory count —
    collect_item wants a lifetime tally so consuming/selling the item
    later doesn't un-complete the objective; bring_item wants the live
    inventory count since the point is handing it over."""
    return f'item_count:{item_id}'


def build_objective_condition(obj_type, params):
    """Turn an objective type + params dict into a FlagManager condition
    tree and a human-readable description. This is the only place that
    needs to know what each legacy objective 'type' means — everything
    downstream (evaluation, progress display) just reads 'condition'."""
    p = params or {}

    if obj_type == 'kill':
        enemy_id = p.get('enemy_id', '')
        count    = int(p.get('count', 1))
        room     = p.get('room', '')
        condition = variable_is(_kill_count_var(enemy_id, room or None), '>=', count)
        who  = enemy_id or 'any enemy'
        desc = f"Kill {count}x {who}" + (f" in {room}" if room else "")
        return condition, desc

    if obj_type == 'reach_room':
        room_name = p.get('room_name', '')
        return flag_is(f'room_entered:{room_name}'), f"Travel to {room_name or '?'}"

    if obj_type == 'bring_item':
        item_id = p.get('item_id', '')
        count   = int(p.get('count', 1))
        return check_item(item_id, min_quantity=count), f"Bring {count}x {item_id or '?'}"

    if obj_type == 'collect_item':
        item_id = p.get('item_id', '')
        count   = int(p.get('count', 1))
        return variable_is(_item_count_var(item_id), '>=', count), f"Collect {count}x {item_id or '?'}"

    if obj_type == 'talk_to_npc':
        npc_id = p.get('npc_instance_id', '')
        return flag_is(f'npc_talked:{npc_id}'), f"Talk to NPC ({npc_id or '?'})"

    # 'custom' / unknown type — params IS the condition tree directly, and
    # description must already be set by whoever built it (e.g. a
    # ConditionBuilder-driven "custom objective" row in the mission editor).
    return p.get('condition') or {}, p.get('description', 'Objective')


class MissionManager:

    def __init__(self):
        self.missions: dict[str, dict] = {}   # id → mission_dict
        self._saved_state: dict = {}
        self.block_saves = False   # set True during test mode to prevent disk writes
        self._load()

    # =========================================================================
    # Registration
    # =========================================================================

    def register_mission(self, mission: dict):
        """Add or refresh a mission definition, preserving live runtime state."""
        mid = mission.get('id', '')
        if not mid:
            return
        mission = dict(mission)
        mission['objectives'] = [self._ensure_condition(dict(o)) for o in mission.get('objectives', [])]
        self._migrate_legacy_dialogues(mission)
        self._migrate_legacy_rewards(mission)

        saved = self._saved_state.get(mid, {})
        if mid in self.missions:
            # Already live — grab current runtime state instead of saved state
            saved = {
                'state':      self.missions[mid].get('state',      'inactive'),
                'objectives': self.missions[mid].get('objectives', mission.get('objectives', [])),
            }
        mission['state'] = saved.get('state', mission.get('state', 'inactive'))
        if saved.get('objectives'):
            # Merge completed-flags from saved runtime state onto the fresh
            # objective definitions (definitions may have been re-edited).
            saved_by_id = {o.get('id'): o for o in saved['objectives']}
            for obj in mission['objectives']:
                prior = saved_by_id.get(obj.get('id'))
                if prior is not None:
                    obj['completed'] = prior.get('completed', False)
        mission.setdefault('sequential', False)
        self.missions[mid] = mission

    def _ensure_condition(self, obj):
        """Backfill 'condition' on an objective dict that only has legacy
        'type'/'params' (e.g. loaded from older room/save data)."""
        if not obj.get('condition'):
            condition, desc = build_objective_condition(obj.get('type', ''), obj.get('params', {}))
            obj['condition'] = condition
            obj.setdefault('description', desc)
        obj.setdefault('completed', False)
        return obj

    def _migrate_legacy_dialogues(self, mission):
        """Old shape: mission['dialogues'][phase] = str | list[str].
        New shape:    mission['dialogue_actions'][phase] = [action, ...].
        Only runs if 'dialogue_actions' isn't already present, so re-saving
        from the new editor never gets clobbered back to the old shape."""
        if mission.get('dialogue_actions'):
            return
        from core.event_actions import dialogue_box, dialogue_choice, mission as mission_action

        legacy = mission.get('dialogues', {}) or {}
        actions = {}
        for phase in ('offer', 'active', 'completed', 'rewarded'):
            lines = legacy.get(phase, [])
            if isinstance(lines, str):
                lines = [lines] if lines.strip() else []
            phase_actions = [dialogue_box('character', line) for line in lines]

            if phase == 'offer' and phase_actions:
                accepted_lines = legacy.get('accepted', '')
                if isinstance(accepted_lines, str):
                    accepted_lines = [accepted_lines] if accepted_lines.strip() else []
                accept_actions = [dialogue_box('character', line) for line in accepted_lines]
                accept_actions.append(mission_action('start', mission.get('id', '')))
                phase_actions.append(dialogue_choice(
                    prompt=None,
                    options=[
                        {'text': 'Accept', 'actions': accept_actions},
                        {'text': 'Not now', 'actions': []},
                    ],
                ))
            if phase == 'completed' and phase_actions:
                phase_actions.append(mission_action('complete', mission.get('id', '')))

            actions[phase] = phase_actions
        mission['dialogue_actions'] = actions

    def _migrate_legacy_rewards(self, mission):
        """Old shape: mission['rewards'] = {'xp': int, 'items': [{'item_id','count'}]}.
        New shape:    mission['reward_actions'] = [exp(...), item(...), ...]."""
        if mission.get('reward_actions'):
            return
        from core.event_actions import exp as exp_action, item as item_action

        legacy = mission.get('rewards', {}) or {}
        actions = []
        xp = legacy.get('xp', 0)
        if xp:
            actions.append(exp_action(mode='add', amount=xp))
        for entry in legacy.get('items', []):
            item_id = entry.get('item_id', '')
            count   = int(entry.get('count', 1))
            if item_id:
                actions.append(item_action(mode='add', item_id=item_id, quantity=count))
        mission['reward_actions'] = actions

    def scan_rooms_for_missions(self, room_manager):
        """Walk every room entity list and register NPC missions. Idempotent."""
        if not room_manager:
            return
        for room in getattr(room_manager, 'rooms', []):
            for entity in getattr(room, 'entities', []):
                if entity.get('entity_type') != 'npc':
                    continue
                mission_def = entity.get('mission')
                if not mission_def:
                    continue
                mission_def = dict(mission_def)
                iid = entity.get('instance_id', '')
                mission_def['giver_instance_id'] = iid
                if not mission_def.get('id'):
                    mission_def['id'] = iid
                self.register_mission(mission_def)

    # =========================================================================
    # NPC dialogue state
    # =========================================================================

    def get_npc_dialogue_state(self, npc_instance_id: str):
        """Return the dialogue-branch key for this NPC, or None if no mission."""
        if not npc_instance_id:
            return None
        for mission in self.missions.values():
            if mission.get('giver_instance_id') != npc_instance_id:
                continue
            state = mission.get('state', 'inactive')
            if state == 'inactive':
                return 'offer'
            if state == 'active':
                return 'completed' if self._all_objectives_done(mission) else 'active'
            if state == 'completed':
                return 'rewarded'
        return None

    def get_mission_for_npc(self, npc_instance_id: str):
        """Return the mission dict for this NPC, or None."""
        for mission in self.missions.values():
            if mission.get('giver_instance_id') == npc_instance_id:
                return mission
        return None

    # =========================================================================
    # Lifecycle — driven by the 'mission' event action (see event_actions.py)
    # =========================================================================

    def accept_mission(self, mission_id: str):
        if mission_id in self.missions:
            self.missions[mission_id]['state'] = 'active'
            self.save()

    def claim_reward(self, mission_id: str) -> list:
        """Mark mission completed and return its reward action list, for the
        'mission' action handler to run through EventRunner. Returns []
        (never a dict) — callers stopped needing to know reward shape."""
        mission = self.missions.get(mission_id)
        if not mission:
            return []
        mission['state'] = 'completed'
        self.save()
        return mission.get('reward_actions', [])

    def fail_mission(self, mission_id: str):
        if mission_id in self.missions:
            self.missions[mission_id]['state'] = 'failed'
            self.save()

    def reset_mission(self, mission_id: str):
        """Return a mission to 'inactive' and clear objective progress —
        e.g. for a repeatable quest, or dev-tool testing."""
        mission = self.missions.get(mission_id)
        if not mission:
            return
        mission['state'] = 'inactive'
        for obj in mission.get('objectives', []):
            obj['completed'] = False
        self.save()

    # =========================================================================
    # Evaluation — replaces the old on_enemy_killed/on_room_entered/
    # on_item_acquired/on_npc_talked hooks. Call once a frame (cheap — it's
    # just dict/condition-tree walks over however many missions are active).
    # =========================================================================

    def evaluate_active_missions(self, flag_manager) -> list:
        """Re-check every incomplete objective on every active mission
        against current flag/variable/live state. Returns the list of
        mission ids that had at least one objective newly complete this
        call (e.g. so Game can play a sfx) — empty list most frames."""
        newly_progressed = []
        changed = False
        for mission in self.missions.values():
            if mission.get('state') != 'active':
                continue
            objectives = mission.get('objectives', [])
            sequential = mission.get('sequential', False)
            for obj in objectives:
                if obj.get('completed'):
                    continue
                if flag_manager.evaluate(obj.get('condition')):
                    obj['completed'] = True
                    changed = True
                    if mission['id'] not in newly_progressed:
                        newly_progressed.append(mission['id'])
                if sequential:
                    # Sequential missions only ever have their next
                    # incomplete objective live — stop at the first one,
                    # whether it just completed or not.
                    break
        if changed:
            self.save()
        return newly_progressed

    def get_objective_progress(self, obj: dict, flag_manager):
        """(current, required) for HUD display — generic over whatever kind
        of condition the objective actually holds, so new objective types
        never need bespoke progress-tracking code here."""
        condition = obj.get('condition') or {}
        ctype = condition.get('type')

        if obj.get('completed'):
            required = condition.get('value', 1) if ctype in ('variable', 'live') else 1
            return required, required

        if ctype == 'variable':
            current = flag_manager.get_variable(condition['name'], 0)
            return current, condition.get('value', 1)
        if ctype == 'live':
            current = flag_manager.get_live_value(condition['lookup'], *condition.get('args', []))
            return (current or 0), condition.get('value', 1)
        return 0, 1

    # =========================================================================
    # Helpers
    # =========================================================================

    def _all_objectives_done(self, mission: dict) -> bool:
        objectives = mission.get('objectives', [])
        return bool(objectives) and all(obj.get('completed', False) for obj in objectives)

    def get_active_missions(self) -> list:
        return [m for m in self.missions.values() if m.get('state') == 'active']

    def get_completed_missions(self) -> list:
        return [m for m in self.missions.values() if m.get('state') == 'completed']

    def snapshot(self) -> dict:
        """Deep copy of all mission states — used by test mode to restore on exit."""
        import copy
        return {
            mid: {
                'state':      m.get('state', 'inactive'),
                'objectives': copy.deepcopy(m.get('objectives', [])),
            }
            for mid, m in self.missions.items()
        }

    def restore(self, snap: dict):
        """Restore mission states from a snapshot — never writes to disk."""
        for mid, saved in snap.items():
            if mid in self.missions:
                self.missions[mid]['state']      = saved.get('state', 'inactive')
                self.missions[mid]['objectives'] = saved.get('objectives', [])

    @staticmethod
    def make_objective(obj_type: str, params: dict = None) -> dict:
        """Create a fresh objective dict of the given type. Called by the
        mission editor whenever a row's type dropdown changes or a field is
        edited — regenerates 'condition' + 'description' from 'params' each
        time so the two can never drift out of sync."""
        import uuid
        p = dict(OBJECTIVE_DEFAULTS.get(obj_type, {}))
        p.update(params or {})
        condition, description = build_objective_condition(obj_type, p)
        return {
            'id':          str(uuid.uuid4())[:8],
            'type':        obj_type,
            'description': description,
            'params':      p,
            'condition':   condition,
            'completed':   False,
        }

    @staticmethod
    def update_objective_params(obj: dict, params: dict) -> dict:
        """Editor calls this after the designer edits a field — merges
        `params` onto obj['params'] and regenerates 'condition'/'description'
        in place. Returns obj for chaining."""
        obj['params'] = {**obj.get('params', {}), **params}
        condition, description = build_objective_condition(obj.get('type', ''), obj['params'])
        obj['condition']   = condition
        obj['description'] = description
        return obj

    @staticmethod
    def objective_summary(obj: dict) -> str:
        """Short human-readable label shown in the editor and HUD."""
        return obj.get('description') or build_objective_condition(
            obj.get('type', ''), obj.get('params', {}))[1]

    # =========================================================================
    # Persistence
    # =========================================================================

    def save(self):
        if self.block_saves:
            return
        try:
            os.makedirs(os.path.dirname(SAVE_PATH), exist_ok=True)
            state_data = {
                mid: {
                    'state':      m.get('state', 'inactive'),
                    'objectives': m.get('objectives', []),
                }
                for mid, m in self.missions.items()
            }
            with open(SAVE_PATH, 'w') as f:
                json.dump(state_data, f, indent=2)
        except Exception:
            pass

    def _load(self):
        self._saved_state = {}
        if not os.path.exists(SAVE_PATH):
            return
        try:
            with open(SAVE_PATH) as f:
                self._saved_state = json.load(f)
        except Exception:
            pass