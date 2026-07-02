"""
core/cutscene_runtime.py

Plays a cutscene data dict in real time. Used identically by the in-game
trigger system and the editor's preview mode, so what you see in the editor
is exactly what plays in the game.

──────────────────────────────────────────────────────────────────────────────
Cutscene JSON format
──────────────────────────────────────────────────────────────────────────────
{
    "id":       "intro_cutscene",
    "room":     "throne_room",          # room to load
    "duration": 10.0,                   # total length in seconds
    "actors": [
        {
            "id":         "actor_0",
            "type":       "enemy",      # "player", "enemy", "boss"
            "enemy_type": "pui_pui",    # enemy only
            "variant":    "default",
            "x": 100, "y": 80          # start position (world coords)
        }
    ],
    "actions": [
        # Camera actions  (target = "camera")
        {"time": 0.0, "target": "camera", "type": "pan_to",
         "params": {"x": 120, "y": 80, "duration": 1.5}},
        {"time": 0.0, "target": "camera", "type": "snap_to",
         "params": {"x": 120, "y": 80}},
        {"time": 2.0, "target": "camera", "type": "shake",
         "params": {"intensity": 8, "duration": 0.4}},

        # Screen actions  (target = "screen")
        {"time": 0.0,  "target": "screen", "type": "fade_in",
         "params": {"duration": 1.0}},
        {"time": 9.0,  "target": "screen", "type": "fade_out",
         "params": {"duration": 1.0, "color": [0,0,0]}},
        {"time": 3.0,  "target": "screen", "type": "dialogue",
         "params": {"speaker": "Pui Pui", "text": "You dare challenge me?"}},

        # Actor actions  (target = actor id string)
        {"time": 1.0, "target": "actor_0", "type": "set_animation",
         "params": {"state": "walk", "direction": "right"}},
        {"time": 1.0, "target": "actor_0", "type": "move_to",
         "params": {"x": 200, "y": 80, "duration": 1.2}},
        {"time": 4.0, "target": "actor_0", "type": "face",
         "params": {"direction": "left"}},
        {"time": 4.0, "target": "actor_0", "type": "teleport",
         "params": {"x": 50, "y": 80}}
    ]
}
──────────────────────────────────────────────────────────────────────────────
"""
import pygame
from .cutscene_actor import CutsceneActor
from .cutscene_camera_target import CutsceneCameraTarget

# Grace period for weather fading in/out — keeps transitions smooth so
# weather never just pops on or off at full opacity.
_WEATHER_START_FADE_IN = 1.5
_WEATHER_STOP_FADE_OUT = 1.5


