"""
dev_tools/cutscene_editor.py

Standalone cutscene editor with an After-Effects-inspired layout.

Data model (cutscene_data dict, stored as JSON)
────────────────────────────────────────────────
  id:       str           — matches the filename stem
  room:     str           — which room to display in the viewport
  duration: float         — total scene length in seconds
  actors:   list[dict]    — {id, type, enemy_type, variant, x, y, …}
  actions:  list[dict]    — {time, target, type, params{…}}, always sorted by time

All mutations must call _push_undo() *before* changing cutscene_data so that
Ctrl-Z can restore the state that existed just before that edit.

Layout (edit view)
──────────────────
  ┌────────────────────────────────────────────────────────────┐
  │  TOP BAR  (42 px) — name, timecode, play/stop, save        │
  ├─────────────┬──────────────────────────┬───────────────────┤
  │  LEFT PANEL │                          │  RIGHT PANEL      │
  │  - Room     │   VIEWPORT               │  - Inspector      │
  │  - Layers / │   (tiles + actors)       │    (action form)  │
  │    Tracks   │   WASD to pan            │                   │
  │  - Actors   │                          │                   │
  │  (195 px)   │                          │  (270 px)         │
  ├─────────────┴──────────────────────────┴───────────────────┤
  │  TIMELINE  (220 px)                                        │
  │  ┌──────────────┬──────────────────────────────────────┐   │
  │  │  ADD DUP DEL │  Time ruler  0──1──2──3──4──         │   │
  │  │  Camera      │  ◆                   ◆               │   │
  │  │  Screen      │       ◆       ◆                      │   │
  │  │  actor_0     │           ◆◆       ◆                 │   │
  │  └──────────────┴──────────────────────────────────────┘   │
  └────────────────────────────────────────────────────────────┘
"""

import json
import os
import pygame
from core.camera import Camera
from config.settings import RENDER_SCALE

# ── Layout constants ──────────────────────────────────────────────────────────
_TOP_H       = 42
_LEFT_W      = 195
_RIGHT_W     = 270
_BOTTOM_H    = 220

_TL_HDR_H    = 32    # timeline: top control strip height
_TL_RULER_H  = 22    # timeline: time ruler strip height
_TL_ROW_H    = 28    # timeline: per-track row height
_TL_LABEL_W  = 130   # timeline: left label column width

_BTN_H       = 28
_ROW_H       = 28

# ── Colour palette ────────────────────────────────────────────────────────────
_C = {
    'bg':        (14,  16,  22),
    'panel':     (22,  25,  36),
    'panel2':    (18,  21,  30),   # slightly darker panel variant
    'border':    (48,  54,  82),
    'accent':    (82, 122, 255),
    'accent2':   (50, 195, 120),
    'danger':    (215,  55,  55),
    'text':      (215, 220, 238),
    'text_dim':  (110, 122, 155),
    'highlight': (38,  44,  68),
    'sel':       (60,  80, 150),
    'sel2':      (50, 100,  60),   # green selection variant
    'white':     (255, 255, 255),
    'black':     (0,   0,   0),
    'ruler_bg':  (16,  18,  28),
    'playhead':  (255,  65,  65),
    'kf_border': (255, 255, 255),
}

# Animation states per entity type
_PLAYER_STATES = ['idle', 'walk', 'run', 'melee', 'kiblast',
                  'charge', 'firebeam', 'hurt', 'transform', 'untransform',
                  'flying']
_ENEMY_STATES  = ['idle', 'walk', 'melee', 'hurt', 'flying']
_DIRECTIONS    = ['down', 'up', 'left', 'right']

# Action type → list of (param_key, label, type_hint).
# type_hint drives two things: _commit_form() casts the raw string value using
# it ('float'/'int'/anything-else), and _draw_action_form() decides whether to
# show a cycle-button (for enum hints like 'dir', 'anim', 'portrait', …) or a
# plain text field (for 'float', 'int', 'str').
_ACTION_PARAMS = {
    'pan_to':        [('x', 'End X', 'float'), ('y', 'End Y', 'float'),
                      ('duration', 'Duration (s)', 'float'),
                      ('start_x', 'Start X (opt)', 'float'),
                      ('start_y', 'Start Y (opt)', 'float')],
    'snap_to':       [('x', 'World X', 'float'), ('y', 'World Y', 'float')],
    'shake':         [('intensity', 'Intensity', 'float'),
                      ('duration', 'Duration (s)', 'float')],
    'scroll':        [('direction', 'Direction', 'scroll_dir'),
                      ('speed',     'Speed (wu/s)', 'float')],
    'scroll_stop':   [],
    'change_room':   [('room_name', 'Room Name', 'room')],
    'fade_in':       [('duration', 'Duration (s)', 'float')],
    'fade_out':      [('duration', 'Duration (s)', 'float')],
    'flash':         [('duration', 'Duration (s)', 'float')],
    'invert':        [('duration', 'Duration (s)', 'float'),
                      ('mode', 'Mode', 'invert_mode')],
    'dialogue':      [('portrait', 'Character', 'portrait'), ('text', 'Text', 'str')],
    'weather_start': [('weather_type', 'Weather Type', 'weather_type'),
                      ('speed',        'Speed (px/s)',  'float'),
                      ('alpha',        'Alpha (0-255)', 'float')],
    'weather_stop':  [],
    'set_animation': [('state', 'State', 'anim'), ('direction', 'Direction', 'dir')],
    'move_to':       [('x', 'World X', 'float'), ('y', 'World Y', 'float'),
                      ('duration', 'Duration (s)', 'float'),
                      ('anim_state', 'Anim State', 'anim'),
                      ('direction', 'Direction', 'dir')],
    'face':          [('direction', 'Direction', 'dir')],
    'teleport':      [('x', 'World X', 'float'), ('y', 'World Y', 'float')],
    'fly_to':        [('x', 'World X', 'float'), ('y', 'World Y', 'float'),
                      ('duration', 'Duration (s)', 'float'),
                      ('arc_height', 'Arc Height', 'float'),
                      ('direction', 'Direction', 'dir')],
}

_CAMERA_ACTIONS = ['pan_to', 'snap_to', 'shake']
_SCREEN_ACTIONS = ['fade_in', 'fade_out', 'flash', 'invert', 'dialogue',
                   'weather_start', 'weather_stop']
_ROOM_ACTIONS   = ['change_room']
_ACTOR_ACTIONS  = ['set_animation', 'move_to', 'face', 'teleport', 'fly_to']
_INVERT_MODES   = ['full', 'red', 'green', 'blue', 'greyscale']

_CUTSCENE_DIR        = os.path.join('data', 'cutscenes')
# Hidden JSON file that stores per-cutscene camera position + zoom so the
# viewport reopens exactly where the dev left off.
_EDITOR_VIEWPORTS    = os.path.join(_CUTSCENE_DIR, '.editor_viewports.json')
# How long between background saves while there are unsaved changes.
# Short enough that a crash loses very little work; long enough not to thrash disk.
_AUTOSAVE_INTERVAL   = 30.0   # seconds

# Per-actor track colours — cycled modulo so any number of actors all get
# a distinct colour without the list needing to grow with the project.
_ACTOR_COLORS = [
    (80,  185, 255),
    (255, 155,  50),
    (100, 255, 110),
    (255,  80, 175),
    (190, 125, 255),
    (255, 215,  60),
]

# Fixed track colours
_CAMERA_COLOR = (82, 122, 255)
_SCREEN_COLOR = (110, 122, 155)
_ROOM_COLOR   = (60, 180, 130)


def _ensure_dir():
    os.makedirs(_CUTSCENE_DIR, exist_ok=True)


def _cutscene_path(name):
    return os.path.join(_CUTSCENE_DIR, f'{name}.json')


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


