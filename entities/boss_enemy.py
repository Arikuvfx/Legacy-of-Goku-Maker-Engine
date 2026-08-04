import pygame
from entities.enemy import Enemy
from core.sprite_system import create_boss_sprite


# ---------------------------------------------------------------------------
# Boss registry
# Maps boss 'id' strings (from the entity editor) to their config dicts.
# Add new bosses here — no other file needs to change.
# ---------------------------------------------------------------------------
BOSS_REGISTRY = {
    'pui_pui': {
        'hp':            200,
        'speed':         1.4,
        'attack_damage': 18,
        'attack_range':  17,
        'width':         64,
        'height':        64,
        'hitbox_width':  20,
        'hitbox_height': 38,
        'ai_type':       'advanced',
        'enemy_category': 'melee',
        'display_name':  'Pui Pui',
        'shadow_size':   'small',
        'shadow_width':  32,
        'shadow_y_offset': 13.5,
        'zeni_pool':     'tier3',
    },
    'android_17': {
        'hp':            350,
        'speed':         1.6,
        'attack_damage': 14,
        'attack_range':  200,
        'width':         32,
        'height':        32,
        'hitbox_width':  18,
        'hitbox_height': 28,
        'ai_type':       'advanced',
        'enemy_category': 'shooter',
        'shooter_style': 'kiblast',
        'projectile_sprite': 'kiblast',
        'display_name':  'Android 17',
        'shadow_size':   'small',
        'shadow_width':  24,
        'shadow_y_offset': 10.0,
        'zeni_pool':     'tier4',
    },
    'android_18': {
        'hp':            350,
        'speed':         1.6,
        'attack_damage': 14,
        'attack_range':  200,
        'width':         32,
        'height':        32,
        'hitbox_width':  18,
        'hitbox_height': 28,
        'ai_type':       'advanced',
        'enemy_category': 'shooter',
        'shooter_style': 'kiblast',
        'projectile_sprite': 'kiblast',
        'display_name':  'Android 18',
        'shadow_size':   'small',
        'shadow_width':  24,
        'shadow_y_offset': 10.0,
        'zeni_pool':     'tier4',
    },
}


