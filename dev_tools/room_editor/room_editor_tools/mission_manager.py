"""
MissionManager
──────────────
Central authority for all mission state.  Pure data — no pygame dependency.

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
            'type':        str,   # kill | reach_room | bring_item | collect_item | talk_to_npc
            'description': str,   # auto-generated summary
            'params':      dict,  # type-specific (see OBJECTIVE_PARAM_FIELDS)
            'progress':    int,
            'required':    int,
            'completed':   bool,
        }, ...
    ],
    'rewards': {
        'xp':   int,
        'items': [{'item_id': str, 'count': int}, ...]
    },
    'dialogues': {
        'accepted':  str | list[str],  # shown once right after accepting
        'active':    str | list[str],  # NPC says while mission is ongoing
        'completed': str | list[str],  # NPC says when all objectives are done
        'rewarded':  str | list[str],  # NPC says after reward already claimed
    }
}

Objective params by type
────────────────────────
kill:         {'enemy_id': str,  'count': int, 'room': str}   (empty = any)
reach_room:   {'room_name': str}
bring_item:   {'item_id': str,   'count': int}
collect_item: {'item_id': str,   'count': int}
talk_to_npc:  {'npc_instance_id': str}
"""

import json
import os

SAVE_PATH = 'saves/missions.json'

# ── Objective type metadata (also used by the editor) ────────────────────────
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
        saved = self._saved_state.get(mid, {})
        if mid in self.missions:
            # Already live — grab current runtime state instead of saved state
            saved = {
                'state':      self.missions[mid].get('state',      'inactive'),
                'objectives': self.missions[mid].get('objectives', mission.get('objectives', [])),
            }
        mission = dict(mission)
        mission['state']      = saved.get('state',      mission.get('state', 'inactive'))
        mission['objectives'] = saved.get('objectives', mission.get('objectives', []))
        self.missions[mid] = mission

    def scan_rooms_for_missions(self, room_manager):
        """Walk every room entity list and register NPC missions.  Idempotent."""
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
    # Lifecycle
    # =========================================================================

    def accept_mission(self, mission_id: str):
        if mission_id in self.missions:
            self.missions[mission_id]['state'] = 'active'
            self.save()

    def claim_reward(self, mission_id: str) -> dict:
        """Mark mission completed and return the rewards dict."""
        mission = self.missions.get(mission_id)
        if not mission:
            return {}
        mission['state'] = 'completed'
        self.save()
        return mission.get('rewards', {})

    def check_bring_item(self, mission_id: str, player_inventory: list) -> bool:
        """Evaluate bring_item objectives against the player's inventory.
        Returns True if every objective in this mission is now complete."""
        mission = self.missions.get(mission_id)
        if not mission or mission.get('state') != 'active':
            return False
        changed = False
        for obj in mission.get('objectives', []):
            if obj.get('completed') or obj.get('type') != 'bring_item':
                continue
            item_id  = obj['params'].get('item_id', '')
            required = int(obj['params'].get('count', 1))
            # Inventory entries can be plain id strings or {'id': ...} dicts
            have = sum(
                1 for item in player_inventory
                if (isinstance(item, str) and item == item_id) or
                   (isinstance(item, dict) and item.get('id') == item_id)
            )
            if have >= required:
                obj['progress']  = required
                obj['completed'] = True
                changed = True
        if changed:
            self.save()
        return self._all_objectives_done(mission)

    # =========================================================================
    # Event hooks  (called from game.py)
    # =========================================================================

    def on_enemy_killed(self, enemy_id: str, room_name: str):
        changed = False
        for mission in self.missions.values():
            if mission.get('state') != 'active':
                continue
            for obj in mission.get('objectives', []):
                if obj.get('completed') or obj.get('type') != 'kill':
                    continue
                p = obj['params']
                if p.get('enemy_id') and p['enemy_id'] != enemy_id:
                    continue
                if p.get('room') and p['room'] != room_name:
                    continue
                obj['progress'] = obj.get('progress', 0) + 1
                if obj['progress'] >= int(p.get('count', 1)):
                    obj['completed'] = True
                changed = True
        if changed:
            self.save()

    def on_room_entered(self, room_name: str):
        changed = False
        for mission in self.missions.values():
            if mission.get('state') != 'active':
                continue
            for obj in mission.get('objectives', []):
                if obj.get('completed') or obj.get('type') != 'reach_room':
                    continue
                if obj['params'].get('room_name') == room_name:
                    obj['completed'] = True
                    changed = True
        if changed:
            self.save()

    def on_item_acquired(self, item_id: str, count: int = 1):
        changed = False
        for mission in self.missions.values():
            if mission.get('state') != 'active':
                continue
            for obj in mission.get('objectives', []):
                if obj.get('completed') or obj.get('type') != 'collect_item':
                    continue
                if obj['params'].get('item_id') != item_id:
                    continue
                obj['progress'] = obj.get('progress', 0) + count
                if obj['progress'] >= int(obj['params'].get('count', 1)):
                    obj['completed'] = True
                changed = True
        if changed:
            self.save()

    def on_npc_talked(self, npc_instance_id: str):
        changed = False
        for mission in self.missions.values():
            if mission.get('state') != 'active':
                continue
            for obj in mission.get('objectives', []):
                if obj.get('completed') or obj.get('type') != 'talk_to_npc':
                    continue
                if obj['params'].get('npc_instance_id') == npc_instance_id:
                    obj['completed'] = True
                    changed = True
        if changed:
            self.save()

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
    def make_objective(obj_type: str) -> dict:
        """Create a fresh objective dict of the given type."""
        import uuid
        params = dict(OBJECTIVE_DEFAULTS.get(obj_type, {}))
        return {
            'id':          str(uuid.uuid4())[:8],
            'type':        obj_type,
            'description': '',
            'params':      params,
            'progress':    0,
            'required':    int(params.get('count', 1)),
            'completed':   False,
        }

    @staticmethod
    def objective_summary(obj: dict) -> str:
        """Short human-readable label shown in the editor and HUD."""
        t = obj.get('type', '')
        p = obj.get('params', {})
        if t == 'kill':
            who  = p.get('enemy_id') or 'any enemy'
            room = f" in {p['room']}" if p.get('room') else ''
            return f"Kill {p.get('count', 1)}x {who}{room}"
        if t == 'reach_room':
            return f"Travel to {p.get('room_name', '?')}"
        if t == 'bring_item':
            return f"Bring {p.get('count', 1)}x {p.get('item_id', '?')}"
        if t == 'collect_item':
            return f"Collect {p.get('count', 1)}x {p.get('item_id', '?')}"
        if t == 'talk_to_npc':
            return f"Talk to NPC ({p.get('npc_instance_id', '?')})"
        return t

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