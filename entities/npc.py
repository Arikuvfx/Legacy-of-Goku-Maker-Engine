import pygame
import random
from config.settings import RENDER_SCALE
from core.draw_layers import DrawLayer


class NPC:
    def __init__(self, x, y, dialogue_config=None, facing_direction='down', npc_type='static'):
        """Create an NPC at world position (*x*, *y*).

        Args:
            x, y: Starting world coordinates.
            dialogue_config: Optional dict controlling dialogue behaviour.
                Keys: 'dialogues' (list of strings), 'trigger_limit' (-1 for
                unlimited), 'triggers_used', 'after_limit_text',
                'random_order', 'give_item', 'item_given'.
            facing_direction: Initial facing direction ('down', 'up', 'left',
                'right').  Defaults to 'down'.
            npc_type: 'static' or 'moving'.  Defaults to 'static'.
        """
        self.x = x
        self.y = y
        self.width = 32
        self.height = 32
        self.speed = 1.5
        self.active = True
        self.npc_type = npc_type

        # Movement for moving NPCs
        self.idle_timer = 2.0  # start with a wait so NPCs don't all move at once
        self.idle_wait_time = 2.0  # seconds between moves
        self.idle_move_timer = 0
        self.idle_move_duration = 1.5  # how long to move
        self.idle_direction = None
        self.spawn_x = x
        self.spawn_y = y
        self.max_idle_distance = 200  # must be larger than speed * duration * 60 = 135
        self.is_moving = False
        self.move_velocity_x = 0
        self.move_velocity_y = 0

        # Dialogue system
        self.dialogue_config = dialogue_config or {
            'dialogues': ["Hello, traveler!"],
            'trigger_limit': -1,  # -1 = unlimited
            'triggers_used': 0,
            'after_limit_text': "I have nothing more to say.",
            'random_order': False,
            'give_item': None,
            'item_given': False
        }
        self.current_dialogue_index = 0
        self.is_talking = False
        self.facing_direction = facing_direction  # set from constructor, not hardcoded

        # True once the sprite animation has been synced to facing_direction.
        # Stays False until the first update() so we can sync even if the
        # sprite is assigned after construction (as game._spawn_room_entities does).
        self._sprite_facing_synced = False

        # Interaction
        self.interaction_range = 50

        self.draw_layer = DrawLayer.NPCS
        self.y_sort = True
        self.shadow_size = 'small'
        # Ground shadow px width — same field/units as Player.shadow_width and
        # BossEnemy.shadow_width (character_creator.py's Shadow Size slider,
        # 8-96 step 4). Defaults to this NPC's own width until overridden by
        # its entity_creator config (see game._spawn_room_entities), matching
        # LayerManager._draw_shadow()'s getattr(obj, 'shadow_width', obj.width)
        # fallback for entities that never get configured.
        self.shadow_width = self.width
        self.sprite     = None   # set by game._spawn_room_entities if asset found
        self.has_sprite = False
        self.variant    = 'default'
        self.instance_id = ''    # set from entity data; used by MissionManager

    def get_sort_key(self):
        """Return draw-layer sort key using feet position for correct depth ordering."""
        return (self.draw_layer, self.y + self.height // 2)

    def distance_to(self, x, y):
        """Return Euclidean distance from this NPC's centre to (*x*, *y*)."""
        dx = self.x - x
        dy = self.y - y
        return (dx * dx + dy * dy) ** 0.5

    def update(self, dt, player, world_width, world_height):
        """Advance NPC state: face a talking player, or wander if moving type.

        Args:
            dt: Seconds elapsed since last frame.
            player: The player object (used for facing direction and distance).
            world_width, world_height: Room bounds for movement clamping.
        """
        if not self.active:
            return

        if self.has_sprite and self.sprite:
            self.sprite.update(dt)

        # On the very first update after the sprite is assigned, sync its
        # animation to the configured facing direction.  This handles the case
        # where the sprite is set externally (after __init__) and would
        # otherwise always start in its default idle-down pose.
        if not self._sprite_facing_synced and self.has_sprite and self.sprite:
            self.sprite.set_animation('idle', self.facing_direction)
            self._sprite_facing_synced = True

        # Check if player is in interaction range
        player_distance = self.distance_to(player.x, player.y)

        # If talking, face the player and don't move
        if self.is_talking:
            dx = player.x - self.x
            dy = player.y - self.y
            new_dir = self.facing_direction
            if abs(dx) > abs(dy):
                new_dir = 'right' if dx > 0 else 'left'
            else:
                new_dir = 'down' if dy > 0 else 'up'
            if new_dir != self.facing_direction:
                self.facing_direction = new_dir
                if self.has_sprite and self.sprite:
                    self.sprite.set_animation('idle', self.facing_direction)
            return

        # Static NPCs always hold their configured facing direction
        if self.npc_type == 'static':
            return

        # Moving NPC behavior
        if self.npc_type == 'moving':
            self.idle_behavior(dt, world_width, world_height)

    def idle_behavior(self, dt, world_width, world_height):
        if self.is_moving:
            self.idle_move_timer -= dt

            self.x += self.move_velocity_x * dt
            self.y += self.move_velocity_y * dt

            self.x = max(0, min(self.x, world_width))
            self.y = max(0, min(self.y, world_height))

            new_dir = self.facing_direction
            if abs(self.move_velocity_x) > abs(self.move_velocity_y):
                new_dir = 'right' if self.move_velocity_x > 0 else 'left'
            else:
                new_dir = 'down' if self.move_velocity_y > 0 else 'up'

            if new_dir != self.facing_direction:
                self.facing_direction = new_dir
                if self.has_sprite and self.sprite:
                    self.sprite.set_animation('walk', self.facing_direction)

            if self.idle_move_timer <= 0:
                self.is_moving = False
                self.move_velocity_x = 0
                self.move_velocity_y = 0
                self.idle_timer = self.idle_wait_time
                if self.has_sprite and self.sprite:
                    self.sprite.set_animation('idle', self.facing_direction)
        else:
            self.idle_timer -= dt

            if self.idle_timer <= 0:
                directions = ['up', 'down', 'left', 'right']
                valid_directions = []
                for direction in directions:
                    move_distance = self.speed * self.idle_move_duration * 60
                    test_x = self.x
                    test_y = self.y

                    if direction == 'up':    test_y -= move_distance
                    elif direction == 'down':  test_y += move_distance
                    elif direction == 'left':  test_x -= move_distance
                    elif direction == 'right': test_x += move_distance

                    if (self.distance_to_spawn(test_x, test_y) < self.max_idle_distance and
                            0 < test_x < world_width and 0 < test_y < world_height):
                        valid_directions.append(direction)

                if valid_directions:
                    self.idle_direction = random.choice(valid_directions)

                    if self.idle_direction == 'up':
                        self.move_velocity_x = 0;  self.move_velocity_y = -self.speed * 40
                    elif self.idle_direction == 'down':
                        self.move_velocity_x = 0;  self.move_velocity_y = self.speed * 40
                    elif self.idle_direction == 'left':
                        self.move_velocity_x = -self.speed * 40;  self.move_velocity_y = 0
                    elif self.idle_direction == 'right':
                        self.move_velocity_x = self.speed * 40;   self.move_velocity_y = 0

                    self.is_moving = True
                    self.idle_move_timer = self.idle_move_duration
                    self.facing_direction = self.idle_direction
                    if self.has_sprite and self.sprite:
                        self.sprite.set_animation('walk', self.facing_direction)
                else:
                    self.idle_timer = self.idle_wait_time

    def distance_to_spawn(self, x, y):
        dx = x - self.spawn_x
        dy = y - self.spawn_y
        return (dx * dx + dy * dy) ** 0.5

    def can_interact(self, player):
        distance = self.distance_to(player.x, player.y)
        return distance < self.interaction_range

    def start_dialogue(self):
        """Start or continue dialogue"""
        config = self.dialogue_config

        # Check if trigger limit reached
        if config['trigger_limit'] != -1 and config['triggers_used'] >= config['trigger_limit']:
            return config['after_limit_text'], True, None  # text, is_final, item

        # First interaction
        if self.current_dialogue_index == 0 and not self.is_talking:
            self.is_talking = True
            config['triggers_used'] += 1

        # Get dialogues
        dialogues = config['dialogues']

        # Random order
        if config['random_order'] and self.current_dialogue_index == 0:
            dialogues = dialogues.copy()
            random.shuffle(dialogues)
            config['_shuffled_dialogues'] = dialogues
        elif config['random_order']:
            dialogues = config.get('_shuffled_dialogues', dialogues)

        # Get current dialogue
        if self.current_dialogue_index < len(dialogues):
            text = dialogues[self.current_dialogue_index]
            self.current_dialogue_index += 1

            # Check if this is the last dialogue
            if self.current_dialogue_index >= len(dialogues):
                is_final = True
                item = config['give_item'] if not config['item_given'] else None
                if item:
                    config['item_given'] = True
            else:
                is_final = False
                item = None

            return text, is_final, item

        return None, True, None

    def end_dialogue(self):
        """Reset dialogue state"""
        self.is_talking = False
        self.current_dialogue_index = 0
        if '_shuffled_dialogues' in self.dialogue_config:
            del self.dialogue_config['_shuffled_dialogues']

    def draw(self, screen, camera, colors):
        """Draw the NPC body, facing indicator, and speech-bubble when talking."""
        if not self.active:
            return

        screen_x = (self.x * RENDER_SCALE) - camera.x
        screen_y = (self.y * RENDER_SCALE) - camera.y
        scaled_width  = self.width  * RENDER_SCALE
        scaled_height = self.height * RENDER_SCALE

        if self.has_sprite and self.sprite:
            self.sprite.draw(screen, self.x, self.y, camera, RENDER_SCALE, 0.0)
        else:
            npc_color = (100, 200, 100) if self.npc_type == 'moving' else (50, 150, 200)
            if self.is_talking:
                npc_color = (200, 200, 50)
            pygame.draw.rect(screen, npc_color,
                             (screen_x - scaled_width // 2, screen_y - scaled_height // 2,
                              scaled_width, scaled_height))