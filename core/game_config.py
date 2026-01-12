class GameConfig:
    """Global configuration for game systems - editable via dev menu"""

    def __init__(self):
        # Level/XP System
        self.max_level = 99
        self.base_xp_requirement = 100
        self.xp_scaling_factor = 1.5  # Exponential growth

        # Stats System
        self.max_stat_value = 99
        self.stat_points_per_level = 3
        self.starting_stat_value = 1

        # Enemy XP Rewards
        self.basic_enemy_xp = 25
        self.strong_enemy_xp = 75
        self.boss_enemy_xp = 200

        #Transformatio Bar
        self.transformation_fill_time = 1.0  # Fill in 30 seconds instead of 60
        self.transformation_points_per_enemy = 0.05  # Need 20 enemies instead of 10
        self.transformation_fill_mode = 'time'  # or 'time'

    def get_xp_for_level(self, level):
        """Calculate XP required to reach next level"""
        return int(self.base_xp_requirement * (self.xp_scaling_factor ** (level - 1)))