class _WeatherEffect:
    """Scrolling tiled weather overlay (rain, snow, fog, dust, etc.).

    Static weather (rain, fog…): loads a single PNG tile, scales it, tiles it
    across the screen, and scrolls it downward each frame so it loops.

    Animated weather (snow…): when the PNG is a horizontal spritesheet
    (width > height, square frames), each frame is extracted, scaled, and
    alpha-baked individually. Frames cycle at _ANIM_FPS independently of the
    vertical scroll so snowflakes both drift down and animate.

    Frame size is auto-detected as image height — no metadata file needed.
    """

    # Types that ship as animated spritesheets (horizontal strip of square frames).
    _ANIMATED_TYPES = {'snow'}

    # Animation playback rate for spritesheet weather.
    _ANIM_FPS = 8.0

    # Per-type horizontal drift as a fraction of self.speed.
    # Positive = rightward, negative = leftward. Unlisted types get 0.
    _DRIFT_X = {
        'snow': 1.0,    # blows sideways at full speed
        'fog':  -0.08,  # very slow leftward creep
        'dust': 1.0,    # blows across screen at full speed
    }

    # Types that scroll purely horizontally — vertical scroll is suppressed.
    _HORIZONTAL_ONLY = {'dust'}

    # Types blitted with BLEND_ADD so bright pixels add light and dark pixels
    # contribute nothing (dark = transparent under additive blending).
    _ADDITIVE_TYPES = {'fog', 'dust'}

    # Per-type default alpha. For additive types this scales brightness, not opacity.
    _DEFAULT_ALPHA = {
        'fog':  60,
        'dust': 255,
    }

    def __init__(self, weather_type: str, speed: float = 120.0, alpha: int = -1):
        self.weather_type = weather_type
        self.speed        = float(speed)
        # Use per-type default when caller passes the sentinel -1.
        if alpha < 0:
            alpha = self._DEFAULT_ALPHA.get(weather_type, 180)
        self.alpha        = int(max(0, min(255, alpha)))
        self.scroll_y     = 0.0
        self.scroll_x     = 0.0   # horizontal drift (see _DRIFT_X)
        self.opacity      = 1.0   # live multiplier — animated by the runtime for fades
        self._frames: list = []   # pre-scaled, alpha-baked surfaces (animated only)
        self._frame_idx   = 0.0   # sub-frame accumulator
        self._surf        = None  # active tile (static) or current frame
        self._load()

    # ── Loading ───────────────────────────────────────────────────────────────

    def _bake_alpha(self, img):
        """Apply self.alpha to img and return it.

        Additive types (fog, dust): we scale RGB values by alpha/255 to dim the
        additive contribution. The surface is kept plain RGB (no alpha channel)
        because BLEND_ADD ignores alpha anyway and SRCALPHA surfaces can behave
        unexpectedly with special blit flags.

        Standard types (rain, snow): alpha is baked into the per-pixel alpha
        channel so normal blitting produces the right opacity.
        """
        if self.weather_type in self._ADDITIVE_TYPES:
            plain = img.convert()  # plain RGB — no alpha channel
            if self.alpha < 255:
                try:
                    import numpy as np
                    arr = pygame.surfarray.pixels3d(plain)
                    arr[:] = (arr.astype('float32') * (self.alpha / 255.0)).astype('uint8')
                    del arr
                except Exception:
                    # numpy unavailable — fall back to pygame's built-in multiply
                    plain.fill((self.alpha, self.alpha, self.alpha),
                               special_flags=pygame.BLEND_RGB_MULT)
            return plain

        # Standard per-pixel alpha bake.
        if self.alpha >= 255:
            return img
        try:
            import numpy as np
            arr = pygame.surfarray.pixels_alpha(img)
            arr[:] = (arr.astype('float32') * (self.alpha / 255.0)).astype('uint8')
            del arr
        except Exception:
            img.set_alpha(self.alpha)
        return img

    def _scale(self, img):
        """Scale img by RENDER_SCALE and return the new surface."""
        from config.settings import RENDER_SCALE
        if RENDER_SCALE == 1:
            return img
        return pygame.transform.scale(
            img,
            (int(img.get_width() * RENDER_SCALE),
             int(img.get_height() * RENDER_SCALE)),
        )

    def _load(self):
        import os
        path = os.path.join('assets', 'weather', f'{self.weather_type}.png')
        try:
            sheet = pygame.image.load(path).convert_alpha()
        except Exception as e:
            print(f'[WeatherEffect] could not load {path}: {e}')
            self._surf = None
            return

        if self.weather_type in self._ANIMATED_TYPES:
            # Frames are square; frame size == sheet height.
            frame_size = sheet.get_height()
            num_frames = max(1, sheet.get_width() // frame_size)
            self._frames = []
            for i in range(num_frames):
                frame_surf = pygame.Surface((frame_size, frame_size), pygame.SRCALPHA)
                frame_surf.blit(sheet, (0, 0), (i * frame_size, 0, frame_size, frame_size))
                frame_surf = self._scale(frame_surf)
                frame_surf = self._bake_alpha(frame_surf)
                self._frames.append(frame_surf)
            self._frame_idx = 0.0
            self._surf = self._frames[0] if self._frames else None
        else:
            self._surf = self._bake_alpha(self._scale(sheet))

    # ── Update ────────────────────────────────────────────────────────────────

    def update(self, dt: float):
        if self._surf is None:
            return

        # Vertical scroll — skipped for purely horizontal types like dust.
        if self.weather_type not in self._HORIZONTAL_ONLY:
            h = self._surf.get_height()
            if h > 0:
                self.scroll_y = (self.scroll_y + self.speed * dt) % h

        # Advance spritesheet frame for animated types.
        if self._frames:
            self._frame_idx = (self._frame_idx + self._ANIM_FPS * dt) % len(self._frames)
            self._surf = self._frames[int(self._frame_idx)]

        # Horizontal drift — rate and direction from _DRIFT_X.
        drift = self._DRIFT_X.get(self.weather_type, 0.0)
        if drift != 0.0:
            w = self._surf.get_width()
            if w > 0:
                self.scroll_x = (self.scroll_x + self.speed * drift * dt) % w

    # ── Draw ──────────────────────────────────────────────────────────────────

    def draw(self, screen, screen_w: int, screen_h: int):
        if self._surf is None or self.opacity <= 0.001:
            return
        img_w = self._surf.get_width()
        img_h = self._surf.get_height()
        if img_w <= 0 or img_h <= 0:
            return

        # Start one full tile above/left so seams stay off-screen on both axes.
        start_y     = int(self.scroll_y) - img_h
        start_x     = int(self.scroll_x) - img_w
        is_additive = self.weather_type in self._ADDITIVE_TYPES

        if self.opacity >= 0.999:
            # Fast path — no intermediate surface needed.
            blit_flags = pygame.BLEND_ADD if is_additive else 0
            y = start_y
            while y < screen_h:
                x = start_x
                while x < screen_w:
                    screen.blit(self._surf, (x, y), special_flags=blit_flags)
                    x += img_w
                y += img_h
            return

        # Fading path — tile into an intermediate surface, apply opacity, then blit.
        #
        # Additive (fog, dust): tile with BLEND_ADD into a plain RGB surface,
        # then scale brightness by opacity using BLEND_RGB_MULT, then blit with
        # BLEND_ADD. This correctly dims the additive light contribution.
        #
        # Standard (rain, snow): tile into an SRCALPHA surface normally, then
        # scale the alpha channel by opacity.
        if is_additive:
            need_new = (not hasattr(self, '_fade_surf')
                        or self._fade_surf.get_size() != (screen_w, screen_h)
                        or (self._fade_surf.get_flags() & pygame.SRCALPHA))
            if need_new:
                self._fade_surf = pygame.Surface((screen_w, screen_h))
            self._fade_surf.fill((0, 0, 0))
            y = start_y
            while y < screen_h:
                x = start_x
                while x < screen_w:
                    self._fade_surf.blit(self._surf, (x, y), special_flags=pygame.BLEND_ADD)
                    x += img_w
                y += img_h
            scale = int(255 * max(0.0, min(1.0, self.opacity)))
            self._fade_surf.fill((scale, scale, scale), special_flags=pygame.BLEND_RGB_MULT)
            screen.blit(self._fade_surf, (0, 0), special_flags=pygame.BLEND_ADD)
        else:
            need_new = (not hasattr(self, '_fade_surf')
                        or self._fade_surf.get_size() != (screen_w, screen_h)
                        or not (self._fade_surf.get_flags() & pygame.SRCALPHA))
            if need_new:
                self._fade_surf = pygame.Surface((screen_w, screen_h), pygame.SRCALPHA)
            self._fade_surf.fill((0, 0, 0, 0))
            y = start_y
            while y < screen_h:
                x = start_x
                while x < screen_w:
                    self._fade_surf.blit(self._surf, (x, y))
                    x += img_w
                y += img_h
            try:
                import numpy as np
                arr = pygame.surfarray.pixels_alpha(self._fade_surf)
                arr[:] = (arr.astype('float32') * self.opacity).astype('uint8')
                del arr
            except Exception:
                pass  # numpy unavailable — tile drawn at full opacity
            screen.blit(self._fade_surf, (0, 0))


class CutsceneRuntime:
    """Plays back a cutscene dict.

    Args:
        cutscene_data:  The cutscene dict (loaded from JSON).
        camera:         The game's Camera instance to control.
        entity_factory: Callable(actor_def dict) → entity | None.
                        Lets the caller (game or editor) supply real entities.
        dialogue_box:   Optional DialogueBox for 'dialogue' actions.
    """

    def __init__(self, cutscene_data, camera, entity_factory, dialogue_box=None,
                 sound_manager=None):
        from config.settings import RENDER_SCALE

        self.data          = cutscene_data
        self.camera        = camera
        self.dialogue_box  = dialogue_box
        # Used by 'sound'-target actions (play_music / play_sfx). None is
        # tolerated — those actions just become no-ops, same as a missing
        # dialogue_box silently skipping dialogue actions.
        self.sound_manager = sound_manager
        self.elapsed       = 0.0
        self.finished      = False
        self.paused        = False
        self._RENDER_SCALE = RENDER_SCALE

        # Actions sorted by trigger time so we can fire them in order.
        self.pending_actions = sorted(
            cutscene_data.get('actions', []),
            key=lambda a: a['time']
        )
        self.action_index = 0

        # Colour overlay state — used by fade_in, fade_out, flash, set_overlay.
        self.overlay_alpha  = 0.0
        self.overlay_target = 0.0
        self.overlay_speed  = 0.0   # alpha units/second (unsigned)
        self.overlay_color  = (0, 0, 0)

        # Camera target starts at the current view centre so the first pan_to
        # has a sensible origin even if no snap_to fires at t=0.
        self.camera_target = CutsceneCameraTarget(
            (camera.x + camera.screen_width  // 2) / RENDER_SCALE,
            (camera.y + camera.screen_height // 2) / RENDER_SCALE,
        )

        # Build actor wrappers from the actor definitions.
        self.actors: dict[str, CutsceneActor] = {}
        for actor_def in cutscene_data.get('actors', []):
            entity = entity_factory(actor_def)
            if entity is not None:
                self.actors[actor_def['id']] = CutsceneActor(actor_def['id'], entity)

        # Cached room dimensions — set on first update(), consumed by seek()
        # so it can drive the camera on scrubbed frames.
        self._world_width  = 0
        self._world_height = 0

        # (action_time, text) of the dialogue line currently on screen.
        # Compared by seek() before calling show() so scrubbing over the same
        # line doesn't restart the typewriter animation.
        self._active_dialogue_key = None

        # Dialogue-pause state: while a box is open, elapsed stops and actors
        # idle. Playback resumes the moment the box is dismissed.
        self._dialogue_paused      = False
        self._pre_dialogue_state: dict = {}  # snapshot of actor tweens taken at pause time

        # Invert effect — time-windowed pixel inversion applied in draw_overlay().
        # _invert_mode: 'full' | 'red' | 'green' | 'blue' | 'greyscale'
        self._invert_active   = False
        self._invert_end_time = 0.0
        self._invert_mode     = 'full'

        # Active weather overlay. Created by weather_start / weather_fade_in,
        # cleared when a fade-out completes or on restart / seek.
        self._weather: '_WeatherEffect | None' = None

        # Weather fade tween — animates _weather.opacity between 0 and 1.
        # _weather_fade_dur == 0.0 means no tween is active.
        self._weather_fade_from    = 1.0
        self._weather_fade_to      = 1.0
        self._weather_fade_dur     = 0.0
        self._weather_fade_elapsed = 0.0

        # Room-change callback — set by the caller (e.g. game.py) after construction
        # so the runtime can request a room transition without importing game state.
        # Signature: on_change_room(room_name: str, spawn_x: None, spawn_y: None)
        self.on_change_room = None

        # Auto-weather: set via the top-level "weather" key in the cutscene JSON.
        # The runtime handles the full fade-in/out lifecycle automatically so
        # designers don't need to add timeline actions for it. Fade-in fires
        # immediately; fade-out fires when duration elapses and holds finished=False
        # until the effect fully disappears.
        _auto_wcfg = cutscene_data.get('weather')
        self._auto_weather_fade_in_dur  = 0.0
        self._auto_weather_fade_out_dur = 0.0
        self._auto_fade_out_started     = False
        if _auto_wcfg and _auto_wcfg.get('type'):
            _wtype  = _auto_wcfg['type']
            _wspeed = float(_auto_wcfg.get('speed', 120.0))
            _walpha = int(_auto_wcfg.get('alpha', -1))
            self._auto_weather_fade_in_dur  = float(_auto_wcfg.get('fade_in_duration',  2.0))
            self._auto_weather_fade_out_dur = float(_auto_wcfg.get('fade_out_duration', 2.0))
            # Only create the effect if seek() hasn't already done so (the editor
            # may call seek() before __init__ returns).
            if self._weather is None:
                self._weather         = _WeatherEffect(_wtype, speed=_wspeed, alpha=_walpha)
                self._weather.opacity = 0.0
            # Kick off the auto fade-in immediately.
            if self._auto_weather_fade_in_dur > 0:
                self._weather_fade_from    = 0.0
                self._weather_fade_to      = 1.0
                self._weather_fade_dur     = self._auto_weather_fade_in_dur
                self._weather_fade_elapsed = 0.0
            else:
                self._weather.opacity = 1.0

    # ── Public API ────────────────────────────────────────────────────────────

    def update(self, dt, world_width, world_height):
        """Advance the cutscene by dt seconds."""
        self._world_width  = world_width   # kept fresh so seek() can use them
        self._world_height = world_height
        if self.finished or self.paused:
            return

        # While a dialogue box is open, freeze time but keep weather animating —
        # rain shouldn't freeze mid-air just because a text box appeared.
        if self._dialogue_paused:
            if not (self.dialogue_box and getattr(self.dialogue_box, 'active', False)):
                self._resume_from_dialogue()
            if self._weather is not None:
                self._weather.update(dt)
                self._tick_weather_fade(dt)
            return

        self.elapsed += dt

        # Fire all actions whose timestamp has been reached.
        while (self.action_index < len(self.pending_actions) and
               self.pending_actions[self.action_index]['time'] <= self.elapsed):
            self._execute_action(self.pending_actions[self.action_index])
            self.action_index += 1

        self.camera_target.update(dt)
        self.camera.update(self.camera_target, world_width, world_height, dt)

        for actor in self.actors.values():
            actor.update(dt)

        # Advance the colour overlay fade.
        if self.overlay_speed > 0:
            diff = self.overlay_target - self.overlay_alpha
            step = self.overlay_speed * dt
            if abs(diff) <= step:
                self.overlay_alpha = self.overlay_target
                self.overlay_speed = 0.0
            else:
                self.overlay_alpha += step if diff > 0 else -step

        # Expire the invert effect once its window closes.
        if self._invert_active and self.elapsed >= self._invert_end_time:
            self._invert_active = False

        if self._weather is not None:
            self._weather.update(dt)
            self._tick_weather_fade(dt)

        # Check whether the cutscene should finish.
        duration = self.data.get('duration', 0)
        if duration > 0 and self.elapsed >= duration:
            self._handle_end_of_duration()

    def _tick_weather_fade(self, dt):
        """Advance the weather opacity tween and clear weather when it fully fades out."""
        if self._weather is None or self._weather_fade_dur <= 0.0:
            return
        self._weather_fade_elapsed = min(self._weather_fade_dur,
                                         self._weather_fade_elapsed + dt)
        t     = self._weather_fade_elapsed / self._weather_fade_dur
        eased = t * t * (3.0 - 2.0 * t)  # smoothstep
        self._weather.opacity = (self._weather_fade_from
                                 + (self._weather_fade_to - self._weather_fade_from) * eased)
        if self._weather_fade_elapsed >= self._weather_fade_dur:
            self._weather_fade_dur = 0.0
            if self._weather_fade_to <= 0.0:
                self._weather = None  # fade-out complete — remove the effect

    def _handle_end_of_duration(self):
        """Called when elapsed >= duration. Handles auto weather fade-out before finishing."""
        if (self._auto_weather_fade_out_dur > 0
                and self._weather is not None
                and not self._auto_fade_out_started):
            # First frame past the end — kick off the auto fade-out and hold
            # finished=False so the weather can exit gracefully.
            self._auto_fade_out_started = True
            self._weather_fade_from     = self._weather.opacity
            self._weather_fade_to       = 0.0
            self._weather_fade_dur      = self._auto_weather_fade_out_dur
            self._weather_fade_elapsed  = 0.0

        elif self._auto_fade_out_started:
            # Waiting for the auto fade-out to complete.
            if self._weather is None:
                self.finished = True

        elif self._weather is not None:
            # Timeline weather is still active at the natural end. Fade it out
            # gracefully rather than popping it off when the runtime is destroyed.
            # If a weather_fade_out action is already mid-tween, just wait for it.
            self._auto_fade_out_started = True
            if self._weather_fade_dur <= 0.0:
                self._weather_fade_from    = self._weather.opacity
                self._weather_fade_to      = 0.0
                self._weather_fade_dur     = _WEATHER_STOP_FADE_OUT
                self._weather_fade_elapsed = 0.0

        else:
            self.finished = True

    def draw_actors(self, screen, camera, colors):
        """Draw all cutscene actors in Y-sorted order.

        Actors are sorted by their ground Y position (bottom-to-top) so
        entities higher on screen (smaller Y) draw behind those lower on
        screen (larger Y), matching the LayerManager's depth behaviour.

        IMPORTANT: call this BEFORE blitting the foreground tile layer so
        that foreground tiles (trees, buildings — tile.layer >= 0) correctly
        occlude actors that stand behind them.  draw_overlay() is still
        called last.
        """
        sorted_actors = sorted(self.actors.values(), key=lambda a: a.entity.y)
        for actor in sorted_actors:
            actor.draw(screen, camera, colors)

    def draw_weather(self, screen, screen_width, screen_height):
        """Draw the weather layer.

        Call before the dialogue box so weather scrolls behind it, and before
        draw_overlay() so fades and invert sit on top of everything.
        """
        if self._weather is not None:
            self._weather.draw(screen, screen_width, screen_height)

    def draw_overlay(self, screen, screen_width, screen_height):
        """Draw the invert effect and colour-fade overlay on top of everything.

        Layering order: world → actors → weather → dialogue box → this overlay.
        """
        if self._invert_active:
            self._apply_invert(screen, self._invert_mode)

        alpha = int(self.overlay_alpha)
        if alpha <= 0:
            return
        size = (screen_width, screen_height)
        if not hasattr(self, '_overlay_surf') or self._overlay_surf.get_size() != size:
            self._overlay_surf = pygame.Surface(size)
        self._overlay_surf.fill(self.overlay_color)
        self._overlay_surf.set_alpha(min(255, alpha))
        screen.blit(self._overlay_surf, (0, 0))

    @staticmethod
    def _apply_invert(screen, mode):
        """Invert the pixels of screen in-place according to mode.

        Modes:
          full      — invert all three channels (classic negative)
          red       — invert red channel only   (teal tint)
          green     — invert green channel only (magenta tint)
          blue      — invert blue channel only  (amber tint)
          greyscale — desaturate then invert    (ghostly white-on-black)
        """
        try:
            import numpy as np
            arr = pygame.surfarray.pixels3d(screen)
            if mode == 'red':
                arr[:, :, 0] = 255 - arr[:, :, 0]
            elif mode == 'green':
                arr[:, :, 1] = 255 - arr[:, :, 1]
            elif mode == 'blue':
                arr[:, :, 2] = 255 - arr[:, :, 2]
            elif mode == 'greyscale':
                grey = (arr[:, :, 0].astype(np.uint16) + arr[:, :, 1] + arr[:, :, 2]) // 3
                inv  = (255 - grey).astype(np.uint8)
                arr[:, :, 0] = arr[:, :, 1] = arr[:, :, 2] = inv
            else:  # 'full'
                arr[:] = 255 - arr
            del arr  # release the pixel lock
        except Exception:
            # numpy unavailable — BLEND_RGB_XOR is equivalent to 255-x per channel.
            # We can't desaturate without numpy, so greyscale falls back to full.
            channel_fill = {
                'red':   (255,   0,   0),
                'green': (  0, 255,   0),
                'blue':  (  0,   0, 255),
            }
            fill = channel_fill.get(mode, (255, 255, 255))
            w, h = screen.get_size()
            inv = pygame.Surface((w, h))
            inv.fill(fill)
            screen.blit(inv, (0, 0), special_flags=pygame.BLEND_RGB_XOR)

    def restart(self):
        """Reset to the beginning (used by the editor's play loop)."""
        self.elapsed        = 0.0
        self.action_index   = 0
        self.finished       = False
        self.paused         = False
        self.overlay_alpha  = 0.0
        self.overlay_target = 0.0
        self.overlay_speed  = 0.0
        self._invert_active   = False
        self._invert_end_time = 0.0
        self.camera_target.stop()
        self._weather              = None
        self._weather_fade_from    = 1.0
        self._weather_fade_to      = 1.0
        self._weather_fade_dur     = 0.0
        self._weather_fade_elapsed = 0.0
        # Re-sort in case actions were edited since last play.
        self.pending_actions = sorted(
            self.data.get('actions', []),
            key=lambda a: a['time']
        )
        for actor in self.actors.values():
            actor._tween = None
            actor.set_animation('idle', actor.entity.direction)

    def seek(self, t):
        """Jump to time t by resetting state and replaying all actions up to that point.

        Works like scrubbing in After Effects — every action whose timestamp
        <= t is executed instantly, giving a correct scene snapshot at any point.
        """
        # Full state reset.
        self.elapsed             = 0.0
        self.finished            = False
        self.paused              = False
        self.overlay_alpha       = 0.0
        self.overlay_target      = 0.0
        self.overlay_speed       = 0.0
        self._dialogue_paused    = False
        self._pre_dialogue_state = {}
        self._invert_active      = False
        self._invert_end_time    = 0.0
        self.camera_target.stop()
        self._clear_camera_shake()
        self._weather              = None
        self._weather_fade_from    = 1.0
        self._weather_fade_to      = 1.0
        self._weather_fade_dur     = 0.0
        self._weather_fade_elapsed = 0.0

        self.pending_actions = sorted(
            self.data.get('actions', []),
            key=lambda a: a['time']
        )

        # Reset all actors to their cutscene start positions.
        for actor_def in self.data.get('actors', []):
            aid = actor_def.get('id', '')
            if aid in self.actors:
                actor = self.actors[aid]
                actor._tween    = None
                actor.entity.x  = float(actor_def.get('x', 0))
                actor.entity.y  = float(actor_def.get('y', 0))
                actor.set_animation('idle', 'down')

        # Replay all actions up to t, tracking the most-recent of each type
        # so we can resolve the correct state afterwards.
        last_anim_time       = {aid: 0.0 for aid in self.actors}
        last_move_start      = {}   # actor_id → timestamp the active move_to fired
        last_overlay_action  = None
        last_shake_action    = None
        last_dialogue_action = None
        last_invert_action   = None
        last_weather_on      = None  # most-recent weather_start / weather_fade_in
        last_weather_off     = None  # most-recent weather_stop  / weather_fade_out
        last_music_action    = None  # most-recent play_music (sound target)

        self.action_index = 0
        self._seeking = True

        for i, action in enumerate(self.pending_actions):
            if action['time'] > t:
                break

            target = action.get('target', '')
            atype  = action.get('type',   '')

            # Before a camera pan_to fires, resolve all in-progress camera tweens
            # to their state at this action's timestamp. Without this, each pan
            # reads the stale initial position and produces wrong deltas.
            if atype == 'pan_to' and target == 'camera' and self.camera_target._tweens:
                done = []
                for tw in self.camera_target._tweens:
                    fire_t   = tw.get('fire_time') or 0.0
                    elapsed  = action['time'] - fire_t
                    tw['elapsed'] = max(0.0, elapsed)
                    if elapsed >= tw['duration']:
                        self.camera_target._base_x += tw['delta_x']
                        self.camera_target._base_y += tw['delta_y']
                        done.append(tw)
                for tw in done:
                    self.camera_target._tweens.remove(tw)
                self.camera_target._tween = (self.camera_target._tweens[-1]
                                             if self.camera_target._tweens else None)
                self.camera_target._recompute_xy()

            # Before a move/animation action fires, resolve the actor's current
            # world position from any still-active tween. In live playback
            # actor.update() does this every frame; here we do it manually so
            # sequential move_to calls don't all start from the original spawn point.
            if atype in ('move_to', 'fly_to', 'set_animation', 'face') and target in self.actors:
                actor = self.actors[target]
                if actor._tween:
                    prev_start      = last_move_start.get(target, 0.0)
                    elapsed_in_prev = action['time'] - prev_start
                    tw   = actor._tween
                    prog = min(1.0, elapsed_in_prev / tw.duration) if tw.duration > 0 else 1.0
                    actor.entity.x = tw.start_x + (tw.end_x - tw.start_x) * prog
                    actor.entity.y = tw.start_y + (tw.end_y - tw.start_y) * prog
                    if prog >= 1.0:
                        actor._tween = None  # clear finished tween before the next action runs

            self._execute_action(action)
            self.action_index = i + 1

            # Track the most-recent action of each category for post-loop resolution.
            if target == 'camera':
                if atype == 'pan_to':
                    # Tag the tween with its fire time so the post-loop block can
                    # compute the correct elapsed at t.
                    if self.camera_target._tweens:
                        self.camera_target._tweens[-1]['fire_time'] = action['time']
                elif atype == 'shake':
                    last_shake_action = action
            elif target == 'screen':
                if atype in ('fade_in', 'fade_out', 'set_overlay', 'flash'):
                    last_overlay_action = action
                elif atype == 'dialogue':
                    last_dialogue_action = action
                elif atype == 'invert':
                    last_invert_action = action
                elif atype in ('weather_start', 'weather_fade_in'):
                    last_weather_on  = action
                    last_weather_off = None  # on-action cancels any prior off
                elif atype == 'weather_stop':
                    last_weather_off = action
                    last_weather_on  = None  # instant stop — nothing left to resume
                elif atype == 'weather_fade_out':
                    last_weather_off = action
            elif target == 'sound':
                if atype in ('play_music', 'stop_music'):
                    # Whichever of these fired most recently determines the
                    # music state at t — a stop_music after the last
                    # play_music means music should be silent at t, not
                    # resumed. Both are persistent state, unlike play_sfx.
                    last_music_action = action
                # play_sfx deliberately untracked — it's a one-shot fire-and-
                # forget effect, not persistent state, so there's nothing to
                # resolve for it after scrubbing (same as it staying silent
                # during the loop above via the _seeking guard in _do_sound).
            elif target in self.actors:
                if atype in ('set_animation', 'move_to', 'fly_to', 'face'):
                    last_anim_time[target] = action['time']
                if atype in ('move_to', 'fly_to'):
                    last_move_start[target] = action['time']
                elif atype in ('teleport', 'set_animation', 'face',
                               'set_character', 'set_costume'):
                    last_move_start.pop(target, None)

        self._seeking = False

        # ── Music: actually start whatever track should be playing at t ───────
        # play_music was suppressed during the loop above (like change_room),
        # but unlike change_room/dialogue, music is ongoing state — if we
        # never resolved it, a track set to fire at t=0.0 would be silently
        # lost forever: game.py always calls seek(0.0) right after creating a
        # fresh runtime to establish the first frame, which would consume the
        # action (advance past it in the pending_actions list) without ever
        # actually starting the track. So re-fire it for real now that
        # _seeking is False. SoundEngine.play_music() already no-ops if this
        # exact track is already playing, so repeated scrubs across the same
        # window don't restart it.
        if last_music_action is not None:
            if last_music_action['type'] == 'play_music':
                self._do_sound(last_music_action['type'], last_music_action.get('params', {}))
            elif last_music_action['type'] == 'stop_music' and self.sound_manager is not None:
                # Cut instantly rather than fading — we just jumped straight
                # to t, there's no in-progress fade to animate through.
                self.sound_manager.stop_music(fade_out=False)

        # ── Dialogue: never open a box while scrubbing ────────────────────────
        # show() is suppressed during the replay loop (via _seeking). Here we
        # just update _active_dialogue_key so that resuming playback doesn't
        # re-trigger a line the playhead has already passed.
        if self.dialogue_box:
            if last_dialogue_action is not None:
                _p    = last_dialogue_action.get('params', {})
                _text = _p.get('text', '')
                self._active_dialogue_key = (last_dialogue_action['time'], _text)
            else:
                if self._active_dialogue_key is not None:
                    self.dialogue_box.hide()
                    self._active_dialogue_key = None

        # ── Shake: re-apply with remaining duration at t ──────────────────────
        # start_shake is suppressed during replay so stale shake never bleeds
        # into playback. We re-apply it here only if t falls inside the window.
        if last_shake_action is not None:
            sp    = last_shake_action.get('params', {})
            s_dur = float(sp.get('duration', 0.3))
            s_end = last_shake_action['time'] + s_dur
            if last_shake_action['time'] <= t < s_end:
                self.camera.start_shake(
                    intensity=sp.get('intensity', 8),
                    duration=s_end - t,
                )

        # ── Camera: resolve all active pan tweens to their state at t ─────────
        # pan_to() added tweens with elapsed=0. We now set each tween's elapsed
        # from its recorded fire_time so the blended camera position is correct.
        # Completed tweens are baked into _base; in-progress tweens keep their
        # elapsed so update() resumes animating from the right point.
        done = []
        for tw in self.camera_target._tweens:
            fire_t  = tw.get('fire_time') or 0.0
            elapsed = t - fire_t
            if elapsed >= tw['duration']:
                self.camera_target._base_x += tw['delta_x']
                self.camera_target._base_y += tw['delta_y']
                done.append(tw)
            else:
                tw['elapsed'] = elapsed
        for tw in done:
            self.camera_target._tweens.remove(tw)
        self.camera_target._tween = (self.camera_target._tweens[-1]
                                     if self.camera_target._tweens else None)
        self.camera_target._recompute_xy()

        # ── Overlay: interpolate alpha to the correct value at t ──────────────
        # _execute_action sets overlay_alpha/speed but never advances time, so
        # without this the screen stays fully black/transparent while scrubbing.
        if last_overlay_action is not None:
            oa     = last_overlay_action
            atype  = oa.get('type', '')
            t_fire = oa['time']
            params = oa.get('params', {})
            if atype == 'fade_in':
                dur = params.get('duration', 1.0)
                elapsed = t - t_fire
                self.overlay_alpha = (0.0 if elapsed >= dur
                                      else 255.0 * (1.0 - elapsed / dur) if dur > 0 else 0.0)
                self.overlay_speed = 0.0
            elif atype == 'fade_out':
                dur = params.get('duration', 1.0)
                elapsed = t - t_fire
                self.overlay_alpha = (255.0 if elapsed >= dur
                                      else 255.0 * (elapsed / dur) if dur > 0 else 255.0)
                self.overlay_speed = 0.0
            elif atype == 'flash':
                dur = params.get('duration', 0.3)
                elapsed = t - t_fire
                self.overlay_color = (255, 255, 255)
                self.overlay_alpha = (0.0 if elapsed >= dur
                                      else 255.0 * (1.0 - elapsed / dur) if dur > 0 else 0.0)
                self.overlay_speed = 0.0
            # set_overlay: _execute_action already wrote the correct alpha.

        # ── Invert: activate if t is inside the effect's time window ──────────
        if last_invert_action is not None:
            ip    = last_invert_action.get('params', {})
            i_dur = ip.get('duration', 1.0)
            i_end = last_invert_action['time'] + i_dur
            if last_invert_action['time'] <= t < i_end:
                self._invert_active   = True
                self._invert_end_time = i_end
                self._invert_mode     = ip.get('mode', 'full')
            else:
                self._invert_active = False
        else:
            self._invert_active = False

        # ── Weather: rebuild effect and resolve opacity at t ──────────────────
        # Compare timestamps of the most-recent on/off actions to determine
        # whether weather is "on" or "off" at t, then compute the correct
        # opacity for any in-progress fade tween.
        weather_on_t  = last_weather_on['time']  if last_weather_on  else -1.0
        weather_off_t = last_weather_off['time'] if last_weather_off else -1.0

        def _build_weather_from(action):
            """Instantiate _WeatherEffect from an on-action and advance its scroll to t."""
            wp  = action.get('params', {})
            eff = _WeatherEffect(
                wp.get('weather_type', 'rain'),
                speed=float(wp.get('speed', 120.0)),
                alpha=int(wp.get('alpha', -1)),
            )
            elapsed = t - action['time']
            if eff._surf is not None and eff.weather_type not in eff._HORIZONTAL_ONLY:
                h = eff._surf.get_height()
                if h > 0:
                    eff.scroll_y = (eff.speed * elapsed) % h
            if eff._frames:
                n = len(eff._frames)
                eff._frame_idx = (eff._ANIM_FPS * elapsed) % n
                eff._surf = eff._frames[int(eff._frame_idx)]
            drift = eff._DRIFT_X.get(eff.weather_type, 0.0)
            if drift != 0.0 and eff._surf is not None:
                w = eff._surf.get_width()
                if w > 0:
                    eff.scroll_x = (eff.speed * drift * elapsed) % w
            return eff

        def _smoothstep(p):
            p = max(0.0, min(1.0, p))
            return p * p * (3.0 - 2.0 * p)

        if weather_on_t >= weather_off_t and last_weather_on is not None:
            # Most-recent action is an on-action — weather should be visible.
            self._weather = _build_weather_from(last_weather_on)
            on_type       = last_weather_on.get('type', '')
            if on_type == 'weather_fade_in':
                dur          = float(last_weather_on.get('params', {}).get('duration', 2.0))
                elapsed_fade = t - weather_on_t
                self._weather.opacity = (1.0 if elapsed_fade >= dur
                                         else _smoothstep(elapsed_fade / dur if dur > 0 else 1.0))
            elif on_type == 'weather_start':
                elapsed_fade = t - weather_on_t
                self._weather.opacity = (1.0 if elapsed_fade >= _WEATHER_START_FADE_IN
                                         else _smoothstep(elapsed_fade / _WEATHER_START_FADE_IN
                                                          if _WEATHER_START_FADE_IN > 0 else 1.0))
            else:
                self._weather.opacity = 1.0

        elif weather_off_t > weather_on_t and last_weather_off is not None:
            off_type = last_weather_off.get('type', '')
            if off_type == 'weather_fade_out':
                # Rebuild from the prior on-action so scroll is continuous,
                # then apply the fade-out opacity.
                if last_weather_on is not None:
                    self._weather = _build_weather_from(last_weather_on)
                    dur          = float(last_weather_off.get('params', {}).get('duration', 2.0))
                    elapsed_fade = t - weather_off_t
                    if elapsed_fade >= dur:
                        self._weather = None  # fade fully done
                    else:
                        p = elapsed_fade / dur if dur > 0 else 1.0
                        self._weather.opacity = 1.0 - _smoothstep(p)
                else:
                    self._weather = None  # fade-out with nothing to fade
            else:
                self._weather = None  # weather_stop — instant off
        else:
            self._weather = None

        # ── Actors: resolve tween positions and advance sprites to t ──────────
        for aid, actor in self.actors.items():
            if actor._tween:
                tw               = actor._tween
                action_start     = last_move_start.get(aid, t)
                elapsed_in_tween = t - action_start
                progress         = (min(1.0, elapsed_in_tween / tw.duration)
                                    if tw.duration > 0 else 1.0)

                actor.entity.x = tw.start_x + (tw.end_x - tw.start_x) * progress
                actor.entity.y = tw.start_y + (tw.end_y - tw.start_y) * progress

                if progress >= 1.0:
                    # Tween finished before t — settle into idle.
                    actor._tween = None
                    actor.set_animation('idle', actor.entity.direction)
                    last_anim_time[aid] = action_start + tw.duration
                else:
                    # Tween still in progress — store elapsed so update() resumes
                    # mid-motion rather than rewinding to the start position.
                    actor._tween.elapsed = elapsed_in_tween
                    # For fly tweens, recompute the visual arc offset so the
                    # sprite appears airborne while scrubbing.
                    from core.cutscene_actor import _FlyTween as _FT
                    if isinstance(actor._tween, _FT):
                        import math as _math
                        actor._tween.fly_offset_y = (
                            actor._tween.arc_height * _math.sin(progress * _math.pi)
                        )

            # Simulate sprite animation with small fixed steps rather than one
            # large dt. Most sprite systems advance only one frame per call
            # regardless of how large dt is, so one big step would only ever
            # produce 1-2 frames. Stepping at ~60 fps reproduces every transition
            # exactly as live playback would.
            elapsed_in_anim = max(0.0, t - last_anim_time[aid])
            if hasattr(actor.entity, 'sprite') and actor.entity.sprite:
                _STEP     = 1.0 / 60.0
                remaining = elapsed_in_anim
                while remaining > 1e-9:
                    actor.entity.sprite.update(min(_STEP, remaining))
                    remaining -= _STEP

        self.elapsed = float(t)

    def _clear_camera_shake(self):
        """Zero out shake attributes on the camera object.

        Camera implementations use different attribute names — we try all
        common variants so this stays decoupled from Camera internals.
        """
        for attr in ('_shake_intensity', '_shake_duration', '_shake_elapsed',
                     'shake_intensity',  'shake_duration',  'shake_elapsed'):
            if hasattr(self.camera, attr):
                setattr(self.camera, attr, 0.0)

    # ── Internal action dispatch ──────────────────────────────────────────────

    def _execute_action(self, action):
        target = action.get('target', '')
        atype  = action.get('type',   '')
        params = action.get('params', {})

        if target == 'camera':
            self._do_camera(atype, params)
        elif target == 'screen':
            self._do_screen(atype, params)
        elif target == 'room':
            self._do_room(atype, params)
        elif target == 'sound':
            self._do_sound(atype, params)
        elif target in self.actors:
            self._do_actor(self.actors[target], atype, params)

    def _do_camera(self, atype, params):
        if atype == 'pan_to':
            self.camera_target.pan_to(
                params['x'], params['y'],
                params.get('duration', 1.0),
                start_x=params.get('start_x'),
                start_y=params.get('start_y'),
            )
        elif atype == 'snap_to':
            self.camera_target.snap_to(params['x'], params['y'])
        elif atype == 'shake':
            # Suppressed during seek() replay — the correct shake state is
            # applied analytically at the end of seek() instead.
            if not getattr(self, '_seeking', False):
                self.camera.start_shake(
                    intensity=params.get('intensity', 8),
                    duration=params.get('duration', 0.3),
                )

    def _do_room(self, atype, params):
        """Handle 'room' target actions.

        change_room — immediately transition to a different room.
                      Calls self.on_change_room(room_name, None, None)
                      if the callback has been wired up by the host (game.py).

        All actions are suppressed during seek() replay — the post-loop block
        handles any state that needs to be recomputed analytically at t.
        """
        if getattr(self, '_seeking', False):
            return

        if atype == 'change_room':
            room_name = params.get('room_name', '').strip()
            if room_name and callable(self.on_change_room):
                self.on_change_room(room_name, None, None)

    def _do_sound(self, atype, params):
        """Play music or fire a one-shot sound effect via SoundManager.

        Skipped while scrubbing (self._seeking), for the same reason
        change_room is skipped in _do_room above: this is a one-shot side
        effect rather than state seek() can meaningfully resolve at an
        arbitrary t, so we just don't fire it — the action is still marked
        consumed (seek() advances action_index past it) so it won't fire
        again on resumed forward playback either. Scrubbing never plays audio.
        """
        if getattr(self, '_seeking', False):
            return
        if self.sound_manager is None:
            return

        if atype == 'play_music':
            track = params.get('track', '').strip()
            if track:
                loop = params.get('loop', True)
                self.sound_manager.play_music(
                    track,
                    loops=-1 if loop else 0,
                    fade_in=params.get('fade_in', True),
                )
        elif atype == 'play_sfx':
            sfx = params.get('sfx', '').strip()
            if sfx:
                self.sound_manager.play_sfx(sfx)
        elif atype == 'stop_music':
            self.sound_manager.stop_music(fade_out=params.get('fade_out', True))

    def _do_screen(self, atype, params):
        if atype == 'fade_out':
            duration = params.get('duration', 1.0)
            self.overlay_color  = tuple(params.get('color', [0, 0, 0]))
            self.overlay_target = 255.0
            self.overlay_speed  = 255.0 / duration if duration > 0 else 9999.0
        elif atype == 'fade_in':
            duration = params.get('duration', 1.0)
            self.overlay_color  = tuple(params.get('color', [0, 0, 0]))
            self.overlay_target = 0.0
            self.overlay_speed  = 255.0 / duration if duration > 0 else 9999.0
        elif atype == 'set_overlay':
            self.overlay_color  = tuple(params.get('color', [0, 0, 0]))
            self.overlay_alpha  = float(params.get('alpha', 255))
            self.overlay_target = self.overlay_alpha
            self.overlay_speed  = 0.0
        elif atype == 'flash':
            # White overlay at full opacity that fades to transparent — same
            # mechanic as fade_in but always white regardless of params.
            duration = params.get('duration', 0.3)
            self.overlay_color  = (255, 255, 255)
            self.overlay_alpha  = 255.0
            self.overlay_target = 0.0
            self.overlay_speed  = 255.0 / duration if duration > 0 else 9999.0
        elif atype == 'invert':
            duration = params.get('duration', 1.0)
            self._invert_active   = True
            self._invert_end_time = self.elapsed + duration
            self._invert_mode     = params.get('mode', 'full')
        elif atype == 'weather_start':
            # Always fade in over _WEATHER_START_FADE_IN seconds rather than
            # snapping on at full opacity. If the same type is already playing,
            # tween from the current opacity to avoid a visible jump.
            weather_type = params.get('weather_type', 'rain')
            if self._weather is None or self._weather.weather_type != weather_type:
                self._weather         = _WeatherEffect(
                    weather_type,
                    speed=float(params.get('speed', 120.0)),
                    alpha=int(params.get('alpha', -1)),
                )
                self._weather.opacity   = 0.0
                self._weather_fade_from = 0.0
            else:
                self._weather_fade_from = self._weather.opacity
            self._weather_fade_to      = 1.0
            self._weather_fade_dur     = _WEATHER_START_FADE_IN
            self._weather_fade_elapsed = 0.0
        elif atype == 'weather_stop':
            # Fade out over _WEATHER_STOP_FADE_OUT rather than popping off.
            # No-op if nothing is playing.
            if self._weather is not None:
                self._weather_fade_from    = self._weather.opacity
                self._weather_fade_to      = 0.0
                self._weather_fade_dur     = _WEATHER_STOP_FADE_OUT
                self._weather_fade_elapsed = 0.0
        elif atype == 'weather_fade_in':
            weather_type = params.get('weather_type', 'rain')
            duration     = float(params.get('duration', 2.0))
            if self._weather is None or self._weather.weather_type != weather_type:
                self._weather         = _WeatherEffect(
                    weather_type,
                    speed=float(params.get('speed', 120.0)),
                    alpha=int(params.get('alpha', -1)),
                )
                self._weather.opacity   = 0.0
                self._weather_fade_from = 0.0
            else:
                # Same type already playing — continue from current opacity.
                self._weather_fade_from = self._weather.opacity
            self._weather_fade_to      = 1.0
            self._weather_fade_dur     = duration
            self._weather_fade_elapsed = 0.0
        elif atype == 'weather_fade_out':
            # No-op if nothing is playing.
            if self._weather is not None:
                duration = float(params.get('duration', 2.0))
                self._weather_fade_from    = self._weather.opacity
                self._weather_fade_to      = 0.0
                self._weather_fade_dur     = duration
                self._weather_fade_elapsed = 0.0
        elif atype == 'dialogue':
            if self.dialogue_box and not getattr(self, '_seeking', False):
                portrait_key = params.get('portrait', '').strip() or None
                # Derive a display name from the portrait key:
                # "tiger_bandit" → "Tiger Bandit"
                speaker = (portrait_key.replace('_', ' ').title()
                           if portrait_key else params.get('speaker', '') or '')
                self.dialogue_box.show(
                    params.get('text', ''),
                    speaker,
                    True, None,
                    portrait_key=portrait_key,
                )
                self._active_dialogue_key = (self.elapsed, params.get('text', ''))
                # Freeze the cutscene: snapshot every actor's state, idle them
                # all, then stop advancing time until the box is dismissed.
                self._pre_dialogue_state = {}
                for aid, actor in self.actors.items():
                    self._pre_dialogue_state[aid] = {
                        'tween': actor._tween,
                        'anim':  getattr(actor.entity, 'current_animation_state', 'idle'),
                        'dir':   getattr(actor.entity, 'direction', 'down'),
                    }
                    actor._tween = None
                    actor.set_animation('idle', getattr(actor.entity, 'direction', 'down'))
                self._dialogue_paused = True

    def _resume_from_dialogue(self):
        """Restore actor states frozen by a dialogue pause and resume playback."""
        self._dialogue_paused = False
        for aid, snap in self._pre_dialogue_state.items():
            if aid not in self.actors:
                continue
            actor = self.actors[aid]
            tween = snap['tween']
            if tween and not tween.finished:
                # Restore the in-progress tween; elapsed was frozen so it
                # continues exactly where it left off.
                actor._tween = tween
                actor.set_animation(snap['anim'], snap['dir'])
            else:
                actor._tween = None
                actor.set_animation('idle', snap['dir'])
        self._pre_dialogue_state = {}

    def _do_actor(self, actor, atype, params):
        if atype == 'set_animation':
            actor.set_animation(
                params.get('state', 'idle'),
                params.get('direction', 'down'),
            )
        elif atype == 'move_to':
            actor.move_to(
                params['x'], params['y'],
                params.get('duration', 1.0),
                anim_state=params.get('anim_state', 'walk'),
                direction=params.get('direction', None),
            )
        elif atype == 'face':
            actor.face(params.get('direction', 'down'))
        elif atype == 'teleport':
            actor.teleport(params['x'], params['y'])
        elif atype == 'fly_to':
            actor.fly_to(
                params['x'], params['y'],
                params.get('duration', 1.0),
                arc_height=params.get('arc_height', 48.0),
                direction=params.get('direction', None),
            )
        elif atype == 'set_character':
            new_char = params.get('character', '').strip()
            if new_char and hasattr(actor.entity, 'sprite'):
                from core.sprite_system import create_character_sprite
                current_anim = getattr(actor.entity, 'current_animation_state', 'idle')
                current_dir  = getattr(actor.entity, 'direction', 'down')
                actor.entity.character = new_char
                actor.entity.sprite    = create_character_sprite(new_char, 'base', 32, 32)
                actor.entity.direction = current_dir
                actor.set_animation(current_anim, current_dir)
        elif atype == 'set_costume':
            new_costume = params.get('costume', '').strip()
            if new_costume:
                actor.set_costume(new_costume)