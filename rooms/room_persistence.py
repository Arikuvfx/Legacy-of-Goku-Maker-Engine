import json
import os
from pathlib import Path


class RoomPersistence:
    """Handles reading and writing room data to JSON files on disk."""

    def __init__(self, save_directory="data/rooms"):
        self.save_directory = save_directory
        Path(self.save_directory).mkdir(parents=True, exist_ok=True)

    def _get_room_filepath(self, room_name):
        safe = "".join(c for c in room_name if c.isalnum() or c in (' ', '_', '-')).strip()
        return os.path.join(self.save_directory, f"{safe.replace(' ', '_')}.json")

    # ── Save / load ───────────────────────────────────────────────────────────

    def save_room(self, room):
        try:
            data = {
                'name':                 room.name,
                'width':                room.width,
                'height':               room.height,
                'group':                room.group,
                'spawn_points':         self._serialize_spawn_points(room),
                'tiles':                self._serialize_tiles(room),
                'collision_objects':    self._serialize_collision_objects(room),
                'destructible_stones':  self._serialize_destructible_stones(room),
                'room_transitions':     self._serialize_room_transitions(room),
                'level_gates':          self._serialize_level_gates(room),
                'entities':             self._serialize_entities(room),
            }
            with open(self._get_room_filepath(room.name), 'w') as f:
                json.dump(data, f, indent=2)
            return True
        except Exception as e:
            print(f"Error saving room {room.name}: {e}")
            return False

    def load_room(self, room_name):
        try:
            path = self._get_room_filepath(room_name)
            if not os.path.exists(path):
                return None
            with open(path, 'r') as f:
                return json.load(f)
        except Exception:
            return None

    def get_all_saved_rooms(self):
        try:
            return [f[:-5].replace('_', ' ')
                    for f in os.listdir(self.save_directory)
                    if f.endswith('.json')]
        except Exception:
            return []

    def delete_room_file(self, room_name):
        try:
            path = self._get_room_filepath(room_name)
            if os.path.exists(path):
                os.remove(path)
                return True
            return False
        except Exception:
            return False

    # ── Serializers ───────────────────────────────────────────────────────────

    def _serialize_tiles(self, room):
        if not getattr(room, 'tiles', None):
            return []
        return [{'x': t.x, 'y': t.y, 'tileset_name': t.tileset_name,
                 'tile_x': t.tile_x, 'tile_y': t.tile_y, 'layer': t.layer}
                for t in room.tiles]

    def _serialize_collision_objects(self, room):
        if not getattr(room, 'collision_objects', None):
            return []
        return [{'x': o.x, 'y': o.y, 'width': o.width, 'height': o.height,
                 'collision_type': getattr(o, 'collision_type', 'wall')}
                for o in room.collision_objects]

    def _serialize_level_gates(self, room):
        if not getattr(room, 'level_gates', None):
            return []
        return [{'x': g.x, 'y': g.y, 'gate_type': g.gate_type,
                 'required_level': g.required_level, 'max_health': g.max_health,
                 'health': g.health, 'width': g.width, 'height': g.height,
                 'active': g.active, 'is_unlocked': getattr(g, 'is_unlocked', False)}
                for g in room.level_gates]

    def _serialize_spawn_points(self, room):
        """Handles both the modern SpawnObject list and the old (x, y) tuple format."""
        out = []
        if getattr(room, 'spawn_points', None):
            for sp in room.spawn_points:
                if hasattr(sp, 'x'):
                    out.append({'x': sp.x, 'y': sp.y,
                                'width': getattr(sp, 'width', 32),
                                'height': getattr(sp, 'height', 32),
                                'room_name': getattr(sp, 'room_name', room.name)})
        elif getattr(room, 'spawn_point', None):
            sp = room.spawn_point
            if isinstance(sp, tuple) and len(sp) == 2:
                out.append({'x': sp[0], 'y': sp[1], 'width': 32, 'height': 32,
                            'room_name': room.name})
        return out

    def _serialize_destructible_stones(self, room):
        if not getattr(room, 'destructible_stones', None):
            return []
        return [{'x': s.x, 'y': s.y, 'stone_type': s.stone_type,
                 'max_health': s.max_health, 'health': s.health,
                 'width': s.width, 'height': s.height}
                for s in room.destructible_stones]

    def _serialize_room_transitions(self, room):
        if not getattr(room, 'room_transitions', None):
            return []
        return [{'x': t.x, 'y': t.y, 'width': t.width, 'height': t.height,
                 'target_room': t.target_room, 'exit_direction': t.exit_direction,
                 'entry_direction': t.entry_direction,
                 'spawn_x': t.spawn_x, 'spawn_y': t.spawn_y}
                for t in room.room_transitions]

    def _serialize_entities(self, room):
        if not getattr(room, 'entities', None):
            return []
        out = []
        for ent in room.entities:
            d = {
                'id':          ent.get('id', 'unknown'),
                'name':        ent.get('name', 'Unknown Entity'),
                'entity_type': ent.get('entity_type', 'enemy'),
                'x':           ent.get('x', 0),
                'y':           ent.get('y', 0),
                'width':       ent.get('width', 32),
                'height':      ent.get('height', 32),
            }
            # Optional fields — only include when present
            for key in ('variant_type', 'variant_name', 'ai_type', 'enemy_category'):
                if ent.get(key):
                    d[key] = ent[key]
            if ent.get('variant_color'):
                d['variant_color'] = list(ent['variant_color'])  # tuple → list for JSON

            if ent.get('entity_type') == 'npc':
                for key in ('instance_id', 'npc_mode', 'npc_facing'):
                    if ent.get(key):
                        d[key] = ent[key]
                for key in ('dialogue_config', 'mission'):
                    if ent.get(key) is not None:
                        d[key] = ent[key]

            for key in ('hitbox_height', 'shadow_width', 'shadow_y_offset'):
                if ent.get(key) is not None:
                    d[key] = ent[key]

            out.append(d)
        return out

    # ── Deserializers ─────────────────────────────────────────────────────────

    def deserialize_tiles(self, tiles_data):
        from dev_tools.room_editor.room_editor_tools.tileset_editor import Tile
        return [Tile(x=t['x'], y=t['y'], tileset_name=t['tileset_name'],
                     tile_x=t['tile_x'], tile_y=t['tile_y'], layer=t['layer'])
                for t in tiles_data]

    def deserialize_collision_objects(self, collision_data):
        from objects.collision_object import CollisionObject
        out = []
        for d in collision_data:
            obj = CollisionObject(x=d['x'], y=d['y'], width=d['width'], height=d['height'])
            if 'collision_type' in d:
                obj.collision_type = d['collision_type']
            out.append(obj)
        return out

    def deserialize_level_gates(self, gates_data):
        from objects.level_gate import LevelGate
        out = []
        for d in gates_data:
            gate = LevelGate(x=d['x'], y=d['y'], gate_type=d['gate_type'],
                             required_level=d.get('required_level', 1))
            if 'max_health' in d:
                gate.max_health = d['max_health']
                gate.health     = d.get('health', d['max_health'])
            if 'is_unlocked' in d:
                gate.is_unlocked = d['is_unlocked']
            out.append(gate)
        return out

    def deserialize_spawn_points(self, spawn_data):
        from objects.spawn_object import SpawnObject
        out = []
        for d in spawn_data:
            sp = SpawnObject(x=d['x'], y=d['y'], room_name=d.get('room_name', ''))
            sp.width  = d.get('width',  32)
            sp.height = d.get('height', 32)
            out.append(sp)
        return out

    def deserialize_destructible_stones(self, stones_data):
        from objects.destructible_stone import DestructibleStone
        out = []
        for d in stones_data:
            stone = DestructibleStone(x=d['x'], y=d['y'], stone_type=d['stone_type'])
            if 'max_health' in d:
                stone.max_health = d['max_health']
                stone.health     = d.get('health', d['max_health'])
            out.append(stone)
        return out

    def deserialize_room_transitions(self, transitions_data):
        from objects.room_transition import RoomTransition
        out = []
        for d in transitions_data:
            t = RoomTransition(x=d['x'], y=d['y'], width=d['width'], height=d['height'])
            t.target_room     = d['target_room']
            t.exit_direction  = d['exit_direction']
            t.entry_direction = d['entry_direction']
            t.spawn_x         = d['spawn_x']
            t.spawn_y         = d['spawn_y']
            out.append(t)
        return out

    def deserialize_entities(self, entities_data):
        """
        Returns raw dicts, not live instances. The game creates actual
        Enemy/NPC objects from these when loading the room.

        Boss entries are refreshed against BOSS_REGISTRY so old saves
        automatically pick up geometry changes without a re-place.
        """
        if not entities_data:
            return []

        from entities.boss_enemy import BOSS_REGISTRY
        out = []
        for ent in entities_data:
            if ent.get('entity_type') == 'boss':
                cfg = BOSS_REGISTRY.get(ent.get('id', ''), {})
                if cfg:
                    ent = dict(ent)
                    ent['hitbox_height']   = cfg.get('hitbox_height',   cfg.get('height', ent.get('height', 32)))
                    ent['shadow_width']    = cfg.get('shadow_width',    32)
                    ent['shadow_y_offset'] = cfg.get('shadow_y_offset', 0)
            out.append(ent)
        return out


