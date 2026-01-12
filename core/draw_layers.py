"""
Draw Layer System for Legacy of Goku Style Engine

This system manages the rendering order of all game objects.
Layer 0 is the player base layer. Negative layers draw behind the player,
positive layers draw in front of the player.

Layer Guidelines:
    -100 to -50: Ground tiles, floor decorations
    -49 to -1: Background objects, shadows
    0: Player (base layer)
    1 to 49: Same-level objects (NPCs, enemies, items)
    50 to 99: Foreground objects (trees, buildings)
    100+: Effects and UI overlays
"""

import pygame
from typing import List, Tuple, Callable


class DrawLayer:
    """Defines standard layer constants"""
    # Background layers (behind player)
    GROUND = -100
    FLOOR_DECORATIONS = -75
    SHADOWS = -50
    BACKGROUND_OBJECTS = -25

    # Base layer
    PLAYER = 0

    # Mid layers (same level as player)
    NPCS = 0  # NPCs at same layer as player
    ENEMIES = 0  # Enemies at same layer as player
    ITEMS = 0

    # Foreground layers (in front of player based on Y position)
    EFFECTS_BEHIND = -1  # Effects that should be behind player when player is below them
    EFFECTS_FRONT = 50  # Effects that always draw in front

    # Top layers
    FOREGROUND_OBJECTS = 75
    PARTICLES = 100
    UI_OVERLAY = 200


class DrawableObject:
    """Base class for objects that can be drawn with layer support"""

    def __init__(self, layer: int = 0):
        self.draw_layer = layer
        self.y_sort = False  # If True, uses Y position for depth sorting

    def get_sort_key(self) -> Tuple[int, float]:
        """
        Returns a tuple for sorting: (layer, y_position or 0)
        Objects are sorted by layer first, then by Y position if y_sort is enabled
        """
        y_pos = self.y if self.y_sort and hasattr(self, 'y') else 0
        return (self.draw_layer, y_pos)


class LayerManager:
    """Manages all drawable objects and renders them in correct order"""

    def __init__(self):
        self.drawable_objects: List[DrawableObject] = []
        self.debug_mode = False

    def add_object(self, obj: DrawableObject):
        """Add an object to the render queue"""
        if obj not in self.drawable_objects:
            self.drawable_objects.append(obj)

    def remove_object(self, obj: DrawableObject):
        """Remove an object from the render queue"""
        if obj in self.drawable_objects:
            self.drawable_objects.remove(obj)

    def clear(self):
        """Clear all objects from render queue"""
        self.drawable_objects.clear()

    def draw_all(self, screen: pygame.Surface, camera, colors: dict):
        """
        Draw all objects in correct layer order

        Objects are sorted by:
        1. Layer (ascending: -100 to 200+)
        2. Y position (ascending: top to bottom) if y_sort enabled
        """
        # Sort objects by layer and Y position
        sorted_objects = sorted(self.drawable_objects, key=lambda obj: obj.get_sort_key())

        # Draw each object
        for obj in sorted_objects:
            if hasattr(obj, 'active') and not obj.active:
                continue

            if hasattr(obj, 'draw'):
                obj.draw(screen, camera, colors)

        # Debug visualization
        if self.debug_mode:
            self._draw_debug_info(screen, sorted_objects)

    def _draw_debug_info(self, screen: pygame.Surface, sorted_objects: List[DrawableObject]):
        """Draw debug information about layers"""
        font = pygame.font.Font(None, 20)
        y_offset = 10

        # Show layer count
        layer_counts = {}
        for obj in sorted_objects:
            layer = obj.draw_layer
            layer_counts[layer] = layer_counts.get(layer, 0) + 1

        text = font.render(f"Layers active: {len(layer_counts)}", True, (255, 255, 255))
        screen.blit(text, (10, y_offset))
        y_offset += 20

        for layer, count in sorted(layer_counts.items()):
            text = font.render(f"Layer {layer}: {count} objects", True, (255, 255, 0))
            screen.blit(text, (10, y_offset))
            y_offset += 18


