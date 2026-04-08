"""
Draw layer system for the Legacy of Goku engine.

Layer 0 is the player. Negative values draw behind, positive in front.

    -100 to -50  ground tiles, floor decorations
     -49 to  -1  background objects, shadows
          0      player (base)
       1 to 49   same-level objects (NPCs, enemies, items)
      50 to 99   foreground objects (trees, buildings)
         100+    effects and UI overlays
"""

import pygame
from typing import List, Tuple, Callable
from config.settings import RENDER_SCALE


class DrawLayer:
    """Standard layer constants — edit these if the draw order ever needs adjusting."""
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
    """Anything that goes through the layer manager inherits this."""

    def __init__(self, layer: int = 0):
        self.draw_layer = layer
        self.y_sort = False  # enable to depth-sort by Y position within the layer

    def get_sort_key(self) -> Tuple[int, float]:
        """(layer, y) tuple used by the layer manager to sort draw order."""
        y_pos = self.y if self.y_sort and hasattr(self, 'y') else 0
        return (self.draw_layer, y_pos)


class LayerManager:
    """Collects drawable objects each frame and blits them in the right order."""

    # Entity types that receive a ground shadow.
    _SHADOW_TYPES = ('Player', 'Enemy', 'BossEnemy', 'NPC')

    def __init__(self):
        self.drawable_objects: List[DrawableObject] = []
        self.debug_mode = False
        self._shadow_sprite = None
        self._shadow_sprite_big = None
        self._shadow_cache: dict = {}   # (width, big) -> scaled Surface
        self._load_shadow()

    def _load_shadow(self):
        """Load shadow sprites once at startup; fall back to a drawn ellipse if missing."""
        try:
            raw = pygame.image.load('assets/sprites/universal/shadow.png').convert_alpha()
            self._shadow_sprite = raw
        except Exception:
            s = pygame.Surface((32, 12), pygame.SRCALPHA)
            pygame.draw.ellipse(s, (0, 0, 0, 80), s.get_rect())
            self._shadow_sprite = s

        try:
            raw_big = pygame.image.load('assets/sprites/universal/shadowbig.png').convert_alpha()
            self._shadow_sprite_big = raw_big
        except Exception:
            s = pygame.Surface((64, 20), pygame.SRCALPHA)
            pygame.draw.ellipse(s, (0, 0, 0, 80), s.get_rect())
            self._shadow_sprite_big = s

    def _get_scaled_shadow(self, entity_width: int, big: bool = False) -> pygame.Surface:
        """Cached shadow scaled to ~60% of entity_width so it doesn't look like a plank."""
        source = self._shadow_sprite_big if big else self._shadow_sprite
        if source is None:
            return None
        key = (entity_width, big)
        if key not in self._shadow_cache:
            orig_w = source.get_width()
            orig_h = source.get_height()
            # Use 60% of the rendered sprite width so the shadow looks grounded
            # rather than as wide as the whole sprite frame.
            target_w = max(8, int(entity_width * RENDER_SCALE * 0.32))
            target_h = max(4, int(orig_h * target_w / orig_w))
            self._shadow_cache[key] = pygame.transform.scale(source, (target_w, target_h))
        return self._shadow_cache[key]

    def _draw_shadow(self, screen, obj, camera):
        """Draw a ground shadow centred under the entity's feet."""
        type_name = type(obj).__name__
        if not any(t in type_name for t in self._SHADOW_TYPES):
            return

        use_big = getattr(obj, 'shadow_size', 'small') == 'big'

        entity_height = getattr(obj, 'height', 32)
        # shadow_width can be set independently of hitbox width (e.g. on bosses)
        shadow_w = getattr(obj, 'shadow_width', getattr(obj, 'width', 32))

        shadow_surf = self._get_scaled_shadow(shadow_w, big=use_big)
        if shadow_surf is None:
            return

        feet_x = (obj.x * RENDER_SCALE) - camera.x + 0.7
        feet_y = (obj.y * RENDER_SCALE) - camera.y + (entity_height * RENDER_SCALE) // 2.25
        feet_y += getattr(obj, 'shadow_y_offset', 0)

        sx = int(feet_x - shadow_surf.get_width()  // 2)
        sy = int(feet_y - shadow_surf.get_height() // 2)
        screen.blit(shadow_surf, (sx, sy))

    def add_object(self, obj: DrawableObject):
        """Register an object for rendering this frame."""
        if obj not in self.drawable_objects:
            self.drawable_objects.append(obj)

    def remove_object(self, obj: DrawableObject):
        """Drop an object from the render queue."""
        if obj in self.drawable_objects:
            self.drawable_objects.remove(obj)

    def clear(self):
        """Wipe the render queue — call at the start of each draw pass."""
        self.drawable_objects.clear()

    def draw_all(self, screen, camera, colors, render_scale=1):
        """
        Sort everything by (layer, y) and draw.
        Layer goes -100 → 200+, y goes top → bottom when y_sort is on.
        Shadows are drawn just before the entity that casts them.
        """
        # Sort objects by layer and Y position
        sorted_objects = sorted(self.drawable_objects, key=lambda obj: obj.get_sort_key())

        # Draw each object
        for obj in sorted_objects:
            if hasattr(obj, 'active') and not obj.active:
                continue

            # Ground shadow drawn just before the entity itself
            self._draw_shadow(screen, obj, camera)

            if hasattr(obj, 'draw'):
                obj.draw(screen, camera, colors)

        # Debug visualization
        if self.debug_mode:
            self._draw_debug_info(screen, sorted_objects)

    def _draw_debug_info(self, screen: pygame.Surface, sorted_objects: List[DrawableObject]):
        """Overlay layer counts in the top-left corner when debug_mode is on."""
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
    Drop this into any class to give it layer-manager support.

    Usage:
        class Player(LayeredDrawMixin):
            def __init__(self, x, y):
                LayeredDrawMixin.__init__(self, layer=DrawLayer.PLAYER)
                ...
    """

    def __init__(self, layer: int = 0, y_sort: bool = False):
        self.draw_layer = layer
        self.y_sort = y_sort

    def get_sort_key(self) -> Tuple[int, float]:
        """(layer, y) sorting key consumed by LayerManager."""
        y_pos = self.y if self.y_sort and hasattr(self, 'y') else 0
        return (self.draw_layer, y_pos)

    def set_layer(self, layer: int):
        """Move this object to a different draw layer at runtime."""
        self.draw_layer = layer


# Utility functions for dynamic layer assignment

def get_beam_layer(beam_direction: str, player_direction: str) -> int:
    """
    Pick the right layer for a beam based on which way it's travelling.
    Down = in front of everything, up = behind player, sideways = same level.
    """
    if beam_direction == 'down':
        return DrawLayer.EFFECTS_FRONT
    elif beam_direction == 'up':
        return DrawLayer.EFFECTS_BEHIND
    else:
        return DrawLayer.PLAYER


def get_dynamic_layer_for_object(obj_y: float, reference_y: float,
                                 base_layer: int = 0) -> int:
    """
    Y-based depth: objects below the reference point draw in front, above draw behind.
    """
    if obj_y > reference_y:
        return base_layer + 10
    elif obj_y < reference_y:
        return base_layer - 10
    else:
        return base_layer


def apply_depth_sorting(objects: List, reference_y: float, base_layer: int = 0):
    """Recalculate draw layers for a list of objects based on their Y vs reference_y."""
    for obj in objects:
        if hasattr(obj, 'y') and hasattr(obj, 'draw_layer'):
            obj.draw_layer = get_dynamic_layer_for_object(obj.y, reference_y, base_layer)


class LayerIntegrationHelper:
    """Convenience wrappers for wiring up layer support on existing objects."""

    @staticmethod
    def setup_player(player):
        player.draw_layer = DrawLayer.PLAYER
        player.y_sort = False
        player.get_sort_key = lambda: (player.draw_layer, 0)

    @staticmethod
    def setup_npc(npc):
        """NPCs are Y-sorted so taller ones draw behind shorter ones."""
        npc.draw_layer = DrawLayer.NPCS
        npc.y_sort = True
        npc.get_sort_key = lambda: (npc.draw_layer, npc.y)

    @staticmethod
    def setup_enemy(enemy):
        enemy.draw_layer = DrawLayer.ENEMIES
        enemy.y_sort = True
        enemy.get_sort_key = lambda: (enemy.draw_layer, enemy.y)

    @staticmethod
    def setup_beam(beam, direction):
        beam.draw_layer = get_beam_layer(direction, direction)
        beam.y_sort = False
        beam.get_sort_key = lambda: (beam.draw_layer, 0)

    @staticmethod
    def setup_projectile(projectile):
        projectile.draw_layer = DrawLayer.EFFECTS_FRONT
        projectile.y_sort = False
        projectile.get_sort_key = lambda: (projectile.draw_layer, 0)

    @staticmethod
    def setup_melee(melee):
        melee.draw_layer = DrawLayer.EFFECTS_FRONT
        melee.y_sort = False
        melee.get_sort_key = lambda: (melee.draw_layer, 0)