class RoomManagerWithPersistence:
    """Room manager that auto-saves and auto-loads from disk."""

    def __init__(self):
        self.rooms        = []
        self.groups       = ["Default"]
        self.current_room = None
        self.persistence  = RoomPersistence()
        self._load_all_saved_rooms()

    def _load_all_saved_rooms(self):
        for name in self.persistence.get_all_saved_rooms():
            self.load_room(name)

    def create_room(self, name, width, height, group):
        from rooms.room import Room
        room = Room(name, width, height, group)
        self.rooms.append(room)
        if group not in self.groups:
            self.groups.append(group)
        self.persistence.save_room(room)
        return room

    def create_transient_room(self, name, width, height, group="Default"):
        """
        Creates a room that never gets saved and is hidden from the editor.
        Used as a startup fallback so there's always an active room to work with.
        """
        from rooms.room import Room
        room = Room(name, width, height, group)
        room.is_transient = True
        self.rooms.append(room)
        return room

    def save_room(self, room):
        return self.persistence.save_room(room)

    def save_all_rooms(self):
        return sum(
            1 for r in self.rooms
            if not getattr(r, 'is_transient', False) and self.persistence.save_room(r)
        )

    def load_room(self, room_name, spawn_manager=None):
        data = self.persistence.load_room(room_name)
        if not data:
            return None

        from rooms.room import Room
        room = Room(name=data['name'], width=data['width'],
                    height=data['height'], group=data['group'])

        # Spawn points
        if data.get('spawn_points'):
            room.spawn_points = self.persistence.deserialize_spawn_points(data['spawn_points'])
            if spawn_manager and room.spawn_points:
                for sp in room.spawn_points:
                    spawn_manager.spawn_points[room.name] = sp
            # Keep legacy attribute around for any code that still references it
            if room.spawn_points:
                room.spawn_point = (room.spawn_points[0].x, room.spawn_points[0].y)
        else:
            room.spawn_points = []
            room.spawn_point  = None

        room.tiles               = self.persistence.deserialize_tiles(data['tiles'])               if data.get('tiles')               else []
        room.collision_objects   = self.persistence.deserialize_collision_objects(data['collision_objects'])   if data.get('collision_objects')   else []
        room.destructible_stones = self.persistence.deserialize_destructible_stones(data['destructible_stones']) if data.get('destructible_stones') else []
        room.room_transitions    = self.persistence.deserialize_room_transitions(data['room_transitions'])    if data.get('room_transitions')    else []
        room.level_gates         = self.persistence.deserialize_level_gates(data['level_gates'])         if data.get('level_gates')         else []
        room.entities            = self.persistence.deserialize_entities(data['entities'])            if data.get('entities')            else []

        existing = self.get_room_by_name(room_name)
        if existing:
            self.rooms[self.rooms.index(existing)] = room
        else:
            self.rooms.append(room)
            if room.group not in self.groups:
                self.groups.append(room.group)

        return room

    def delete_room(self, room):
        if room in self.rooms:
            self.persistence.delete_room_file(room.name)
            self.rooms.remove(room)
            if self.current_room == room:
                self.current_room = None

    def get_rooms_in_group(self, group):
        return [r for r in self.rooms
                if r.group == group and not getattr(r, 'is_transient', False)]

    def get_room_names(self):
        return [r.name for r in self.rooms]

    def get_room_by_name(self, room_name):
        return next((r for r in self.rooms if r.name == room_name), None)

    def create_group(self, group_name):
        if group_name not in self.groups:
            self.groups.append(group_name)

    def delete_group(self, group_name):
        """Deletes a group and reassigns all its rooms to Default."""
        if group_name == "Default" or group_name not in self.groups:
            return
        for room in self.rooms:
            if room.group == group_name:
                room.group = "Default"
                self.persistence.save_room(room)
        self.groups.remove(group_name)