class LayeredDrawMixin:
    """
    Mixin class to add layer support to existing game objects

    Usage:
        class Player(LayeredDrawMixin):
            def __init__(self, x, y):
                LayeredDrawMixin.__init__(self, layer=DrawLayer.PLAYER)
                self.x = x
                self.y = y
                # ... rest of init
    """

    def __init__(self, layer: int = 0, y_sort: bool = False):
        self.draw_layer = layer
        self.y_sort = y_sort

    def get_sort_key(self) -> Tuple[int, float]:
        """Returns sorting key for layer manager"""
        y_pos = self.y if self.y_sort and hasattr(self, 'y') else 0
        return (self.draw_layer, y_pos)

    def set_layer(self, layer: int):
        """Change the draw layer of this object"""
        self.draw_layer = layer


# Utility functions for dynamic layer assignment

def get_beam_layer(beam_direction: str, player_direction: str) -> int:
    """
    Determine beam draw layer based on direction

    For downward beams, they should draw in front of player
    For upward beams, they should draw behind player
    Side beams at same level as player
    """
    if beam_direction == 'down':
        return DrawLayer.EFFECTS_FRONT  # Draw in front when shooting down
    elif beam_direction == 'up':
        return DrawLayer.EFFECTS_BEHIND  # Draw behind when shooting up
    else:
        return DrawLayer.PLAYER  # Same layer for side attacks


def get_dynamic_layer_for_object(obj_y: float, reference_y: float,
                                 base_layer: int = 0) -> int:
    """
    Calculate dynamic layer based on Y position relative to reference

    Objects below reference point draw in front
    Objects above reference point draw behind
    """
    if obj_y > reference_y:
        return base_layer + 10  # In front
    elif obj_y < reference_y:
        return base_layer - 10  # Behind
    else:
        return base_layer  # Same level


def apply_depth_sorting(objects: List, reference_y: float, base_layer: int = 0):
    """
    Apply Y-based depth sorting to a list of objects
    Updates their draw layers based on Y position
    """
    for obj in objects:
        if hasattr(obj, 'y') and hasattr(obj, 'draw_layer'):
            obj.draw_layer = get_dynamic_layer_for_object(obj.y, reference_y, base_layer)


# Example integration helper
class LayerIntegrationHelper:
    """Helper class for integrating layer system into existing game"""

    @staticmethod
    def setup_player(player):
        """Add layer support to player"""
        player.draw_layer = DrawLayer.PLAYER
        player.y_sort = False
        player.get_sort_key = lambda: (player.draw_layer, 0)

    @staticmethod
    def setup_npc(npc):
        """Add layer support to NPC with Y sorting"""
        npc.draw_layer = DrawLayer.NPCS
        npc.y_sort = True
        npc.get_sort_key = lambda: (npc.draw_layer, npc.y)

    @staticmethod
    def setup_enemy(enemy):
        """Add layer support to enemy with Y sorting"""
        enemy.draw_layer = DrawLayer.ENEMIES
        enemy.y_sort = True
        enemy.get_sort_key = lambda: (enemy.draw_layer, enemy.y)

    @staticmethod
    def setup_beam(beam, direction):
        """Add layer support to beam based on direction"""
        beam.draw_layer = get_beam_layer(direction, direction)
        beam.y_sort = False
        beam.get_sort_key = lambda: (beam.draw_layer, 0)

    @staticmethod
    def setup_projectile(projectile):
        """Add layer support to projectile"""
        projectile.draw_layer = DrawLayer.EFFECTS_FRONT
        projectile.y_sort = False
        projectile.get_sort_key = lambda: (projectile.draw_layer, 0)

    @staticmethod
    def setup_melee(melee):
        """Add layer support to melee attack"""
        melee.draw_layer = DrawLayer.EFFECTS_FRONT
        melee.y_sort = False
        melee.get_sort_key = lambda: (melee.draw_layer, 0)