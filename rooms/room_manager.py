from .room import Room
from .room_persistence import RoomManagerWithPersistence


class RoomManager(RoomManagerWithPersistence):
    """
    RoomManager now inherits from RoomManagerWithPersistence
    This gives it automatic save/load capabilities

    IMPORTANT: We override __init__ to call the parent's __init__
    """

    def __init__(self):
        # Call parent class __init__ to set up persistence
        super().__init__()

        # Parent already initialized these, but we can override if needed:
        # self.rooms = []
        # self.groups = ["Default"]
        # self.current_room = None
        # self.persistence = RoomPersistence()

        print("✓ RoomManager initialized with persistence enabled")

    # All other methods are inherited from RoomManagerWithPersistence
    # But we can override them if we need custom behavior

    # The parent class already has these methods:
    # - create_room(name, width, height, group)
    # - delete_room(room)
    # - save_room(room)
    # - save_all_rooms()
    # - load_room(room_name)
    # - get_rooms_in_group(group)
    # - get_room_names()
    # - get_room_by_name(room_name)
    # - create_group(group_name)
    # - delete_group(group_name)