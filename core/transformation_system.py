import pygame
import math


class TransformationSystem:
    """
    Manages the transformation bar and transformation state
    """

    def __init__(self, player, game_config):
        self.player = player
        self.game_config = game_config

        # Current transformation progress (0.0 to 1.0)
        self.progress = 0.0

        # Is transformation available?
        self.is_ready = False

        # Shine effect when ready
        self.shine_timer = 0.0
        self.shine_duration = 1.0
        self.is_shining = False

        # Has the player been notified?
        self.ready_notification_shown = False

        # Transformation state
        self.is_transformed = False
        self.is_transforming = False  # Currently playing transform animation
        self.is_untransforming = False  # Currently playing untransform animation

        # Transformation animation progress (0.0 to 1.0)
        self.transform_animation_progress = 0.0
        self.transform_animation_duration = 1.0  # Duration in seconds (adjust to match your animation)

        # Transformed Ki (used while transformed)
        self.transformed_ki = 100.0
        self.max_transformed_ki = 100.0
        self.transformed_ki_drain_rate = 10.0  # Ki per second while transformed

        # Original sprite info (to restore after transformation)
        self.original_character = None
        self.original_costume = None

        # Sprite folder for the active transformation (resolved from character config)
        self.current_transform_costume = None

    def update(self, dt, enemies_defeated_this_frame=0):
        """
        Update transformation progress

        Args:
            dt: Delta time
            enemies_defeated_this_frame: Number of enemies defeated in this frame
        """
        # Update transformation animation progress
        if self.is_transforming:
            self.transform_animation_progress += dt / self.transform_animation_duration
            self.transform_animation_progress = min(1.0, self.transform_animation_progress)
            # Don't fill regular progress during transformation
            return

        # Handle transformation state first
        if self.is_transformed:
            # Drain transformed ki over time
            self.transformed_ki -= self.transformed_ki_drain_rate * dt

            # Check if transformation should end
            if self.transformed_ki <= 0:
                self.transformed_ki = 0
                self.start_untransform()

            # Don't fill progress bar while transformed
            return

        # Handle shine effect when ready
        if self.is_ready:
            if self.is_shining:
                self.shine_timer += dt
                if self.shine_timer >= self.shine_duration:
                    # Loop the shine effect
                    self.shine_timer = 0.0
            else:
                self.is_shining = True
            return

        # Fill based on configured mode
        if self.game_config.transformation_fill_mode == 'time':
            # Fill over time
            fill_rate = 1.0 / self.game_config.transformation_fill_time
            self.progress += fill_rate * dt

        elif self.game_config.transformation_fill_mode == 'combat':
            # Fill through defeating enemies
            if enemies_defeated_this_frame > 0:
                points_per_enemy = self.game_config.transformation_points_per_enemy
                progress_gain = enemies_defeated_this_frame * points_per_enemy
                self.progress += progress_gain

        # Clamp progress
        if self.progress >= 1.0:
            self.progress = 1.0
            self.is_ready = True
            self.is_shining = True
            self.shine_timer = 0.0

    def get_shine_alpha(self):
        """Get the alpha value for the shine effect (0-255)"""
        if not self.is_shining:
            return 0

        # Pulse effect: fade in and out continuously
        normalized_time = self.shine_timer / self.shine_duration

        # Create a smooth pulse using sine wave (continuous loop)
        pulse = math.sin(normalized_time * math.pi * 2)  # Full sine wave cycle

        # Map to 0-255 range with minimum brightness
        alpha = int(abs(pulse) * 150) + 105  # Range from 105 to 255
        return alpha

    def start_transform(self):
        """
        Called when player initiates transformation (X key)
        Returns True if transformation animation started
        """
        if self.is_ready and not self.is_transformed and not self.is_transforming:
            character = getattr(self.player.sprite, 'character', 'goku')

            # Resolve the sprite folder for this transformation from the character
            # config BEFORE committing to any state change. This replaces the old
            # hardcoded 'ssj' costume string — SSJ is a transformation, not a
            # costume, and its folder is defined in the character's JSON under
            # "transformations[n].costume", scoped to whichever costume is
            # currently worn.
            transform_costume = self._resolve_transform_costume(character)
            if not transform_costume:
                # The active costume has no transformation registered — nothing
                # to transform into. Bail out here rather than starting the
                # transform animation and having nowhere to land (leaves the
                # player stuck on a placeholder sprite).
                return False

            self.is_transforming = True
            self.is_ready = False
            self.transform_animation_progress = 0.0  # Reset animation progress

            # Store original sprite info
            if not self.original_character:
                self.original_character = character
                self.original_costume = getattr(self.player.sprite, 'costume', 'base')

            self.current_transform_costume = transform_costume

            # Set transformation animation
            self.player.sprite.set_animation('transform', self.player.direction)
            self.player.current_animation_state = 'transform'

            return True
        return False

    def complete_transform(self):
        """
        Called when transformation animation finishes
        Switches to transformed sprite sheets
        """
        if self.is_transforming:
            self.is_transforming = False
            self.is_transformed = True
            self.transformed_ki = self.max_transformed_ki
            self.transform_animation_progress = 1.0  # Ensure it's at 100%

            # Switch to transformed sprite sheets.
            # Use the folder resolved in start_transform (e.g. "ssj", "ssj2") —
            # NOT a hardcoded string, because the transformation form is defined
            # in assets/characters/{id}.json, not hard-wired here.
            from core.sprite_system import create_character_sprite
            character = self.original_character or getattr(self.player.sprite, 'character', 'goku')
            transform_costume = self.current_transform_costume or 'ssj'  # 'ssj' only as last-resort fallback

            self.player.sprite = create_character_sprite(character, transform_costume, 32, 32)
            self.player.sprite.set_animation('idle', self.player.direction)
            self.player.current_animation_state = 'idle'

            print(f"Transformation complete! Transformed Ki: {self.transformed_ki}")

    def start_untransform(self):
        """
        Called when transformed Ki reaches 0
        Starts untransform animation
        """
        if self.is_transformed and not self.is_untransforming:
            self.is_untransforming = True
            self.is_transformed = False

            # Cancel any in-progress attack. If a melee/blast was started in the
            # same frame that ki hits 0, start_untransform() will overwrite
            # current_animation_state before the melee branch can clear
            # is_attacking — leaving the player permanently frozen. Clearing it
            # here (and again in complete_untransform as a safety net) prevents that.
            self.player.is_attacking = False
            self.player.pending_blast = None

            # Set untransform animation
            self.player.sprite.set_animation('untransform', self.player.direction)
            self.player.current_animation_state = 'untransform'

    def complete_untransform(self):
        """
        Called when untransform animation finishes
        Reverts to base sprite sheets
        """
        if self.is_untransforming:
            self.is_untransforming = False

            # Safety net: ensure is_attacking is cleared even if start_untransform
            # interrupted a melee/blast mid-animation (would otherwise freeze movement).
            self.player.is_attacking = False
            self.player.pending_blast = None

            # Restore original sprite sheets
            from core.sprite_system import create_character_sprite
            character = self.original_character or 'goku'
            costume = self.original_costume or 'base'

            self.player.sprite = create_character_sprite(character, costume, 32, 32)
            self.player.sprite.set_animation('idle', self.player.direction)
            self.player.current_animation_state = 'idle'

            # Reset transformation progress
            self.progress = 0.0
            self.is_ready = False
            self.is_shining = False
            self.shine_timer = 0.0
            self.transform_animation_progress = 0.0

            print("Reverted to base form")

    def can_player_act(self):
        """Check if player can perform actions during transformation states"""
        return not (self.is_transforming or self.is_untransforming)

    def reset(self):
        """Reset transformation progress"""
        self.progress = 0.0
        self.is_ready = False
        self.is_shining = False
        self.shine_timer = 0.0
        self.ready_notification_shown = False
        self.is_transformed = False
        self.is_transforming = False
        self.is_untransforming = False
        self.transformed_ki = self.max_transformed_ki
        self.transform_animation_progress = 0.0
        self.current_transform_costume = None

    def _resolve_transform_costume(self, char_id: str):
        """Return the costume path for the player's active transformation.

        Sourced from assets/characters/{char_id}.json — the same config
        character_creator.py writes via sync_transformations() — rather than
        re-scanning the sprite folders directly. This matters now that:
          1. Transformation sprites live nested under the base costume
             (assets/sprites/player/{char_id}/{base_costume}/transformations/{form}/),
             not directly under the character folder.
          2. A character can have more than one registered transformation
             (SSJ, SSJ2, ...), so "just list the folder and take an entry"
             no longer reliably picks the right one — os.listdir() sorts
             alphabetically, which has nothing to do with which
             transformation the creator tool actually registered first or
             which one the game intends to use.

        Currently always resolves to the FIRST entry in cfg["transformations"]
        (authoring order, not alphabetical) — there's no in-game selection
        between multiple forms yet. If/when the game adds a way to pick
        between SSJ/SSJ2/etc, this is the place to index into the list
        instead of always taking [0].
        """
        from dev_tools import character_creator

        cfg = character_creator.load_config(char_id)

        # Scope to the costume this character is CURRENTLY configured to
        # wear — the same cfg["costume"] field game.py's has_transformation
        # check uses to decide whether "transform" is even offered as a ki
        # mode. Previously this took transformations[0] unconditionally,
        # so a character with a transformation on one costume but not
        # another would resolve to whichever costume's transformation
        # happened to be registered first — not necessarily the one
        # actually being worn — pointing the sprite loader at frames that
        # don't exist for the active costume (the stuck purple placeholder
        # cube / freeze).
        active_costume = cfg.get("costume") or "base"
        prefix = f"{active_costume}/transformations/"

        transformations = cfg.get("transformations", [])
        for t in transformations:
            costume = t.get("costume")
            if costume and costume.startswith(prefix):
                return costume

        # Fallback: no transformation registered in the config for the
        # active costume (e.g. an old project whose sprites were never run
        # through sync_transformations()). Scan the filesystem directly as
        # a last resort, scoped the same way — this mirrors
        # discover_transformations()'s nested path.
        import os
        transforms_dir = f"assets/sprites/player/{char_id}/{active_costume}/transformations"
        if os.path.isdir(transforms_dir):
            entries = sorted(
                e for e in os.listdir(transforms_dir)
                if os.path.isdir(os.path.join(transforms_dir, e))
                and not e.startswith(".")
            )
            if entries:
                return f"{active_costume}/transformations/{entries[0]}"

        # Nothing found for the active costume — return None rather than
        # guessing a hardcoded folder name that may not exist for this
        # character. start_transform() treats this as "nothing to
        # transform into" and aborts instead of starting an animation with
        # nowhere to land.
        return None

    def add_progress(self, amount):
        """Manually add progress (for special events, items, etc)"""
        if not self.is_ready and not self.is_transformed:
            self.progress = min(1.0, self.progress + amount)