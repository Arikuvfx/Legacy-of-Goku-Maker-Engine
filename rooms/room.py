class Room:
    """Represents a game room/map"""
    def __init__(self, name, width=2400, height=1800, group="Default"):
        self.name = name
        self.width = width
        self.height = height
        self.group = group
        self.enemies = []
        self.npcs = []
        self.objects = []
        self.spawn_point = (width // 2, height // 2)
