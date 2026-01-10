class RoomManager:
    """Manages all rooms and groups"""
    def __init__(self):
        self.rooms = []
        self.groups = ["Default"]
        self.current_room = None
        
    def create_room(self, name, width, height, group):
        room = Room(name, width, height, group)
        self.rooms.append(room)
        return room
    
    def delete_room(self, room):
        if room in self.rooms:
            self.rooms.remove(room)
            if self.current_room == room:
                self.current_room = None
    
    def get_rooms_in_group(self, group):
        return [r for r in self.rooms if r.group == group]
    
    def create_group(self, group_name):
        if group_name not in self.groups:
            self.groups.append(group_name)
    
    def delete_group(self, group_name):
        if group_name != "Default" and group_name in self.groups:
            # Move rooms to Default group
            for room in self.rooms:
                if room.group == group_name:
                    room.group = "Default"
            self.groups.remove(group_name)
