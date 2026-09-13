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
        # Mirrors sound_effects' keys, but stores the filepath each Sound was
        # loaded from — needed so hot-reload (see reload_sound_effect) knows
        # where to re-read a given name from without a fresh directory scan.
        self.sound_effect_paths = {}

        # Looping sound effects (e.g. transformation aura) — tracks the
        # Channel each looping sfx is playing on so it can be stopped later.
        self.looping_channels = {}

        # BGS (background sound) — ambient loops like rain, wind, a
        # crackling fire, etc. Kept separate from sound_effects/
        # looping_channels rather than reusing play_looping_sound(), for
        # two reasons: (1) it needs its own volume knob independent of
        # sfx_volume, since ambient beds are typically mixed much quieter
        # than combat/UI sfx, and (2) it needs a single well-known "current
        # bgs" slot (mirroring current_music) so a room/context switch can
        # cleanly stop whatever ambient loop was previously playing without
        # the caller needing to know its name.
        self.bgs_volume = 0.4
        self.ambient_sounds = {}
        # Mirrors ambient_sounds' keys — see sound_effect_paths above.
        self.ambient_sound_paths = {}
        self.current_bgs = None
        self.bgs_channel = None

        # Positional BGS — independent ambient loops for world-placed
        # "Ambient Sound" objects (e.g. a waterfall, a beehive, a torch).
        # Kept separate from current_bgs/bgs_channel above: that single
        # slot only ever tracks ONE ambient loop for the whole room, so
        # two placed objects would fight over it and share one volume.
        # Each positional instance instead gets its own pygame Channel,
        # whose volume is set directly via Channel.set_volume() rather
        # than Sound.set_volume() — the latter would change the volume
        # for every channel currently playing that same Sound (e.g. two
        # "campfire" objects in the same room), while Channel.set_volume()
        # only affects that one instance, letting each object fade in/out
        # independently based on its own distance to the player.
        self.positional_bgs_channels = []

        # Music tracks
        self.music_tracks = {}

        # Audio state
        self.music_enabled = True
        self.sfx_enabled = True
        self.bgs_enabled = True

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
                self.sound_effect_paths[name] = filepath
                return True
            except Exception as e:
                print(f"Warning: Could not load sound effect '{filepath}': {e}")
                return False
        else:
            print(f"Warning: Sound effect file not found: {filepath}")
            return False

    def load_bgs(self, name, filepath):
        """Load a BGS (background sound) ambient loop, e.g. rain, wind."""
        if os.path.exists(filepath):
            try:
                sound = pygame.mixer.Sound(filepath)
                sound.set_volume(self.bgs_volume)
                self.ambient_sounds[name] = sound
                self.ambient_sound_paths[name] = filepath
                return True
            except Exception as e:
                print(f"Warning: Could not load BGS '{filepath}': {e}")
                return False
        else:
            print(f"Warning: BGS file not found: {filepath}")
            return False

    # ── Hot-reload support ───────────────────────────────────────────────
    # These let a running game pick up asset changes from disk without a
    # restart. They're driven by AudioAssetLoader.apply_watcher_events(),
    # which is fed by an AssetWatcher polling assets/audio — see
    # apply_watcher_events() below for how filepaths get routed here.

    def reload_music(self, name, filepath):
        """(Re-)register a music track's filepath. Music is loaded lazily
        by pygame.mixer.music.load() at play time, so for a track that
        isn't currently playing this is just a cheap dict update — the
        next play_music(name) call will pick up the new file automatically.

        If this track IS the one currently playing, reload it in place so
        the edit takes effect immediately instead of waiting for the next
        time it's played (e.g. tweaking a boss theme while testing the
        boss fight that's already playing it)."""
        self.music_tracks[name] = filepath
        if self.current_music == name:
            was_playing = self.is_music_playing
            try:
                pygame.mixer.music.load(filepath)
                if was_playing:
                    pygame.mixer.music.play(-1)
                    pygame.mixer.music.set_volume(self.music_volume)
                print(f"🔄 Reloaded music (live): {name}")
            except Exception as e:
                print(f"Warning: could not hot-reload playing music '{name}': {e}")
        else:
            print(f"🔄 Reloaded music: {name}")

    def reload_sound_effect(self, name, filepath):
        """Reload a single sound effect from disk, replacing it in place.
        Any copy of the old Sound already playing on a Channel keeps
        playing untouched (pygame Sounds are immutable once played) — only
        the NEXT play_sound(name) call picks up the new audio."""
        try:
            sound = pygame.mixer.Sound(filepath)
            sound.set_volume(self.sfx_volume)
            self.sound_effects[name] = sound
            self.sound_effect_paths[name] = filepath
            print(f"🔄 Reloaded SFX: {name}")
            return True
        except Exception as e:
            print(f"Warning: Could not reload sound effect '{filepath}': {e}")
            return False

    def reload_bgs(self, name, filepath):
        """Reload a single BGS ambient loop from disk, replacing it in
        place. If it's the one currently looping via play_bgs(), restart it
        immediately so the edit is audible right away; positional BGS
        instances (play_positional_bgs) already in flight keep playing the
        old audio until whatever re-triggers them next."""
        try:
            sound = pygame.mixer.Sound(filepath)
            sound.set_volume(self.bgs_volume)
            self.ambient_sounds[name] = sound
            self.ambient_sound_paths[name] = filepath
            if self.current_bgs == name:
                self.play_bgs(name, fade_in=False)
            print(f"🔄 Reloaded BGS: {name}")
            return True
        except Exception as e:
            print(f"Warning: Could not reload BGS '{filepath}': {e}")
            return False

    def remove_sound_effect(self, name):
        """Drop a sound effect that was deleted from disk. Anything already
        playing keeps playing; play_sound(name) afterwards will warn
        'not loaded' the same as if it had never been loaded."""
        self.sound_effects.pop(name, None)
        self.sound_effect_paths.pop(name, None)

    def remove_bgs(self, name):
        """Drop a BGS ambient loop that was deleted from disk. Stops it
        immediately if it's the one currently looping via play_bgs()."""
        self.ambient_sounds.pop(name, None)
        self.ambient_sound_paths.pop(name, None)
        if self.current_bgs == name:
            self.stop_bgs(fade_out=False)

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

    def play_bgs(self, name, fade_in=True):
        """Start (or switch to) a BGS ambient loop, e.g. 'rain'.

        Unlike play_looping_sound(), this tracks a single "current bgs"
        slot: calling play_bgs() with a different name stops whatever
        ambient loop was previously playing first, the same way
        play_music() replaces the current music track. Calling it again
        with the same name that's already looping is a cheap no-op.
        """
        if not self.bgs_enabled:
            return

        if name not in self.ambient_sounds:
            print(f"Warning: BGS '{name}' not loaded")
            return

        if self.current_bgs == name and self.bgs_channel is not None and self.bgs_channel.get_busy():
            return  # Already playing this ambient loop — don't restart it.

        # Stop whatever ambient loop was playing before switching.
        if self.bgs_channel is not None:
            if fade_in:
                self.bgs_channel.fadeout(self.fade_duration)
            else:
                self.bgs_channel.stop()

        try:
            fade_ms = self.fade_duration if fade_in else 0
            self.bgs_channel = self.ambient_sounds[name].play(loops=-1, fade_ms=fade_ms)
            self.current_bgs = name
        except Exception as e:
            print(f"Error: Could not play BGS '{name}': {e}")

    def stop_bgs(self, fade_out=True):
        """Stop the currently playing BGS ambient loop, if any."""
        if self.bgs_channel is not None:
            if fade_out:
                self.bgs_channel.fadeout(self.fade_duration)
            else:
                self.bgs_channel.stop()

        self.current_bgs = None
        self.bgs_channel = None

    def play_positional_bgs(self, name):
        """Start a standalone ambient loop for a single placed 'Ambient Sound'
        world object, on its own Channel — independent of the current_bgs
        slot used by play_bgs()/stop_bgs(). Starts silent (volume 0); the
        caller (typically an AmbientSoundObject) is expected to drive the
        volume every frame via set_positional_bgs_volume() based on distance
        to the player. Returns the Channel to hang onto for later volume
        updates and stop_positional_bgs(), or None if it couldn't be played.
        """
        if not self.bgs_enabled:
            return None

        if name not in self.ambient_sounds:
            print(f"Warning: BGS '{name}' not loaded")
            return None

        try:
            channel = self.ambient_sounds[name].play(loops=-1)
            if channel is not None:
                channel.set_volume(0.0)
                self.positional_bgs_channels.append(channel)
            return channel
        except Exception as e:
            print(f"Error: Could not play positional BGS '{name}': {e}")
            return None

    def set_positional_bgs_volume(self, channel, volume):
        """Set the per-instance volume (0.0-1.0, before scaling) of a
        positional ambient Channel returned by play_positional_bgs().
        Scaled by the master bgs_volume so the "Ambient Sounds" slider in
        settings still affects placed objects the same way it affects the
        regular room BGS. No-ops quietly if bgs is currently disabled or
        channel is None (e.g. the sound failed to load in this session).
        """
        if channel is None:
            return
        if not self.bgs_enabled:
            channel.set_volume(0.0)
            return
        volume = max(0.0, min(1.0, volume))
        channel.set_volume(volume * self.bgs_volume)

    def stop_positional_bgs(self, channel):
        """Stop a single positional ambient channel started with
        play_positional_bgs(), e.g. when its world object is destroyed or
        the room unloads. Safe to call with None."""
        if channel is None:
            return
        channel.stop()
        if channel in self.positional_bgs_channels:
            self.positional_bgs_channels.remove(channel)

    def stop_all_positional_bgs(self):
        """Stop every currently active positional ambient channel at once,
        e.g. when leaving a room — placed Ambient Sound objects don't
        persist their Channel across a room load, so this prevents orphaned
        loops from playing forever in the background."""
        for channel in self.positional_bgs_channels:
            channel.stop()
        self.positional_bgs_channels.clear()

    def set_bgs_volume(self, volume):
        """Set BGS (ambient loop) volume (0.0 to 1.0)"""
        self.bgs_volume = max(0.0, min(1.0, volume))
        for sound in self.ambient_sounds.values():
            sound.set_volume(self.bgs_volume)

        # NOTE: this does not retroactively rescale already-playing
        # positional ambient channels (see play_positional_bgs) — those are
        # driven every frame by their owning AmbientSoundObject via
        # set_positional_bgs_volume(), which reads self.bgs_volume fresh
        # each call, so the change takes effect on the next frame anyway.

    def toggle_bgs(self):
        """Toggle BGS ambient loops on/off (both the single room BGS slot
        and any positional Ambient Sound objects placed in the world)"""
        self.bgs_enabled = not self.bgs_enabled
        if not self.bgs_enabled:
            self.stop_bgs(fade_out=False)
            self.stop_all_positional_bgs()
        return self.bgs_enabled

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
        for sound in self.ambient_sounds.values():
            sound.stop()
        self.current_bgs = None
        self.bgs_channel = None
        self.positional_bgs_channels.clear()
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
        print(f"🎵 Playing music (direct): {track_name}")
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

    def play_bgs(self, bgs_name, fade_in=True):
        """Start (or switch to) a BGS ambient loop (e.g. 'rain'). Intended
        for room-level ambient sound, the same way play_music() is used for
        room-level music tracks. Switching to a different name automatically
        stops whatever ambient loop was playing before."""
        self.sound_engine.play_bgs(bgs_name, fade_in=fade_in)

    def stop_bgs(self, fade_out=True):
        """Stop whatever BGS ambient loop is currently playing, if any."""
        self.sound_engine.stop_bgs(fade_out=fade_out)

    def play_positional_bgs(self, bgs_name):
        """Start a standalone ambient loop for a placed 'Ambient Sound' world
        object. Returns a Channel the caller must hang onto and drive every
        frame via set_positional_bgs_volume(), based on distance to the
        player. Independent of play_bgs()'s single room-wide BGS slot, so
        many of these can play at once, each fading on its own."""
        return self.sound_engine.play_positional_bgs(bgs_name)

    def set_positional_bgs_volume(self, channel, volume):
        """Set the distance-based volume (0.0-1.0) of a positional ambient
        channel returned by play_positional_bgs()."""
        self.sound_engine.set_positional_bgs_volume(channel, volume)

    def stop_positional_bgs(self, channel):
        """Stop a single positional ambient channel, e.g. its object was
        destroyed or the room is unloading."""
        self.sound_engine.stop_positional_bgs(channel)

    def stop_all_positional_bgs(self):
        """Stop every active positional ambient channel at once (e.g. on
        room change), so placed Ambient Sound objects don't keep looping in
        the background after their room unloads."""
        self.sound_engine.stop_all_positional_bgs()

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

    # Kept as class attributes (rather than only local variables inside
    # load_from_directory) so both load_from_directory() and
    # classify_path()/apply_watcher_events() below share one definition —
    # and so game.py can import these directly when building the
    # AssetWatcher that watches assets/audio, instead of hardcoding a
    # second copy of the same extension list.
    MUSIC_EXTENSIONS = ('.ogg', '.mp3', '.wav', '.it', '.xm', '.s3m', '.mod')
    SFX_EXTENSIONS = ('.wav',)
    BGS_EXTENSIONS = ('.wav', '.ogg')

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
                ambient/            ← BGS (background sound) loops, see below
                    rain.wav
                    wind.wav
                blast.wav           ← files directly in sfx/ still work too

        SFX are looked up by bare filename (no extension, no folder), same as
        before — subfolders are just for your own organization and don't
        affect play_sfx() calls. play_sfx('footstep_run') works whether the
        file lives at sfx/footstep_run.wav or sfx/misc/footstep_run.wav.
        Any folder depth works; add more categories freely.

        BGS (background sound) — sfx/ambient/
        ------------------------------------
        Files directly inside sfx/ambient/ (e.g. rain.wav) are treated as
        BGS ambient loops rather than regular one-shot/combat sfx: they're
        loaded into SoundEngine.ambient_sounds (keyed by bare filename, same
        convention as everything else) instead of sound_effects, and are
        skipped by the generic sfx walk below so they don't also show up as
        a normal play_sound()-able sfx. This keeps them on their own volume
        control (SoundEngine.bgs_volume / set_bgs_volume()) since ambient
        beds are usually mixed much quieter than combat/UI sfx, and lets a
        room set one via play_bgs('rain') the same way it sets a music
        track via play_music(). Subfolders *within* sfx/ambient/ are also
        picked up (any folder depth), same as the rest of sfx/.

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
        if os.path.exists(music_path):
            for filename in os.listdir(music_path):
                if filename.lower().endswith(AudioAssetLoader.MUSIC_EXTENSIONS):
                    name = os.path.splitext(filename)[0]
                    filepath = os.path.join(music_path, filename)
                    sound_engine.load_music(name, filepath)
                    print(f" Loaded music: {name}")

        # BGS (background sound) — ambient loops like rain, wind, etc. Loaded
        # separately from the generic sfx walk below (see docstring) so they
        # land in ambient_sounds/bgs_volume instead of sound_effects/sfx_volume.
        ambient_path = os.path.join(sfx_path, 'ambient')
        if os.path.exists(ambient_path):
            for root, _dirs, filenames in os.walk(ambient_path):
                for filename in filenames:
                    if filename.lower().endswith(AudioAssetLoader.BGS_EXTENSIONS):
                        name = os.path.splitext(filename)[0]
                        if name in sound_engine.ambient_sounds:
                            print(f"⚠️ Duplicate BGS name '{name}' — "
                                  f"{os.path.join(root, filename)} overwrites the earlier one")
                        filepath = os.path.join(root, filename)
                        sound_engine.load_bgs(name, filepath)
                        print(f" Loaded BGS: {name}")

        # Load sound effects — walks the full sfx/ tree so category subfolders
        # (combat/, misc/, etc.) are picked up, not just the top level. The
        # ambient/ subtree is skipped here — it was already loaded above as
        # BGS, not regular sfx.
        if os.path.exists(sfx_path):
            for root, _dirs, filenames in os.walk(sfx_path):
                if root == ambient_path or root.startswith(ambient_path + os.sep):
                    continue
                for filename in filenames:
                    if filename.lower().endswith(AudioAssetLoader.SFX_EXTENSIONS):
                        name = os.path.splitext(filename)[0]
                        if name in sound_engine.sound_effects:
                            print(f"⚠️ Duplicate SFX name '{name}' — "
                                  f"{os.path.join(root, filename)} overwrites the earlier one")
                        filepath = os.path.join(root, filename)
                        sound_engine.load_sound_effect(name, filepath)
                        print(f" Loaded SFX: {name}")

    @staticmethod
    def classify_path(base_path, filepath):
        """Given a filepath that changed on disk, figure out whether it's
        music, sfx, or BGS, and what asset 'name' it belongs under —
        mirroring the exact folder/extension rules load_from_directory()
        uses above, so a watcher-driven reload lands in the same place a
        full restart would put it.

        Returns (category, name) where category is 'music', 'sfx', or
        'bgs' — or None if the path doesn't match any recognized asset
        location/extension (e.g. a stray .txt file, or a path outside
        base_path entirely).
        """
        filepath = os.path.normpath(filepath)
        music_path = os.path.normpath(os.path.join(base_path, 'music'))
        sfx_path = os.path.normpath(os.path.join(base_path, 'sfx'))
        ambient_path = os.path.normpath(os.path.join(sfx_path, 'ambient'))

        filename = os.path.basename(filepath)
        name, ext = os.path.splitext(filename)
        ext = ext.lower()

        def _is_within(path, folder):
            return path == folder or path.startswith(folder + os.sep)

        if _is_within(filepath, music_path) and ext in AudioAssetLoader.MUSIC_EXTENSIONS:
            return ('music', name)

        # Check ambient/ (BGS) before the generic sfx/ check — ambient/ is a
        # subfolder of sfx/, so this order matters (same as load_from_directory).
        if _is_within(filepath, ambient_path) and ext in AudioAssetLoader.BGS_EXTENSIONS:
            return ('bgs', name)

        if _is_within(filepath, sfx_path) and ext in AudioAssetLoader.SFX_EXTENSIONS:
            return ('sfx', name)

        return None

    @staticmethod
    def apply_watcher_events(sound_engine, events, base_path='assets/audio'):
        """Apply a batch of ('added'|'modified'|'removed', filepath) events
        — as produced by core.asset_watcher.AssetWatcher.poll_events() — onto
        a live SoundEngine, hot-reloading whatever changed with no restart
        needed. Call this once per frame from the main thread (e.g. from
        Game.update()) with whatever the watcher's poll_events() returns;
        it's a no-op on an empty list.
        """
        for kind, filepath in events:
            classified = AudioAssetLoader.classify_path(base_path, filepath)
            if classified is None:
                continue
            category, name = classified

            if kind == 'removed':
                if category == 'music':
                    sound_engine.music_tracks.pop(name, None)
                    if sound_engine.current_music == name:
                        sound_engine.stop_music(fade_out=False)
                elif category == 'sfx':
                    sound_engine.remove_sound_effect(name)
                elif category == 'bgs':
                    sound_engine.remove_bgs(name)
                print(f"🗑️ Asset removed: {category} '{name}'")
                continue

            # 'added' and 'modified' get the same treatment — either way we
            # just want sound_engine's in-memory copy to match disk.
            if category == 'music':
                sound_engine.reload_music(name, filepath)
            elif category == 'sfx':
                sound_engine.reload_sound_effect(name, filepath)
            elif category == 'bgs':
                sound_engine.reload_bgs(name, filepath)