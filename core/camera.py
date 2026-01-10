class Camera:
    def __init__(self, screen_width, screen_height):
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.x = 0
        self.y = 0
    
    def update(self, target, world_width, world_height):
        # Center camera on target
        self.x = target.x - self.screen_width // 2
        self.y = target.y - (self.screen_height - 100) // 2
        
        # Keep camera within world bounds
        self.x = max(0, min(self.x, world_width - self.screen_width))
        self.y = max(0, min(self.y, world_height - (self.screen_height - 100)))