class CutsceneEditor:
    """Full cutscene editor.  Mirrors SpriteEditor / RoomEditor API:
    toggle() / handle_input(event) / update(dt) / draw(screen).

    Two top-level views:
      'list' — file browser (create, open, delete cutscenes)
      'edit' — AE-style editor with viewport, timeline, inspector panels
    """

    def __init__(self, room_manager, room_editor, screen_width, screen_height,
                 dialogue_box=None):
        self.room_manager  = room_manager
        self.room_editor   = room_editor
        self.screen_width  = screen_width
        self.screen_height = screen_height
        self.dialogue_box  = dialogue_box
        self.active        = False

        # Fonts
        self.font_title  = pygame.font.Font(None, 40)
        self.font_large  = pygame.font.Font(None, 28)
        self.font_medium = pygame.font.Font(None, 22)
        self.font_small  = pygame.font.Font(None, 18)
        self.font_mono   = pygame.font.Font(None, 20)

        # ── Editor state ──────────────────────────────────────────────────────
        self.view          = 'list'
        self.cutscene_data = None
        self.cutscene_name = ''
        self.unsaved       = False
        self._autosave_t   = 0.0   # seconds since last autosave

        # Undo / redo stacks — each entry is a deep copy of cutscene_data.
        # _push_undo() must be called BEFORE every mutation so Ctrl-Z can
        # restore the state that existed just before that edit.
        self._undo_stack: list = []
        self._redo_stack: list = []
        # _UNDO_LIMIT is defined as a class variable below the undo methods —
        # no need to redefine it here; it shadows the class attr unnecessarily.

        # Viewport camera
        vp_w = screen_width  - _LEFT_W - _RIGHT_W
        vp_h = screen_height - _TOP_H  - _BOTTOM_H
        self.camera       = Camera(vp_w, vp_h)
        self.camera.x     = 0
        self.camera.y     = 0
        self.camera_speed = 300
        self._vp_rect     = pygame.Rect(_LEFT_W, _TOP_H, vp_w, vp_h)

        # ── List view ─────────────────────────────────────────────────────────
        self._files          = []
        self._list_sel       = -1
        self._list_scroll    = 0
        self._new_name_buf   = ''
        self._new_name_focus = False
        self._list_msg       = ''

        # ── Left panel ────────────────────────────────────────────────────────
        self._left_track_sel = -1   # which track row is highlighted (-1=none)
        # 0=camera,1=screen, 2..=actor index

        # ── Edit view — timeline ──────────────────────────────────────────────
        self._tl_sel         = -1
        self._tl_scroll      = 0   # vertical (unused in graphical mode)

        # AE-style graphical timeline
        self._tl_time_zoom   = 70.0   # pixels per second
        self._tl_scroll_x    = 0.0    # horizontal scroll offset (px)
        self._tl_play_drag   = False
        self._tl_playhead_t  = 0.0    # scrub playhead time (seconds)
        self._tl_zoom_min    = 20.0
        self._tl_zoom_max    = 300.0
        self._tl_auto_scroll = 0.0    # px/sec applied during playhead/kf drag near edges
        self._scrub_pending  = False  # True when _tl_playhead_t moved but seek() hasn't run yet

        # ── Right panel — action form (inspector) ─────────────────────────────
        self._form_active    = False
        self._form_new       = False
        self._form_target    = 'camera'
        self._form_type      = 'pan_to'
        self._form_time_buf  = '0.0'
        self._form_params    = {}
        self._form_focus     = None
        self._form_target_idx  = 0
        self._form_type_idx    = 0
        # Tracks the active group filter when browsing rooms for a change_room action.
        self._form_room_group  = ''

        # ── Portrait dropdown overlay ────────────────────────────────────────────
        self._portrait_dropdown_open   = False
        self._portrait_dropdown_items  = []
        self._portrait_dropdown_rect   = None
        self._portrait_dropdown_scroll = 0

        # ── Actor add form (shown in left panel) ──────────────────────────────
        self._actor_form     = False
        self._actor_type_idx = 0
        self._actor_id_buf   = 'actor_0'
        self._actor_etype_buf= 'tiger_bandit'
        self._actor_focus    = None
        self._actor_sel      = -1

        # ── Duration field (top bar inline editor) ────────────────────────────
        self._duration_buf   = '10.0'
        self._duration_focus = False

        # ── Viewport zoom ─────────────────────────────────────────────────────
        # camera.x/y are stored in base-scale pixels (RENDER_SCALE × world_units).
        # zoom only affects rendering; world coord math divides by zoom.
        self._vp_zoom     = 1.0
        self._vp_zoom_min = 0.15
        self._vp_zoom_max = 3.0

        # ── Grid visibility (toggled with G) ──────────────────────────────────
        self._show_grid   = True

        # ── Viewport right-click pan ──────────────────────────────────────────
        self._vp_drag      = False
        self._vp_drag_last = (0, 0)

        # ── Actor sprite previews ──────────────────────────────────────────────
        # Maps actor_id → real entity instance (Enemy / Player / BossEnemy).
        # Created lazily so sprites appear in the viewport without playing.
        self._actor_entities: dict = {}

        # ── Play preview ──────────────────────────────────────────────────────
        self._runtime        = None
        self._playing        = False
        self._last_ticks     = 0   # fallback real-time clock for playback dt

        # ── Viewport pick mode ────────────────────────────────────────────────
        self._pick_mode      = None
        self._place_actor_def= {}

        # ── Keyframe drag ─────────────────────────────────────────────────────
        # Index of the action currently being dragged (-1 = idle).
        self._kf_drag_idx    = -1
        # Click offset in seconds from the diamond centre, so the keyframe
        # doesn't jump to snap its centre under the cursor on drag start.
        self._kf_drag_offset = 0.0

        # ── Button rects (rebuilt each draw) ─────────────────────────────────
        self._btns           = {}
        self._field_meta     = {}

        # ── Mouse tracking (for ghost previews) ───────────────────────────────
        self._mouse_pos      = (0, 0)

        # ── Actor initial-position drag ────────────────────────────────────────
        # Index into cutscene_data['actors'] of the actor being dragged (-1=idle).
        # Sub-pixel grab offsets keep the actor from snapping its centre to the
        # cursor on drag start — same technique as the keyframe drag.
        self._actor_drag_idx      = -1
        self._actor_drag_offset_x = 0.0
        self._actor_drag_offset_y = 0.0

        # ── Pre-baked tile surfaces ───────────────────────────────────────────
        # Keyed (room_name, is_foreground) → Surface, same approach as
        # game._room_tile_surfaces.  Built once per room/layer, then it's a
        # single camera-offset blit per frame instead of N per-tile blits.
        self._vp_tile_surfaces: dict = {}

    # ══════════════════════════════════════════════════════════════════════════
    # Public API
    # ══════════════════════════════════════════════════════════════════════════

    def toggle(self):
        """Open or close the editor.

        On close: auto-saves dirty work, then always persists the viewport
        state (camera pos + zoom) so the next open lands right where you left.
        On open: refreshes the file list so newly added cutscenes appear.
        """
        if self.active:
            # Closing the editor — flush any unsaved work and the viewport state
            if self.view == 'edit' and self.cutscene_data:
                if self.unsaved:
                    self._save_cutscene()
                else:
                    self._save_viewport_state()
        self.active = not self.active
        if self.active:
            self._refresh_file_list()

    def handle_input(self, event):
        """Route a pygame event to the correct sub-handler.

        Priority order so higher-level overlays always get first dibs:
          KEYDOWN → _on_keydown
          Left-click down   → _on_click (buttons, timeline, viewport)
          Left-click up     → finalise playhead/kf drag
          Right-click down  → start viewport pan
          Right-click up    → end viewport pan
          MOUSEMOTION       → _on_mouse_motion (drag, ghost preview)
          MOUSEWHEEL        → _on_scroll (zoom / timeline scroll)
        """
        if not self.active:
            return None

        if event.type == pygame.KEYDOWN:
            return self._on_keydown(event)

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            return self._on_click(event.pos)

        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            self._tl_play_drag   = False
            self._tl_auto_scroll = 0.0
            # Fire one final scrub on release so the viewport snaps exactly to
            # the resting playhead position (the deferred update() scrub may
            # not have run for the very last mouse position yet).
            if self._scrub_pending:
                self._scrub_pending = False
                self._scrub_to(self._tl_playhead_t)
            # Finish a keyframe drag: re-sort actions by time so the runtime
            # always sees them in order, then remap _tl_sel to the moved action.
            if self._kf_drag_idx >= 0 and self.cutscene_data:
                actions   = self.cutscene_data.get('actions', [])
                moved_act = actions[self._kf_drag_idx] if self._kf_drag_idx < len(actions) else None
                actions.sort(key=lambda a: a['time'])
                if moved_act is not None and moved_act in actions:
                    self._tl_sel = actions.index(moved_act)
                self._kf_drag_idx = -1
                self._runtime     = None   # stale; rebuild on next scrub/play
            # Finish an actor initial-position drag.
            if self._actor_drag_idx >= 0:
                self._actor_drag_idx = -1
                self._runtime = None  # actor start pos changed; force rebuild

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 3:
            if self.view == 'edit' and self._vp_rect.collidepoint(event.pos):
                self._vp_drag      = True
                self._vp_drag_last = event.pos
            return None

        if event.type == pygame.MOUSEBUTTONUP and event.button == 3:
            self._vp_drag = False

        if event.type == pygame.MOUSEMOTION:
            self._on_mouse_motion(event.pos)

        if event.type == pygame.MOUSEWHEEL:
            self._on_scroll(event)

        return None

    def update(self, dt):
        """Advance editor state by *dt* seconds each frame.

        Handles: WASD camera pan, deferred timeline scrub, edge-scroll while
        dragging, autosave timer, and live playback via CutsceneRuntime.
        """
        if not self.active:
            return

        # Suppress manual camera pan during active playback — the runtime owns
        # the camera while playing, and fighting it causes jitter.  Also guards
        # K_s so it doesn't block the play path when no text field is focused.
        if (self.view == 'edit'
                and not self._playing
                and not self._form_focus
                and not self._actor_focus
                and not self._duration_focus):
            keys  = pygame.key.get_pressed()
            shift = keys[pygame.K_LSHIFT] or keys[pygame.K_RSHIFT]
            # Pan speed scales with zoom so movement feels consistent:
            # zoomed out → faster pan (covers more world), zoomed in → slower
            spd = self.camera_speed * (2 if shift else 1) / self._vp_zoom
            if keys[pygame.K_a] or keys[pygame.K_LEFT]:
                self.camera.x -= spd * dt
            if keys[pygame.K_d] or keys[pygame.K_RIGHT]:
                self.camera.x += spd * dt
            if keys[pygame.K_w] or keys[pygame.K_UP]:
                self.camera.y -= spd * dt
            if keys[pygame.K_s] or keys[pygame.K_DOWN]:
                self.camera.y += spd * dt
            self._clamp_camera()

        # ── Flush deferred scrub (set by _on_mouse_motion during playhead drag).
        # Running seek() here guarantees it fires at most once per frame no
        # matter how many MOUSEMOTION events queued up since the last tick. ───
        if self._scrub_pending and not self._playing:
            self._scrub_pending = False
            self._scrub_to(self._tl_playhead_t)

        # ── Apply auto-scroll while dragging playhead or keyframe near an edge.
        # _tl_auto_scroll is computed in _on_mouse_motion; applying it here
        # (once per frame, scaled by dt) gives smooth continuous scrolling. ──
        if self.view == 'edit' and self._tl_auto_scroll != 0.0 and self.cutscene_data:
            self._tl_scroll_x = max(0.0, self._tl_scroll_x + self._tl_auto_scroll * dt)
            tl          = self._tl_panel_rect()
            label_end_x = tl.x + _TL_LABEL_W
            mx          = self._mouse_pos[0]
            dur         = self.cutscene_data.get('duration', 10.0)
            if self._tl_play_drag:
                t = (mx - label_end_x + self._tl_scroll_x) / self._tl_time_zoom
                self._tl_playhead_t = _clamp(t, 0.0, dur)
                self._scrub_pending = True
            elif self._kf_drag_idx >= 0:
                actions = self.cutscene_data.get('actions', [])
                if self._kf_drag_idx < len(actions):
                    raw_t = (mx - label_end_x + self._tl_scroll_x) / self._tl_time_zoom
                    new_t = round(_clamp(raw_t + self._kf_drag_offset, 0.0, dur), 3)
                    actions[self._kf_drag_idx]['time'] = new_t
                    self._form_time_buf = f'{new_t:.2f}'
                    self.unsaved        = True

        # ── Auto-save: write to disk every _AUTOSAVE_INTERVAL seconds while
        # the editor has unsaved changes, so a crash never loses more than that
        # window of work.
        if self.view == 'edit' and self.cutscene_data:
            self._autosave_t += dt
            if self._autosave_t >= _AUTOSAVE_INTERVAL:
                self._autosave_t = 0.0
                if self.unsaved:
                    self._save_cutscene()

        if self._playing and self._runtime:
            # Fallback: if the main loop passes dt=0 (editor not wired into the
            # game clock, or called before the first real tick), derive real
            # elapsed time from pygame's own millisecond counter so the timer
            # and timeline always advance during playback.
            if dt <= 0:
                now = pygame.time.get_ticks()
                dt  = (now - self._last_ticks) / 1000.0 if self._last_ticks else 0.016
                dt  = min(dt, 0.1)  # cap to avoid a huge jump on the first tick
            self._last_ticks = pygame.time.get_ticks()

            room = self._get_current_room()
            w = room.width  if room else 10000
            h = room.height if room else 10000
            try:
                self._runtime.update(dt, w, h)
            except Exception as _e:
                import traceback
                print(f'[CutsceneEditor] runtime.update error: {_e}')
                traceback.print_exc()
                self._stop_preview()
                return
            # Keep the dialogue box animation ticking (typewriter effect, etc.)
            if self.dialogue_box:
                self.dialogue_box.update(dt)
            if room:
                self._clamp_camera()
            self._tl_playhead_t = self._runtime.elapsed
            if self._runtime.finished:
                self._stop_preview()

    def draw(self, screen):
        if not self.active:
            return
        if self.view == 'list':
            self._draw_list(screen)
        else:
            self._draw_edit(screen)

    # ══════════════════════════════════════════════════════════════════════════
    # Input handlers
    # ══════════════════════════════════════════════════════════════════════════

    def _on_keydown(self, event):
        """Dispatch keyboard events for the editor.

        ESC walks back through layers (dropdown → pick mode → actor form →
        inspector form → back to list → close editor).
        Space toggles playback when no text field is active.
        Ctrl-S saves; Ctrl-Z/Y/Shift-Z undo/redo; G toggles the grid.
        Everything else falls through to whichever text field has focus.
        """
        key = event.key

        if key == pygame.K_ESCAPE:
            # Dismiss overlays in stack order — most-modal first.
            if self._portrait_dropdown_open:
                self._portrait_dropdown_open = False
                return None
            if self._pick_mode:
                self._pick_mode = None
                return None
            if self._actor_form:
                self._actor_form = False
                return None
            if self._form_active:
                self._form_active = False
                self._form_focus  = None
                return None
            if self.view == 'edit':
                if self._playing:
                    self._stop_preview()
                else:
                    self.view = 'list'
                    self._refresh_file_list()
                return None
            self.active = False
            return None

        # Space plays / stops — but not while a dialogue box is open because
        # the user needs space to dismiss dialogue lines during preview.
        if (key == pygame.K_SPACE
                and self.view == 'edit'
                and not self._form_focus
                and not self._actor_focus
                and not self._new_name_focus):
            if self.dialogue_box and getattr(self.dialogue_box, 'active', False):
                return None
            if self._playing:
                self._stop_preview()
            else:
                self._start_preview()
            return None

        # E key: advance / dismiss the dialogue box during cutscene preview,
        # mirroring the in-game interact behaviour.
        if (key == pygame.K_e and self.view == 'edit' and self._playing
                and self.dialogue_box and getattr(self.dialogue_box, 'active', False)
                and self.dialogue_box._state != 'closing'):
            if self.dialogue_box._chars_shown < len(self.dialogue_box.current_text):
                # First press: snap typewriter to full text instantly
                self.dialogue_box._chars_shown = len(self.dialogue_box.current_text)
            else:
                # Second press: close the box (runtime resumes via _dialogue_paused check)
                self.dialogue_box.hide()
            return None

        if key == pygame.K_s and (event.mod & pygame.KMOD_CTRL):
            if self.view == 'edit':
                self._save_cutscene()
            return None

        if key == pygame.K_z and (event.mod & pygame.KMOD_CTRL) and self.view == 'edit':
            if event.mod & pygame.KMOD_SHIFT:
                self._redo()
            else:
                self._undo()
            return None

        if key == pygame.K_y and (event.mod & pygame.KMOD_CTRL) and self.view == 'edit':
            self._redo()
            return None

        # Grid toggle — only when no text field has focus so typing 'g' in a
        # name / param field is never intercepted.
        if (key == pygame.K_g and self.view == 'edit'
                and not self._form_focus and not self._actor_focus
                and not self._new_name_focus):
            self._show_grid = not self._show_grid
            return None

        # Route typing to whichever text field currently owns focus.
        if self._new_name_focus:
            self._handle_text_field('_new_name_buf', event)
            return None
        if self._duration_focus:
            self._handle_duration_field(event)
            return None
        if self._form_focus:
            self._handle_text_field(None, event, field_key=self._form_focus)
            return None
        if self._actor_focus:
            self._handle_text_field(None, event, actor_key=self._actor_focus)
            return None

        return None

    def _handle_text_field(self, attr, event, field_key=None, actor_key=None):
        """Apply one KEYDOWN event to whichever text buffer currently has focus.

        Three routing modes (exactly one should be set):
          attr      — a direct attribute on self (e.g. '_new_name_buf')
          field_key — a key in _form_params, or 'time' for _form_time_buf
          actor_key — 'id' or 'etype' for the actor-add form buffers

        Return/Tab/Esc always clear focus without committing a value.
        """
        key = event.key
        if key in (pygame.K_RETURN, pygame.K_TAB, pygame.K_ESCAPE):
            self._new_name_focus = False
            self._form_focus     = None
            self._actor_focus    = None
            return

        if attr:
            buf = getattr(self, attr)
        elif field_key == 'time':
            buf = self._form_time_buf
        elif field_key:
            buf = self._form_params.get(field_key, '')
        elif actor_key == 'id':
            buf = self._actor_id_buf
        elif actor_key == 'etype':
            buf = self._actor_etype_buf
        else:
            buf = ''

        if key == pygame.K_BACKSPACE:
            buf = buf[:-1]
        elif event.unicode and event.unicode.isprintable():
            buf += event.unicode

        if attr:
            setattr(self, attr, buf)
        elif field_key == 'time':
            self._form_time_buf = buf
        elif field_key:
            self._form_params[field_key] = buf
        elif actor_key == 'id':
            self._actor_id_buf = buf
        elif actor_key == 'etype':
            self._actor_etype_buf = buf

    def _handle_duration_field(self, event):
        key = event.key
        if key in (pygame.K_RETURN, pygame.K_TAB, pygame.K_ESCAPE):
            self._commit_duration()
            self._duration_focus = False
            return
        if key == pygame.K_BACKSPACE:
            self._duration_buf = self._duration_buf[:-1]
        elif event.unicode and event.unicode.isprintable():
            self._duration_buf += event.unicode

    def _commit_duration(self):
        """Parse _duration_buf and write it back to cutscene_data['duration']."""
        if not self.cutscene_data:
            return
        try:
            val = float(self._duration_buf)
            if val > 0:
                self._push_undo()
                self.cutscene_data['duration'] = round(val, 2)
                self.unsaved = True
        except ValueError:
            pass
        # Re-sync buf to whatever is actually stored (rolls back bad input)
        self._duration_buf = str(self.cutscene_data.get('duration', 10.0))

    def _scrub_to(self, t):
        """Seek the scene to time *t*, creating a paused runtime if needed."""
        if self._runtime is None and self.cutscene_data:
            try:
                from core.cutscene_runtime import CutsceneRuntime
                self._runtime = CutsceneRuntime(
                    self.cutscene_data, self.camera, self._entity_factory,
                    dialogue_box=self.dialogue_box)
            except Exception as e:
                print(f'[CutsceneEditor] _scrub_to runtime error: {e}')
                return
        if self._runtime:
            self._runtime.seek(t)
            # Snap the editor camera directly to the camera_target world position.
            # Don't call camera.update() here — that tweens, causing the camera to
            # keep drifting after scrub ends. Instead set x/y directly.
            from config.settings import RENDER_SCALE
            import math
            ct = self._runtime.camera_target
            room = self._get_current_room()
            w = (room.width  if room else 10000) * RENDER_SCALE
            h = (room.height if room else 10000) * RENDER_SCALE
            self.camera.x = ct.x * RENDER_SCALE - self.camera.screen_width  // 2
            self.camera.y = ct.y * RENDER_SCALE - self.camera.screen_height // 2
            self.camera.x = max(0, min(self.camera.x, w - self.camera.screen_width))
            self.camera.y = max(0, min(self.camera.y, h - self.camera.screen_height))
            # Scrub view is a static snapshot — clear any live shake that seek()
            # may have applied (shake inside its window) so the camera doesn't
            # wobble while the playhead is stationary.  A deterministic jitter
            # offset is added below purely for the scrub preview frame.
            self._runtime._clear_camera_shake()
            # Apply a deterministic shake preview when the playhead is inside a
            # shake window.  Uses the camera_target position (already set above)
            # as the base; the jitter is overwritten on the next scrub call so it
            # never bleeds into camera state or playback.
            for _action in (self.cutscene_data or {}).get('actions', []):
                if (_action.get('target') == 'camera'
                        and _action.get('type') == 'shake'):
                    _p    = _action.get('params', {})
                    _end  = _action['time'] + _p.get('duration', 0.3)
                    if _action['time'] <= t < _end:
                        _i = _p.get('intensity', 8)
                        self.camera.x += math.sin(t * 50.7) * _i
                        self.camera.y += math.cos(t * 37.3) * _i
                        break
        self._tl_playhead_t = t

    def _on_mouse_motion(self, pos):
        """Drag playhead when scrubbing; also keep mouse position for ghost previews."""
        self._mouse_pos = pos

        if self._vp_drag:
            dx = pos[0] - self._vp_drag_last[0]
            dy = pos[1] - self._vp_drag_last[1]
            self._vp_drag_last = pos
            self.camera.x -= dx / self._vp_zoom
            self.camera.y -= dy / self._vp_zoom
            self._clamp_camera()
            return

        # ── Keyframe drag ─────────────────────────────────────────────────────
        if self._kf_drag_idx >= 0 and self.cutscene_data:
            actions = self.cutscene_data.get('actions', [])
            if self._kf_drag_idx < len(actions):
                tl          = self._tl_panel_rect()
                label_end_x = tl.x + _TL_LABEL_W
                raw_t       = (pos[0] - label_end_x + self._tl_scroll_x) / self._tl_time_zoom
                new_t       = raw_t + self._kf_drag_offset
                dur         = self.cutscene_data.get('duration', 10.0)
                new_t       = round(_clamp(new_t, 0.0, dur), 3)
                actions[self._kf_drag_idx]['time'] = new_t
                # Keep the inspector time field in sync while dragging
                self._form_time_buf = f'{new_t:.2f}'
                self.unsaved        = True
            self._tl_auto_scroll = self._calc_tl_auto_scroll(pos[0])
            return

        # ── Actor initial-position drag ───────────────────────────────────────
        if self._actor_drag_idx >= 0 and self.cutscene_data:
            actors = self.cutscene_data.get('actors', [])
            if self._actor_drag_idx < len(actors):
                vp = self._vp_rect
                vx = pos[0] - vp.x
                vy = pos[1] - vp.y
                wx = vx / (RENDER_SCALE * self._vp_zoom) + self.camera.x / RENDER_SCALE
                wy = vy / (RENDER_SCALE * self._vp_zoom) + self.camera.y / RENDER_SCALE
                actors[self._actor_drag_idx]['x'] = round(wx + self._actor_drag_offset_x, 1)
                actors[self._actor_drag_idx]['y'] = round(wy + self._actor_drag_offset_y, 1)
                self.unsaved = True
            return

        if not self._tl_play_drag:
            self._tl_auto_scroll = 0.0
            return
        tl = self._tl_panel_rect()
        mx = pos[0]
        label_end_x = tl.x + _TL_LABEL_W
        t = (mx - label_end_x + self._tl_scroll_x) / self._tl_time_zoom
        dur = self.cutscene_data.get('duration', 10.0) if self.cutscene_data else 10.0
        # Update the playhead position instantly so it renders at the cursor
        # without waiting for the expensive seek().  The actual scene scrub
        # is deferred to update() so it runs at most once per frame.
        self._tl_playhead_t  = _clamp(t, 0.0, dur)
        self._scrub_pending  = True
        self._tl_auto_scroll = self._calc_tl_auto_scroll(mx)

    def _calc_tl_auto_scroll(self, mouse_x):
        """Return px/sec scroll speed based on how close mouse_x is to the
        left/right edge of the timeline time area.  Positive = scroll right."""
        tl          = self._tl_panel_rect()
        label_end_x = tl.x + _TL_LABEL_W
        edge_zone   = 60   # px from edge that triggers auto-scroll
        if mouse_x > tl.right - edge_zone:
            return ((mouse_x - (tl.right - edge_zone)) / edge_zone) * 400
        if mouse_x < label_end_x + edge_zone:
            return -((label_end_x + edge_zone - mouse_x) / edge_zone) * 400
        return 0.0

    def _on_click(self, pos):
        # Portrait dropdown: consume click before anything else
        if self._portrait_dropdown_open:
            if self._portrait_dropdown_rect and self._portrait_dropdown_rect.collidepoint(pos):
                mx2, my2 = pos
                item_h   = 22
                rel_y    = my2 - self._portrait_dropdown_rect.y - 4
                item_idx = rel_y // item_h
                actual   = item_idx + self._portrait_dropdown_scroll
                if 0 <= actual < len(self._portrait_dropdown_items):
                    self._form_params['portrait'] = self._portrait_dropdown_items[actual]
            self._portrait_dropdown_open = False
            return None

        mx, my = pos

        # Commit an in-progress duration edit when the user clicks elsewhere
        if self._duration_focus:
            dur_rect = self._btns.get('dur_field')
            if not (dur_rect and dur_rect.collidepoint(mx, my)):
                self._commit_duration()
                self._duration_focus = False

        if self.view == 'list':
            tf = pygame.Rect(80, 125, self.screen_width - 280, 30)
            if tf.collidepoint(mx, my):
                self._new_name_focus = True
                return None

        for name, rect in self._btns.items():
            if rect.collidepoint(mx, my):
                return self._handle_btn(name)

        if self.view == 'edit':
            if self._vp_rect.collidepoint(mx, my):
                vx = mx - self._vp_rect.x
                vy = my - self._vp_rect.y
                # Correct world coords: viewport pixel → base-scale pixel (÷zoom), then → world (÷RS)
                wx = vx / (RENDER_SCALE * self._vp_zoom) + self.camera.x / RENDER_SCALE
                wy = vy / (RENDER_SCALE * self._vp_zoom) + self.camera.y / RENDER_SCALE
                return self._on_viewport_click(wx, wy)

            tl = self._tl_panel_rect()
            if tl.collidepoint(mx, my):
                self._on_tl_click(mx, my, tl)
                return None

        if self.view == 'list':
            self._on_list_click(mx, my)

        return None

    def _on_viewport_click(self, wx, wy):
        if self._pick_mode == 'pick_pan_to_start':
            self._form_params['start_x'] = f'{wx:.1f}'
            self._form_params['start_y'] = f'{wy:.1f}'
            self._pick_mode = None
            return None
        if self._pick_mode in ('pick_pan_to', 'pick_snap_to',
                               'pick_move_to', 'pick_fly_to', 'pick_teleport',):
            self._form_params['x'] = f'{wx:.1f}'
            self._form_params['y'] = f'{wy:.1f}'
            self._pick_mode = None
            return None
        if self._pick_mode == 'pick_actor':
            self._place_actor_def['x'] = round(wx, 1)
            self._place_actor_def['y'] = round(wy, 1)
            self._push_undo()
            self.cutscene_data['actors'].append(dict(self._place_actor_def))
            self._pick_mode  = None
            self.unsaved     = True
            self._actor_form = False
            self._runtime    = None  # runtime.actors is stale; force full rebuild
            return None

        # ── Actor drag: hit-test each actor's initial position ────────────────
        # Only when not playing and the runtime is idle (showing the static
        # actor markers drawn by _draw_actor_marker).
        if not self._playing and not self._runtime and self.cutscene_data:
            actors = self.cutscene_data.get('actors', [])
            hit_radius = 20.0  # world-unit tolerance (≈ one sprite body)
            for i, actor in enumerate(actors):
                ax = float(actor.get('x', 0))
                ay = float(actor.get('y', 0))
                if abs(wx - ax) <= hit_radius and abs(wy - ay) <= hit_radius:
                    self._push_undo()
                    self._actor_drag_idx      = i
                    self._actor_drag_offset_x = ax - wx
                    self._actor_drag_offset_y = ay - wy
                    self._actor_sel           = i
                    return None

        return None

    def _on_tl_click(self, mx, my, tl):
        """Handle clicks in the AE-style graphical timeline."""
        content_top  = tl.y + _TL_HDR_H
        ruler_bottom = content_top + _TL_RULER_H
        label_end_x  = tl.x + _TL_LABEL_W
        time_area_x  = label_end_x

        # Click in ruler → start playhead drag
        if content_top <= my < ruler_bottom and mx >= time_area_x:
            t = (mx - time_area_x + self._tl_scroll_x) / self._tl_time_zoom
            dur = self.cutscene_data.get('duration', 10.0) if self.cutscene_data else 10.0
            self._scrub_to(_clamp(t, 0.0, dur))
            self._tl_play_drag  = True
            return

        # Click in a track row → find nearest keyframe on that track
        tracks = self._tl_tracks()
        for i, (label, color, target) in enumerate(tracks):
            row_y = ruler_bottom + i * _TL_ROW_H
            if row_y <= my < row_y + _TL_ROW_H:
                if not self.cutscene_data:
                    return
                all_actions = self.cutscene_data.get('actions', [])
                best_idx   = -1
                best_dist  = 12  # pixel hit tolerance
                for ai, action in enumerate(all_actions):
                    if action.get('target') != target:
                        continue
                    kf_x = time_area_x + action['time'] * self._tl_time_zoom - self._tl_scroll_x
                    dist = abs(mx - kf_x)
                    if dist < best_dist:
                        best_dist = dist
                        best_idx  = ai

                if best_idx >= 0:
                    self._tl_sel = best_idx
                    self._open_action_form(best_idx)
                    # Begin drag — store the sub-pixel offset so the keyframe
                    # doesn't jump on the very first motion event.
                    clicked_t = (mx - time_area_x + self._tl_scroll_x) / self._tl_time_zoom
                    self._push_undo()
                    self._kf_drag_idx    = best_idx
                    self._kf_drag_offset = all_actions[best_idx]['time'] - clicked_t
                else:
                    self._tl_sel      = -1
                    self._kf_drag_idx = -1
                    self._form_active = False
                return

    def _on_list_click(self, mx, my):
        items_top = 200
        row_h     = 38
        for i, name in enumerate(self._files):
            ry = items_top + i * row_h - self._list_scroll
            r  = pygame.Rect(60, ry, self.screen_width - 120, row_h - 4)
            if r.collidepoint(mx, my):
                self._list_sel = i
                return

    def _on_scroll(self, event):
        dy = event.y
        mx, my = pygame.mouse.get_pos()

        # Portrait dropdown scroll — consumed before any other handler so
        # scrolling inside the dropdown never also zooms/pans the viewport.
        if (self._portrait_dropdown_open and self._portrait_dropdown_rect
                and self._portrait_dropdown_rect.collidepoint(mx, my)):
            max_scroll = max(0, len(self._portrait_dropdown_items) - 8)
            self._portrait_dropdown_scroll = max(0, min(max_scroll,
                self._portrait_dropdown_scroll - dy))
            return

        if self.view == 'list':
            self._list_scroll = _clamp(self._list_scroll - dy * 20, 0, 9999)
            return

        # ── Viewport scroll → zoom (no modifier needed) ───────────────────────
        if self.view == 'edit' and self._vp_rect.collidepoint(mx, my):
            # Keep the world point under the mouse fixed as we zoom
            vx = mx - self._vp_rect.x
            vy = my - self._vp_rect.y
            # World coords of the mouse cursor before zoom (correct formula)
            pivot_wx = vx / (RENDER_SCALE * self._vp_zoom) + self.camera.x / RENDER_SCALE
            pivot_wy = vy / (RENDER_SCALE * self._vp_zoom) + self.camera.y / RENDER_SCALE
            # Apply zoom
            self._vp_zoom = _clamp(
                self._vp_zoom * (1.12 ** dy),
                self._vp_zoom_min, self._vp_zoom_max)
            # Re-anchor: camera.x = (pivot_wx - vx/(RS*zoom)) * RS
            self.camera.x = (pivot_wx - vx / (RENDER_SCALE * self._vp_zoom)) * RENDER_SCALE
            self.camera.y = (pivot_wy - vy / (RENDER_SCALE * self._vp_zoom)) * RENDER_SCALE
            self._clamp_camera()
            return

        # ── Timeline scroll ────────────────────────────────────────────────────
        tl = self._tl_panel_rect()
        if tl.collidepoint(mx, my):
            keys = pygame.key.get_pressed()
            if keys[pygame.K_LCTRL] or keys[pygame.K_RCTRL]:
                pivot_t = (mx - tl.x - _TL_LABEL_W + self._tl_scroll_x) / self._tl_time_zoom
                self._tl_time_zoom = _clamp(
                    self._tl_time_zoom * (1.12 ** dy),
                    self._tl_zoom_min, self._tl_zoom_max)
                self._tl_scroll_x = pivot_t * self._tl_time_zoom - (mx - tl.x - _TL_LABEL_W)
                self._tl_scroll_x = max(0.0, self._tl_scroll_x)
            else:
                self._tl_scroll_x = max(0.0, self._tl_scroll_x - dy * 30)

        lp = self._left_panel_rect()
        if lp.collidepoint(mx, my):
            self._left_scroll = _clamp(
                getattr(self, '_left_scroll', 0) - dy * 20, 0, 9999)

    # ══════════════════════════════════════════════════════════════════════════
    # Button handler
    # ══════════════════════════════════════════════════════════════════════════

    def _handle_btn(self, name):
        """Dispatch a named button click.

        Every clickable rect registers itself in self._btns during draw().
        _on_click() looks up the hit rect's name and calls this method.
        All branches return None so callers can always forward the return value.
        """
        # ── Text-field clicks — the field registered its own metadata ──────────
        # _field_meta maps the field's btn name → (form_key, actor_key) so we
        # know which buffer to route keyboard events to.
        if name.startswith('_field_'):
            meta = self._field_meta.get(name, (None, None))
            form_key, actor_key = meta
            if form_key == 'time':
                self._form_focus  = 'time'
                self._actor_focus = None
            elif form_key:
                self._form_focus  = form_key
                self._actor_focus = None
            elif actor_key:
                self._actor_focus = actor_key
                self._form_focus  = None
            return None

        if name == 'close':
            self.active = False
            return None

        if name == 'list_new':
            n = self._new_name_buf.strip()
            if not n:
                self._list_msg = 'Enter a name first.'
                return None
            if os.path.exists(_cutscene_path(n)):
                self._list_msg = 'A cutscene with that name already exists.'
                return None
            self._create_and_open(n)
            return None

        if name == 'list_open':
            if 0 <= self._list_sel < len(self._files):
                self._load_cutscene(self._files[self._list_sel])
            return None

        if name == 'list_delete':
            if 0 <= self._list_sel < len(self._files):
                path = _cutscene_path(self._files[self._list_sel])
                if os.path.exists(path):
                    os.remove(path)
                self._refresh_file_list()
                self._list_sel = -1
            return None

        if name == 'back':
            if self._playing:
                self._stop_preview()
            if self.unsaved:
                # _save_cutscene internally calls _save_viewport_state too
                self._save_cutscene()
            else:
                self._save_viewport_state()
            self.view = 'list'
            self._refresh_file_list()
            return None

        if name == 'save':
            self._save_cutscene()
            return None

        if name == 'dur_field':
            # Open the inline duration editor in the top bar
            if self.cutscene_data:
                self._duration_buf   = str(self.cutscene_data.get('duration', 10.0))
                self._duration_focus = True
                self._form_focus     = None
                self._actor_focus    = None
            return None

        if name == 'play':
            if self._playing:
                self._stop_preview()
            else:
                self._start_preview()
            return None

        if name == 'room_prev':
            self._cycle_room(-1)
            return None
        if name == 'room_next':
            self._cycle_room(1)
            return None

        if name == 'actor_add':
            # Pre-fill the actor form with a sensible default ID
            self._actor_form      = True
            self._actor_focus     = None
            self._actor_id_buf    = f'actor_{len(self.cutscene_data["actors"])}'
            self._actor_etype_buf = 'tiger_bandit'
            return None

        if name == 'actor_del':
            actors = self.cutscene_data.get('actors', [])
            if 0 <= self._actor_sel < len(actors):
                rid = actors[self._actor_sel]['id']
                self._push_undo()
                actors.pop(self._actor_sel)
                # Also delete every action that targets this actor
                self.cutscene_data['actions'] = [
                    a for a in self.cutscene_data['actions']
                    if a.get('target') != rid
                ]
                self._actor_entities.pop(rid, None)  # drop cached sprite
                self._actor_sel = -1
                self.unsaved    = True
                self._runtime   = None  # runtime.actors is stale; force full rebuild
            return None

        if name == 'actor_place_confirm':
            # Build the actor def and enter pick mode so the next viewport
            # click sets the spawn position.
            atype_names = ['enemy', 'boss', 'player']
            atype = atype_names[self._actor_type_idx % 3]
            actor_def = {
                'id':      self._actor_id_buf.strip() or f'actor_{len(self.cutscene_data["actors"])}',
                'type':    atype,
                'variant': 'default',
                'x': 100.0, 'y': 100.0,
            }
            if atype in ('enemy', 'boss'):
                actor_def['enemy_type'] = self._actor_etype_buf.strip()
            self._place_actor_def = actor_def
            self._pick_mode = 'pick_actor'
            return None

        if name == 'actor_type_prev':
            self._actor_type_idx = (self._actor_type_idx - 1) % 3
            return None
        if name == 'actor_type_next':
            self._actor_type_idx = (self._actor_type_idx + 1) % 3
            return None

        if name.startswith('actor_row_'):
            # Selecting an actor row in the left panel clears the inspector form
            self._actor_sel   = int(name.split('_')[-1])
            self._form_active = False
            return None

        if name == 'tl_add':
            self._open_new_action_form()
            return None

        if name == 'tl_del':
            actions = self.cutscene_data.get('actions', [])
            if 0 <= self._tl_sel < len(actions):
                self._push_undo()
                actions.pop(self._tl_sel)
                self._tl_sel      = _clamp(self._tl_sel - 1, -1, len(actions) - 1)
                self._form_active = False
                self.unsaved      = True
            return None

        if name == 'tl_dup':
            actions = self.cutscene_data.get('actions', [])
            if 0 <= self._tl_sel < len(actions):
                import copy
                self._push_undo()
                dup = copy.deepcopy(actions[self._tl_sel])
                # Nudge the duplicate forward 0.1 s so it doesn't sit on top
                # of the original in the timeline and is immediately visible.
                dup['time'] = round(dup['time'] + 0.1, 3)
                actions.append(dup)
                actions.sort(key=lambda a: a['time'])
                self.unsaved = True
            return None

        if name == 'form_target_prev':
            self._cycle_form_target(-1)
            return None
        if name == 'form_target_next':
            self._cycle_form_target(1)
            return None
        if name == 'form_type_prev':
            self._cycle_form_type(-1)
            return None
        if name == 'form_type_next':
            self._cycle_form_type(1)
            return None

        if name == 'form_commit':
            self._commit_form()
            return None
        if name == 'form_cancel':
            self._form_active = False
            self._form_focus  = None
            return None

        if name in ('pick_pan_to', 'pick_snap_to', 'pick_move_to',
                    'pick_teleport', 'pick_pan_to_start'):
            # Enter pick mode — the next viewport click writes coords into the form
            self._pick_mode = name
            return None

        if name.startswith('cycle_'):
            if name == 'cycle_portrait':
                self._open_portrait_dropdown(name)
                return None
            self._cycle_dropdown(name)
            return None

        if name in ('room_group_prev', 'room_group_next'):
            self._cycle_room_group(-1 if name.endswith('prev') else 1)
            return None
        if name in ('room_name_prev', 'room_name_next'):
            self._cycle_room_in_group(-1 if name.endswith('prev') else 1)
            return None

        # ── Timeline zoom buttons ──────────────────────────────────────────────
        if name == 'tl_zoom_in':
            self._tl_time_zoom = min(self._tl_zoom_max, self._tl_time_zoom * 1.25)
            return None
        if name == 'tl_zoom_out':
            self._tl_time_zoom = max(self._tl_zoom_min, self._tl_time_zoom / 1.25)
            return None
        if name == 'vp_zoom_reset':
            self._vp_zoom = 1.0
            return None

        return None

    # ══════════════════════════════════════════════════════════════════════════
    # Form helpers
    # ══════════════════════════════════════════════════════════════════════════

    def _open_new_action_form(self):
        self._form_active   = True
        self._form_new      = True
        self._form_focus    = None
        self._form_time_buf = f'{self._tl_playhead_t:.2f}'
        self._set_form_target('camera')
        self._set_form_type('pan_to')

    def _open_action_form(self, idx):
        actions = self.cutscene_data.get('actions', [])
        if not (0 <= idx < len(actions)):
            return
        a = actions[idx]
        self._form_active   = True
        self._form_new      = False
        self._form_focus    = None
        self._form_time_buf = str(a.get('time', 0.0))
        self._set_form_target(a.get('target', 'camera'))
        self._set_form_type(a.get('type', 'pan_to'))
        for key, _label, _hint in _ACTION_PARAMS.get(self._form_type, []):
            val = a.get('params', {}).get(key, '')
            self._form_params[key] = str(val) if val != '' else self._default_param(key)
        # Sync the room-group browser to whichever group the saved room belongs to.
        if self._form_type == 'change_room':
            rname = self._form_params.get('room_name', '')
            room  = self.room_manager.get_room_by_name(rname) if rname else None
            self._form_room_group = room.group if room else ''

    def _set_form_target(self, target):
        self._form_target = target
        actors = self.cutscene_data.get('actors', []) if self.cutscene_data else []
        all_targets = ['camera', 'screen', 'room'] + [a['id'] for a in actors]
        self._form_target_idx = all_targets.index(target) if target in all_targets else 0
        self._reset_form_params()

    def _set_form_type(self, atype):
        self._form_type = atype
        self._reset_form_params()

    def _cycle_form_target(self, delta):
        actors = self.cutscene_data.get('actors', []) if self.cutscene_data else []
        all_targets = ['camera', 'screen', 'room'] + [a['id'] for a in actors]
        self._form_target_idx = (self._form_target_idx + delta) % len(all_targets)
        self._form_target = all_targets[self._form_target_idx]
        if self._form_target == 'camera':
            self._set_form_type('pan_to')
        elif self._form_target == 'screen':
            self._set_form_type('fade_in')
        elif self._form_target == 'room':
            self._set_form_type('change_room')
        else:
            self._set_form_type('set_animation')

    def _cycle_form_type(self, delta):
        if self._form_target == 'camera':
            pool = _CAMERA_ACTIONS
        elif self._form_target == 'screen':
            pool = _SCREEN_ACTIONS
        elif self._form_target == 'room':
            pool = _ROOM_ACTIONS
        else:
            pool = _ACTOR_ACTIONS
        idx = pool.index(self._form_type) if self._form_type in pool else 0
        self._form_type = pool[(idx + delta) % len(pool)]
        self._reset_form_params()

    def _open_portrait_dropdown(self, btn_name):
        import os, glob
        portraits_dir = os.path.join('assets', 'portraits')
        files = sorted(glob.glob(os.path.join(portraits_dir, '*.png')))
        keys  = [''] + [os.path.splitext(os.path.basename(f))[0] for f in files]
        self._portrait_dropdown_items  = keys
        self._portrait_dropdown_scroll = 0
        btn_rect = self._btns.get(btn_name)
        if btn_rect:
            item_h  = 22
            visible = min(8, len(keys))
            pop_h   = visible * item_h + 8
            pop_w   = max(btn_rect.width, 160)
            self._portrait_dropdown_rect = pygame.Rect(
                btn_rect.x, btn_rect.bottom + 2, pop_w, pop_h)
        self._portrait_dropdown_open = True

    def _get_actor_anim_states(self, actor_def):
        """Return a sorted list of animation state names for *actor_def*.

        Gets or creates the live entity, asks its sprite object which folder
        it loaded from, then returns every .png stem in that folder — so any
        file you drop in (walk2.png, kiblast3.png, …) is instantly available.
        Falls back to the hardcoded lists only if the entity can't be created.
        """
        import os, glob as _glob

        entity = None
        if actor_def is not None:
            entity = self._actor_entities.get(actor_def.get('id', ''))
            if entity is None:
                try:
                    entity = self._entity_factory(actor_def)
                    if entity:
                        self._actor_entities[actor_def['id']] = entity
                except Exception:
                    pass

        folder = self._actor_sprite_folder(entity)
        if folder and os.path.isdir(folder):
            states = sorted(
                os.path.splitext(os.path.basename(f))[0]
                for f in _glob.glob(os.path.join(folder, '*.png'))
            )
            if states:
                return states

        # Fallback when the entity or its sprite folder can't be found.
        actor_type = actor_def.get('type', 'enemy') if actor_def else 'player'
        return list(_PLAYER_STATES) if actor_type == 'player' else list(_ENEMY_STATES)

    def _actor_sprite_folder(self, entity):
        """Return the sprite sheet folder by reading it directly from the sprite object.

        Tries every attribute name sprite systems commonly use to store their
        base directory. Returns None if the folder can't be determined.
        """
        if entity is None:
            return None
        sprite = getattr(entity, 'sprite', None)
        if sprite is None:
            return None
        for attr in ('sprite_dir', 'folder', 'base_path', 'base_dir',
                     'sheet_dir', '_sprite_dir', '_folder', '_base_path'):
            val = getattr(sprite, attr, None)
            if isinstance(val, str) and val:
                return val
        return None

    def _cycle_dropdown(self, name):
        field = name[len('cycle_'):]
        buf   = self._form_params.get(field, '')
        if field in ('state', 'anim_state'):
            actor_def = None
            if self._form_target not in ('camera', 'screen', 'room'):
                for a in (self.cutscene_data or {}).get('actors', []):
                    if a['id'] == self._form_target:
                        actor_def = a
                        break
            pool = self._get_actor_anim_states(actor_def)
        elif field == 'direction' and self._form_type == 'scroll':
            pool = ['right', 'left', 'down', 'up',
                    'down_right', 'down_left', 'up_right', 'up_left']
        elif field == 'direction':
            # For move_to, the first slot is '' (auto-derive from movement vector).
            # For all other actions direction must be explicit.
            if self._form_type in ('move_to', 'fly_to'):
                pool = ['', 'down', 'up', 'left', 'right']
            else:
                pool = _DIRECTIONS
        elif field == 'mode' and self._form_type == 'invert':
            pool = _INVERT_MODES
        elif field == 'weather_type':
            import os, glob as _glob
            weather_dir = os.path.join('assets', 'weather')
            try:
                files = sorted(_glob.glob(os.path.join(weather_dir, '*.png')))
                pool  = [os.path.splitext(os.path.basename(f))[0] for f in files]
            except Exception:
                pool = []
            if not pool:
                pool = ['rain', 'snow', 'fog']
        else:
            return
        idx = pool.index(buf) if buf in pool else 0
        self._form_params[field] = pool[(idx + 1) % len(pool)]

    def _reset_form_params(self):
        self._form_params = {}
        for key, _label, _hint in _ACTION_PARAMS.get(self._form_type, []):
            self._form_params[key] = self._default_param(key)
        # move_to direction defaults to '' (auto-derive from movement vector),
        # overriding the 'down' fallback from _default_param.
        if self._form_type in ('move_to', 'fly_to'):
            self._form_params.setdefault('direction', '')
            self._form_params['direction'] = ''
        # For camera positional actions (pan_to / snap_to), override the x/y
        # defaults with the end position of the most recent preceding camera
        # action so each new action starts where the last one left off.
        if (self._form_target == 'camera'
                and self._form_type in ('pan_to', 'snap_to')
                and self.cutscene_data):
            ex, ey = self._last_camera_end_xy()
            if ex is not None:
                self._form_params['x'] = f'{ex:.1f}'
                self._form_params['y'] = f'{ey:.1f}'

    def _last_camera_end_xy(self):
        """Return (x, y) of the end position of the most recent camera
        pan_to / snap_to action whose time <= the current form time.
        Falls back to (None, None) if no such action exists.
        """
        try:
            form_t = float(self._form_time_buf)
        except (ValueError, AttributeError):
            form_t = float('inf')  # no time set yet — consider all actions
        best_t = -1.0
        best_x = best_y = None
        for action in (self.cutscene_data or {}).get('actions', []):
            if action.get('target') != 'camera':
                continue
            atype = action.get('type', '')
            if atype not in ('pan_to', 'snap_to'):
                continue
            t = action.get('time', 0.0)
            if t > form_t:  # only look at actions before current form time
                continue
            p = action.get('params', {})
            x = p.get('x')
            y = p.get('y')
            if x is None or y is None:
                continue
            if t >= best_t:
                best_t = t
                best_x = float(x)
                best_y = float(y)
        return best_x, best_y

    def _default_param(self, key):
        base = {
            'x': '100.0', 'y': '80.0', 'duration': '1.0',
            'intensity': '8', 'state': 'idle', 'direction': 'down',
            'anim_state': 'walk', 'portrait': '', 'text': '',
            'weather_type': 'rain', 'speed': '120.0', 'alpha': '-1',
            'room_name': '',
        }
        # scroll uses a much slower default speed than weather
        return base.get(key, '')

    def _commit_form(self):
        try:
            t = float(self._form_time_buf)
        except ValueError:
            t = 0.0

        params = {}
        for key, _label, hint in _ACTION_PARAMS.get(self._form_type, []):
            raw = self._form_params.get(key, '')
            if raw == '':
                # Empty optional fields are omitted so the runtime treats them
                # as unset via .get(key, None).
                continue
            try:
                if hint == 'float':   params[key] = float(raw)
                elif hint == 'int':   params[key] = int(raw)
                else:                 params[key] = raw
            except ValueError:
                params[key] = raw

        action = {'time': t, 'target': self._form_target,
                  'type': self._form_type, 'params': params}
        actions = self.cutscene_data.setdefault('actions', [])
        self._push_undo()
        if self._form_new:
            actions.append(action)
        else:
            if 0 <= self._tl_sel < len(actions):
                actions[self._tl_sel] = action

        actions.sort(key=lambda a: a['time'])
        self._tl_sel = next(
            (i for i, a in enumerate(actions)
             if a['time'] == t and a['type'] == self._form_type), 0)
        self._form_active = False
        self._form_focus  = None
        self.unsaved      = True

    # ══════════════════════════════════════════════════════════════════════════
    # Viewport-state persistence  (per-cutscene camera position + zoom)
    # ══════════════════════════════════════════════════════════════════════════

    def _save_viewport_state(self):
        """Persist current camera position and zoom for the open cutscene."""
        if not self.cutscene_name:
            return
        try:
            _ensure_dir()
            try:
                with open(_EDITOR_VIEWPORTS, 'r') as f:
                    db = json.load(f)
            except (OSError, json.JSONDecodeError):
                db = {}
            db[self.cutscene_name] = {
                'cam_x': self.camera.x,
                'cam_y': self.camera.y,
                'zoom':  self._vp_zoom,
            }
            with open(_EDITOR_VIEWPORTS, 'w') as f:
                json.dump(db, f, indent=2)
        except OSError:
            pass   # non-fatal; viewport just resets next open

    def _restore_viewport_state(self):
        """Restore saved camera position and zoom for the open cutscene.

        Falls back to _reset_camera_for_room() when no saved state exists.
        """
        if not self.cutscene_name:
            return
        try:
            with open(_EDITOR_VIEWPORTS, 'r') as f:
                db = json.load(f)
            state = db.get(self.cutscene_name)
            if state:
                self.camera.x  = float(state.get('cam_x', self.camera.x))
                self.camera.y  = float(state.get('cam_y', self.camera.y))
                self._vp_zoom  = float(state.get('zoom',  self._vp_zoom))
                self._clamp_camera()
                return
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            pass
        # No saved state → fall back to centering on the room
        self._reset_camera_for_room()

    # ══════════════════════════════════════════════════════════════════════════
    # File management
    # ══════════════════════════════════════════════════════════════════════════

    def _refresh_file_list(self):
        _ensure_dir()
        try:
            self._files = sorted(
                f[:-5] for f in os.listdir(_CUTSCENE_DIR) if f.endswith('.json'))
        except OSError:
            self._files = []
        self._list_msg = ''

    def _create_and_open(self, name):
        _ensure_dir()
        rooms = [r.name for r in self.room_manager.rooms
                 if not getattr(r, 'is_transient', False)]
        data = {'id': name, 'room': rooms[0] if rooms else '',
                'duration': 10.0, 'actors': [], 'actions': []}
        with open(_cutscene_path(name), 'w') as f:
            json.dump(data, f, indent=2)
        self.cutscene_name  = name
        self.cutscene_data  = data
        self.unsaved        = False
        self._tl_sel        = -1
        self._actor_sel     = -1
        self._form_active   = False
        self._actor_form    = False
        self._actor_entities.clear()
        self._undo_stack.clear()
        self._redo_stack.clear()
        self.view           = 'edit'
        self._new_name_focus = False
        self._duration_buf   = str(data.get('duration', 10.0))
        self._duration_focus = False
        # Centre the viewport on the room
        self._reset_camera_for_room()

    def _load_cutscene(self, name):
        """Load a cutscene JSON file and switch to the edit view.

        Clears all transient state (selections, undo history, cached sprites)
        so the editor starts fresh for the new file.
        """
        try:
            with open(_cutscene_path(name), 'r') as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            self._list_msg = f'Error loading: {e}'
            return
        self.cutscene_name  = name
        self.cutscene_data  = data
        self.unsaved        = False
        self._tl_sel        = -1
        self._actor_sel     = -1
        self._form_active   = False
        self._actor_form    = False
        self._actor_entities.clear()
        self._undo_stack.clear()
        self._redo_stack.clear()
        self.view           = 'edit'
        self._new_name_focus = False
        self._runtime       = None   # force fresh runtime for new cutscene
        self._stop_preview()
        self._duration_buf   = str(data.get('duration', 10.0))
        self._duration_focus = False
        # Drop any cached tile data so the new room reloads from disk
        te = getattr(self.room_editor, 'tileset_editor', None)
        if te is not None and hasattr(te, 'room_tiles'):
            te.room_tiles.pop(data.get('room', ''), None)
        # Restore saved viewport (falls back to centring on the room)
        self._restore_viewport_state()

    def _save_cutscene(self):
        """Write cutscene_data to disk and persist the viewport state."""
        if not self.cutscene_data:
            return
        _ensure_dir()
        with open(_cutscene_path(self.cutscene_name), 'w') as f:
            json.dump(self.cutscene_data, f, indent=2)
        self.unsaved     = False
        self._autosave_t = 0.0   # reset the autosave debounce timer
        self._save_viewport_state()

    # ══════════════════════════════════════════════════════════════════════════
    # Preview / entity factory
    # ══════════════════════════════════════════════════════════════════════════

    def _start_preview(self):
        """Begin playing from the current playhead position.

        Reuses an existing runtime if one is alive (created during scrubbing)
        to avoid a full rebuild every time the user presses Play.
        Snaps the camera to the correct starting position before handing over
        so there's no one-frame jump when playback begins.
        """
        if not self.cutscene_data:
            return
        try:
            from core.cutscene_runtime import CutsceneRuntime
            if self._runtime is None:
                self._runtime = CutsceneRuntime(
                    self.cutscene_data, self.camera, self._entity_factory,
                    dialogue_box=self.dialogue_box)
            self._runtime.seek(self._tl_playhead_t)
            # Snap the camera to the correct start position so there is no
            # one-frame jump when playback begins (same logic as _scrub_to).
            from config.settings import RENDER_SCALE
            ct   = self._runtime.camera_target
            room = self._get_current_room()
            w = (room.width  if room else 10000) * RENDER_SCALE
            h = (room.height if room else 10000) * RENDER_SCALE
            self.camera.x = ct.x * RENDER_SCALE - self.camera.screen_width  // 2
            self.camera.y = ct.y * RENDER_SCALE - self.camera.screen_height // 2
            self.camera.x = max(0, min(self.camera.x, w - self.camera.screen_width))
            self.camera.y = max(0, min(self.camera.y, h - self.camera.screen_height))
        except Exception as e:
            print(f'[CutsceneEditor] _start_preview error: {e}')
            import traceback
            traceback.print_exc()
            self._runtime = None
            return
        self._playing = True
        self._last_ticks = pygame.time.get_ticks()
        self._form_active = False

    # ══════════════════════════════════════════════════════════════════════════
    # Undo / Redo
    # ══════════════════════════════════════════════════════════════════════════

    _UNDO_LIMIT = 50

    def _push_undo(self):
        """Snapshot current cutscene_data onto the undo stack before a mutation."""
        if self.cutscene_data is None:
            return
        import copy
        self._undo_stack.append(copy.deepcopy(self.cutscene_data))
        if len(self._undo_stack) > self._UNDO_LIMIT:
            self._undo_stack.pop(0)
        # Any new edit clears the redo history.
        self._redo_stack.clear()

    def _undo(self):
        """Restore the snapshot at the top of the undo stack."""
        if not self._undo_stack or self.cutscene_data is None:
            return
        import copy
        self._redo_stack.append(copy.deepcopy(self.cutscene_data))
        self.cutscene_data = self._undo_stack.pop()
        self._after_history_jump()

    def _redo(self):
        """Reapply the snapshot at the top of the redo stack."""
        if not self._redo_stack or self.cutscene_data is None:
            return
        import copy
        self._undo_stack.append(copy.deepcopy(self.cutscene_data))
        self.cutscene_data = self._redo_stack.pop()
        self._after_history_jump()

    def _after_history_jump(self):
        """Tidy up editor state after an undo or redo."""
        self.unsaved      = True
        self._tl_sel      = -1
        self._form_active = False
        self._form_focus  = None
        self._actor_sel   = -1
        self._runtime     = None   # stale — rebuild on next scrub/play
        if self._playing:
            self._stop_preview()

    def _stop_preview(self):
        """Halt live playback but intentionally keep the runtime alive.

        Keeping the runtime means scrubbing (playhead drag) still works
        immediately after stopping without paying a full rebuild cost.
        """
        self._playing = False
        # Dismiss any dialogue box so it doesn't stay frozen on screen.
        if self.dialogue_box:
            self.dialogue_box.hide()
        # Clear residual camera shake so it doesn't bleed into the next
        # playback session.  We check both private and public attribute names
        # because Camera implementations have varied across project versions.
        for _attr in ('_shake_intensity', '_shake_duration', '_shake_elapsed',
                      'shake_intensity', 'shake_duration', 'shake_elapsed'):
            if hasattr(self.camera, _attr):
                setattr(self.camera, _attr, 0.0)

    def _entity_factory(self, actor_def):
        """Instantiate a live entity from an actor definition dict.

        Called by CutsceneRuntime during playback/scrubbing and by
        _get_or_create_actor_entity for static viewport previews.
        Returns None on error so callers can fall back to a plain circle.
        """
        atype = actor_def.get('type', 'enemy')
        x, y  = actor_def.get('x', 100), actor_def.get('y', 80)
        try:
            if atype == 'player':
                from entities.player import Player
                return Player(x, y, character=actor_def.get('character', 'goku'),
                              costume=actor_def.get('costume', 'base'))
            elif atype == 'boss':
                from entities.boss_enemy import BossEnemy
                return BossEnemy(x, y, boss_id=actor_def.get('enemy_type', 'pui_pui'),
                                 variant=actor_def.get('variant', 'default'))
            else:
                from entities.enemy import Enemy
                return Enemy(x, y, enemy_type=actor_def.get('enemy_type', 'tiger_bandit'),
                             variant=actor_def.get('variant', 'default'))
        except Exception as ex:
            print(f'[CutsceneEditor] entity_factory error: {ex}')
            return None

    # ══════════════════════════════════════════════════════════════════════════
    # Camera helpers
    # ══════════════════════════════════════════════════════════════════════════

    def _reset_camera_for_room(self):
        """Reset zoom and position the camera at the room's top-left corner.

        We do NOT call _clamp_camera here: for rooms smaller than the viewport,
        that would produce a negative camera.x/y which some rendering paths
        don't handle gracefully.  Clamping happens naturally as the user pans.
        """
        self._vp_zoom = 1.0
        self.camera.x = 0.0
        self.camera.y = 0.0
        room = self._get_current_room()
        self._ensure_room_tiles(room)

    def _clamp_camera(self):
        """Keep the camera inside the room bounds at the current zoom level.

        If the room is smaller than the visible window (common at high zoom),
        we centre on it instead of clamping so the room never disappears off-
        screen into a corner.
        """
        room = self._get_current_room()
        if not room:
            return
        vp   = self._vp_rect
        zoom = self._vp_zoom
        # Full room extent in base-scale pixels
        rw = room.width  * RENDER_SCALE
        rh = room.height * RENDER_SCALE
        # How many base-scale pixels fit in the viewport at this zoom
        win_w = vp.width  / zoom
        win_h = vp.height / zoom
        # Clamp or centre
        if rw <= win_w:
            self.camera.x = (rw - win_w) / 2
        else:
            self.camera.x = _clamp(self.camera.x, 0, rw - win_w)
        if rh <= win_h:
            self.camera.y = (rh - win_h) / 2
        else:
            self.camera.y = _clamp(self.camera.y, 0, rh - win_h)

    def _get_current_room(self):
        """Return the Room object that should be visible at the current playhead.

        Starts with the cutscene's base room, then applies any change_room
        actions whose time is at or before the playhead — so scrubbing the
        timeline shows the correct background, just like the runtime would.
        """
        if not self.cutscene_data:
            return None
        room_name = self.cutscene_data.get('room', '')
        t = self._tl_playhead_t
        for action in self.cutscene_data.get('actions', []):
            if action.get('type') == 'change_room' and action.get('time', 0.0) <= t:
                name = action.get('params', {}).get('room_name', '').strip()
                if name:
                    room_name = name
        return self.room_manager.get_room_by_name(room_name)

    def _ensure_room_tiles(self, room):
        """Guarantee the tileset_editor exists and has tile data for *room*.

        room_editor.tileset_editor is created lazily (only when the room editor
        is opened for the first time).  _draw_viewport_tiles reads room.tiles
        directly for tile positions, but needs te.tileset_manager to look up
        tileset images - so te must exist even if te.room_tiles is not used.

        Fix: if te is None, create it ourselves exactly as room_editor would.
        Then seed te.room_tiles from room.tiles (the authoritative in-memory
        list populated by the room manager when rooms are loaded from disk).
        """
        if not room:
            return

        # -- Ensure tileset_editor exists -------------------------------------
        if getattr(self.room_editor, 'tileset_editor', None) is None:
            try:
                from dev_tools.room_editor.room_editor_tools.tileset_editor import TilesetEditor
                self.room_editor.tileset_editor = TilesetEditor(
                    self.room_editor.screen_width,
                    self.room_editor.screen_height,
                )
            except Exception as e:
                print(f'[CutsceneEditor] Could not init tileset_editor: {e}')
                return

        te = self.room_editor.tileset_editor

        # -- Seed te.room_tiles from the room object --------------------------
        # room.tiles is the canonical list of Tile objects - the same source
        # the room editor copies at _enter_view_room.
        if not te.room_tiles.get(room.name):
            te.room_tiles[room.name] = list(getattr(room, 'tiles', []))

    def _get_or_create_actor_entity(self, actor_def):
        """Return a cached entity for the actor def, creating one if needed."""
        aid = actor_def.get('id', '')
        if aid not in self._actor_entities:
            entity = self._entity_factory(actor_def)
            if entity is not None:
                # Park the entity at its start position and idle animation
                entity.x = float(actor_def.get('x', 100))
                entity.y = float(actor_def.get('y', 80))
                if hasattr(entity, 'sprite') and entity.sprite:
                    entity.sprite.set_animation('idle', 'down')
                self._actor_entities[aid] = entity
        return self._actor_entities.get(aid)

    # ══════════════════════════════════════════════════════════════════════════
    # Panel rect helpers
    # ══════════════════════════════════════════════════════════════════════════

    def _tl_panel_rect(self):
        return pygame.Rect(0, self.screen_height - _BOTTOM_H,
                           self.screen_width, _BOTTOM_H)

    def _left_panel_rect(self):
        return pygame.Rect(0, _TOP_H, _LEFT_W,
                           self.screen_height - _TOP_H - _BOTTOM_H)

    def _right_panel_rect(self):
        return pygame.Rect(self.screen_width - _RIGHT_W, _TOP_H,
                           _RIGHT_W, self.screen_height - _TOP_H - _BOTTOM_H)

    # ══════════════════════════════════════════════════════════════════════════
    # Track list helper
    # ══════════════════════════════════════════════════════════════════════════

    def _tl_tracks(self):
        """Return list of (label, color, target_id) for all tracks."""
        tracks = [
            ('Camera', _CAMERA_COLOR, 'camera'),
            ('Screen', _SCREEN_COLOR, 'screen'),
            ('Room',   _ROOM_COLOR,   'room'),
        ]
        actors = self.cutscene_data.get('actors', []) if self.cutscene_data else []
        for i, a in enumerate(actors):
            tracks.append((a['id'], _ACTOR_COLORS[i % len(_ACTOR_COLORS)], a['id']))
        return tracks

    # ══════════════════════════════════════════════════════════════════════════
    # Drawing — list view
    # ══════════════════════════════════════════════════════════════════════════

    def _draw_list(self, screen):
        """Render the file browser (create / open / delete cutscenes).

        Rebuilds self._btns every frame so hit-testing is always in sync with
        what was actually painted — never cache this dict across frames.
        """
        self._btns = {}
        screen.fill(_C['bg'])
        W, H = self.screen_width, self.screen_height

        t = self.font_title.render('CUTSCENE EDITOR', True, _C['text'])
        screen.blit(t, (W // 2 - t.get_width() // 2, 28))

        self._btns['close'] = self._draw_button(
            screen, W - 104, 18, 84, _BTN_H, '✕  CLOSE', _C['danger'])

        # New cutscene box
        pygame.draw.rect(screen, _C['panel'], (60, 88, W - 120, 76), border_radius=6)
        pygame.draw.rect(screen, _C['border'], (60, 88, W - 120, 76), 1, border_radius=6)
        lbl = self.font_medium.render('New cutscene name:', True, _C['text_dim'])
        screen.blit(lbl, (78, 102))
        tf = pygame.Rect(78, 122, W - 286, 30)
        col = _C['accent'] if self._new_name_focus else _C['border']
        pygame.draw.rect(screen, _C['highlight'], tf, border_radius=4)
        pygame.draw.rect(screen, col, tf, 1, border_radius=4)
        txt = self.font_medium.render(
            self._new_name_buf + ('|' if self._new_name_focus else ''), True, _C['white'])
        screen.blit(txt, (tf.x + 6, tf.y + 6))
        self._btns['list_new'] = self._draw_button(
            screen, W - 198, 122, 118, 30, '+ CREATE', _C['accent2'])

        if self._list_msg:
            screen.blit(self.font_small.render(self._list_msg, True, _C['danger']), (78, 158))

        items_top = 188
        if not self._files:
            msg = self.font_medium.render('No cutscenes yet.  Create one above.', True, _C['text_dim'])
            screen.blit(msg, (W // 2 - msg.get_width() // 2, items_top + 20))
        else:
            row_h = 38
            clip  = pygame.Rect(60, items_top, W - 120, H - items_top - 80)
            screen.set_clip(clip)
            for i, name in enumerate(self._files):
                ry  = items_top + i * row_h - self._list_scroll
                col = _C['sel'] if i == self._list_sel else _C['panel']
                r   = pygame.Rect(60, ry, W - 120, row_h - 4)
                pygame.draw.rect(screen, col, r, border_radius=5)
                pygame.draw.rect(screen, _C['border'], r, 1, border_radius=5)
                # colour bar
                pygame.draw.rect(screen, _ACTOR_COLORS[i % len(_ACTOR_COLORS)],
                                 (r.x, r.y, 4, r.height), border_radius=2)
                nt = self.font_large.render(name, True, _C['text'])
                screen.blit(nt, (r.x + 14, r.y + (row_h - 4 - nt.get_height()) // 2))
            screen.set_clip(None)

        bw, bh, by = 110, 32, H - 55
        self._btns['list_open']   = self._draw_button(screen, W // 2 - 170, by, bw, bh, '▶  OPEN',   _C['accent'])
        self._btns['list_delete'] = self._draw_button(screen, W // 2 -  50, by, bw, bh, '✕  DELETE', _C['danger'])

    # ══════════════════════════════════════════════════════════════════════════
    # Drawing — edit view (AE layout)
    # ══════════════════════════════════════════════════════════════════════════

    def _draw_edit(self, screen):
        """Render the full edit view.

        Draw order is deliberate — each later pass paints over earlier edges:
          1. Viewport (fullscreen room + actors)
          2. Top bar          — overlaps viewport top edge
          3. Left panel       — overlaps viewport left edge
          4. Right panel      — overlaps viewport right edge
          5. Timeline         — overlaps viewport bottom edge
          6. Dialogue box     — floats inside viewport rect
          7. Portrait dropdown — topmost overlay, drawn last
        """
        self._btns       = {}
        self._field_meta = {}
        screen.fill(_C['bg'])

        # ── 1. Viewport (draw first so panels overlay borders) ─────────────────
        vp = self._vp_rect
        vp_surf = pygame.Surface((vp.width, vp.height))
        vp_surf.fill((30, 120, 30))
        self._draw_viewport(vp_surf)
        screen.blit(vp_surf, (vp.x, vp.y))
        pygame.draw.rect(screen, _C['border'], vp, 1)

        # Pick-mode banner
        if self._pick_mode:
            banner = self.font_medium.render(
                f'Click viewport to pick position  [{self._pick_mode}]  (Esc to cancel)',
                True, _C['accent'])
            bg = pygame.Surface((banner.get_width() + 18, banner.get_height() + 10),
                                pygame.SRCALPHA)
            bg.fill((0, 0, 0, 185))
            bx, by = vp.x + 10, vp.y + 10
            screen.blit(bg, (bx - 8, by - 4))
            screen.blit(banner, (bx, by))

        # Actor drag banner — shown while dragging an actor's start position
        if self._actor_drag_idx >= 0 and self.cutscene_data:
            actors = self.cutscene_data.get('actors', [])
            if self._actor_drag_idx < len(actors):
                a = actors[self._actor_drag_idx]
                drag_txt = (f'Dragging  {a.get("id", "actor")}  →  '
                            f'X {a.get("x", 0):.1f}   Y {a.get("y", 0):.1f}')
                banner = self.font_medium.render(drag_txt, True, _C['accent2'])
                bg = pygame.Surface((banner.get_width() + 18, banner.get_height() + 10),
                                    pygame.SRCALPHA)
                bg.fill((0, 0, 0, 185))
                bx, by = vp.x + 10, vp.y + 10
                screen.blit(bg, (bx - 8, by - 4))
                screen.blit(banner, (bx, by))

        # Hover highlight — draw a ring around whichever actor the cursor is
        # near so the user knows it can be dragged (only in static/idle mode).
        if (not self._playing and not self._runtime and not self._pick_mode
                and self._actor_drag_idx < 0 and self.cutscene_data
                and vp.collidepoint(pygame.mouse.get_pos())):
            mx2, my2 = pygame.mouse.get_pos()
            vx2 = mx2 - vp.x
            vy2 = my2 - vp.y
            hwx = vx2 / (RENDER_SCALE * self._vp_zoom) + self.camera.x / RENDER_SCALE
            hwy = vy2 / (RENDER_SCALE * self._vp_zoom) + self.camera.y / RENDER_SCALE
            actors = self.cutscene_data.get('actors', [])
            hit_radius = 20.0
            for i, actor in enumerate(actors):
                ax = float(actor.get('x', 0))
                ay = float(actor.get('y', 0))
                if abs(hwx - ax) <= hit_radius and abs(hwy - ay) <= hit_radius:
                    col = _ACTOR_COLORS[i % len(_ACTOR_COLORS)]
                    # Convert world position → screen position for the ring
                    scr_x = int(vp.x + (ax * RENDER_SCALE - self.camera.x) * self._vp_zoom)
                    scr_y = int(vp.y + (ay * RENDER_SCALE - self.camera.y) * self._vp_zoom)
                    ring_r = int(16 * self._vp_zoom)
                    pygame.draw.circle(screen, col,   (scr_x, scr_y), ring_r, 2)
                    pygame.draw.circle(screen, _C['white'], (scr_x, scr_y), ring_r, 1)
                    hint = self.font_small.render('drag to move', True, col)
                    screen.blit(hint, (scr_x + ring_r + 4,
                                       scr_y - hint.get_height() // 2))
                    break

        # Mouse world-coords readout in viewport (zoom-corrected)
        mx, my = pygame.mouse.get_pos()
        if vp.collidepoint(mx, my):
            vx = mx - vp.x
            vy = my - vp.y
            # Correct: viewport pixel → base-scale pixel (÷zoom) → world (÷RS)
            wx = vx / (RENDER_SCALE * self._vp_zoom) + self.camera.x / RENDER_SCALE
            wy = vy / (RENDER_SCALE * self._vp_zoom) + self.camera.y / RENDER_SCALE
            coord = self.font_mono.render(f'X {wx:.1f}  Y {wy:.1f}', True, _C['text_dim'])
            screen.blit(coord, (vp.x + 8, vp.y + vp.height - 20))

        # ── Ghost / placement preview overlays ─────────────────────────────────
        mx, my = pygame.mouse.get_pos()
        if self._pick_mode and vp.collidepoint(mx, my):
            vx = mx - vp.x
            vy = my - vp.y
            wx = vx / (RENDER_SCALE * self._vp_zoom) + self.camera.x / RENDER_SCALE
            wy = vy / (RENDER_SCALE * self._vp_zoom) + self.camera.y / RENDER_SCALE

            if self._pick_mode == 'pick_actor':
                # Ghost actor circle + crosshair at cursor
                num_actors = len(self.cutscene_data.get('actors', [])) if self.cutscene_data else 0
                ghost_col = _ACTOR_COLORS[num_actors % len(_ACTOR_COLORS)]
                pygame.draw.circle(screen, ghost_col, (mx, my), 13, 2)
                pygame.draw.circle(screen, _C['white'], (mx, my), 13, 1)
                pygame.draw.line(screen, ghost_col, (mx - 18, my), (mx + 18, my), 1)
                pygame.draw.line(screen, ghost_col, (mx, my - 18), (mx, my + 18), 1)
                actor_id = self._place_actor_def.get('id', 'actor')
                lbl = self.font_small.render(actor_id, True, ghost_col)
                screen.blit(lbl, (mx + 16, my - lbl.get_height() // 2))
                coord_lbl = self.font_mono.render(f'({wx:.1f}, {wy:.1f})', True, _C['text_dim'])
                screen.blit(coord_lbl, (mx + 16, my + lbl.get_height() // 2 + 2))

            elif self._pick_mode in ('pick_pan_to', 'pick_snap_to',
                                     'pick_move_to', 'pick_teleport'):
                # Crosshair + world-coord label for camera / move targets
                arm = 22
                pygame.draw.line(screen, _C['accent'], (mx - arm, my), (mx + arm, my), 1)
                pygame.draw.line(screen, _C['accent'], (mx, my - arm), (mx, my + arm), 1)
                pygame.draw.circle(screen, _C['accent'], (mx, my), 5, 1)
                coord_lbl = self.font_mono.render(f'({wx:.1f}, {wy:.1f})', True, _C['accent'])
                screen.blit(coord_lbl, (mx + arm + 4, my - coord_lbl.get_height() // 2))

        # Zoom level readout + reset button (bottom-right of viewport).
        # Click it to snap back to 100%, which matches the in-game zoom level.
        zoom_pct = int(self._vp_zoom * 100)
        is_default_zoom = abs(self._vp_zoom - 1.0) < 0.01
        btn_label = f'{zoom_pct}%  (click to reset)'
        btn_col   = _C['highlight'] if is_default_zoom else _C['accent']
        btn_w     = self.font_small.size(btn_label)[0] + 16
        btn_h     = 22
        btn_x     = vp.right  - btn_w - 6
        btn_y     = vp.bottom - btn_h - 6
        self._btns['vp_zoom_reset'] = self._draw_button(
            screen, btn_x, btn_y, btn_w, btn_h, btn_label, btn_col)

        # ── 2. Top bar ─────────────────────────────────────────────────────────
        self._draw_top_bar(screen)

        # ── 3. Left panel ──────────────────────────────────────────────────────
        lp = self._left_panel_rect()
        pygame.draw.rect(screen, _C['panel2'], lp)
        pygame.draw.line(screen, _C['border'], (lp.right, lp.y), (lp.right, lp.bottom), 1)
        self._draw_left_panel(screen, lp)

        # ── 4. Right panel ─────────────────────────────────────────────────────
        rp = self._right_panel_rect()
        pygame.draw.rect(screen, _C['panel2'], rp)
        pygame.draw.line(screen, _C['border'], (rp.x, rp.y), (rp.x, rp.bottom), 1)
        self._draw_right_panel(screen, rp)

        # ── 5. Timeline ────────────────────────────────────────────────────────
        tl = self._tl_panel_rect()
        pygame.draw.rect(screen, _C['panel'], tl)
        pygame.draw.line(screen, _C['border'], (tl.x, tl.y), (tl.right, tl.y), 1)
        self._draw_timeline(screen, tl)

        # ── 6. Dialogue box ────────────────────────────────────────────────────
        # The DialogueBox positions itself relative to screen_width/screen_height.
        # To keep it inside the viewport (and at the correct relative position),
        # we draw it onto a full-screen-sized offscreen surface, then scale that
        # down to the viewport dimensions and blit only into the viewport rect.
        if self.dialogue_box and getattr(self.dialogue_box, 'active', False):
            _dlg_colors = {'WHITE': (255, 255, 255), 'RED': (220, 60, 60)}
            _dlg_surf = pygame.Surface(
                (self.screen_width, self.screen_height), pygame.SRCALPHA)
            _dlg_surf.fill((0, 0, 0, 0))
            self.dialogue_box.draw(_dlg_surf, _dlg_colors)
            vp = self._vp_rect
            _dlg_scaled = pygame.transform.scale(_dlg_surf, (vp.width, vp.height))
            screen.blit(_dlg_scaled, (vp.x, vp.y))

        # Portrait dropdown overlay — drawn last so it sits on top of everything
        if self._portrait_dropdown_open and self._portrait_dropdown_rect:
            dr       = self._portrait_dropdown_rect
            item_h   = 22
            visible  = min(8, len(self._portrait_dropdown_items))
            cur_val  = self._form_params.get('portrait', '')
            shadow = pygame.Surface((dr.width + 4, dr.height + 4), pygame.SRCALPHA)
            shadow.fill((0, 0, 0, 110))
            screen.blit(shadow, (dr.x + 2, dr.y + 2))
            pygame.draw.rect(screen, _C['panel2'], dr)
            pygame.draw.rect(screen, _C['accent'],  dr, 1)
            scroll = self._portrait_dropdown_scroll
            for i in range(visible):
                actual = i + scroll
                if actual >= len(self._portrait_dropdown_items):
                    break
                key   = self._portrait_dropdown_items[actual]
                label = key if key else '(none)'
                iy    = dr.y + 4 + i * item_h
                ir    = pygame.Rect(dr.x + 1, iy, dr.width - 2, item_h)
                if key == cur_val:
                    pygame.draw.rect(screen, _C['accent'], ir)
                    text_col = _C['bg']
                else:
                    hmx, hmy = pygame.mouse.get_pos()
                    if ir.collidepoint(hmx, hmy):
                        pygame.draw.rect(screen, _C['highlight'], ir)
                    text_col = _C['text']
                lbl = self.font_small.render(label, True, text_col)
                screen.blit(lbl, (ir.x + 6, iy + (item_h - lbl.get_height()) // 2))
            if len(self._portrait_dropdown_items) > visible:
                hint = self.font_small.render('scroll ↕', True, _C['text_dim'])
                screen.blit(hint, (dr.x + dr.width - hint.get_width() - 4,
                                   dr.bottom - hint.get_height() - 2))

    def _draw_viewport(self, surf):
        """Render the room and actors into *surf* with zoom support.

        Strategy: render at RENDER_SCALE into a smaller intermediate surface
        (iw × ih = vw/zoom × vh/zoom), then scale that surface back up to fill
        *surf*.  This means tile drawing code and sprite drawing code are
        unchanged — they still use RENDER_SCALE — and zoom is purely visual.
        """
        zoom = self._vp_zoom
        vw, vh = surf.get_size()

        # Intermediate surface size: how much world we see at base scale
        iw = max(1, int(vw / zoom))
        ih = max(1, int(vh / zoom))
        if not hasattr(self, '_inter_surf') or self._inter_surf.get_size() != (iw, ih):
            self._inter_surf = pygame.Surface((iw, ih)).convert()
        inter = self._inter_surf
        inter.fill((30, 120, 30))

        room = self._get_current_room()
        te   = getattr(self.room_editor, 'tileset_editor', None)

        # Ensure tiles are seeded into the tileset_editor even if the room
        # editor has never opened this room.
        self._ensure_room_tiles(room)

        # camera.x/y are stored at base scale (= world_x * RENDER_SCALE).
        cam_x = int(self.camera.x)
        cam_y = int(self.camera.y)

        if room:
            inter.blit(self._get_baked_tile_surface(room, False), (-cam_x, -cam_y))

        if room and self._vp_zoom >= 0.4 and self._show_grid:
            self._draw_viewport_grid(inter, room)
            # Room boundary outline
            rx = -cam_x
            ry = -cam_y
            pygame.draw.rect(inter, _C['accent'],
                             (rx, ry, room.width * RENDER_SCALE, room.height * RENDER_SCALE), 2)

        # ── Actors + foreground tiles — draw in the correct order so fg tiles
        # (trees, buildings, anything with tile.layer >= 0) occlude actors that
        # stand behind them, exactly as the LayerManager does in normal gameplay.
        #
        # Order:
        #   1. Actors (Y-sorted)      ← drawn BEFORE fg tiles
        #   2. Foreground tile layer  ← drawn AFTER actors, occludes those behind
        #
        # This mirrors the LayerManager split: bg tiles → actors (Y-sorted) → fg tiles.
        if self._runtime:
            # Runtime path — covers both active playback and scrubbing (paused).
            colors = {'WHITE': (255, 255, 255), 'RED': (220, 60, 60)}
            self._runtime.draw_actors(inter, self.camera, colors)  # Y-sorted, before fg
        else:
            # Static editor path — Y-sort actor markers so deeper ones draw behind.
            actors = self.cutscene_data.get('actors', []) if self.cutscene_data else []
            actors_sorted = sorted(enumerate(actors), key=lambda t: t[1].get('y', 0))
            for i, actor in actors_sorted:
                self._draw_actor_marker(inter, actor, i, i == self._actor_sel)

        # Foreground tile layer — drawn after actors so it occludes them correctly.
        if room:
            inter.blit(self._get_baked_tile_surface(room, True), (-cam_x, -cam_y))

        if self._runtime:
            self._runtime.draw_overlay(inter, iw, ih)

        # Scale the intermediate surface to fill the viewport.
        # Cache the output surface — pygame.transform.scale allocates a new
        # Surface on every call, which means a malloc+free every frame at any
        # zoom level other than 1.0.  Reuse a persistent surface instead.
        if zoom == 1.0:
            surf.blit(inter, (0, 0))
        else:
            if (not hasattr(self, '_scaled_surf')
                    or self._scaled_surf.get_size() != (vw, vh)):
                self._scaled_surf = pygame.Surface((vw, vh)).convert()
            pygame.transform.scale(inter, (vw, vh), self._scaled_surf)
            surf.blit(self._scaled_surf, (0, 0))

    def _invalidate_tile_cache(self, room_name=None):
        """Drop baked tile surfaces so they are rebuilt on the next draw.

        Call with no argument to clear everything (e.g. tileset change), or
        pass room_name to invalidate only that room (e.g. after _cycle_room).
        """
        if room_name is None:
            self._vp_tile_surfaces.clear()
        else:
            self._vp_tile_surfaces.pop((room_name, True),  None)
            self._vp_tile_surfaces.pop((room_name, False), None)

    def _get_baked_tile_surface(self, room, foreground):
        """Return the baked tile surface for *room*/*foreground*, building it if needed."""
        key = (room.name, foreground)
        if key not in self._vp_tile_surfaces:
            self._vp_tile_surfaces[key] = self._build_tile_surface(room, foreground)
        return self._vp_tile_surfaces[key]

    def _build_tile_surface(self, room, foreground):
        """Pre-render all tiles for one layer into a single room-sized Surface.

        Mirrors game._build_room_tile_surface so that panning is a single
        camera-offset blit (O(1)) instead of N per-tile blits every frame.
        """
        te = getattr(self.room_editor, 'tileset_editor', None)
        if not te:
            return pygame.Surface((1, 1))
        surf = pygame.Surface(
            (int(room.width * RENDER_SCALE), int(room.height * RENDER_SCALE)),
            pygame.SRCALPHA,
        )
        tiles = list(getattr(room, 'tiles', None) or [])
        if not tiles:
            tiles = te.room_tiles.get(room.name, [])
        for tile in tiles:
            is_fg = tile.layer >= 0
            if foreground != is_fg:
                continue
            tileset = te.tileset_manager.get_tileset(tile.tileset_name)
            if not tileset:
                continue
            scaled = tileset.get_scaled_tile_surface(tile.tile_x, tile.tile_y, RENDER_SCALE)
            if scaled:
                surf.blit(scaled, (int(tile.x * RENDER_SCALE), int(tile.y * RENDER_SCALE)))
        # Convert to display format so every subsequent blit (camera pan,
        # tiled scroll) is hardware-accelerated rather than per-pixel software.
        # Background layer: no transparency needed → convert() (faster, no alpha).
        # Foreground layer: has transparent gaps → convert_alpha().
        return surf.convert() if not foreground else surf.convert_alpha()

    def _draw_viewport_grid(self, surf, room):
        """Draw tile grid onto the intermediate surface using base-scale camera coords."""
        from config.settings import TILE_SIZE
        cx, cy   = int(self.camera.x), int(self.camera.y)
        vw, vh   = surf.get_size()
        grid_col = (40, 140, 40)

        xs = (cx // RENDER_SCALE // TILE_SIZE) * TILE_SIZE
        x  = xs
        while x * RENDER_SCALE - cx <= vw:
            sx = x * RENDER_SCALE - cx
            if 0 <= sx <= vw:
                pygame.draw.line(surf, grid_col, (sx, 0), (sx, vh), 1)
            x += TILE_SIZE

        ys = (cy // RENDER_SCALE // TILE_SIZE) * TILE_SIZE
        y  = ys
        while y * RENDER_SCALE - cy <= vh:
            sy = y * RENDER_SCALE - cy
            if 0 <= sy <= vh:
                pygame.draw.line(surf, grid_col, (0, sy), (vw, sy), 1)
            y += TILE_SIZE

    def _draw_viewport_tiles(self, inter, room, cam_x, cam_y, foreground: bool):
        """Draw tiles for *room* onto the intermediate surface.

        Reads directly from room.tiles (the canonical game-state list, always
        populated from disk) rather than te.room_tiles (only populated when the
        room editor has opened the room at least once).  Falls back to
        te.room_tiles if room.tiles is empty, so tiles painted during the
        current session without saving are also visible.

        Culls against the actual inter surface dimensions instead of the
        tileset_editor's screen_width, which is the full game resolution and
        would incorrectly allow tiles far outside the viewport to be drawn.
        """
        te = getattr(self.room_editor, 'tileset_editor', None)
        if not te:
            return
        iw, ih = inter.get_size()

        tiles = list(getattr(room, 'tiles', None) or [])
        if not tiles:
            # Fallback: tiles painted in the current session (room editor open)
            tiles = te.room_tiles.get(room.name, [])

        for tile in tiles:
            if foreground and tile.layer < 0:
                continue
            if not foreground and tile.layer >= 0:
                continue

            tileset = te.tileset_manager.get_tileset(tile.tileset_name)
            if not tileset:
                continue

            # Use the cached pre-scaled surface to avoid per-frame rescaling
            scaled = tileset.get_scaled_tile_surface(tile.tile_x, tile.tile_y,
                                                     RENDER_SCALE)
            if not scaled:
                continue

            sx = int(tile.x * RENDER_SCALE - cam_x)
            sy = int(tile.y * RENDER_SCALE - cam_y)
            sw = tileset.tile_width  * RENDER_SCALE
            sh = tileset.tile_height * RENDER_SCALE

            # Cull against the actual inter surface size (not the full game screen)
            if -sw <= sx <= iw and -sh <= sy <= ih:
                inter.blit(scaled, (sx, sy))

    def _draw_actor_marker(self, inter_surf, actor, idx, selected):
        """Draw actor onto the intermediate surface.

        Tries to show the real sprite (via a cached entity).  Falls back to a
        coloured circle if the entity/sprite couldn't be created.
        Drawn at base RENDER_SCALE on the intermediate surface; zoom is handled
        by the caller scaling the whole surface afterward.
        """
        col = _ACTOR_COLORS[idx % len(_ACTOR_COLORS)]
        cam_x = int(self.camera.x)
        cam_y = int(self.camera.y)
        sx = int(actor.get('x', 0) * RENDER_SCALE - cam_x)
        sy = int(actor.get('y', 0) * RENDER_SCALE - cam_y)

        # Attempt sprite preview
        entity = self._get_or_create_actor_entity(actor)
        if entity is not None:
            # Sync entity position to stored actor coords
            entity.x = float(actor.get('x', 0))
            entity.y = float(actor.get('y', 0))
            # Tick the sprite animation (use dt=0 so it won't advance frames)
            if hasattr(entity, 'sprite') and entity.sprite:
                entity.sprite.update(0.016)  # advance ~1 frame so sprite is visible
            # Draw through the entity's own draw() using our viewport camera.
            # Camera.apply(x, y) = (x*RS - cam.x, y*RS - cam.y) which matches
            # our intermediate surface coordinates exactly.
            try:
                entity.in_cutscene = True
                entity.draw(inter_surf, self.camera, {})
            except Exception:
                pass  # silently fall back to circle below
            finally:
                entity.in_cutscene = False

            # Selection highlight ring around the sprite centre
            if selected:
                pygame.draw.circle(inter_surf, col, (sx, sy), 14, 2)
                pygame.draw.circle(inter_surf, _C['white'], (sx, sy), 14, 1)
            else:
                pygame.draw.circle(inter_surf, col, (sx, sy), 10, 1)
        else:
            # Fallback: plain coloured circle
            r = 10 if selected else 7
            pygame.draw.circle(inter_surf, col, (sx, sy), r)
            if selected:
                pygame.draw.circle(inter_surf, _C['white'], (sx, sy), r, 2)

        # Label (always drawn so the actor is identifiable)
        lbl = self.font_small.render(actor.get('id', '?'), True, col)
        inter_surf.blit(lbl, (sx + 12, sy - lbl.get_height() // 2))

    # ── Top bar ────────────────────────────────────────────────────────────────

    def _draw_top_bar(self, screen):
        bar_w = self.screen_width
        pygame.draw.rect(screen, _C['panel'], (0, 0, bar_w, _TOP_H))
        pygame.draw.line(screen, _C['border'], (0, _TOP_H), (bar_w, _TOP_H), 1)

        # Back
        self._btns['back'] = self._draw_button(
            screen, 8, 7, 74, _BTN_H, '← BACK', _C['text_dim'])

        # Name + unsaved
        name_col = _C['danger'] if self.unsaved else _C['text']
        title = self.font_large.render(
            self.cutscene_name + (' ●' if self.unsaved else ''), True, name_col)
        screen.blit(title, (92, (_TOP_H - title.get_height()) // 2))

        # Duration (inline editable — click to change, Enter/Esc to confirm)
        dur    = self.cutscene_data.get('duration', 10.0) if self.cutscene_data else 10.0
        dur_x  = 92 + title.get_width() + 14
        dur_fw = 68
        dur_fh = 24
        dur_fy = (_TOP_H - dur_fh) // 2
        dur_display = (self._duration_buf + '|') if self._duration_focus else f'{dur:.1f} s'
        dur_col     = _C['accent'] if self._duration_focus else _C['border']
        dur_r       = pygame.Rect(dur_x, dur_fy, dur_fw, dur_fh)
        pygame.draw.rect(screen, _C['highlight'], dur_r, border_radius=3)
        pygame.draw.rect(screen, dur_col, dur_r, 1, border_radius=3)
        dur_lbl = self.font_small.render(
            dur_display, True, _C['white'] if self._duration_focus else _C['text_dim'])
        screen.blit(dur_lbl, (dur_r.x + 5, dur_r.y + (dur_fh - dur_lbl.get_height()) // 2))
        self._btns['dur_field'] = dur_r

        # Timecode (playhead time)
        tc = self.font_mono.render(
            f'{self._tl_playhead_t:05.2f}', True, _C['accent2'])
        cx = bar_w // 2
        pygame.draw.rect(screen, _C['highlight'],
                         (cx - tc.get_width() // 2 - 10, 7,
                          tc.get_width() + 20, _BTN_H), border_radius=4)
        screen.blit(tc, (cx - tc.get_width() // 2, (_TOP_H - tc.get_height()) // 2))

        # Play / Stop
        play_col   = _C['danger']  if self._playing else _C['accent2']
        play_label = '■ STOP'      if self._playing else '▶  PLAY'
        self._btns['play'] = self._draw_button(
            screen, bar_w - 190, 7, 82, _BTN_H, play_label, play_col)

        # Save
        save_col = _C['accent'] if self.unsaved else _C['border']
        self._btns['save'] = self._draw_button(
            screen, bar_w - 100, 7, 88, _BTN_H, '💾 SAVE', save_col)

        # Grid toggle hint (far-left of the right cluster)
        grid_col   = _C['accent'] if self._show_grid else _C['text_dim']
        grid_label = '[G] Grid ✓' if self._show_grid else '[G] Grid'
        grid_hint  = self.font_small.render(grid_label, True, grid_col)
        screen.blit(grid_hint, (bar_w - 280,
                                (_TOP_H - grid_hint.get_height()) // 2))

    # ── Left panel (layers / tracks) ──────────────────────────────────────────

    def _draw_left_panel(self, screen, lp):
        x  = lp.x + 8
        y  = lp.y + 8
        W  = lp.width - 16
        scroll = getattr(self, '_left_scroll', 0)

        # ── Section: Room ──────────────────────────────────────────────────────
        self._draw_section_header(screen, x, y, W, 'ROOM')
        y += 20

        room_name = self.cutscene_data.get('room', '(none)') if self.cutscene_data else '(none)'
        bw = 20
        self._btns['room_prev'] = self._draw_button(
            screen, x, y, bw, 24, '<', _C['highlight'])
        rn = self.font_small.render(room_name, True, _C['text'])
        screen.blit(rn, (x + bw + 4, y + (24 - rn.get_height()) // 2))
        self._btns['room_next'] = self._draw_button(
            screen, x + W - bw, y, bw, 24, '>', _C['highlight'])
        y += 30

        self._draw_divider(screen, x, y, W); y += 8

        # ── Section: Layers ────────────────────────────────────────────────────
        self._draw_section_header(screen, x, y, W, 'LAYERS')
        y += 20

        tracks = self._tl_tracks()
        actors = self.cutscene_data.get('actors', []) if self.cutscene_data else []

        # Fixed tracks (camera, screen)
        for ti, (label, color, target) in enumerate(tracks):
            is_actor = ti >= 3
            actor_idx = ti - 3 if is_actor else -1
            selected  = (is_actor and actor_idx == self._actor_sel)

            row_r = pygame.Rect(x, y, W, 26)
            bg    = _C['sel'] if selected else _C['highlight']
            pygame.draw.rect(screen, bg, row_r, border_radius=3)

            # Color strip
            pygame.draw.rect(screen, color, (x, y, 3, 26), border_radius=2)

            # Icon + label
            icon = '🎬' if target == 'camera' else ('🖥' if target == 'screen' else ('🏙' if target == 'room' else '👤'))
            it = self.font_small.render(icon + ' ' + label, True, _C['text'])
            screen.blit(it, (x + 7, y + (26 - it.get_height()) // 2))

            # Action count badge
            if self.cutscene_data:
                n_actions = sum(1 for a in self.cutscene_data.get('actions', [])
                                if a.get('target') == target)
                if n_actions:
                    badge = self.font_small.render(str(n_actions), True, _C['text_dim'])
                    screen.blit(badge, (x + W - badge.get_width() - 4,
                                        y + (26 - badge.get_height()) // 2))

            if is_actor:
                self._btns[f'actor_row_{actor_idx}'] = row_r

            y += 28

        self._draw_divider(screen, x, y, W); y += 8

        # ── Section: Add / Del actor ───────────────────────────────────────────
        self._btns['actor_add'] = self._draw_button(
            screen, x, y, W // 2 - 2, 26, '+ ACTOR', _C['accent2'])
        self._btns['actor_del'] = self._draw_button(
            screen, x + W // 2 + 2, y, W // 2 - 2, 26, '– DEL', _C['danger'])
        y += 32

        # ── Actor add form ─────────────────────────────────────────────────────
        if self._actor_form:
            self._draw_divider(screen, x, y, W); y += 8
            y = self._draw_actor_form(screen, x, y, W)

    # ── Right panel (Inspector / Properties) ──────────────────────────────────

    def _draw_right_panel(self, screen, rp):
        x = rp.x + 10
        y = rp.y + 8
        W = rp.width - 20

        self._draw_section_header(screen, x, y, W, 'INSPECTOR')
        y += 22
        self._draw_divider(screen, x, y, W); y += 8

        if self._form_active:
            y = self._draw_action_form(screen, x, y, W)
        else:
            # Empty state
            lines = [
                'Click a keyframe in the',
                'timeline to edit it,',
                'or press  + ADD  to create',
                'a new action.',
            ]
            for line in lines:
                t = self.font_small.render(line, True, _C['text_dim'])
                screen.blit(t, (x, y)); y += 18

            # Quick summary of selected action if there is one
            if self._tl_sel >= 0 and self.cutscene_data:
                actions = self.cutscene_data.get('actions', [])
                if 0 <= self._tl_sel < len(actions):
                    a = actions[self._tl_sel]
                    y += 8
                    self._draw_divider(screen, x, y, W); y += 8
                    info = [
                        ('Time',   f'{a["time"]:.2f} s'),
                        ('Target', a.get('target', '')),
                        ('Type',   a.get('type', '')),
                    ]
                    for k, v in info:
                        kl = self.font_small.render(k + ':', True, _C['text_dim'])
                        vl = self.font_small.render(v,    True, _C['text'])
                        screen.blit(kl, (x, y))
                        screen.blit(vl, (x + 60, y))
                        y += 16

    # ── Actor add form ─────────────────────────────────────────────────────────

    def _draw_actor_form(self, screen, x, y, W):
        hdr = self.font_medium.render('ADD ACTOR', True, _C['accent2'])
        screen.blit(hdr, (x, y)); y += 22

        types = ['enemy', 'boss', 'player']
        atype = types[self._actor_type_idx % 3]
        lbl = self.font_small.render('Type:', True, _C['text_dim'])
        screen.blit(lbl, (x, y)); y += 16
        self._btns['actor_type_prev'] = self._draw_button(screen, x, y, 20, 22, '<', _C['highlight'])
        tn = self.font_medium.render(atype, True, _C['accent'])
        screen.blit(tn, (x + 24, y + 2))
        self._btns['actor_type_next'] = self._draw_button(screen, x + W - 20, y, 20, 22, '>', _C['highlight'])
        y += 26

        lbl2 = self.font_small.render('Actor ID:', True, _C['text_dim'])
        screen.blit(lbl2, (x, y)); y += 14
        y = self._draw_text_field(screen, x, y, W, '_actor_id', self._actor_id_buf,
                                  self._actor_focus == 'id', actor_key='id')
        y += 4

        if atype != 'player':
            lbl3 = self.font_small.render('Enemy type / Boss ID:', True, _C['text_dim'])
            screen.blit(lbl3, (x, y)); y += 14
            y = self._draw_text_field(screen, x, y, W, '_actor_etype', self._actor_etype_buf,
                                      self._actor_focus == 'etype', actor_key='etype')
            y += 4

        hint = self.font_small.render('Then click in viewport.', True, _C['text_dim'])
        screen.blit(hint, (x, y)); y += 16
        self._btns['actor_place_confirm'] = self._draw_button(
            screen, x, y, W, 26, 'PLACE IN VIEWPORT', _C['accent'])
        y += 30
        return y

    # ── Action inspector form ──────────────────────────────────────────────────

    def _draw_action_form(self, screen, x, y, W):
        hdr_txt = 'NEW ACTION' if self._form_new else 'EDIT ACTION'
        col     = _C['accent2'] if self._form_new else _C['accent']
        screen.blit(self.font_medium.render(hdr_txt, True, col), (x, y)); y += 22

        # Time
        screen.blit(self.font_small.render('Time (s):', True, _C['text_dim']), (x, y)); y += 14
        y = self._draw_text_field(screen, x, y, W, 'form_time', self._form_time_buf,
                                  self._form_focus == 'time', form_key='time')
        y += 6

        # Target
        actors  = self.cutscene_data.get('actors', []) if self.cutscene_data else []
        screen.blit(self.font_small.render('Target:', True, _C['text_dim']), (x, y)); y += 14
        self._btns['form_target_prev'] = self._draw_button(screen, x, y, 20, 22, '<', _C['highlight'])
        tc_col = (_CAMERA_COLOR if self._form_target == 'camera' else
                  _SCREEN_COLOR if self._form_target == 'screen' else
                  _ROOM_COLOR   if self._form_target == 'room'   else _C['accent2'])
        tn = self.font_medium.render(self._form_target, True, tc_col)
        screen.blit(tn, (x + 24, y + 2))
        self._btns['form_target_next'] = self._draw_button(screen, x + W - 20, y, 20, 22, '>', _C['highlight'])
        y += 26

        # Action type
        screen.blit(self.font_small.render('Action type:', True, _C['text_dim']), (x, y)); y += 14
        self._btns['form_type_prev'] = self._draw_button(screen, x, y, 20, 22, '<', _C['highlight'])
        at = self.font_medium.render(self._form_type, True, _C['text'])
        screen.blit(at, (x + 24, y + 2))
        self._btns['form_type_next'] = self._draw_button(screen, x + W - 20, y, 20, 22, '>', _C['highlight'])
        y += 26

        self._draw_divider(screen, x, y, W); y += 8

        # Parameters
        for key, label, hint in _ACTION_PARAMS.get(self._form_type, []):
            screen.blit(self.font_small.render(f'{label}:', True, _C['text_dim']), (x, y))
            y += 14
            buf = self._form_params.get(key, '')
            if hint in ('dir', 'anim', 'anim_player', 'anim_enemy', 'portrait',
                        'invert_mode', 'weather_type', 'scroll_dir'):
                display_buf = buf if buf != '' else 'auto'
                self._btns[f'cycle_{key}'] = self._draw_button(
                    screen, x, y, W, 22, f'◀  {display_buf}  ▶', _C['highlight'])
                y += 26
            elif hint == 'room':
                # Two-row browser: group filter on top, room name below.
                self._btns['room_group_prev'] = self._draw_button(
                    screen, x, y, 20, 22, '<', _C['highlight'])
                gname = self._form_room_group or 'All Groups'
                gs = self.font_small.render(gname, True, _C['text_dim'])
                screen.blit(gs, (x + 24, y + (22 - gs.get_height()) // 2))
                self._btns['room_group_next'] = self._draw_button(
                    screen, x + W - 20, y, 20, 22, '>', _C['highlight'])
                y += 26
                self._btns['room_name_prev'] = self._draw_button(
                    screen, x, y, 20, 22, '<', _C['highlight'])
                rname = buf or '(none)'
                rs = self.font_medium.render(rname, True, _C['text'])
                screen.blit(rs, (x + 24, y + (22 - rs.get_height()) // 2))
                self._btns['room_name_next'] = self._draw_button(
                    screen, x + W - 20, y, 20, 22, '>', _C['highlight'])
                y += 30
            else:
                y = self._draw_text_field(screen, x, y, W, key, buf,
                                          self._form_focus == key, form_key=key)
                if key in ('x', 'y') and self._form_type in (
                        'pan_to', 'snap_to', 'move_to', 'fly_to', 'teleport'):
                    # Only draw the viewport-pick button once (after the first of x/y)
                    pick_name = f'pick_{self._form_type}'
                    if pick_name not in self._btns:
                        self._btns[pick_name] = self._draw_button(
                            screen, x, y, W, 20,
                            '⊕ Click viewport for X,Y', _C['highlight'])
                        y += 24
                if key in ('start_x', 'start_y') and self._form_type == 'pan_to':
                    if 'pick_pan_to_start' not in self._btns:
                        self._btns['pick_pan_to_start'] = self._draw_button(
                            screen, x, y, W, 20,
                            '⊕ Click viewport for Start X,Y', _C['highlight'])
                        y += 24
                y += 4

        y += 6
        self._btns['form_commit'] = self._draw_button(
            screen, x, y, W // 2 - 2, _BTN_H, '✓ OK', _C['accent'])
        self._btns['form_cancel'] = self._draw_button(
            screen, x + W // 2 + 2, y, W // 2 - 2, _BTN_H, '✕ CANCEL', _C['danger'])
        return y + _BTN_H

    # ── Timeline ───────────────────────────────────────────────────────────────

    def _draw_timeline(self, screen, tl):
        """AE-style graphical timeline with per-track rows and keyframe diamonds."""
        if not self.cutscene_data:
            return

        dur          = self.cutscene_data.get('duration', 10.0)
        time_zoom    = self._tl_time_zoom
        scroll_x     = self._tl_scroll_x
        label_end_x  = tl.x + _TL_LABEL_W
        time_area_w  = tl.width - _TL_LABEL_W
        hdr_y        = tl.y
        content_y    = tl.y + _TL_HDR_H
        ruler_y      = content_y
        tracks_y     = ruler_y + _TL_RULER_H

        # ── Header strip ───────────────────────────────────────────────────────
        pygame.draw.rect(screen, _C['panel'], (tl.x, hdr_y, tl.width, _TL_HDR_H))
        pygame.draw.line(screen, _C['border'], (tl.x, hdr_y + _TL_HDR_H),
                         (tl.right, hdr_y + _TL_HDR_H), 1)

        # ADD / DUP / DEL
        bx = tl.x + 8
        by = hdr_y + 2
        self._btns['tl_add'] = self._draw_button(screen, bx,      by, 52, 26, '+ ADD', _C['accent2'])
        self._btns['tl_dup'] = self._draw_button(screen, bx + 56, by, 52, 26, 'DUP',  _C['highlight'])
        self._btns['tl_del'] = self._draw_button(screen, bx +112, by, 52, 26, 'DEL',  _C['danger'])

        # Zoom buttons
        self._btns['tl_zoom_in']  = self._draw_button(screen, bx + 172, by, 28, 26, '+', _C['highlight'])
        self._btns['tl_zoom_out'] = self._draw_button(screen, bx + 204, by, 28, 26, '−', _C['highlight'])
        zm = self.font_small.render('ZOOM', True, _C['text_dim'])
        screen.blit(zm, (bx + 236, by + (26 - zm.get_height()) // 2))

        # Duration readout
        dur_t = self.font_mono.render(f'{dur:.1f}s total', True, _C['text_dim'])
        screen.blit(dur_t, (tl.right - dur_t.get_width() - 10,
                            hdr_y + (_TL_HDR_H - dur_t.get_height()) // 2))

        # ── Ruler ──────────────────────────────────────────────────────────────
        pygame.draw.rect(screen, _C['ruler_bg'],
                         (label_end_x, ruler_y, time_area_w, _TL_RULER_H))
        pygame.draw.line(screen, _C['border'],
                         (label_end_x, ruler_y + _TL_RULER_H),
                         (tl.right, ruler_y + _TL_RULER_H), 1)

        # Clip time area
        screen.set_clip(pygame.Rect(label_end_x, ruler_y, time_area_w, tl.height))

        # Tick marks at 0.5s intervals, labels every 1s
        step = 0.5
        t    = 0.0
        while t <= dur + step:
            tx = label_end_x + t * time_zoom - scroll_x
            is_second = (round(t * 2) % 2 == 0)
            tick_h    = 10 if is_second else 5
            col       = _C['text_dim'] if is_second else _C['border']
            pygame.draw.line(screen, col,
                             (tx, ruler_y + _TL_RULER_H - tick_h),
                             (tx, ruler_y + _TL_RULER_H), 1)
            if is_second:
                lbl = self.font_mono.render(f'{t:.0f}', True, _C['text_dim'])
                screen.blit(lbl, (tx - lbl.get_width() // 2, ruler_y + 2))
            t = round(t + step, 3)

        # ── Track rows ─────────────────────────────────────────────────────────
        tracks  = self._tl_tracks()
        actions = self.cutscene_data.get('actions', [])

        for i, (label, color, target) in enumerate(tracks):
            row_y = tracks_y + i * _TL_ROW_H

            # Row background
            row_bg = _C['highlight'] if i % 2 == 0 else _C['panel2']
            pygame.draw.rect(screen, row_bg,
                             (label_end_x, row_y, time_area_w, _TL_ROW_H))

            # Draw keyframes for this track
            for ai, action in enumerate(actions):
                if action.get('target') != target:
                    continue
                kf_t     = action['time']
                kf_x     = label_end_x + kf_t * time_zoom - scroll_x
                kf_cy    = row_y + _TL_ROW_H // 2
                selected = (ai == self._tl_sel)

                # ── Duration bar ───────────────────────────────────────────────
                # Actions with a 'duration' param span time — draw a filled bar
                # from the keyframe start to its end time so you can see the
                # window it occupies in the timeline at a glance.
                kf_dur = float(action.get('params', {}).get('duration', 0.0))
                if kf_dur > 0:
                    bar_end_x = label_end_x + (kf_t + kf_dur) * time_zoom - scroll_x
                    bar_left  = max(label_end_x, kf_x)
                    bar_right = min(tl.right,    bar_end_x)
                    if bar_right > bar_left:
                        bar_h    = _TL_ROW_H - 10
                        bar_top  = row_y + (_TL_ROW_H - bar_h) // 2
                        bar_w    = bar_right - bar_left
                        # Opacity: bright for selected, subtle for unselected
                        alpha    = 160 if selected else 55
                        bar_surf = pygame.Surface((bar_w, bar_h), pygame.SRCALPHA)
                        r, g, b  = color
                        bar_surf.fill((r, g, b, alpha))
                        screen.blit(bar_surf, (bar_left, bar_top))
                        # Crisp right-edge cap so the end boundary is obvious
                        if bar_end_x <= tl.right:
                            cap_alpha = 200 if selected else 80
                            cap_surf  = pygame.Surface((2, bar_h), pygame.SRCALPHA)
                            cap_surf.fill((r, g, b, cap_alpha))
                            screen.blit(cap_surf, (bar_right - 2, bar_top))

                dragging = (ai == self._kf_drag_idx)
                kf_size  = 8 if dragging else 6
                self._draw_keyframe_diamond(screen, kf_x, kf_cy, kf_size, color, selected)

            pygame.draw.line(screen, _C['border'],
                             (label_end_x, row_y + _TL_ROW_H - 1),
                             (tl.right, row_y + _TL_ROW_H - 1), 1)

        # ── Playhead ───────────────────────────────────────────────────────────
        ph_x = label_end_x + self._tl_playhead_t * time_zoom - scroll_x
        if label_end_x <= ph_x <= tl.right:
            pygame.draw.line(screen, _C['playhead'],
                             (ph_x, ruler_y), (ph_x, tl.bottom), 1)
            # Triangle handle at top of ruler
            pts = [(ph_x, ruler_y + _TL_RULER_H),
                   (ph_x - 6, ruler_y + 4),
                   (ph_x + 6, ruler_y + 4)]
            pygame.draw.polygon(screen, _C['playhead'], pts)

        screen.set_clip(None)

        # ── Label column ───────────────────────────────────────────────────────
        # Draw over the clip region so labels are always visible
        pygame.draw.rect(screen, _C['panel'],
                         (tl.x, content_y, _TL_LABEL_W, tl.height - _TL_HDR_H))
        pygame.draw.line(screen, _C['border'],
                         (label_end_x, content_y), (label_end_x, tl.bottom), 1)

        # Ruler label cell
        pygame.draw.rect(screen, _C['ruler_bg'],
                         (tl.x, ruler_y, _TL_LABEL_W, _TL_RULER_H))
        pygame.draw.line(screen, _C['border'],
                         (tl.x, ruler_y + _TL_RULER_H),
                         (label_end_x, ruler_y + _TL_RULER_H), 1)
        ph_lbl = self.font_mono.render(f'{self._tl_playhead_t:.2f}s', True, _C['playhead'])
        screen.blit(ph_lbl, (tl.x + 6,
                              ruler_y + (_TL_RULER_H - ph_lbl.get_height()) // 2))

        for i, (label, color, target) in enumerate(tracks):
            row_y = tracks_y + i * _TL_ROW_H
            row_bg = _C['highlight'] if i % 2 == 0 else _C['panel2']
            pygame.draw.rect(screen, row_bg,
                             (tl.x, row_y, _TL_LABEL_W, _TL_ROW_H))
            # Color swatch
            pygame.draw.rect(screen, color,
                             (tl.x, row_y + 2, 3, _TL_ROW_H - 4), border_radius=1)
            lbl_surf = self.font_small.render(label, True, _C['text'])
            screen.blit(lbl_surf,
                        (tl.x + 7, row_y + (_TL_ROW_H - lbl_surf.get_height()) // 2))
            pygame.draw.line(screen, _C['border'],
                             (tl.x, row_y + _TL_ROW_H - 1),
                             (label_end_x, row_y + _TL_ROW_H - 1), 1)

        # Empty hint
        if not actions:
            hint = self.font_medium.render(
                'No actions yet.  Press  + ADD  or double-click a track.',
                True, _C['text_dim'])
            hint_y = tracks_y + max(len(tracks), 1) * _TL_ROW_H // 2
            screen.blit(hint, (label_end_x + 20, hint_y))

    # ══════════════════════════════════════════════════════════════════════════
    # Drawing helpers
    # ══════════════════════════════════════════════════════════════════════════

    def _draw_keyframe_diamond(self, screen, cx, cy, size, color, selected):
        """Draw a diamond (rotated square) keyframe marker centred at (cx, cy).

        Selected keyframes get a white outline so they stand out on any colour;
        unselected ones get a thin dark border to separate overlapping diamonds.
        """
        pts = [(cx, cy - size), (cx + size, cy),
               (cx, cy + size), (cx - size, cy)]
        pygame.draw.polygon(screen, color, pts)
        if selected:
            pygame.draw.polygon(screen, _C['white'], pts, 2)
        else:
            pygame.draw.polygon(screen, (0, 0, 0, 120), pts, 1)

    def _draw_section_header(self, screen, x, y, W, text):
        t = self.font_small.render(text, True, _C['text_dim'])
        screen.blit(t, (x, y))
        pygame.draw.line(screen, _C['border'],
                         (x + t.get_width() + 6, y + t.get_height() // 2),
                         (x + W, y + t.get_height() // 2), 1)

    def _draw_divider(self, screen, x, y, W):
        pygame.draw.line(screen, _C['border'], (x, y), (x + W, y), 1)

    def _draw_button(self, screen, x, y, w, h, label, color):
        r = pygame.Rect(x, y, w, h)
        pygame.draw.rect(screen, color, r, border_radius=4)
        t = self.font_small.render(label, True, _C['white'])
        screen.blit(t, (r.centerx - t.get_width() // 2,
                        r.centery - t.get_height() // 2))
        return r

    def _draw_text_field(self, screen, x, y, W, _id, buf, focused,
                         form_key=None, actor_key=None):
        """Draw a text input field and register it for click + keyboard routing.

        Registers two entries each frame:
          self._btns[f'_field_{_id}']      → the hit rect for _handle_btn
          self._field_meta[f'_field_{_id}'] → (form_key, actor_key) so
              _handle_btn knows which buffer to send keypresses to.

        Returns the Y coordinate immediately below the field (for flow layout).
        """
        r   = pygame.Rect(x, y, W, 24)
        col = _C['accent'] if focused else _C['border']
        pygame.draw.rect(screen, _C['highlight'], r, border_radius=3)
        pygame.draw.rect(screen, col, r, 1, border_radius=3)
        display = buf + ('|' if focused else '')
        t = self.font_small.render(display, True, _C['white'])
        screen.blit(t, (r.x + 5, r.y + (24 - t.get_height()) // 2))

        field_btn_name = f'_field_{_id}'
        self._btns[field_btn_name] = r
        self._field_meta[field_btn_name] = (form_key, actor_key)
        return y + 26

    # ── Room cycle (used by button handler) ───────────────────────────────────

    def _rooms_for_group(self, group):
        """Return room names visible in *group*.

        An empty/None *group* means "All Groups" — every non-transient room is
        included.  Otherwise only rooms whose .group attribute matches are returned.
        """
        return [r.name for r in self.room_manager.rooms
                if not getattr(r, 'is_transient', False)
                and (not group or r.group == group)]

    def _cycle_room_group(self, delta):
        """Cycle through room groups in the change_room action form.

        Moves to the previous/next group and resets room_name to the first
        room in that group so the value is always valid.
        """
        groups = [g for g in self.room_manager.groups]
        if not groups:
            return
        cur   = self._form_room_group
        idx   = groups.index(cur) if cur in groups else 0
        self._form_room_group = groups[(idx + delta) % len(groups)]
        rooms = self._rooms_for_group(self._form_room_group)
        self._form_params['room_name'] = rooms[0] if rooms else ''

    def _cycle_room_in_group(self, delta):
        """Cycle through rooms within the currently selected group."""
        rooms = self._rooms_for_group(self._form_room_group)
        if not rooms:
            return
        cur   = self._form_params.get('room_name', '')
        idx   = rooms.index(cur) if cur in rooms else 0
        self._form_params['room_name'] = rooms[(idx + delta) % len(rooms)]

    def _cycle_room(self, delta):
        """Advance (+1) or reverse (-1) through the room list.

        Also invalidates baked tile surfaces so the viewport doesn't flash the
        old room's tiles for one frame before the new ones load.
        """
        rooms = [r.name for r in self.room_manager.rooms
                 if not getattr(r, 'is_transient', False)]
        if not rooms:
            return
        cur      = self.cutscene_data.get('room', '')
        idx      = rooms.index(cur) if cur in rooms else 0
        new_name = rooms[(idx + delta) % len(rooms)]
        self._push_undo()
        self.cutscene_data['room'] = new_name
        self.unsaved = True
        self._invalidate_tile_cache(new_name)
        # Drop the tileset_editor's cached tile list so _ensure_room_tiles
        # reseeds it from room.tiles on the next draw call.
        te = getattr(self.room_editor, 'tileset_editor', None)
        if te is not None and hasattr(te, 'room_tiles'):
            te.room_tiles.pop(new_name, None)