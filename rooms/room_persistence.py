import json
import os
from pathlib import Path


class RoomPersistence:
    """Saves and loads room data to/from JSON files"""

    def __init__(self, save_directory="data/rooms"):
        self.save_directory = save_directory
        self._ensure_directory_exists()

    def _ensure_directory_exists(self):
        """Make sure the save directory actually exists"""
        Path(self.save_directory).mkdir(parents=True, exist_ok=True)

    def _get_room_filepath(self, room_name):
        """Turn a room name into a safe filename"""
        safe_name = "".join(c for c in room_name if c.isalnum() or c in (' ', '_', '-')).strip()
        safe_name = safe_name.replace(' ', '_')
        return os.path.join(self.save_directory, f"{safe_name}.json")

    def save_room(self, room):
        """Write room data to a JSON file"""
        try:
            room_data = {
                'name': room.name,
                'width': room.width,
                'height': room.height,
                'group': room.group,
                'spawn_points': self._serialize_spawn_points(room),
                'tiles': self._serialize_tiles(room),
                'collision_objects': self._serialize_collision_objects(room),
                'destructible_stones': self._serialize_destructible_stones(room),
                'room_transitions': self._serialize_room_transitions(room),
                'level_gates': self._serialize_level_gates(room),
                'entities': self._serialize_entities(room)  # ADD THIS LINE
            }

            filepath = self._get_room_filepath(room.name)
            with open(filepath, 'w') as f:
                json.dump(room_data, f, indent=2)

            return True

        except Exception as e:
            print(f"Error saving room {room.name}: {e}")
            return False

    def load_room(self, room_name):
        """Read room data from a JSON file"""
        try:
            filepath = self._get_room_filepath(room_name)

            if not os.path.exists(filepath):
                return None

            with open(filepath, 'r') as f:
                return json.load(f)

        except Exception:
            return None

    def get_all_saved_rooms(self):
        """Get names of all rooms that have been saved"""
        try:
            saved_rooms = []
            for filename in os.listdir(self.save_directory):
                if filename.endswith('.json'):
                    room_name = filename[:-5].replace('_', ' ')
                    saved_rooms.append(room_name)
            return saved_rooms
        except Exception:
            return []

    def delete_room_file(self, room_name):
        """Remove a room's save file from disk"""
        try:
            filepath = self._get_room_filepath(room_name)
            if os.path.exists(filepath):
                os.remove(filepath)
                return True
            return False
        except Exception:
            return False

    # Serialization helpers - convert objects to dicts

    def _serialize_tiles(self, room):
        """Convert tiles to JSON-friendly format"""
        if not hasattr(room, 'tiles') or not room.tiles:
            return []

        return [{
            'x': tile.x,
            'y': tile.y,
            'tileset_name': tile.tileset_name,
            'tile_x': tile.tile_x,
            'tile_y': tile.tile_y,
            'layer': tile.layer
        } for tile in room.tiles]

    def _serialize_collision_objects(self, room):
        """Convert collision objects to JSON-friendly format"""
        if not hasattr(room, 'collision_objects') or not room.collision_objects:
            return []

        return [{
            'x': obj.x,
            'y': obj.y,
            'width': obj.width,
            'height': obj.height,
            'collision_type': getattr(obj, 'collision_type', 'wall')
        } for obj in room.collision_objects]

    def _serialize_level_gates(self, room):
        """Convert level gates to JSON-friendly format"""
        if not hasattr(room, 'level_gates') or not room.level_gates:
            return []

        return [{
            'x': gate.x,
            'y': gate.y,
            'gate_type': gate.gate_type,
            'required_level': gate.required_level,
            'max_health': gate.max_health,
            'health': gate.health,
            'width': gate.width,
            'height': gate.height,
            'active': gate.active,
            'is_unlocked': getattr(gate, 'is_unlocked', False)  # Save unlocked state for metal gates
        } for gate in room.level_gates]

    def _serialize_spawn_points(self, room):
        """Convert spawn points to JSON-friendly format

        Handles both modern SpawnObject list and legacy tuple format
        """
        spawn_data = []

        # Try the modern format first
        if hasattr(room, 'spawn_points') and room.spawn_points:
            for spawn in room.spawn_points:
                if hasattr(spawn, 'x') and hasattr(spawn, 'y'):
                    spawn_data.append({
                        'x': spawn.x,
                        'y': spawn.y,
                        'width': getattr(spawn, 'width', 32),
                        'height': getattr(spawn, 'height', 32),
                        'room_name': getattr(spawn, 'room_name', room.name)
                    })

        # Fall back to legacy tuple format
        elif hasattr(room, 'spawn_point') and room.spawn_point:
            if isinstance(room.spawn_point, tuple) and len(room.spawn_point) == 2:
                spawn_data.append({
                    'x': room.spawn_point[0],
                    'y': room.spawn_point[1],
                    'width': 32,
                    'height': 32,
                    'room_name': room.name
                })

        return spawn_data

    def _serialize_destructible_stones(self, room):
        """Convert destructible stones to JSON-friendly format"""
        if not hasattr(room, 'destructible_stones') or not room.destructible_stones:
            return []

        return [{
            'x': stone.x,
            'y': stone.y,
            'stone_type': stone.stone_type,
            'max_health': stone.max_health,
            'health': stone.health,
            'width': stone.width,
            'height': stone.height
        } for stone in room.destructible_stones]

    def _serialize_room_transitions(self, room):
        """Convert room transitions to JSON-friendly format"""
        if not hasattr(room, 'room_transitions') or not room.room_transitions:
            return []

        return [{
            'x': transition.x,
            'y': transition.y,
            'width': transition.width,
            'height': transition.height,
            'target_room': transition.target_room,
            'exit_direction': transition.exit_direction,
            'entry_direction': transition.entry_direction,
            'spawn_x': transition.spawn_x,
            'spawn_y': transition.spawn_y
        } for transition in room.room_transitions]

    def _serialize_entities(self, room):
        """Convert entities (NPCs, Enemies, Bosses) to JSON-friendly format"""
        if not hasattr(room, 'entities') or not room.entities:
            return []

        serialized = []
        for entity in room.entities:
            # Entity data is already a dict from the entity editor
            # Just ensure we have all required fields
            entity_dict = {
                'id': entity.get('id', 'unknown'),
                'name': entity.get('name', 'Unknown Entity'),
                'entity_type': entity.get('entity_type', 'enemy'),
                'x': entity.get('x', 0),
                'y': entity.get('y', 0),
                'width': entity.get('width', 32),
                'height': entity.get('height', 32),
            }

            # Include variant information if present
            if entity.get('variant_type'):
                entity_dict['variant_type'] = entity['variant_type']
            if entity.get('variant_name'):
                entity_dict['variant_name'] = entity['variant_name']
            if entity.get('variant_color'):
                entity_dict['variant_color'] = list(entity['variant_color'])  # JSON needs list not tuple
            if entity.get('ai_type'):
                entity_dict['ai_type'] = entity['ai_type']
            if entity.get('enemy_category'):
                entity_dict['enemy_category'] = entity['enemy_category']

            serialized.append(entity_dict)

        return serialized

    # Deserialization helpers - convert dicts back to objects

    def deserialize_tiles(self, tiles_data):
        """Rebuild tile objects from JSON data"""
        from dev_tools.room_editor.room_editor_tools.tileset_editor import Tile

        return [Tile(
            x=tile_dict['x'],
            y=tile_dict['y'],
            tileset_name=tile_dict['tileset_name'],
            tile_x=tile_dict['tile_x'],
            tile_y=tile_dict['tile_y'],
            layer=tile_dict['layer']
        ) for tile_dict in tiles_data]

    def deserialize_collision_objects(self, collision_data):
        """Rebuild collision objects from JSON data"""
        from objects.collision_object import CollisionObject

        collision_objects = []
        for obj_dict in collision_data:
            obj = CollisionObject(
                x=obj_dict['x'],
                y=obj_dict['y'],
                width=obj_dict['width'],
                height=obj_dict['height']
            )
            if 'collision_type' in obj_dict:
                obj.collision_type = obj_dict['collision_type']
            collision_objects.append(obj)

        return collision_objects

    def deserialize_level_gates(self, gates_data):
        """Rebuild level gate objects from JSON data"""
        from objects.level_gate import LevelGate

        gates = []
        for gate_dict in gates_data:
            gate = LevelGate(
                x=gate_dict['x'],
                y=gate_dict['y'],
                gate_type=gate_dict['gate_type'],
                required_level=gate_dict.get('required_level', 1)
            )
            # Restore saved health values
            if 'max_health' in gate_dict:
                gate.max_health = gate_dict['max_health']
                gate.health = gate_dict.get('health', gate_dict['max_health'])
            # Restore unlocked state for metal gates
            if 'is_unlocked' in gate_dict:
                gate.is_unlocked = gate_dict['is_unlocked']
            gates.append(gate)

        return gates

    def deserialize_spawn_points(self, spawn_data):
        """Rebuild spawn point objects from JSON data"""
        from objects.spawn_object import SpawnObject

        spawn_points = []
        for spawn_dict in spawn_data:
            spawn = SpawnObject(
                x=spawn_dict['x'],
                y=spawn_dict['y'],
                room_name=spawn_dict.get('room_name', '')
            )
            # Use saved dimensions if they exist
            if 'width' in spawn_dict:
                spawn.width = spawn_dict['width']
            if 'height' in spawn_dict:
                spawn.height = spawn_dict['height']
            spawn_points.append(spawn)

        return spawn_points

    def deserialize_destructible_stones(self, stones_data):
        """Rebuild destructible stone objects from JSON data"""
        from objects.destructible_stone import DestructibleStone

        stones = []
        for stone_dict in stones_data:
            stone = DestructibleStone(
                x=stone_dict['x'],
                y=stone_dict['y'],
                stone_type=stone_dict['stone_type']
            )
            # Restore saved health values
            if 'max_health' in stone_dict:
                stone.max_health = stone_dict['max_health']
                stone.health = stone_dict.get('health', stone_dict['max_health'])
            stones.append(stone)

        return stones

    def deserialize_room_transitions(self, transitions_data):
        """Rebuild room transition objects from JSON data"""
        from objects.room_transition import RoomTransition

        transitions = []
        for trans_dict in transitions_data:
            transition = RoomTransition(
                x=trans_dict['x'],
                y=trans_dict['y'],
                width=trans_dict['width'],
                height=trans_dict['height']
            )
            transition.target_room = trans_dict['target_room']
            transition.exit_direction = trans_dict['exit_direction']
            transition.entry_direction = trans_dict['entry_direction']
            transition.spawn_x = trans_dict['spawn_x']
            transition.spawn_y = trans_dict['spawn_y']
            transitions.append(transition)

        return transitions

    def deserialize_entities(self, entities_data):
        """Rebuild entity data from JSON

        Note: This returns entity data dicts, not actual Enemy/NPC instances.
        The game is responsible for creating the actual instances from this data.
        """
        if not entities_data:
            return []

        # Entity data is already in dict format, just return it
        # This preserves all the metadata from the entity editor
        return entities_data


