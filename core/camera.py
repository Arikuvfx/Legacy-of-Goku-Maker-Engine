from config.settings import SCREEN_WIDTH, SCREEN_HEIGHT, WORLD_WIDTH, WORLD_HEIGHT

class Camera:
    def __init__(self, width, height):
        self.width = width
        self.height = height
        self.x = 0
        self.y = 0
    
    def update(self, target):
        self.x = target.x - SCREEN_WIDTH // 2
        self.y = target.y - (SCREEN_HEIGHT - 100) // 2
        
        self.x = max(0, min(self.x, WORLD_WIDTH - SCREEN_WIDTH))
        self.y = max(0, min(self.y, WORLD_HEIGHT - (SCREEN_HEIGHT - 100)))
