class Room:
    def __init__(self, name, width, height, group="Default"):
        self.name = name
        self.width = width
        self.height = height
        self.group = group


        # Initialize all data containers
        self.tiles = []
        self.collision_objects = []
        self.destructible_stones = []
        self.room_transitions = []
        self.spawn_point = []
