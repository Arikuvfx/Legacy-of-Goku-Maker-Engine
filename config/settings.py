# Game configuration constants
import pygame

# Initialize Pygame for font loading
pygame.init()

# Screen constants
SCREEN_WIDTH = 800
SCREEN_HEIGHT = 600
FPS = 60
TILE_SIZE = 32

# Default world size (can be changed by rooms)
WORLD_WIDTH = 2400
WORLD_HEIGHT = 1800

# Colors
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
GREEN = (0, 255, 0)
RED = (255, 0, 0)
BLUE = (0, 100, 255)
GRAY = (50, 50, 50)
LIGHT_GRAY = (200, 200, 200)
YELLOW = (255, 255, 0)
DARK_GRAY = (30, 30, 30)
CYAN = (0, 255, 255)
ORANGE = (255, 165, 0)
PURPLE = (138, 43, 226)

# Get all colors as a dictionary for easy access
def get_colors():
    return {
        'WHITE': WHITE,
        'BLACK': BLACK,
        'GREEN': GREEN,
        'RED': RED,
        'BLUE': BLUE,
        'GRAY': GRAY,
        'LIGHT_GRAY': LIGHT_GRAY,
        'YELLOW': YELLOW,
        'DARK_GRAY': DARK_GRAY,
        'CYAN': CYAN,
        'ORANGE': ORANGE,
        'PURPLE': PURPLE
    }
