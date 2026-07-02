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
        self.sfx_volume = 0.1
        self.sound_effects = {}

        # Looping sound effects (e.g. transformation aura) — tracks the
        # Channel each looping sfx is playing on so it can be stopped later.
        self.looping_channels = {}

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
            except Exception as e:
                print(f"Warning: Could not load sound effect '{filepath}': {e}")
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
        except Exception as e:
            print(f"Error: Could not play music track '{name}': {e}")

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
        """Play a sound effect. Returns the Channel it's playing on (or None
        if it couldn't be played), so callers can poll get_busy() to know
        when a one-shot effect has finished."""
        if not self.sfx_enabled:
            return None

        if name in self.sound_effects:
            return self.sound_effects[name].play()
        else:
            print(f"Warning: Sound effect '{name}' not loaded")
            return None

    def play_looping_sound(self, name):
        """Start a sound effect looping indefinitely. No-op if it's already looping."""
        if not self.sfx_enabled:
            return

        if name not in self.sound_effects:
            print(f"Warning: Sound effect '{name}' not loaded")
            return

        channel = self.looping_channels.get(name)
        if channel is not None and channel.get_busy():
            return  # Already looping — don't restart it.

        self.looping_channels[name] = self.sound_effects[name].play(loops=-1)

    def stop_looping_sound(self, name):
        """Stop a looping sound effect started with play_looping_sound."""
        channel = self.looping_channels.get(name)
        if channel is not None:
            channel.stop()
            self.looping_channels[name] = None

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
        self.looping_channels.clear()
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
            'menu': 'dev_menu',  # Changed from 'dev_menu' to 'menu'
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
        # If trying to switch to same context without force, return
        if context == self.current_context and not force:
            return

        # Store previous context
        self.previous_context = self.current_context
        self.current_context = context

        print(f"🎵 Switching music context: {self.previous_context} -> {context}")

        # Always stop current music before switching
        self.sound_engine.stop_music(fade_out=True)

        # Small delay to ensure fade out completes
        pygame.time.delay(100)

        if context in self.context_music:
            music_name = self.context_music[context]
            print(f"🎵 Playing music: {music_name}")
            self.sound_engine.play_music(music_name, fade_in=True)
        else:
            print(f"⚠️ No music mapped for context: {context}")

    def set_context_immediate(self, context):
        """Change music context immediately without fade"""
        self.previous_context = self.current_context
        self.current_context = context

        print(f"🎵 Immediate context switch to: {context}")

        # Stop music immediately
        self.sound_engine.stop_music(fade_out=False)

        if context in self.context_music:
            music_name = self.context_music[context]
            print(f"🎵 Playing music: {music_name}")
            self.sound_engine.play_music(music_name, fade_in=False)
        else:
            print(f"⚠️ No music mapped for context: {context}")

    def update_battle_state(self, dt, has_enemies):
        """Update battle music based on enemy presence"""
        # Don't update battle state if in menu context
        if self.current_context == 'menu':
            return

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
        """Play a sound effect. Returns the Channel it's playing on (or None),
        so callers can poll get_busy() to know when it has finished."""
        return self.sound_engine.play_sound(sfx_name)

    def play_music(self, track_name, loops=-1, fade_in=True):
        """Directly play a track by name, bypassing the exploration/battle/
        boss context map entirely. Intended for room-level Music objects.

        NOTE: because this bypasses set_context(), it does NOT update
        self.current_context. That means update_battle_state() will still
        switch to the 'battle'/'boss' context map entries when enemies
        appear (as before), and when combat ends it will call
        set_context('exploration'), which resumes context_music['exploration']
        — the generic exploration theme, NOT whatever room track was playing
        via this method. If you want a room's custom track to resume after
        combat instead of the generic theme, the cleanest fix is to have the
        caller also update self.context_music['exploration'] = track_name
        when applying a room's music, and reset it to the default theme name
        when entering a room with no Music object. That's a product decision
        this method deliberately doesn't make on its own.
        """
        self.sound_engine.play_music(track_name, loops=loops, fade_in=fade_in)

    def stop_music(self, fade_out=True):
        """Stop whatever music is currently playing, regardless of how it was
        started (context map, a room's Music object, or a direct play_music
        call) — it doesn't touch self.current_context, so nothing "resumes"
        into it automatically afterwards. Callers that want a specific
        context playing again should call set_context() instead/afterwards.
        """
        self.sound_engine.stop_music(fade_out=fade_out)

    def play_looping_sfx(self, sfx_name):
        """Start a looping sound effect (e.g. transformation aura). Idempotent."""
        self.sound_engine.play_looping_sound(sfx_name)

    def stop_looping_sfx(self, sfx_name):
        """Stop a looping sound effect started with play_looping_sfx."""
        self.sound_engine.stop_looping_sound(sfx_name)

    def reset_battle_timer(self):
        """Reset the battle music timer"""
        self.battle_music_timer = 0

    def get_current_context(self):
        """Get current music context"""
        return self.current_context

    def restore_previous_context(self):
        """Restore the previous music context"""
        if self.previous_context:
            print(f"🎵 Restoring previous context: {self.previous_context}")
            self.set_context(self.previous_context, force=True)
            return True
        return False


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
                battle.it          ← tracker/module music (see note below)
                boss.ogg
                dev_menu.ogg
            sfx/
                combat/
                    punch.wav
                    enemy_hit.wav
                misc/
                    footstep_run.wav
                    menu_select.wav
                blast.wav           ← files directly in sfx/ still work too

        SFX are looked up by bare filename (no extension, no folder), same as
        before — subfolders are just for your own organization and don't
        affect play_sfx() calls. play_sfx('footstep_run') works whether the
        file lives at sfx/footstep_run.wav or sfx/misc/footstep_run.wav.
        Any folder depth works; add more categories freely.

        Tracker module music (.it / .xm / .s3m / .mod)
        ------------------------------------------------
        pygame.mixer.music plays these natively through SDL_mixer — no extra
        code needed beyond recognizing the extension here. The big win over
        .ogg/.mp3 is looping: tracker formats store their own loop/restart
        position inside the file, so play_music(name, loops=-1) loops back to
        a sample-accurate point with no seam, instead of just restarting at
        byte 0. Drop .it files in assets/audio/music/ like any other track —
        SoundEngine.play_music()/stop_music() don't need to know the format.

        Requires SDL_mixer to have been built with module support (true for
        the official pygame PyPI wheels, SDL_mixer 2.0.2+). If a .it file
        fails to load, play_music()'s except branch will print an error —
        check pygame.mixer.get_sdl_mixer_version() if that happens.
        """
        music_path = os.path.join(base_path, 'music')
        sfx_path = os.path.join(base_path, 'sfx')

        # Load music tracks — includes tracker/module formats alongside the
        # usual streamed formats; SDL_mixer auto-detects from file content.
        MUSIC_EXTENSIONS = ('.ogg', '.mp3', '.wav', '.it', '.xm', '.s3m', '.mod')
        if os.path.exists(music_path):
            for filename in os.listdir(music_path):
                if filename.lower().endswith(MUSIC_EXTENSIONS):
                    name = os.path.splitext(filename)[0]
                    filepath = os.path.join(music_path, filename)
                    sound_engine.load_music(name, filepath)
                    print(f" Loaded music: {name}")

        # Load sound effects — walks the full sfx/ tree so category subfolders
        # (combat/, misc/, etc.) are picked up, not just the top level.
        if os.path.exists(sfx_path):
            for root, _dirs, filenames in os.walk(sfx_path):
                for filename in filenames:
                    if filename.lower().endswith('.wav'):
                        name = os.path.splitext(filename)[0]
                        if name in sound_engine.sound_effects:
                            print(f"⚠️ Duplicate SFX name '{name}' — "
                                  f"{os.path.join(root, filename)} overwrites the earlier one")
                        filepath = os.path.join(root, filename)
                        sound_engine.load_sound_effect(name, filepath)
                        print(f" Loaded SFX: {name}")