class BossEnemy(Enemy):
    """
    Boss enemy subclass.

    Bosses share all AI logic with Enemy (easy / advanced melee / shooter)
    but are created via BossEnemy(x, y, boss_id='pui_pui') and get their
    stats from BOSS_REGISTRY instead of the Enemy defaults.

    Usage (game.py)::

        from entities.boss_enemy import BossEnemy
        boss = BossEnemy(x, y, boss_id='pui_pui')
        self.enemies.append(boss)
    """

    def __init__(self, x, y, boss_id='pui_pui', variant='default'):
        cfg = BOSS_REGISTRY.get(boss_id, BOSS_REGISTRY['pui_pui'])

        # Initialise the base Enemy with boss AI settings.
        # The sprite key re-uses the boss_id so sprite sheets can be added
        # later without touching this file.
        super().__init__(
            x, y,
            enemy_type=boss_id,
            variant=variant,
            ai_type=cfg['ai_type'],
            enemy_category=cfg['enemy_category'],
            shooter_style=cfg.get('shooter_style', 'bomb'),
            zeni_pool=cfg.get('zeni_pool', 'tier3'),
        )

        # FIX: override projectile_sprite after init so the base AI uses the
        # correct sprite (e.g. 'kiblast') instead of whatever it defaulted to.
        if cfg.get('projectile_sprite'):
            self.projectile_sprite = cfg['projectile_sprite']

        self.boss_id      = boss_id
        self.display_name = cfg['display_name']

        self.hp           = cfg['hp']
        self.max_hp       = cfg['hp']
        self.speed        = cfg['speed']

        # width/height drive the hitbox — use the actual visible character
        # size, not the full 64×64 spritesheet frame.
        self.width        = cfg['hitbox_width']
        self.height       = cfg['hitbox_height']

        # full frame size — needed for slicing the spritesheet correctly
        self._frame_width  = cfg['width']
        self._frame_height = cfg['height']

        # overwrite whatever Enemy.__init__ defaulted to
        self.attack_damage = cfg['attack_damage']
        self.attack_range  = cfg['attack_range']

        # bosses spot the player from further away than regular enemies
        self.awareness_range = 280
        self.forget_range    = 450

        # Mark as boss so game systems can react (XP, death events, etc.)
        self.is_boss = True
        self.shadow_size     = cfg.get('shadow_size', 'small')
        self.shadow_width    = cfg.get('shadow_width', self.width)
        self.shadow_y_offset = cfg.get('shadow_y_offset', 0)

        # Pending ki blast — mirrors the player's pending_blast pattern.
        # Set to True when the kiblast animation starts; the base Enemy AI
        # reads 'ready' to know it should actually spawn the projectile.
        self.pending_blast = None

        # Load boss-specific sprite from assets/sprites/enemies/boss/{boss_id}/
        # Pass full frame size so the sheet is sliced correctly.
        boss_sprite = create_boss_sprite(boss_id, variant, cfg['width'], cfg['height'])
        if boss_sprite is not None:
            self.sprite = boss_sprite
            self.has_sprite = True

    def get_sort_key(self):
        """Sort by visual feet position using the full sprite frame height.

        self.height is the hitbox height (e.g. 38), but the rendered sprite
        is _frame_height tall (e.g. 64). Using the frame height here ensures
        the player only draws in front of the boss once their feet are actually
        below the bottom of the boss sprite, not just below its hitbox.
        """
        return (self.draw_layer, self.y + self._frame_height // 2)

    def get_collision_rect(self):
        return pygame.Rect(
            self.x - self.width  // 2,
            self.y - self.height // 2,
            self.width,
            self.height,
        )

    def check_collision_with_obstacles(self, new_x, new_y):
        temp_rect = pygame.Rect(
            new_x - self.width  // 2,
            new_y - self.height // 2,
            self.width,
            self.height,
        )
        for obstacle in self.obstacles:
            if hasattr(obstacle, 'get_collision_rect'):
                if temp_rect.colliderect(obstacle.get_collision_rect()):
                    return True
            elif hasattr(obstacle, 'x') and hasattr(obstacle, 'width'):
                obs_rect = pygame.Rect(
                    obstacle.x - obstacle.width  // 2,
                    obstacle.y - obstacle.height // 2,
                    obstacle.width,
                    obstacle.height,
                )
                if temp_rect.colliderect(obs_rect):
                    return True
        return False

    def get_xp_reward(self, game_config):
        return getattr(game_config, 'boss_enemy_xp', game_config.basic_enemy_xp * 5)

    # ------------------------------------------------------------------
    # Ki-blast animation gate — mirrors the player's pending_blast pattern.
    # Called by the base Enemy AI instead of spawning a projectile directly
    # when shooter_style == 'kiblast'.
    # ------------------------------------------------------------------

    def shoot_blast(self):
        """Start the kiblast animation; the projectile spawns once it finishes."""
        if self.has_sprite and self.sprite:
            self.sprite.set_animation('kiblast', self.direction)
        self.pending_blast = True

    def update(self, dt, *args, **kwargs):
        """Tick base Enemy logic, then advance the kiblast animation gate."""
        super().update(dt, *args, **kwargs)

        # Only bosses that use the kiblast shooter style need this gate.
        if self.pending_blast is None:
            return

        # The base Enemy update() resets the sprite to 'idle' when the attack
        # timer expires — so is_animation_finished() on the kiblast anim can
        # never return True here.  Instead, fire as soon as the attack window
        # has closed (is_attacking just became False in super().update()).
        if self.pending_blast is True and not self.is_attacking:
            self.pending_blast = None
            self.should_spawn_kiblast = True