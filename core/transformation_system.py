import pygame


class TransformationSystem:
    """
    Manages the transformation bar that fills either over time or through combat
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
        self.shine_duration = 1.0  # How long the shine effect lasts
        self.is_shining = False

        # Has the player been notified?
        self.ready_notification_shown = False

    def update(self, dt, enemies_defeated_this_frame=0):
        """
        Update transformation progress

        Args:
            dt: Delta time
            enemies_defeated_this_frame: Number of enemies defeated in this frame
        """
        # Don't fill if already ready
        if self.is_ready:
            # Update shine effect
            if self.is_shining:
                self.shine_timer += dt
                if self.shine_timer >= self.shine_duration:
                    self.is_shining = False
                    self.shine_timer = 0.0
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

        # Pulse effect: fade in and out
        normalized_time = self.shine_timer / self.shine_duration

        # Create a smooth pulse using sine wave
        import math
        pulse = math.sin(normalized_time * math.pi * 4)  # 4 pulses during shine

        # Map to 0-255 range
        alpha = int(abs(pulse) * 200) + 55
        return alpha

    def activate_transformation(self):
        """
        Called when player activates transformation
        Returns True if transformation was activated
        """
        if self.is_ready:
            self.is_ready = False
            self.progress = 0.0
            self.is_shining = False
            self.shine_timer = 0.0
            self.ready_notification_shown = False
            return True
        return False

    def reset(self):
        """Reset transformation progress"""
        self.progress = 0.0
        self.is_ready = False
        self.is_shining = False
        self.shine_timer = 0.0
        self.ready_notification_shown = False

    def add_progress(self, amount):
        """Manually add progress (for special events, items, etc)"""
        if not self.is_ready:
            self.progress = min(1.0, self.progress + amount)