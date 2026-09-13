"""
room.py

Defines the Room data class that holds all tile, object, entity, and
transition data for a single game room.  Instances are created and
managed by RoomManager and serialised/deserialised by RoomPersistence.
"""


class Room:
    """Lightweight data container for a single game room.

    All mutable lists are initialised empty and populated by the editor
    tools or loaded from disk via RoomPersistence.
    """

    def __init__(self, name, width, height, group="Default"):
        """Create a new room with the given dimensions.

        Args:
            name: Unique string identifier for the room.
            width, height: Room size in world units.
            group: Editor group name used for organisation (default 'Default').
        """
        self.name = name
        self.width = width
        self.height = height
        self.group = group

        # Initialize all data containers
        self.tiles = []
        self.collision_objects = []
        self.destructible_stones = []
        self.room_transitions = []
        self.spawn_point = []
        self.level_gates = []
        self.entities = []
        self.save_points = []
        self.cutscene_triggers = []
        self.world_map_objects = []
        self.music_objects = []
        self.animated_regions = []
        self.doors = []
        self.trigger_boxes = []
        self.decorations = []

        # Scouter minimap silhouette painted via the room editor's Map
        # Paint tool: a plain list of [gx, gy] cell pairs (see
        # objects/map_paint.py and RoomPersistence._serialize_map_paint).
        self.map_paint = []

        # Parallax/scrolling background image config, edited via the room
        # editor's Background panel: {'image', 'parallax', 'scroll_x', 'scroll_y'}.
        # Empty dict = no background image set for this room.
        self.scrolling_bg = {}

        # Ambient weather effect for this room, set via the room editor's
        # Weather field ('none', 'rain', 'snow', 'fog', 'storm').
        self.ambient_weather = 'none'

        # Room-wide music (BGM) track and BGS (ambient loop, e.g. rain/wind)
        # track, set via the room editor's 'Set Room Music' / 'Set Room BGS'
        # fields. Empty string means none set for this room.
        self.music_track = ''
        self.bgs_track = ''

        # Per-room volume multipliers (0.0-1.0), set via the room editor's
        # Music Volume / BGS Volume sliders. Applied on top of the player's
        # master music/BGS volume settings (see SoundEngine.play_music's and
        # play_bgs's volume_scale parameter) rather than replacing them, so
        # a room can be mixed quieter or louder without touching the
        # player's own volume preference. Default 1.0 (100%) so rooms that
        # predate this setting keep playing at full volume.
        self.music_volume = 1.0
        self.bgs_volume = 1.0

        # Whether the player is allowed to fight (melee/charged melee/super
        # attacks) while in this room, set via the room editor's
        # 'Can attack?' checkbox. Defaults to True so existing rooms behave
        # exactly as before.
        self.can_attack = True

        # Transient rooms are never saved to disk and hidden from the editor.
        # Used for the startup fallback room created by Game._create_default_room.
        self.is_transient = False