class RoomManagerWithPersistence:
    """Room manager that automatically saves and loads from disk"""

    def __init__(self):
        self.rooms = []
        self.groups = ["Default"]
        self.current_room = None
        self.persistence = RoomPersistence()
        self._load_all_saved_rooms()

    def _load_all_saved_rooms(self):
        """Load any previously saved rooms when starting up"""
        saved_room_names = self.persistence.get_all_saved_rooms()

        if not saved_room_names:
            return

        for room_name in saved_room_names:
            self.load_room(room_name)

    def create_room(self, name, width, height, group):
        """Make a new room and save it right away"""
        from rooms.room import Room

        room = Room(name, width, height, group)
        self.rooms.append(room)

        if group not in self.groups:
            self.groups.append(group)

        self.persistence.save_room(room)
        return room

    def create_transient_room(self, name, width, height, group="Default"):
        """Create a room that is never saved to disk and hidden from the editor.

        Used as a startup fallback so there is always an active room when no
        saved rooms exist yet.  The room will not appear in the room editor
        list and will not be written to disk on cleanup.
        """
        from rooms.room import Room

        room = Room(name, width, height, group)
        room.is_transient = True
        self.rooms.append(room)
        return room

    def save_room(self, room):
        """Save a single room to disk"""
        return self.persistence.save_room(room)

    def save_all_rooms(self):
        """Save every non-transient room we have loaded"""
        return sum(1 for room in self.rooms
                   if not getattr(room, 'is_transient', False)
                   and self.persistence.save_room(room))

    def load_room(self, room_name, spawn_manager=None):
        """Load a room from disk and sync it with the spawn manager if needed"""
        room_data = self.persistence.load_room(room_name)

        if not room_data:
            return None

        from rooms.room import Room

        # Build the basic room
        room = Room(
            name=room_data['name'],
            width=room_data['width'],
            height=room_data['height'],
            group=room_data['group']
        )

        # Restore spawn points
        if room_data.get('spawn_points'):
            room.spawn_points = self.persistence.deserialize_spawn_points(
                room_data['spawn_points']
            )

            # Sync with spawn manager if we have one
            if spawn_manager and room.spawn_points:
                for spawn in room.spawn_points:
                    spawn_manager.spawn_points[room.name] = spawn

            # Keep legacy spawn_point for backward compatibility
            if room.spawn_points:
                room.spawn_point = (room.spawn_points[0].x, room.spawn_points[0].y)
        else:
            room.spawn_points = []
            room.spawn_point = None

        # Restore tiles
        room.tiles = (self.persistence.deserialize_tiles(room_data['tiles'])
                      if room_data.get('tiles') else [])

        # Restore collision objects
        room.collision_objects = (
            self.persistence.deserialize_collision_objects(room_data['collision_objects'])
            if room_data.get('collision_objects') else []
        )

        # Restore destructible stones
        room.destructible_stones = (
            self.persistence.deserialize_destructible_stones(room_data['destructible_stones'])
            if room_data.get('destructible_stones') else []
        )

        # Restore room transitions
        room.room_transitions = (
            self.persistence.deserialize_room_transitions(room_data['room_transitions'])
            if room_data.get('room_transitions') else []
        )

        # Restore level gates
        room.level_gates = (
            self.persistence.deserialize_level_gates(room_data['level_gates'])
            if room_data.get('level_gates') else []
        )

        # Restore entities (NPCs, Enemies, Bosses)
        room.entities = (
            self.persistence.deserialize_entities(room_data['entities'])
            if room_data.get('entities') else []
        )

        # Add to our room list or update existing
        existing_room = self.get_room_by_name(room_name)
        if not existing_room:
            self.rooms.append(room)
            if room.group not in self.groups:
                self.groups.append(room.group)
        else:
            # Replace the old version with this fresh one
            idx = self.rooms.index(existing_room)
            self.rooms[idx] = room

        return room

    def delete_room(self, room):
        """Delete a room from memory and disk"""
        if room in self.rooms:
            self.persistence.delete_room_file(room.name)
            self.rooms.remove(room)

            if self.current_room == room:
                self.current_room = None

    def get_rooms_in_group(self, group):
        """Find all non-transient rooms that belong to a specific group"""
        return [r for r in self.rooms
                if r.group == group and not getattr(r, 'is_transient', False)]

    def get_room_names(self):
        """Get a list of all room names"""
        return [room.name for room in self.rooms]

    def get_room_by_name(self, room_name):
        """Look up a room by its name"""
        for room in self.rooms:
            if room.name == room_name:
                return room
        return None

    def create_group(self, group_name):
        """Add a new group if it doesn't exist yet"""
        if group_name not in self.groups:
            self.groups.append(group_name)

    def delete_group(self, group_name):
        """Remove a group and move all its rooms to Default"""
        if group_name != "Default" and group_name in self.groups:
            # Move all rooms in this group to Default
            for room in self.rooms:
                if room.group == group_name:
                    room.group = "Default"
                    self.persistence.save_room(room)

            self.groups.remove(group_name)