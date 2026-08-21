import pygame
from entities.enemy import Enemy
from core.sprite_system import create_boss_sprite
from dev_tools import entity_creator


class BossEnemy(Enemy):
    """
    Boss enemy subclass.

    Bosses share all AI logic with Enemy (easy / advanced melee / shooter)
    but are created via BossEnemy(x, y, boss_id='pui_pui') and get their
    stats from assets/enemies/{boss_id}.json -- the same config file (and
    editor) a regular enemy uses, distinguished by entity_type == 'boss'.
    No boss-specific registry lives in code anymore; adding a boss is
    purely a data change via the entity editor.

    Usage (game.py)::

        from entities.boss_enemy import BossEnemy
        boss = BossEnemy(x, y, boss_id='pui_pui')
        self.enemies.append(boss)
    """

    def __init__(self, x, y, boss_id='pui_pui', variant='default'):
        cfg = entity_creator.load_config(entity_creator.KIND_ENEMY, boss_id)
        stats = cfg['stats']

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

        self.boss_id       = boss_id
        self.display_name  = cfg.get('display_name') or boss_id.replace('_', ' ').title()

        self.hp             = stats['max_hp']
        self.max_hp         = stats['max_hp']
        self.speed          = stats['speed']
        self.defense        = stats['defense']   # END

        # Hitbox -- the actual visible/collidable character size, independent
        # of the full spritesheet frame (see below).
        self.width          = cfg['hitbox_width']
        self.height         = cfg['hitbox_height']

        # full frame size -- needed for slicing the spritesheet correctly
        self._frame_width   = cfg['width']
        self._frame_height  = cfg['height']

        # Raw STR/POW — stored on the entity even though only one of them
        # actually drives attack_damage below, so UI that shows both stats
        # at once (see ui/scouter_menu.py's _get_data_stats) has real
        # configured values to read instead of Enemy.__init__'s hardcoded
        # fallback.
        self.strength       = stats['strength']
        self.power          = stats['power']

        # overwrite whatever Enemy.__init__ defaulted to — melee bosses hit
        # with STR, shooter bosses (bomb/bullet/rocket/kiblast) hit with POW,
        # same STR/POW split entity_creator.py's stats block uses now.
        self.attack_damage  = (self.strength if cfg['enemy_category'] == 'melee'
                                else self.power)
        self.attack_range   = cfg['attack_range']

        # bosses spot the player from further away than regular enemies --
        # configurable per-boss in the entity editor rather than hardcoded.
        self.awareness_range = cfg['awareness_range']
        self.forget_range    = cfg['forget_range']

        # Mark as boss so game systems can react (XP, death events, etc.)
        self.is_boss = True
        self.shadow_size     = cfg.get('shadow_size', 'small')
        self.shadow_width    = cfg.get('shadow_width', self.width)
        self.shadow_y_offset = cfg.get('shadow_y_offset', 0)

        # Pending ki blast -- mirrors the player's pending_blast pattern.
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