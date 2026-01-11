import pygame
import os


class SoundEngine:
    """Handles all game audio - music and sound effects"""

    def __init__(self):
        # Initialize pygame mixer
        pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=512)

        # Music settings
        self.current_music = None
        self.music_volume = 0.7
        self.is_music_playing = False

        # Sound effects
        self.sfx_volume = 0.8
        self.sound_effects = {}

        # Music tracks
        self.music_tracks = {}

        # Audio state
        self.music_enabled = True
        self.sfx_enabled = True

        # Fade settings
        self.fade_duration = 1000  # milliseconds

    def load_music(self, name, filepath):
        """Load a music track"""
        if os.path.exists(filepath):
            self.music_tracks[name] = filepath
            return True
        else:
            print(f"Warning: Music file not found: {filepath}")
            return False

    def load_sound_effect(self, name, filepath):
        """Load a sound effect"""
        if os.path.exists(filepath):
            try:
                sound = pygame.mixer.Sound(filepath)
                sound.set_volume(self.sfx_volume)
                self.sound_effects[name] = sound
                return True
            except:
                print(f"Warning: Could not load sound effect: {filepath}")
                return False
        else:
            print(f"Warning: Sound effect file not found: {filepath}")
            return False

    def play_music(self, name, loops=-1, fade_in=True):
        """
        Play a music track
        loops: -1 for infinite loop, 0 for play once, n for play n+1 times
        fade_in: whether to fade in the music
        """
        if not self.music_enabled:
            return

        if name not in self.music_tracks:
            print(f"Warning: Music track '{name}' not loaded")
            return

        # Don't restart if already playing
        if self.current_music == name and self.is_music_playing:
            return

        try:
            pygame.mixer.music.load(self.music_tracks[name])
            pygame.mixer.music.set_volume(self.music_volume)

            if fade_in:
                pygame.mixer.music.play(loops, fade_ms=self.fade_duration)
            else:
                pygame.mixer.music.play(loops)

            self.current_music = name
            self.is_music_playing = True
        except:
            print(f"Error: Could not play music track: {name}")

    def stop_music(self, fade_out=True):
        """Stop the current music"""
        if fade_out:
            pygame.mixer.music.fadeout(self.fade_duration)
        else:
            pygame.mixer.music.stop()

        self.is_music_playing = False
        self.current_music = None

    def pause_music(self):
        """Pause the current music"""
        pygame.mixer.music.pause()
        self.is_music_playing = False

    def unpause_music(self):
        """Unpause the current music"""
        pygame.mixer.music.unpause()
        self.is_music_playing = True

    def play_sound(self, name):
        """Play a sound effect"""
        if not self.sfx_enabled:
            return

        if name in self.sound_effects:
            self.sound_effects[name].play()
        else:
            print(f"Warning: Sound effect '{name}' not loaded")

    def set_music_volume(self, volume):
        """Set music volume (0.0 to 1.0)"""
        self.music_volume = max(0.0, min(1.0, volume))
        pygame.mixer.music.set_volume(self.music_volume)

    def set_sfx_volume(self, volume):
        """Set sound effects volume (0.0 to 1.0)"""
        self.sfx_volume = max(0.0, min(1.0, volume))
        for sound in self.sound_effects.values():
            sound.set_volume(self.sfx_volume)

    def toggle_music(self):
        """Toggle music on/off"""
        self.music_enabled = not self.music_enabled
        if not self.music_enabled:
            self.stop_music(fade_out=False)
        return self.music_enabled

    def toggle_sfx(self):
        """Toggle sound effects on/off"""
        self.sfx_enabled = not self.sfx_enabled
        return self.sfx_enabled

    def cleanup(self):
        """Clean up audio resources"""
        pygame.mixer.music.stop()
        for sound in self.sound_effects.values():
            sound.stop()
        pygame.mixer.quit()


class SoundManager:
    """
    High-level sound manager that handles music context
    (exploration, battle, boss, etc.)
    """

    def __init__(self, sound_engine):
        self.sound_engine = sound_engine

        # Music contexts
        self.current_context = None
        self.previous_context = None

        # Context to music mapping
        self.context_music = {
            'exploration': 'exploration_theme',
            'battle': 'battle_theme',
            'boss': 'boss_theme',
            'safe_zone': 'safe_zone_theme',
            'tension': 'tension_theme',
            'victory': 'victory_theme'
        }

        # Battle state
        self.in_battle = False
        self.battle_music_timer = 0
        self.battle_music_delay = 2.0  # Seconds before battle music starts

    def set_context(self, context, force=False):
        """
        Change music context
        force: play immediately even if same context
        """
        if context == self.current_context and not force:
            return

        self.previous_context = self.current_context
        self.current_context = context

        if context in self.context_music:
            music_name = self.context_music[context]
            self.sound_engine.play_music(music_name)

    def update_battle_state(self, dt, has_enemies):
        """Update battle music based on enemy presence"""
        if has_enemies and not self.in_battle:
            # Enemies appeared
            self.battle_music_timer += dt
            if self.battle_music_timer >= self.battle_music_delay:
                self.in_battle = True
                self.set_context('battle')
                self.battle_music_timer = 0
        elif not has_enemies and self.in_battle:
            # All enemies defeated
            self.in_battle = False
            self.battle_music_timer = 0
            # Return to exploration music
            self.set_context('exploration')

    def play_sfx(self, sfx_name):
        """Play a sound effect"""
        self.sound_engine.play_sound(sfx_name)

    def reset_battle_timer(self):
        """Reset the battle music timer"""
        self.battle_music_timer = 0


# Audio asset loader helper
class AudioAssetLoader:
    """Helper class to load audio assets from directory structure"""

    @staticmethod
    def load_from_directory(sound_engine, base_path='assets/audio'):
        """
        Load audio files from standard directory structure:
        assets/audio/
            music/
                exploration.ogg
                battle.ogg
                boss.ogg
            sfx/
                blast.wav
                hit.wav
                etc.
        """
        music_path = os.path.join(base_path, 'music')
        sfx_path = os.path.join(base_path, 'sfx')

        # Load music tracks
        if os.path.exists(music_path):
            for filename in os.listdir(music_path):
                if filename.endswith(('.ogg', '.mp3', '.wav')):
                    name = os.path.splitext(filename)[0]
                    filepath = os.path.join(music_path, filename)
                    sound_engine.load_music(name, filepath)

        # Load sound effects
        if os.path.exists(sfx_path):
            for filename in os.listdir(sfx_path):
                if filename.endswith('.wav'):
                    name = os.path.splitext(filename)[0]
                    filepath = os.path.join(sfx_path, filename)
                    sound_engine.load_sound_effect(name, filepath)