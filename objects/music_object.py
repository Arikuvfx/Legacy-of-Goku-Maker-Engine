"""
music_object.py

Defines the invisible "Music" system object. Placing one in a room tells
the game which track from assets/audio/music/ should play while the
player is in that room. It has no in-game sprite and is never drawn
during actual gameplay — it only shows an icon inside the room editor so
it can be found, selected, and deleted like any other placed object.

Only one Music object should exist per room (a room can only have one
active track at a time), so MusicObjectManager enforces a single entry
per room the same way SpawnObjectManager enforces a single spawn point.
"""

import os

import pygame

# Must mirror AudioAssetLoader.MUSIC_EXTENSIONS in sound_engine.py — that's
# what actually gets loaded into SoundEngine.music_tracks at boot, and .it
# files won't play unless they're recognized in both places.
MUSIC_EXTENSIONS = ('.ogg', '.mp3', '.wav', '.it', '.xm', '.s3m', '.mod')
MUSIC_FOLDER = os.path.join('assets', 'audio', 'music')


def get_available_music_tracks(music_folder=MUSIC_FOLDER):
    """Return a sorted list of track NAMES (stem, no extension) found in
    assets/audio/music.

    SoundEngine.load_music() registers tracks keyed by filename stem
    (os.path.splitext(filename)[0]), and SoundEngine.play_music() is
    called with that same stem — so the dropdown offers stems, and
    MusicObject.track stores that stem directly, ready to hand straight
    to play_music() with no extra conversion at call time.
    """
    try:
        return sorted({
            os.path.splitext(f)[0]
            for f in os.listdir(music_folder)
            if f.lower().endswith(MUSIC_EXTENSIONS)
        })
    except FileNotFoundError:
        return []


class MusicObject:
    """Invisible marker object that sets a room's background music track."""

    def __init__(self, x, y, track=""):
        """
        Args:
            x, y: World coordinates (only used for editor placement/hit-testing).
            track: Track name/key as registered in SoundEngine.music_tracks —
                   i.e. the filename stem with no extension (e.g. 'battle_theme'
                   for assets/audio/music/battle_theme.it). Empty string means
                   "not set yet".
        """
        self.x = x
        self.y = y
        self.track = track
        self.active = True

        # Hit-test size for the editor only — never used for gameplay collision.
        self.width = 24
        self.height = 24

        # Never affects gameplay draw order; layer only matters for the
        # editor's own overlay rendering.
        from core.draw_layers import DrawLayer
        self.draw_layer = DrawLayer.GROUND
        self.y_sort = False

    def get_sort_key(self):
        return (self.draw_layer, 0)

    def to_dict(self):
        return {'x': self.x, 'y': self.y, 'track': self.track}

    @classmethod
    def from_dict(cls, d):
        return cls(x=d['x'], y=d['y'], track=d.get('track', ''))

    def draw(self, screen, camera, colors=None, render_scale=None):
        """Editor-only icon. Never called during actual gameplay/testing — the
        object is intentionally invisible in-game.

        Drawn with the exact same code as the palette icon in
        ObjectEditor._setup_categories, at native 16x16, then scaled as a
        whole surface with pygame.transform.scale — same convention the game
        uses everywhere else to match RENDER_SCALE (see game.py's
        _draw_landing_sprite).
        """
        from config.settings import RENDER_SCALE
        scale = render_scale or RENDER_SCALE

        screen_x = (self.x * scale) - camera.x
        screen_y = (self.y * scale) - camera.y

        icon_surf = pygame.Surface((16, 16), pygame.SRCALPHA)
        icon_surf.fill((80, 220, 180, 100))
        pygame.draw.rect(icon_surf, (80, 220, 180), (0, 0, 16, 16), 2)
        pygame.draw.circle(icon_surf, (80, 220, 180), (5, 12), 3)
        pygame.draw.line(icon_surf, (80, 220, 180), (8, 12), (8, 3), 2)
        pygame.draw.line(icon_surf, (80, 220, 180), (8, 3), (13, 5), 2)

        size = max(1, int(16 * scale))
        icon_surf = pygame.transform.scale(icon_surf, (size, size))

        screen.blit(icon_surf, (int(screen_x - size / 2), int(screen_y - size / 2)))


class MusicObjectManager:
    """Manages the (at most one) Music object per room."""

    def __init__(self):
        self.music_objects = {}  # room_name -> list of MusicObject (kept as list for consistency; length <= 1)

    def add_music_object(self, room_name, music_object):
        """Add a Music object to a room, replacing any existing one."""
        self.music_objects[room_name] = [music_object]

    def remove_music_object(self, room_name, music_object=None):
        """Remove the Music object from a room."""
        if room_name in self.music_objects:
            self.music_objects[room_name] = []

    def get_music_objects(self, room_name):
        """Get all Music objects for a room (list of 0 or 1)."""
        return self.music_objects.get(room_name, [])

    def get_music_object(self, room_name):
        """Get the single Music object for a room, or None."""
        objs = self.get_music_objects(room_name)
        return objs[0] if objs else None

    def has_music_object(self, room_name):
        return bool(self.get_music_objects(room_name))

    def clear_room(self, room_name):
        if room_name in self.music_objects:
            self.music_objects[room_name] = []