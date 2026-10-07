"""
dev_tools/cutscene_editor.py

Cutscene editor — rebuilt on the dev-tool design language (dev_tools.ui_kit +
DevMenu / RoomEditor): bitmap menu font, navy panels with hairline borders,
gold accent, header / footer bars, card + pill widgets, one shared dropdown.

Layout (edit view)
──────────────────
  ┌ header:  back · name · length · timecode · play · undo/redo · grid · save ┐
  │ Scene panel    │      Viewport (world preview)      │  Inspector          │
  │ room · actors  ├────────────────────────────────────┤  (action form,      │
  │ layers         │ Timeline: toolbar · ruler · tracks │   full height)      │
  └───────────────────────────────────────────────────────────────────────────┘

Data model (cutscene_data dict, stored as JSON) — unchanged
────────────────────────────────────────────────────────────
  id:       str           — matches the filename stem
  room:     str           — which room to display in the viewport
  duration: float         — total scene length in seconds
  actors:   list[dict]    — {id, type, enemy_type, variant, x, y, …}
  actions:  list[dict]    — {time, target, type, params{…}}, always sorted by time

All mutations must call _push_undo() *before* changing cutscene_data so that
Ctrl-Z can restore the state that existed just before that edit.

UI architecture
───────────────
The UI is immediate-mode.  Every draw pass rebuilds ``self._hits`` — a list of
(rect, action, arg) registered by the widgets as they are painted — and
``_on_click`` dispatches the topmost hit through ``_on_action``.  Hover
animation is eased per widget key in ``_anim``.  Everything is drawn through
ui_kit's ``*_on`` helpers so it works on a plain Surface or on GPUScreen.
"""

import copy
import glob
import json
import math
import os
import pygame
import dev_tools.ui_kit as uk
from core.camera import Camera
from config.settings import RENDER_SCALE
from dev_tools.room_editor.room_editor_tools.entity_editor import discover_enemy_ids, discover_boss_ids, discover_npc_ids
from dev_tools.character_creator import discover_attacks

# ── Schema constants (unchanged from the previous editor) ────────────────────
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
    'fade_in':       [('duration', 'Duration (s)', 'float'),
                      ('color', 'Color', 'color')],
    'fade_out':      [('duration', 'Duration (s)', 'float'),
                      ('color', 'Color', 'color')],
    'flash':         [('duration', 'Duration (s)', 'float'),
                      ('color', 'Color', 'color')],
    'invert':        [('duration', 'Duration (s)', 'float'),
                      ('mode', 'Mode', 'invert_mode')],
    # portrait '' (shown as "narrator") = no face art — full-width text box.
    # Any other value is a key under assets/portraits/{key}.png.
    'dialogue':      [('portrait', 'Portrait', 'portrait'), ('text', 'Text', 'str')],
    # Pauses the cutscene until the player finishes the QTE bar. spam mode uses
    # Fill/Press + Drain; timing mode uses Sweep Speed + Max Attempts (zones can
    # be added by hand as a "zones" param: [[0,"fail"],[0.5,"success"],...]).
    'qte':           [('qte_id', 'QTE Id', 'str'), ('mode', 'Mode', 'qte_mode'),
                      ('fill_per_press', 'Fill / Press (spam)', 'float'),
                      ('drain_rate', 'Drain / s (spam)', 'float'),
                      ('sweep_speed', 'Sweep Speed (timing)', 'float'),
                      ('max_attempts', 'Max Attempts (0=inf)', 'int'),
                      ('fail_hold', 'Fail Seq Hold (s)', 'float')],
    'weather_start': [('weather_type', 'Weather Type', 'weather_type'),
                      ('speed',        'Speed (px/s)',  'float'),
                      ('alpha',        'Alpha (0-255)', 'float')],
    'weather_stop':  [],
    # loop=True keeps the animation cycling until the next action changes it
    # (the old behaviour); loop=False plays it through once and holds the last
    # frame. The runtime reads it via params.get('loop', True).
    'set_animation': [('state', 'State', 'anim'), ('direction', 'Direction', 'dir'),
                      ('loop', 'Loop', 'bool')],
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
    # Swaps which player character a 'player'-type actor displays — e.g. a
    # Goku actor turning into Gohan mid-scene. Sourced from the same character
    # roster as the in-game character switch menu (assets/sprites/player/).
    'set_character': [('character', 'Character', 'character')],
    # Switches the actor's sprite folder to a different costume — e.g. from
    # 'base' to 'ssj' mid-scene. Costumes are discovered from the actor's
    # current character folder (assets/sprites/player/{character}/).
    'set_costume':   [('costume',   'Costume',   'costume')],
    # Shows or hides this actor's ground shadow for the rest of the scene (or
    # until another set_shadow flips it back). Useful for actors floating,
    # flying above the ground, or standing on something that shouldn't cast
    # the normal ground shadow.
    'set_shadow':    [('visible', 'Visible', 'bool')],
    # Starts a music track through SoundManager.play_music(), bypassing the
    # exploration/battle/boss context map — same as a room's Music object.
    'play_music':    [('track', 'Music Track', 'music_track'),
                      ('loop', 'Loop', 'bool'),
                      ('fade_in', 'Fade In', 'bool')],
    # Fires a one-shot sound effect through SoundManager.play_sfx().
    # 'loop' repeats it until a stop_sfx action (or the cutscene ends).
    'play_sfx':      [('sfx', 'Sound Effect', 'sfx_name'),
                      ('loop', 'Loop', 'bool')],
    # Stops a looping sound effect. Blank sound = stop every looping SFX.
    'stop_sfx':      [('sfx', 'Sound Effect', 'sfx_name_any')],
    # Stops whatever music is currently playing, via SoundManager.stop_music().
    # fade_out=True fades over SoundEngine.fade_duration (1s); False cuts it
    # instantly. Doesn't care what track is playing or how it was started
    # (context, room Music object, or an earlier play_music action).
    'stop_music':    [('fade_out', 'Fade Out', 'bool')],
    # Scripted attack: plays the matching attack animation, then — once
    # `release_delay` elapses — both fires the real projectile/melee/beam
    # effect (via game.py's on_spawn_attack callback) AND spawns an
    # AttackEffectVisual (core/cutscene_actor.py) so the effect is actually
    # visible while previewing/scrubbing in the editor, sourced from
    # assets/sprites/attacks/{attack_type}/ (beam-shaped sheets grow in,
    # a hit/impact/collision sprite is used if the folder has one).
    # attack_type is any id discover_attacks() finds there — see
    # _param_pool()'s 'attack_type' branch below — not a fixed list.
    # `duration` is how long the actor holds the attack pose in total
    # before returning to idle — release_delay must be < duration (not just
    # <=): the spawned effect's own lifetime is (duration - release_delay),
    # so setting it equal to duration gives the effect ~0 time to render.
    # Leaving this blank now defaults to 2/3 of duration (see
    # CutsceneActor.attack() in core/cutscene_actor.py) rather than the
    # full duration, but an explicit value earlier in the pose (e.g. right
    # as the throw/swing frame lands) usually looks best.
    # target_x/target_y are optional aim points (e.g. a blast fired at
    # another actor's position) rather than firing straight along
    # `direction`. For the generic AttackEffectVisual fallback (melee/
    # charge/unmapped attack_types), a target point also caps how far a
    # beam-style sprite visually travels before it holds — locked to
    # whichever axis matches `direction` (only target_y matters facing
    # up/down, only target_x matters facing left/right), since a
    # 4-directional beam can't actually travel diagonally toward an
    # off-axis point. Leave both blank and the beam grows to its sprite
    # sheet's full painted length instead, same as before.
    # effect_duration overrides how long the spawned effect stays open —
    # left blank, it defaults to (duration - release_delay). Real
    # attacks/*.py classes (kamehameha, masenko, ...) call their own
    # stop_method (e.g. start_decay) once this elapses, which for most of
    # them is also what stops them from growing/traveling further — so
    # this is the main lever for "how far" a real beam gets before closing
    # (target_x/target_y only reach real classes for masenko/
    # ghost_kamikaze, which already aim themselves via _target_point()).
    'attack':        [('attack_type', 'Attack Type', 'attack_type'),
                      ('direction', 'Direction', 'dir'),
                      ('duration', 'Pose Duration (s)', 'float'),
                      ('release_delay', 'Release Delay (s, opt)', 'float'),
                      ('effect_duration', 'Effect Duration (s, opt)', 'float'),
                      ('target_x', 'Target X (opt)', 'float'),
                      ('target_y', 'Target Y (opt)', 'float'),
                      ('deal_damage', 'Deal Damage', 'bool')],
}

_CAMERA_ACTIONS = ['pan_to', 'snap_to', 'shake']
_SCREEN_ACTIONS = ['fade_in', 'fade_out', 'flash', 'invert', 'dialogue', 'qte',
                   'weather_start', 'weather_stop']
_ROOM_ACTIONS   = ['change_room']
_SOUND_ACTIONS  = ['play_music', 'play_sfx', 'stop_sfx', 'stop_music']
_ACTOR_ACTIONS  = ['set_animation', 'move_to', 'face', 'teleport', 'fly_to',
                   'set_character', 'set_costume', 'attack', 'set_shadow']
_INVERT_MODES   = ['full', 'red', 'green', 'blue', 'greyscale']
# Fallback attack_type pool used only if discover_attacks() finds nothing on
# disk (e.g. assets/sprites/attacks/ missing) — see _param_pool() below,
# which otherwise sources the full, ever-growing attack roster straight from
# that folder, same as character_creator.py's Attacks tab.
_ATTACK_TYPES   = ['melee', 'kiblast', 'firebeam', 'charge']

# Named colour presets for fade_in / fade_out / flash 'color' params — cycled
# through with the same '<  name  >' button used for other enum fields (see
# _param_pool). Stored in the cutscene JSON as an [r, g, b] list (what
# CutsceneRuntime._execute_action already expects via params.get('color', …)),
# so _commit_form converts the preset name to its RGB triple on save and
# _default_param/_draw_action_form convert back the other way for display.
_COLOR_PRESETS = {
    'black':   (0,   0,   0),
    'white':   (255, 255, 255),
    'red':     (220, 40,  40),
    'orange':  (255, 150, 40),
    'yellow':  (255, 225, 60),
    'green':   (60,  200, 90),
    'cyan':    (70,  220, 220),
    'blue':    (70,  120, 255),
    'purple':  (160, 90,  230),
    'pink':    (255, 110, 180),
}

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

# Fixed track colours (tuned to sit on the navy panels next to the gold accent)
_CAMERA_COLOR = uk.Theme.KI_BLUE
_SCREEN_COLOR = (150, 158, 182)
_ROOM_COLOR   = (76, 200, 150)
_SOUND_COLOR  = (238, 146, 70)

# ── Surface colours shared by every widget (same values DevMenu / RoomEditor
#    use for their bars, cards and field rows) ────────────────────────────────
_BG_BASE     = (8, 11, 17)
_BG_CONTENT  = (10, 13, 20)
_BAR_BG      = (12, 15, 23)
_BAR_LINE    = (43, 49, 63)
_ROW_BASE    = (22, 26, 35)
_ROW_HOVER   = (28, 33, 44)
_FIELD_BASE  = (20, 23, 32)
_FIELD_HOVER = (27, 31, 42)
_CANVAS      = (15, 19, 28)      # viewport canvas outside / behind the room
_WHITE       = (255, 255, 255)

# Transport / playhead accent
_PLAYHEAD    = uk.Theme.GOLD_BRIGHT

# Field-key constants for the four text-entry buffers
_ACTOR_TYPES = ['enemy', 'boss', 'npc', 'player']
_TARGET_FIXED = ['camera', 'screen', 'room', 'sound']
# Picks that place an *actor* (as opposed to the camera): these snap to the
# actor grid when it's enabled and show a ghost of the actor at the cursor.
_ACTOR_PICKS = {'pick_move_to': 'move_to', 'pick_fly_to': 'fly_to',
                'pick_teleport': 'teleport'}
_PICK_ACTIONS = ('pick_pan_to', 'pick_snap_to', 'pick_move_to', 'pick_fly_to',
                 'pick_teleport', 'pick_pan_to_start', 'pick_attack_target')


def _ensure_dir():
    os.makedirs(_CUTSCENE_DIR, exist_ok=True)

def _cutscene_path(name):
    return os.path.join(_CUTSCENE_DIR, f'{name}.json')

def discover_cutscene_ids():
    """All saved cutscene ids, sourced the same way CutsceneEditor's own
    file browser does (_refresh_file_list() below) — the .json filename
    stems in data/cutscenes/, so this list never drifts out of sync with
    what cutscenes actually exist on disk. Used by other dev tools (e.g.
    the event editor's play_cutscene action) that need the cutscene roster
    without pulling in a full CutsceneEditor instance. Explicitly excludes
    the hidden .editor_viewports.json bookkeeping file (see
    _EDITOR_VIEWPORTS above), which os.listdir() would otherwise happily
    return right alongside real cutscenes. Returns [] if the cutscene
    folder doesn't exist yet rather than raising."""
    try:
        viewport_name = os.path.basename(_EDITOR_VIEWPORTS)
        return sorted(
            f[:-5] for f in os.listdir(_CUTSCENE_DIR)
            if f.endswith('.json') and f != viewport_name)
    except OSError:
        return []

def _clamp(v, lo, hi):
    return max(lo, min(hi, v))

class _ZoomedViewport:
    """Virtual zoom canvas projected directly onto a sub-rect of the real
    GPUScreen.

    Self-contained copy of the same fix room_editor.py uses for its own
    continuous zoom (its ``_ZoomedScreen``) -- kept private to this module
    rather than imported, since room_editor.py's own canvas always covers
    the whole screen and has no notion of a screen-space (ox, oy) origin.
    Adding that origin here is what lets the same trick work for a viewport
    that's only a sub-panel of the edit-view layout (surrounded by the top
    bar / side panels / timeline), rather than the full window.

    draw_* / blit calls made against an instance of this class are in
    "virtual" (unscaled, base-scale) coordinates -- exactly the same
    coordinate space the old CPU intermediate Surface used -- and are
    projected onto the real GPUScreen as (coord * zoom) + origin, so SDL's
    GPU-side stretched blit does the scaling per call instead of a
    CPU-bound pygame.transform.scale() over the whole canvas once a frame.
    """

    __slots__ = ("_screen", "_zoom", "_w", "_h", "_ox", "_oy")

    def __init__(self, screen, zoom, real_width, real_height, origin=(0, 0)):
        self._screen = screen
        self._zoom = float(zoom)
        self._w = max(1, int(real_width / self._zoom))
        self._h = max(1, int(real_height / self._zoom))
        self._ox, self._oy = origin

    def _rect(self, rect):
        r = pygame.Rect(rect)
        return pygame.Rect(
            round(r.x * self._zoom) + self._ox, round(r.y * self._zoom) + self._oy,
            max(1, round(r.w * self._zoom)),
            max(1, round(r.h * self._zoom)),
        )

    def _point(self, point):
        return (round(point[0] * self._zoom) + self._ox, round(point[1] * self._zoom) + self._oy)

    def _width(self, width):
        return 0 if width <= 0 else max(1, round(width * self._zoom))

    def blit(self, surface, dest, area=None, special_flags=0):
        if isinstance(dest, pygame.Rect):
            dst = self._rect(dest)
        else:
            x, y = dest
            a = area if isinstance(area, pygame.Rect) else (pygame.Rect(area) if area is not None else None)
            w, h = a.size if a else surface.get_size()
            dst = pygame.Rect(
                round(x * self._zoom) + self._ox, round(y * self._zoom) + self._oy,
                max(1, round(w * self._zoom)),
                max(1, round(h * self._zoom)),
            )
        return self._screen.blit(surface, dst, area=area, special_flags=special_flags)

    def blit_scaled(self, surface, dst_rect, area=None):
        return self.blit(surface, dst_rect, area=area)

    def blits(self, seq, doreturn=True):
        rects = [] if doreturn else None
        for item in seq:
            if len(item) == 2:
                surface, dest = item
                area = None
            else:
                surface, dest, area = item
            self.blit(surface, dest, area=area)
            if doreturn:
                if isinstance(dest, pygame.Rect):
                    rects.append(self._rect(dest))
                else:
                    x, y = dest
                    a = area if isinstance(area, pygame.Rect) else (pygame.Rect(area) if area is not None else None)
                    w, h = a.size if a else surface.get_size()
                    rects.append(pygame.Rect(
                        round(x * self._zoom) + self._ox, round(y * self._zoom) + self._oy,
                        max(1, round(w * self._zoom)),
                        max(1, round(h * self._zoom)),
                    ))
        return rects

    def blit_transient(self, surface, dest, area=None):
        if isinstance(dest, pygame.Rect):
            dst = self._rect(dest)
        else:
            x, y = dest
            a = area if isinstance(area, pygame.Rect) else (pygame.Rect(area) if area is not None else None)
            w, h = a.size if a else surface.get_size()
            dst = pygame.Rect(
                round(x * self._zoom) + self._ox, round(y * self._zoom) + self._oy,
                max(1, round(w * self._zoom)),
                max(1, round(h * self._zoom)),
            )
        return self._screen.blit_transient(surface, dst, area=area)

    def fill(self, color, rect=None, special_flags=0):
        # GPUScreen.fill() (unlike a real Surface.fill()) takes no
        # special_flags -- it's flat-color fill/rect only, no blend-mode
        # variants -- so it isn't forwarded here. Kept as an accepted
        # parameter for interface compatibility with any caller that
        # passes it positionally/by keyword the way Surface.fill() allows.
        if rect is None:
            rect = pygame.Rect(0, 0, self._w, self._h)
        return self._screen.fill(color, self._rect(rect))

    def get_size(self):
        return self._w, self._h

    def get_width(self):
        return self._w

    def get_height(self):
        return self._h

    def get_rect(self, **kwargs):
        rect = pygame.Rect(0, 0, self._w, self._h)
        for attr, value in kwargs.items():
            setattr(rect, attr, value)
        return rect

    def set_clip(self, rect):
        return self._screen.set_clip(None if rect is None else self._rect(rect))

    def get_clip(self):
        return self._screen.get_clip()

    def draw_rect(self, color, rect, width=0, border_radius=0,
                  border_top_left_radius=-1, border_top_right_radius=-1,
                  border_bottom_left_radius=-1, border_bottom_right_radius=-1):
        scale_radius = lambda r: -1 if r < 0 else self._width(r)
        return self._screen.draw_rect(
            color, self._rect(rect), self._width(width),
            border_radius=scale_radius(border_radius),
            border_top_left_radius=scale_radius(border_top_left_radius),
            border_top_right_radius=scale_radius(border_top_right_radius),
            border_bottom_left_radius=scale_radius(border_bottom_left_radius),
            border_bottom_right_radius=scale_radius(border_bottom_right_radius),
        )

    def draw_line(self, color, start_pos, end_pos, width=1):
        return self._screen.draw_line(
            color, self._point(start_pos), self._point(end_pos), self._width(width)
        )

    def draw_circle(self, color, center, radius, width=0):
        return self._screen.draw_circle(
            color, self._point(center), max(1, round(radius * self._zoom)),
            self._width(width)
        )

    def draw_polygon(self, color, points, width=0):
        return self._screen.draw_polygon(
            color, [self._point(p) for p in points], self._width(width)
        )

    def filled_circle(self, x, y, radius, color):
        return self._screen.filled_circle(
            round(x * self._zoom) + self._ox, round(y * self._zoom) + self._oy,
            max(1, round(radius * self._zoom)), color
        )

    def aacircle(self, x, y, radius, color):
        return self._screen.aacircle(
            round(x * self._zoom) + self._ox, round(y * self._zoom) + self._oy,
            max(1, round(radius * self._zoom)), color
        )


# =============================================================================
# Fonts
# =============================================================================

class _UiFont:
    """One BitmapFont pinned to a pixel height, exposing the plain
    ``render(text, antialias, color)`` / ``size(text)`` call shape (same idea
    as DevMenu / RoomEditor's _BitmapFontView).

    Unlike that adapter this one also covers glyphs the bitmap set doesn't
    ship (``% < > [ ] | = " * # &`` and so on).  The bitmap font silently
    drops those, which is fine for fixed UI labels but not for text the
    designer *types* — dialogue lines, ids, names — where a vanishing
    character would look like lost input.  Any unsupported character is
    rendered with a default pygame font at a matching size instead.
    """

    _CACHE_LIMIT = 1500

    # Per-glyph vertical nudge (pixels, positive = down) for bitmap-font
    # characters that render sitting too high / flush with the baseline
    # instead of dropping below it. Comma is the common offender — tune the
    # value to taste if it still looks off at a given font size.
    _GLYPH_Y_OFFSET = {',': 4}

    def __init__(self, bitmap_font, height):
        self._bmp = bitmap_font
        self.height = int(height)
        self._fallback = None
        self._cache = {}

    def _has(self, ch):
        if ch == ' ':
            return True
        try:
            return self._bmp._glyph(ch) is not None
        except Exception:
            return False

    def _kind(self, ch):
        """'fallback' (bitmap set doesn't have it), 'offset' (bitmap glyph
        that needs the vertical nudge above), or 'ok' (render as-is)."""
        if not self._has(ch):
            return 'fallback'
        if ch in self._GLYPH_Y_OFFSET:
            return 'offset'
        return 'ok'

    def _fb_metrics(self):
        """(font, baseline_row, cap_h): a fallback pygame font sized so its
        capital 'H' is as tall as the bitmap font's, plus where its baseline
        sits — so mixed runs share one baseline and look like one line."""
        if self._fallback is None:
            cap_h = self._bmp.render('H', color=(255, 255, 255), height=self.height).get_height()
            size = max(9, int(cap_h * 1.4))
            for _ in range(3):
                bb = uk.Theme.font(None, size).render('H', True, (255, 255, 255)).get_bounding_rect()
                if bb.h <= 0:
                    break
                new = max(9, int(round(size * cap_h / bb.h)))
                if new == size:
                    break
                size = new
            font = uk.Theme.font(None, size)
            bb = font.render('H', True, (255, 255, 255)).get_bounding_rect()
            self._fallback = (font, bb.bottom, cap_h)
        return self._fallback

    def render(self, text, antialias=True, color=(255, 255, 255)):
        text = '' if text is None else str(text)
        color = tuple(color)
        if all(self._kind(c) == 'ok' for c in text):
            return self._bmp.render(text, color=color, height=self.height)

        key = (text, color)
        hit = self._cache.get(key)
        if hit is not None:
            return hit

        runs, cur, cur_kind = [], '', None
        for ch in text:
            k = self._kind(ch)
            if cur and k != cur_kind:
                runs.append((cur, cur_kind))
                cur = ''
            cur += ch
            cur_kind = k
        if cur:
            runs.append((cur, cur_kind))

        fb_font, fb_base, cap_h = self._fb_metrics()
        parts = []          # (surface, y) — bitmap runs hang from y=0 (or their
                             # glyph offset), fallback runs sit on the baseline
        for chunk, kind in runs:
            if kind == 'fallback':
                parts.append((fb_font.render(chunk, True, color[:3]), cap_h - fb_base))
            else:
                y_off = self._GLYPH_Y_OFFSET.get(chunk[0], 0) if kind == 'offset' else 0
                parts.append((self._bmp.render(chunk, color=color, height=self.height), y_off))
        top = min(0, min(y for _, y in parts))
        w = sum(p.get_width() for p, _ in parts) + max(0, len(parts) - 1)
        h = max(y - top + p.get_height() for p, y in parts)
        out = pygame.Surface((max(1, w), max(1, h)), pygame.SRCALPHA)
        x = 0
        for p, y in parts:
            out.blit(p, (x, y - top))
            x += p.get_width() + 1
        if len(self._cache) >= self._CACHE_LIMIT:
            self._cache.clear()
        self._cache[key] = out
        return out

    def size(self, text):
        return self.render(text, True, (255, 255, 255)).get_size()

    def get_height(self):
        return self.size('Ag')[1]


def _load_dev_menu_icon(icon_key, box_size, fallback_fn=None, fallback_color=None):
    """Load one of the shared dev-menu PNG icons (assets/ui/dev_menu/icons/)
    with the same crop + point-sample scaling DevMenu._load_icon uses, so an
    icon such as 'back' is pixel-identical here and on the dev menu header.
    If the PNG isn't on disk, bake the vector fallback instead of returning
    an invisible surface. fallback_color lets a caller match a specific spot
    (e.g. a dimmed empty-state icon) instead of the default gold tint."""
    path = os.path.join('assets', 'ui', 'dev_menu', 'icons', f'{icon_key}.png')
    try:
        raw = pygame.image.load(path).convert_alpha()
    except (FileNotFoundError, pygame.error):
        if fallback_fn is not None:
            return uk.render_icon_surface(fallback_fn, box_size, fallback_color or uk.Theme.GOLD)
        return pygame.Surface((box_size, box_size), pygame.SRCALPHA)

    content_rect = raw.get_bounding_rect(min_alpha=1)
    if content_rect.width <= 0 or content_rect.height <= 0:
        content_rect = raw.get_rect()
    raw = raw.subsurface(content_rect).copy()

    iw, ih = raw.get_size()
    scale = min(box_size / max(1, iw), box_size / max(1, ih))
    nw, nh = max(1, round(iw * scale)), max(1, round(ih * scale))
    if scale >= 1.0:
        prescale = max(1, math.ceil(scale) * 2)
        big = pygame.transform.scale(raw, (iw * prescale, ih * prescale))
        scaled = pygame.transform.scale(big, (nw, nh))
    else:
        scaled = pygame.transform.scale(raw, (nw, nh))
    canvas = pygame.Surface((box_size, box_size), pygame.SRCALPHA)
    canvas.blit(scaled, ((box_size - nw) // 2, (box_size - nh) // 2))
    return canvas


# =============================================================================
# Anti-aliased polygon / diamond shapes
# =============================================================================
# ui_kit supersamples its rounded rects and circles; it has no polygon helper,
# and GPUScreen's own draw_polygon is aliased.  Same trick here: rasterise at
# N x, smoothscale down, cache, blit through uk.blit_surface.

_SS = 6
_poly_cache = {}


def _poly_surface(norm_pts, w, h, color):
    """Filled polygon whose points are given 0..1 relative to a w x h box."""
    key = (tuple(norm_pts), w, h, tuple(color))
    surf = _poly_cache.get(key)
    if surf is None:
        hi = pygame.Surface((w * _SS, h * _SS), pygame.SRCALPHA)
        pts = [(px * w * _SS, py * h * _SS) for px, py in norm_pts]
        pygame.draw.polygon(hi, uk._rgba(color), pts)
        surf = pygame.transform.smoothscale(hi, (w, h))
        _poly_cache[key] = surf
    return surf


def _blit_poly(surface, rect, color, norm_pts):
    rect = pygame.Rect(rect)
    if rect.w <= 0 or rect.h <= 0:
        return
    uk.blit_surface(surface, _poly_surface(norm_pts, rect.w, rect.h, color),
                    rect.topleft, transient=False)


_diamond_cache = {}


def _diamond_surface(half, fill, border, border_w):
    key = (half, tuple(fill), tuple(border) if border else None, border_w)
    surf = _diamond_cache.get(key)
    if surf is None:
        d = half * 2 + 2
        hi = pygame.Surface((d * _SS, d * _SS), pygame.SRCALPHA)
        c = d * _SS / 2
        r = half * _SS

        def poly(rad, col):
            pygame.draw.polygon(hi, uk._rgba(col),
                                [(c, c - rad), (c + rad, c), (c, c + rad), (c - rad, c)])
        if border and border_w > 0:
            poly(r, border)
            poly(max(1.0, r - border_w * _SS * 1.42), fill)
        else:
            poly(r, fill)
        surf = pygame.transform.smoothscale(hi, (d, d))
        _diamond_cache[key] = surf
    return surf


def _draw_diamond(surface, cx, cy, half, fill, border=None, border_w=0):
    s = _diamond_surface(int(half), tuple(fill), border, border_w)
    uk.blit_surface(surface, s, (int(cx) - s.get_width() // 2, int(cy) - s.get_height() // 2),
                    transient=False)


# =============================================================================
# Vector icon set — fn(surface, rect, color), same shape as ui_kit's icons
# =============================================================================

def _icon_plus(surface, rect, color, width=2):
    cx, cy = rect.center
    s = min(rect.w, rect.h) * 0.30
    uk.draw_line_on(surface, color, (cx - s, cy), (cx + s, cy), width)
    uk.draw_line_on(surface, color, (cx, cy - s), (cx, cy + s), width)


def _icon_minus(surface, rect, color, width=2):
    cx, cy = rect.center
    s = min(rect.w, rect.h) * 0.30
    uk.draw_line_on(surface, color, (cx - s, cy), (cx + s, cy), width)


def _icon_check(surface, rect, color, width=2):
    cx, cy = rect.center
    s = min(rect.w, rect.h) * 0.32
    uk.draw_line_on(surface, color, (cx - s, cy), (cx - s * 0.15, cy + s * 0.8), width)
    uk.draw_line_on(surface, color, (cx - s * 0.15, cy + s * 0.8), (cx + s, cy - s * 0.7), width)


def _icon_close(surface, rect, color, width=2):
    cx, cy = rect.center
    s = min(rect.w, rect.h) * 0.26
    uk.draw_line_on(surface, color, (cx - s, cy - s), (cx + s, cy + s), width)
    uk.draw_line_on(surface, color, (cx - s, cy + s), (cx + s, cy - s), width)


def _icon_trash(surface, rect, color, width=2):
    cx, cy = rect.center
    s = min(rect.w, rect.h)
    body = pygame.Rect(0, 0, s * 0.56, s * 0.52)
    body.centerx = cx
    body.top = int(cy - s * 0.06)
    uk.draw_rect_on(surface, color, body, width, 2)
    lid = pygame.Rect(0, 0, s * 0.78, s * 0.10)
    lid.centerx = cx
    lid.bottom = body.top + 1
    uk.draw_rect_on(surface, color, lid, 0, 1)
    handle = pygame.Rect(0, 0, s * 0.28, s * 0.14)
    handle.centerx = cx
    handle.bottom = lid.top + 1
    uk.draw_rect_on(surface, color, handle, 1, 2)
    for i in (-1, 1):
        x = cx + i * s * 0.14
        uk.draw_line_on(surface, color, (x, body.top + 4), (x, body.bottom - 3), 1)


def _make_chevron(direction):
    def fn(surface, rect, color, width=2):
        cx, cy = rect.center
        s = min(rect.w, rect.h) * 0.24
        if direction == 'left':
            pts = [(cx + s * .6, cy - s), (cx - s * .6, cy), (cx + s * .6, cy + s)]
        elif direction == 'right':
            pts = [(cx - s * .6, cy - s), (cx + s * .6, cy), (cx - s * .6, cy + s)]
        elif direction == 'up':
            pts = [(cx - s, cy + s * .6), (cx, cy - s * .6), (cx + s, cy + s * .6)]
        else:
            pts = [(cx - s, cy - s * .6), (cx, cy + s * .6), (cx + s, cy - s * .6)]
        uk.draw_line_on(surface, color, pts[0], pts[1], width)
        uk.draw_line_on(surface, color, pts[1], pts[2], width)
    return fn


_icon_chev_l = _make_chevron('left')
_icon_chev_r = _make_chevron('right')
_icon_chev_d = _make_chevron('down')
_icon_chev_u = _make_chevron('up')


def _icon_play(surface, rect, color):
    box = pygame.Rect(0, 0, rect.w * 0.44, rect.h * 0.52)
    box.center = (rect.centerx + rect.w * 0.03, rect.centery)
    _blit_poly(surface, box, color, [(0, 0), (1, 0.5), (0, 1)])


def _icon_stop(surface, rect, color):
    s = min(rect.w, rect.h) * 0.40
    box = pygame.Rect(0, 0, s, s)
    box.center = rect.center
    uk.draw_rect_on(surface, color, box, 0, 3)


def _polyline(surface, color, pts, width=2):
    for p, q in zip(pts, pts[1:]):
        uk.draw_line_on(surface, color, p, q, width)


def _arrow_head(surface, color, tip, toward, size, width=2):
    """Two barbs at *tip* opening back toward *toward* (a point on the shaft)."""
    ang = math.atan2(toward[1] - tip[1], toward[0] - tip[0])
    for d in (0.62, -0.62):
        a = ang + d
        uk.draw_line_on(surface, color, tip, (tip[0] + size * math.cos(a), tip[1] + size * math.sin(a)), width)


def _curved_arrow(surface, rect, color, width, mirror):
    """Undo / redo: a 3/4 arc with the arrowhead on its top-left (undo) or
    top-right (redo) end."""
    cx, cy = rect.center
    s = min(rect.w, rect.h)
    r = s * 0.28
    sign = -1 if mirror else 1
    pts = []
    for i in range(0, 12):
        a = math.radians(150 - i * 20)          # 150 deg -> -70 deg, clockwise
        pts.append((cx + sign * r * math.cos(a) + sign * s * 0.03, cy + s * 0.05 - r * math.sin(a)))
    _polyline(surface, color, pts, width)
    _arrow_head(surface, color, pts[0], pts[2], s * 0.24, width)


def _icon_undo(surface, rect, color, width=2):
    _curved_arrow(surface, rect, color, width, False)


def _icon_redo(surface, rect, color, width=2):
    _curved_arrow(surface, rect, color, width, True)


def _icon_save(surface, rect, color, width=2):
    cx, cy = rect.center
    s = min(rect.w, rect.h) * 0.74
    body = pygame.Rect(0, 0, s, s)
    body.center = (cx, cy)
    uk.draw_rect_on(surface, color, body, width, 3)
    top = pygame.Rect(0, 0, s * 0.50, s * 0.30)
    top.midtop = (body.centerx - s * 0.04, body.y + 1)
    uk.draw_rect_on(surface, color, top, 0, 1)
    slot = pygame.Rect(0, 0, s * 0.56, s * 0.34)
    slot.midbottom = (body.centerx, body.bottom - 2)
    uk.draw_rect_on(surface, color, slot, 1, 1)


def _icon_dup(surface, rect, color, width=2):
    cx, cy = rect.center
    s = min(rect.w, rect.h)
    a = pygame.Rect(0, 0, s * 0.44, s * 0.44)
    a.center = (cx - s * 0.10, cy + s * 0.10)
    b = a.move(int(s * 0.20), -int(s * 0.20))
    uk.draw_rect_on(surface, color, b, width, 3)
    uk.draw_rect_on(surface, (*_BAR_BG, 255), a.inflate(1, 1), 0, 3)
    uk.draw_rect_on(surface, color, a, width, 3)


def _icon_paste(surface, rect, color, width=2):
    cx, cy = rect.center
    s = min(rect.w, rect.h)
    board = pygame.Rect(0, 0, s * 0.50, s * 0.60)
    board.center = (cx, cy + s * 0.04)
    uk.draw_rect_on(surface, color, board, width, 3)
    clip = pygame.Rect(0, 0, s * 0.26, s * 0.14)
    clip.midtop = (cx, board.top - s * 0.06)
    uk.draw_rect_on(surface, color, clip, 0, 2)
    uk.draw_rect_on(surface, color, pygame.Rect(board.x + 4, board.centery, board.w - 8, 1), 0, 0)
    uk.draw_rect_on(surface, color, pygame.Rect(board.x + 4, board.centery + 4, board.w - 8, 1), 0, 0)


def _icon_grid(surface, rect, color, width=1):
    cx, cy = rect.center
    s = min(rect.w, rect.h) * 0.30
    for d in (-s * 0.5, s * 0.5):
        uk.draw_line_on(surface, color, (cx + d, cy - s), (cx + d, cy + s), width + 1)
        uk.draw_line_on(surface, color, (cx - s, cy + d), (cx + s, cy + d), width + 1)


def _icon_pick(surface, rect, color, width=2):
    cx, cy = rect.center
    s = min(rect.w, rect.h)
    r = int(s * 0.17)
    uk.draw_circle_on(surface, color, (cx, cy), r, width)
    g = s * 0.30
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        uk.draw_line_on(surface, color, (cx + dx * (r + 1), cy + dy * (r + 1)),
                        (cx + dx * g, cy + dy * g), width)


def _icon_speaker(surface, rect, color, width=2):
    cx, cy = rect.center
    s = min(rect.w, rect.h)
    box = pygame.Rect(0, 0, s * 0.16, s * 0.20)
    box.center = (cx - s * 0.14, cy)
    uk.draw_rect_on(surface, color, box, 0, 1)
    cone = pygame.Rect(box.right - 1, int(cy - s * 0.17), int(s * 0.18), int(s * 0.34))
    _blit_poly(surface, cone, color, [(0, 0.3), (1, 0), (1, 1), (0, 0.7)])
    a = [(cx + s * 0.08 + s * 0.14 * math.cos(math.radians(-50 + 100 * i / 6)),
          cy - s * 0.14 * math.sin(math.radians(-50 + 100 * i / 6))) for i in range(7)]
    _polyline(surface, color, a, 2)


def _icon_camera_track(surface, rect, color, width=2):
    cx, cy = rect.center
    s = min(rect.w, rect.h)
    body = pygame.Rect(0, 0, s * 0.5, s * 0.34)
    body.center = (cx - s * 0.04, cy)
    uk.draw_rect_on(surface, color, body, width, 3)
    lens = pygame.Rect(body.right, int(cy - s * 0.11), int(s * 0.16), int(s * 0.22))
    _blit_poly(surface, lens, color, [(0, 0.3), (1, 0), (1, 1), (0, 0.7)])



# =============================================================================
# CutsceneEditor
# =============================================================================

class CutsceneEditor:
    """Full cutscene editor.  Mirrors SpriteEditor / RoomEditor API:
    toggle() / handle_input(event) / update(dt) / draw(screen).

    Two top-level views:
      'list' — cutscene browser (create, open, delete)
      'edit' — workspace with viewport, timeline and inspector panels
    """

    _UNDO_LIMIT = 50

    # Keyframe clipboard, shared by every editor instance so a group copied in
    # one cutscene can be pasted into another.  [(offset_from_first_s, action)]
    _clipboard: list = []

    def __init__(self, room_manager, room_editor, screen_width, screen_height,
                 dialogue_box=None, sound_manager=None):
        self.room_manager  = room_manager
        self.room_editor   = room_editor
        self.screen_width  = screen_width
        self.screen_height = screen_height
        self.dialogue_box  = dialogue_box
        # The editor's own QTE bar for previewing 'qte' cutscene steps (the
        # game's bar isn't used while the editor is open). Optional — if the
        # widget/assets can't load, 'qte' steps just stay no-ops in preview.
        try:
            from ui.spam_qte import SpamQTEBar
            self._qte_bar = SpamQTEBar()
        except Exception as _e:
            print(f'[CutsceneEditor] QTE preview unavailable: {_e}')
            self._qte_bar = None
        # Used to populate the Music Track / Sound Effect pickers on the
        # play_music / play_sfx action forms, and to let the dev preview a
        # track or sfx instantly from the inspector without running the
        # whole cutscene. None is tolerated (pickers just show no options).
        self.sound_manager = sound_manager
        self.active        = False

        # ── Fonts ─────────────────────────────────────────────────────────────
        # Same two-glyph-set split as DevMenu / RoomEditor: the plain
        # upper/lowercase set for big titles, the menu set for everything else.
        fonts_root = os.path.join('assets', 'ui', 'fonts')
        self._menu_font = uk.BitmapFont(fonts_root, letter_spacing=1)
        self._title_bitmap_font = uk.BitmapFont(fonts_root, letter_spacing=1)
        self._title_bitmap_font.uppercase_dir = os.path.join('assets', 'ui', 'fonts', 'uppercase')
        self._title_bitmap_font.lowercase_dir = os.path.join('assets', 'ui', 'fonts', 'lowercase')
        self.font_title  = _UiFont(self._title_bitmap_font, 32)
        self.font_large  = _UiFont(self._menu_font, 20)
        self.font_medium = _UiFont(self._menu_font, 16)
        self.font_small  = _UiFont(self._menu_font, 12)

        # Icons that ship as PNGs with the dev menu (vector fallback if absent)
        self._icon_back = _load_dev_menu_icon('back', 34, uk.draw_back_icon)
        self._icon_back_sm = _load_dev_menu_icon('back', 26, uk.draw_back_icon)
        self._icon_plus_png = _load_dev_menu_icon('plus', 34, _icon_plus)
        # Header Save button — 18 px is the icon box every labelled button uses.
        self._icon_save_png = _load_dev_menu_icon('save', 18, _icon_save)
        # Delete / trash — 18 px for labelled "Delete" buttons (matches Save
        # above), 26 px for the icon-only trash button on list cards.
        self._icon_trash_png    = _load_dev_menu_icon('trash', 18, _icon_trash)
        self._icon_trash_png_sm = _load_dev_menu_icon('trash', 26, _icon_trash)
        # Pick-in-viewport / duplicate — always used on labelled buttons, so
        # one 18 px bake each is enough (same convention as Save/Delete).
        self._icon_pick_png = _load_dev_menu_icon('pick', 18, _icon_pick)
        self._icon_dup_png  = _load_dev_menu_icon('duplicate', 18, _icon_dup)

        # ── Editor state ──────────────────────────────────────────────────────
        self.view          = 'list'
        self.cutscene_data = None
        self.cutscene_name = ''
        self.unsaved       = False
        self._autosave_t   = 0.0   # seconds since last autosave
        # QTE fail-sequence view (see _enter_fail_edit). None = normal editing.
        self._fail_edit    = None

        # Undo / redo stacks — each entry is a deep copy of cutscene_data.
        # _push_undo() must be called BEFORE every mutation so Ctrl-Z can
        # restore the state that existed just before that edit.
        self._undo_stack: list = []
        self._redo_stack: list = []

        # ── UI runtime (immediate-mode plumbing) ──────────────────────────────
        self._hits         = []       # [(rect, action, arg)] rebuilt every draw
        self._text_rects   = []       # text-entry rects (I-beam cursor)
        self._blocks       = []       # rects that shield widgets underneath
        self._blocks_prev  = []
        self._in_overlay   = False
        self._clip_rect    = None
        self._clip_stack   = []
        self._anims        = {}       # widget key -> eased 0..1
        self._dt           = 1 / 60
        self._blink        = 0.0      # text-caret blink clock
        self._mouse_pos    = tuple(pygame.mouse.get_pos())
        self._last_input   = 'mouse'
        self._fit_cache    = {}
        self._layout_key   = None
        self._marker_labels = []

        # Shared dropdown overlay (one instance for every picker)
        self._dd = None

        # ── List view ─────────────────────────────────────────────────────────
        self._files          = []
        self._file_meta      = {}
        self._list_sel       = -1
        self._list_scroll    = 0
        self._list_confirm   = None   # cutscene name awaiting delete confirmation
        self._new_name_buf   = ''
        self._new_name_focus = False
        self._list_msg       = ''

        # ── Scene panel (left) ────────────────────────────────────────────────
        self._left_scroll    = 0
        self._left_content_h = 0

        # ── Edit view — timeline ──────────────────────────────────────────────
        self._tl_sel         = -1
        self._tl_scroll_y    = 0.0    # vertical scroll offset (px) — track rows

        # Per-category expand/collapse state for the graphical timeline, e.g.
        # {'screen': True} once the designer has clicked open the Screen
        # category to reveal its Fade In / Fade Out / … sub-lanes. Keyed by
        # track target id (matches the 'target' field on actions). Not
        # persisted to the cutscene JSON — purely an editor UI convenience,
        # same spirit as _tl_scroll_y. Missing key == collapsed.
        self._tl_expanded    = {}

        self._tl_time_zoom   = 70.0   # pixels per second
        self._tl_scroll_x    = 0.0    # horizontal scroll offset (px)
        self._tl_play_drag   = False
        self._tl_playhead_t  = 0.0    # scrub playhead time (seconds)
        self._tl_zoom_min    = 20.0
        self._tl_zoom_max    = 300.0
        self._tl_auto_scroll = 0.0    # px/sec applied during playhead/kf drag near edges
        self._scrub_pending  = False  # True when _tl_playhead_t moved but seek() hasn't run yet

        # ── Inspector (right) — action form ───────────────────────────────────
        self._form_active    = False
        self._form_new       = False
        self._form_target    = 'camera'
        self._form_type      = 'pan_to'
        self._form_time_buf  = '0.0'
        self._form_params    = {}
        self._form_focus     = None
        self._text_limit_hit = False  # True while typing hits the dialogue box's capacity
        # Caret / selection shared by every inline text field (only one can be
        # focused at a time) — see the "Inline text editing" helpers.
        self._tc_ident         = None    # which field the caret belongs to
        self._tc_pos           = 0       # caret index into that field's text
        self._tc_anchor        = None    # None = no selection; else the other end of it
        self._tc_drag          = False   # left button held after clicking into a field
        self._tc_scroll        = 0       # horizontal scroll (px) of a focused single-line field
        self._tc_vscroll       = None    # first visible line of a focused multiline field
        self._tc_geom          = None    # hit-test geometry of the focused field (set by draw)
        self._tc_pending_click = None    # click pos to turn into a caret on the next draw
        self._tc_repeat_on     = False   # key auto-repeat enabled while a field is focused
        self._form_target_idx  = 0
        self._form_type_idx    = 0
        # Tracks the active group filter when browsing rooms for a change_room action.
        self._form_room_group  = ''
        self._insp_scroll      = 0
        self._insp_content_h   = 0

        # ── Actor add form (floating card over the viewport) ──────────────────
        self._actor_form     = False
        self._actor_type_idx = 0
        self._actor_id_buf   = 'actor_0'
        self._actor_etype_buf= 'tiger_bandit'
        self._actor_focus    = None
        self._actor_sel      = -1

        # ── Duration field (header inline editor) ─────────────────────────────
        self._duration_buf   = '10.0'
        self._duration_focus = False

        # ── Viewport zoom ─────────────────────────────────────────────────────
        # camera.x/y are stored in base-scale pixels (RENDER_SCALE × world_units).
        # zoom only affects rendering; world coord math divides by zoom.
        self._vp_zoom     = 1.0
        # Same continuous zoom the room editor uses (Ctrl+scroll, 0.55x-1.0x,
        # 0.1 per notch) so both editors feel identical.
        self._vp_zoom_min  = 0.55
        self._vp_zoom_max  = 1.0
        self._vp_zoom_step = 0.1
        # When False, previews (scrub + play) leave the editor camera alone
        # instead of following the cutscene camera. Toggled from the header.
        self._cam_track = True

        # ── Grid visibility (toggled with G) ──────────────────────────────────
        self._show_grid   = True

        # ── Actor placement grid snap ─────────────────────────────────────────
        # Snaps actor spawn position (initial pick_actor placement + drag) to
        # a world-unit grid, same idea as the room editor's tile grid but with
        # its own toggle since actors don't need to sit exactly on a tile.
        self._actor_snap_enabled = False
        self._actor_snap_sizes   = [8, 16, 32]   # world units; TILE_SIZE == 32
        self._actor_snap_idx     = 1             # default 16x16

        # ── Timeline grid snap ────────────────────────────────────────────────
        # Snaps keyframe drag time to a fixed interval, with vertical guide
        # lines drawn through the track area at each interval so alignment
        # across tracks (e.g. lining up a fade_in with a dialogue line) is
        # visual, not just numeric.
        self._tl_grid_enabled   = False
        self._tl_grid_intervals = [1.0, 0.5, 0.25, 0.1]
        self._tl_grid_idx       = 0

        # ── Viewport right-click pan ──────────────────────────────────────────
        self._vp_drag      = False
        self._vp_drag_last = (0, 0)

        # ── Actor sprite previews ─────────────────────────────────────────────
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
        # Multi-select: when two or more keyframes are selected their identities
        # (id(action_dict)) live here and _tl_sel is -1.  With 0-1 selected this
        # set is empty and the single-selection _tl_sel is used as before.
        # Identity (not index) because actions get re-sorted by time.
        self._tl_multi_ids   = set()
        # Everything that moves during a keyframe drag: [(action_dict, start_time)].
        # Always contains at least the grabbed keyframe while a drag is live.
        self._kf_drag_group  = []
        self._kf_drag_orig   = 0.0   # start time of the grabbed keyframe
        # Rubber-band selection started by dragging on empty timeline space.
        # Anchored in content space (time, row-pixels) so it survives scrolling.
        self._tl_marquee     = None

        # ── Actor initial-position drag ───────────────────────────────────────
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
        # Animated tiles (water, flags, etc.) can't live in the surface above —
        # it's baked once and never touched again, so a cycling tile would
        # freeze on whatever frame it happened to be baked with. Mirrors
        # game._animated_tile_lists. Keyed the same as _vp_tile_surfaces.
        self._vp_animated_tiles: dict = {}

        # Geometry (also sizes the camera to the viewport panel)
        self._layout()
        self.camera       = Camera(self._vp_rect.w, self._vp_rect.h)
        self.camera.x     = 0
        self.camera.y     = 0
        self.camera_speed = 300

    # ══════════════════════════════════════════════════════════════════════════
    # Layout
    # ══════════════════════════════════════════════════════════════════════════

    def _layout(self):
        """Compute every panel rect from the current screen size.  Re-run from
        draw() whenever screen_width / screen_height change."""
        w, h = int(self.screen_width), int(self.screen_height)
        self._layout_key = (w, h)

        # --- list view: same numbers RoomEditor / DevMenu use ---------------
        self.margin_x        = 56
        self.list_header_h   = max(86, round(h * 0.12))
        self.list_footer_h   = max(42, round(h * 0.065))
        self.card_h          = 68

        back = max(40, round(self.list_header_h * 0.55))
        self._list_back_rect = pygame.Rect(self.margin_x, (self.list_header_h - back) // 2, back, back)
        self._list_add_rect  = pygame.Rect(self.margin_x, h - self.list_footer_h - 24 - back, back, back)

        # --- edit view --------------------------------------------------------
        gap = self._gap = 10
        self.header_h = 56
        mid_top    = self.header_h + gap
        mid_bottom = h - gap

        tl_h = _clamp(round(h * 0.34), 236, 420)
        tl_h = min(tl_h, max(190, (mid_bottom - mid_top) - 170))
        left_w  = _clamp(round(w * 0.18), 216, 320)
        right_w = _clamp(round(w * 0.235), 270, 420)

        # The Inspector is a full-height sidebar (its forms are the tallest thing
        # in the editor); the timeline runs under the Scene panel + viewport.
        self._right_frame = pygame.Rect(w - gap - right_w, mid_top, right_w, mid_bottom - mid_top)
        self._tl_frame    = pygame.Rect(gap, mid_bottom - tl_h, self._right_frame.left - gap * 2, tl_h)
        top_h             = self._tl_frame.top - gap - mid_top
        self._left_frame  = pygame.Rect(gap, mid_top, left_w, top_h)
        vx = self._left_frame.right + gap
        self._vp_frame    = pygame.Rect(vx, mid_top, self._right_frame.left - gap - vx, top_h)
        self._vp_rect     = self._vp_frame.inflate(-12, -12)

        # Timeline inner metrics
        self._tl_hdr_h   = 44     # toolbar strip
        self._tl_ruler_h = 26
        self._tl_row_h   = 32
        self._tl_label_w = _clamp(round(w * 0.12), 132, 200)

        self._ctl_h = 34          # standard control height

        if getattr(self, 'camera', None) is not None:
            self._sync_camera_view()
            self._clamp_camera()

    def _sync_camera_view(self):
        """Give the camera the *virtual* viewport size (viewport / zoom), the
        same way the room editor does. The runtime centres and clamps the
        camera using camera.screen_width/height, so with the real (unzoomed)
        size it centred on the wrong point whenever the view was zoomed out."""
        cam = self.camera
        zoom = self._vp_zoom or 1.0
        cam.screen_width  = max(1, int(self._vp_rect.w / zoom))
        cam.screen_height = max(1, int(self._vp_rect.h / zoom))

    # ══════════════════════════════════════════════════════════════════════════
    # Public API
    # ══════════════════════════════════════════════════════════════════════════

    def toggle(self):
        """Open or close the editor.

        On close: auto-saves dirty work, then always persists the viewport
        state (camera pos + zoom) so the next open lands right where you left.
        Also cuts any music instantly — the Preview button and the live
        CutsceneRuntime used for timeline scrubbing/playback both drive the
        real SoundManager, so a track previewed (or a play_music/stop_music
        action scrubbed over) while the editor was open can otherwise keep
        playing indefinitely after you leave, with no fade-out ever fired to
        end it. fade_out=False so there's no lingering fade-out delay either.
        On open: refreshes the file list so newly added cutscenes appear.
        """
        if self.active:
            # Closing the editor — flush any unsaved work and the viewport state
            if self.view == 'edit' and self.cutscene_data:
                if self._duration_focus:
                    self._commit_duration()
                if self.unsaved:
                    self._save_cutscene()
                else:
                    self._save_viewport_state()
            if self.sound_manager is not None:
                self.sound_manager.stop_music(fade_out=False)
                if self._runtime is not None:
                    self._runtime.stop_looping_sfx()
            uk.set_text_cursor(False)
            uk.set_hand_cursor(False)
        self.active = not self.active
        self._dd = None
        self._duration_focus = False
        self._form_focus = None
        self._actor_focus = None
        self._new_name_focus = False
        self._tc_reset()
        self._list_confirm = None
        if self.active:
            self._mouse_pos = tuple(pygame.mouse.get_pos())
            self._refresh_file_list()

    def handle_input(self, event):
        """Route a pygame event to the correct sub-handler.

          KEYDOWN           → _on_keydown
          Left-click down   → _on_click (widgets, timeline, viewport)
          Left-click up     → finalise playhead/kf/actor drag
          Right-click down  → start viewport pan
          Right-click up    → end viewport pan
          MOUSEMOTION       → _on_mouse_motion (drag, hover)
          MOUSEWHEEL        → _on_scroll (zoom / scroll)
        """
        if not self.active:
            return None

        if event.type in (pygame.MOUSEMOTION, pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
            self._mouse_pos = tuple(event.pos)

        if event.type == pygame.KEYDOWN:
            self._last_input = 'keyboard'
            return self._on_keydown(event)

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self._last_input = 'mouse'
            return self._on_click(event.pos)

        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            self._tc_drag        = False
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
                if (moved_act is not None and moved_act in actions
                        and not self._tl_multi_ids):
                    self._tl_sel = actions.index(moved_act)
                self._kf_drag_idx = -1
                self._kf_drag_group = []
                self._runtime     = None   # stale; rebuild on next scrub/play
            # Finish a rubber-band selection.
            if self._tl_marquee is not None:
                self._finish_marquee()
            # Finish an actor initial-position drag.
            if self._actor_drag_idx >= 0:
                self._actor_drag_idx = -1
                self._runtime = None  # actor start pos changed; force rebuild

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 3:
            if self.view == 'edit' and self._vp_rect.collidepoint(event.pos) and self._dd is None:
                self._vp_drag      = True
                self._vp_drag_last = event.pos
            return None

        if event.type == pygame.MOUSEBUTTONUP and event.button == 3:
            self._vp_drag = False

        if event.type == pygame.MOUSEMOTION:
            self._last_input = 'mouse'
            if self._tc_drag:
                self._tc_drag_to(event.pos)
            self._on_mouse_motion(event.pos)

        if event.type == pygame.MOUSEWHEEL:
            self._on_scroll(event)

        return None

    def update(self, dt):
        """Advance editor state by *dt* seconds each frame.

        Handles: WASD camera pan, deferred timeline scrub, edge-scroll while
        dragging, autosave timer, and live playback via CutsceneRuntime.
        (The OS cursor is resolved in draw(), not here — see _resolve_cursor.)
        """
        if not self.active:
            return

        self._dt     = min(max(dt, 0.0), 1 / 20) if dt > 0 else 1 / 60
        self._blink += self._dt
        self._tc_sync()

        # Suppress manual camera pan during active playback — the runtime owns
        # the camera while playing, and fighting it causes jitter.  Also guards
        # K_s so it doesn't block the play path when no text field is focused.
        if (self.view == 'edit'
                and (not self._playing or not self._cam_track)
                and not self._form_focus
                and not self._actor_focus
                and not self._duration_focus
                and self._dd is None):
            keys  = pygame.key.get_pressed()
            shift = keys[pygame.K_LSHIFT] or keys[pygame.K_RSHIFT]
            ctrl  = keys[pygame.K_LCTRL] or keys[pygame.K_RCTRL]
            # Pan speed scales with zoom so movement feels consistent:
            # zoomed out → faster pan (covers more world), zoomed in → slower
            spd = self.camera_speed * (2 if shift else 1) / self._vp_zoom
            if not ctrl:   # Ctrl+S / Ctrl+Z etc. must not also pan the camera
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
            self._tl_scroll_x = max(self._tl_min_scroll(), self._tl_scroll_x + self._tl_auto_scroll * dt)
            tl          = self._tl_panel_rect()
            label_end_x = tl.x + self._tl_label_w
            mx          = self._mouse_pos[0]
            dur         = self.cutscene_data.get('duration', 10.0)
            if self._tl_play_drag:
                t = (mx - label_end_x + self._tl_scroll_x) / self._tl_time_zoom
                self._tl_playhead_t = _clamp(t, self._tl_min_t(), dur)
                self._scrub_pending = True
            elif self._kf_drag_idx >= 0:
                self._apply_kf_drag(mx)

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
            hold = None if self._cam_track else (self.camera.x, self.camera.y)
            try:
                self._runtime.update(dt, w, h)
                if hold is not None:
                    self.camera.x, self.camera.y = hold
            except Exception as _e:
                import traceback
                print(f'[CutsceneEditor] runtime.update error: {_e}')
                traceback.print_exc()
                self._stop_preview()
                return
            # Keep the dialogue box animation ticking (typewriter effect, etc.)
            if self.dialogue_box:
                self.dialogue_box.update(dt)
            # Advance a running 'qte' step's bar (the runtime holds the timeline
            # frozen until it goes inactive).
            if self._qte_bar is not None and self._qte_bar.active:
                self._qte_bar.update(dt)
            if room:
                self._clamp_camera()
            self._tl_playhead_t = self._runtime.elapsed
            if self._runtime.finished:
                self._stop_preview()

    def draw(self, screen):
        if not self.active:
            return
        if self._layout_key != (int(self.screen_width), int(self.screen_height)):
            self._layout()

        self._tc_sync()

        # Fresh registries every frame — hit-testing is always in sync with
        # what was actually painted, never cached across frames.
        self._hits       = []
        self._text_rects = []
        self._blocks     = []
        self._in_overlay = False
        self._clip_rect  = None
        self._clip_stack = []
        # While a dropdown is open, everything underneath stops reacting to hover.
        if self._dd is not None:
            self._blocks_prev = [pygame.Rect(0, 0, self.screen_width, self.screen_height)]

        if self.view == 'list':
            self._draw_list(screen)
        else:
            self._draw_edit(screen)
        self._draw_dropdown(screen)
        self._tc_pending_click = None   # the focused field (if visible) already used it

        if self._dd is None:
            self._blocks_prev = self._blocks

        # OS cursor, resolved dead last — after every widget this frame has
        # had a chance to register a hit/text-field rect, and after
        # dev_menu.draw() (called just before this one, see Game.draw())
        # has already made whatever incidental cursor calls it makes for
        # its own always-drawn chrome (e.g. its persistent toggle icon,
        # via ui_kit's shared register_hoverable/update_hover_cursor).
        # Resolving here, last, is what makes this editor's cursor choice
        # stick for the frame. Doing this in update() instead — which runs
        # BEFORE dev_menu.draw() — meant dev_menu's own draw-time cursor
        # call would stomp ours right back to the arrow a moment later,
        # every single frame, which is what caused the hand/arrow flicker.
        self._resolve_cursor()

    def _resolve_cursor(self):
        """Switch the OS cursor to an I-beam over any real text-entry field
        registered this frame (self._text_rects), else to a hand over any
        other clickable hit (self._hits — buttons, list/actor rows, etc.,
        anything with a non-None, non-'field' action) or an open dropdown's
        row, else back to the plain arrow. Same convention and priority as
        ObjectEditor's _resolve_field_cursor + update_hover_cursor: the
        I-beam always wins where a field and a button happen to overlap.

        Must be called at the very end of draw(), after every widget for
        this frame has registered its rect — see the call site's comment
        for why running this any earlier (e.g. from update()) causes the
        cursor to flicker.
        """
        if self._dd is not None:
            # Dropdown open: only its own rows matter — everything
            # underneath is blocked from hover/click either way.
            hovering_text_field = False
            hovering_widget = any(row.collidepoint(self._mouse_pos)
                                   for row, _value in self._dd.get('rows', []))
        else:
            hovering_text_field = any(r.collidepoint(self._mouse_pos) for r in self._text_rects)
            hovering_widget = (not hovering_text_field and any(
                action is not None and action != 'field' and r.collidepoint(self._mouse_pos)
                for r, action, arg, full in self._hits))
        uk.set_text_cursor(hovering_text_field)
        uk.set_hand_cursor(hovering_widget)

    # ══════════════════════════════════════════════════════════════════════════
    # Small shared helpers
    # ══════════════════════════════════════════════════════════════════════════

    def _blur_text(self):
        """Drop focus from every inline text field (committing the duration
        field, which is the only one that applies on blur)."""
        if self._duration_focus:
            self._commit_duration()
        self._duration_focus = False
        self._form_focus     = None
        self._actor_focus    = None

    def _mouse_world(self):
        """World coords under the cursor (only meaningful inside the viewport)."""
        vp = self._vp_rect
        vx, vy = self._mouse_pos[0] - vp.x, self._mouse_pos[1] - vp.y
        # The draw pass truncates the camera to whole pixels; using the same
        # value here keeps a click exactly on the pixel it visually hit.
        wx = (vx / self._vp_zoom + int(self.camera.x)) / RENDER_SCALE
        wy = (vy / self._vp_zoom + int(self.camera.y)) / RENDER_SCALE
        return wx, wy

    def _world_to_screen(self, wx, wy):
        vp = self._vp_rect
        return (int(vp.x + (wx * RENDER_SCALE - int(self.camera.x)) * self._vp_zoom),
                int(vp.y + (wy * RENDER_SCALE - int(self.camera.y)) * self._vp_zoom))

    def _leave_edit(self):
        """Back to the cutscene list, saving first (button and Esc share this
        so neither can drop unsaved work)."""
        if self._playing:
            self._stop_preview()
        self._blur_text()
        self._dd = None
        if self.unsaved:
            # _save_cutscene internally calls _save_viewport_state too
            self._save_cutscene()
        else:
            self._save_viewport_state()
        self.view = 'list'
        self._refresh_file_list()

    def _submit_new_name(self):
        n = self._new_name_buf.strip()
        if not n:
            self._list_msg = 'Enter a name first.'
            return
        if os.path.exists(_cutscene_path(n)):
            self._list_msg = 'A cutscene with that name already exists.'
            return
        self._create_and_open(n)

    # ══════════════════════════════════════════════════════════════════════════
    # Input handlers
    # ══════════════════════════════════════════════════════════════════════════

    def _on_keydown(self, event):
        """Dispatch keyboard events for the editor.

        ESC walks back through layers (dropdown → text field → pick mode →
        actor form → inspector form → back to list → close editor).
        Space toggles playback when no text field is active.
        Ctrl-S saves; Ctrl-Z/Y/Shift-Z undo/redo; G toggles the grid.
        Ctrl-C / X / V copy, cut and paste the selected keyframe(s) at the
        playhead (a whole multi-selection stays together); Ctrl-D duplicates,
        Ctrl-A selects all, Delete removes.  Arrow keys nudge X/Y in an open
        move / fly / teleport form (Shift = 10).
        Everything else falls through to whichever text field has focus.
        """
        key = event.key
        typing = bool(self._form_focus or self._actor_focus
                      or self._new_name_focus or self._duration_focus)

        if key == pygame.K_ESCAPE:
            # Dismiss overlays in stack order — most-modal first.
            if self._dd is not None:
                self._dd = None
                return None
            if self._duration_focus:
                self._commit_duration()
                self._duration_focus = False
                return None
            if self._new_name_focus:
                self._new_name_focus = False
                self._new_name_buf   = ''
                self._list_msg       = ''
                return None
            if self._form_focus or self._actor_focus:
                self._form_focus  = None
                self._actor_focus = None
                return None
            if self._list_confirm is not None:
                self._list_confirm = None
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
                self._stop_preview_sound()
                return None
            if self.view == 'edit' and self._tl_multi_ids:
                self._set_selection([])
                return None
            if self.view == 'edit':
                if self._playing:
                    self._stop_preview()
                else:
                    self._leave_edit()
                return None
            # List view with nothing else to dismiss — same as the back
            # arrow: reopen the Dev Menu instead of vanishing into gameplay.
            self.toggle()
            return 'back_to_dev_menu'

        # Space plays / stops — but not while a dialogue box is open because
        # the user needs space to dismiss dialogue lines during preview.
        if key == pygame.K_SPACE and self.view == 'edit' and not typing:
            if self.dialogue_box and getattr(self.dialogue_box, 'active', False):
                return None
            if self._playing:
                self._stop_preview()
            else:
                self._start_preview()
            return None

        # E / Q while a 'qte' step's bar is up: mash / lock the crosshair, same
        # keys as in game. Takes priority over everything below.
        if (key in (pygame.K_e, pygame.K_q) and self.view == 'edit' and self._playing
                and self._qte_bar is not None and self._qte_bar.active):
            self._qte_bar.register_press()
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

        # Clipboard / selection shortcuts — never while a text field owns the
        # keyboard, so Ctrl-C / Ctrl-V / Delete keep working inside fields.
        if self.view == 'edit' and not typing:
            ctrl = bool(event.mod & (pygame.KMOD_CTRL | pygame.KMOD_META))
            if ctrl and key == pygame.K_c:
                self._copy_selection()
                return None
            if ctrl and key == pygame.K_x:
                self._copy_selection()
                self._delete_selection()
                return None
            if ctrl and key == pygame.K_v:
                self._paste_clipboard()
                return None
            if ctrl and key == pygame.K_d:
                self._on_action('tl_dup', None, None)
                return None
            if ctrl and key == pygame.K_a and self.cutscene_data:
                self._set_selection([a for a in self.cutscene_data.get('actions', [])
                                     if not a.get('_prefix')])
                return None
            if key in (pygame.K_DELETE, pygame.K_BACKSPACE) and self._sel_actions():
                self._delete_selection()
                return None
            # Arrow keys nudge the X / Y of the open action form (1 unit,
            # Shift = 10) — fine-tuning after a rough click in the viewport.
            if (key in (pygame.K_LEFT, pygame.K_RIGHT, pygame.K_UP, pygame.K_DOWN)
                    and self._form_active
                    and {'x', 'y'} <= {k for k, _l, _h in _ACTION_PARAMS.get(self._form_type, [])}):
                step = 10.0 if event.mod & pygame.KMOD_SHIFT else 1.0
                dx = {pygame.K_LEFT: -step, pygame.K_RIGHT: step}.get(key, 0.0)
                dy = {pygame.K_UP: -step, pygame.K_DOWN: step}.get(key, 0.0)
                self._nudge_form_xy(dx, dy)
                return None

        # Grid toggle — only when no text field has focus so typing 'g' in a
        # name / param field is never intercepted.
        if (key == pygame.K_0 and self.view == 'edit' and not typing
                and (pygame.key.get_mods() & pygame.KMOD_CTRL)):
            self._vp_zoom = 1.0          # Ctrl+0 → native zoom, like the room editor
            self._sync_camera_view()
            self._clamp_camera()
            return None

        if key == pygame.K_g and self.view == 'edit' and not typing:
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

        # List-view keyboard navigation
        if self.view == 'list' and self._files:
            n = len(self._files)
            if key in (pygame.K_DOWN, pygame.K_s):
                self._list_sel = min(n - 1, self._list_sel + 1)
            elif key in (pygame.K_UP, pygame.K_w):
                self._list_sel = max(0, self._list_sel - 1) if self._list_sel >= 0 else 0
            elif key in (pygame.K_RETURN, pygame.K_KP_ENTER) and 0 <= self._list_sel < n:
                self._load_cutscene(self._files[self._list_sel])
            elif key == pygame.K_DELETE and 0 <= self._list_sel < n:
                self._list_confirm = self._files[self._list_sel]

        return None

    # ══════════════════════════════════════════════════════════════════════════
    # Inline text editing — caret, selection highlight, clipboard
    #
    # Same behaviour the room editor's text fields have: a movable caret,
    # Shift+arrows / Shift+click / mouse-drag selection (drawn as a highlight),
    # Ctrl+A select-all, Ctrl+C / Ctrl+X / Ctrl+V, Home / End, and
    # Backspace / Delete / typing that replace the selection first.  The
    # dialogue text box is multiline: Up / Down / Home / End work per visual
    # line and Enter inserts a line break.
    # ══════════════════════════════════════════════════════════════════════════

    _NAME_BAD_CHARS = '\\/:*?"<>|'   # not allowed in a cutscene (file) name

    def _tc_ident_now(self):
        """Identity of the focused field, in the same priority order the
        keyboard routing in _on_keydown uses."""
        if self._new_name_focus:
            return ('new_name',)
        if self._duration_focus:
            return ('duration',)
        if self._form_focus:
            return ('form', self._form_focus)
        if self._actor_focus:
            return ('actor', self._actor_focus)
        return None

    def _tc_get(self, ident):
        if ident is None:
            return ''
        kind = ident[0]
        if kind == 'new_name':
            v = self._new_name_buf
        elif kind == 'duration':
            v = self._duration_buf
        elif kind == 'form':
            v = self._form_time_buf if ident[1] == 'time' else self._form_params.get(ident[1], '')
        elif kind == 'actor':
            v = self._actor_id_buf if ident[1] == 'id' else ''
        else:
            v = ''
        return v if isinstance(v, str) else str(v)

    def _tc_set(self, ident, val):
        kind = ident[0]
        if kind == 'new_name':
            self._new_name_buf = val
        elif kind == 'duration':
            self._duration_buf = val
        elif kind == 'form':
            if ident[1] == 'time':
                self._form_time_buf = val
            else:
                self._form_params[ident[1]] = val
        elif kind == 'actor' and ident[1] == 'id':
            self._actor_id_buf = val

    def _tc_is_multiline(self, ident):
        return ident == ('form', 'text') and self._form_type == 'dialogue'

    def _tc_set_repeat(self, on):
        """Hold-to-repeat for Backspace / arrows while a field is focused
        (the room editor does the same while editing text)."""
        if on == self._tc_repeat_on:
            return
        self._tc_repeat_on = on
        try:
            pygame.key.set_repeat(400, 50) if on else pygame.key.set_repeat(0, 0)
        except pygame.error:
            pass

    def _tc_reset(self):
        self._tc_ident         = None
        self._tc_pos           = 0
        self._tc_anchor        = None
        self._tc_drag          = False
        self._tc_scroll        = 0
        self._tc_vscroll       = None
        self._tc_geom          = None
        self._tc_pending_click = None
        self._tc_set_repeat(False)

    def _tc_sync(self):
        """Reconcile caret state with whichever field is focused right now.
        A newly focused field starts with the caret at its end, no selection.
        Returns the focused field's identity (or None)."""
        ident = self._tc_ident_now()
        if ident != self._tc_ident:
            self._tc_ident   = ident
            self._tc_anchor  = None
            self._tc_pos     = len(self._tc_get(ident))
            self._tc_scroll  = 0
            self._tc_vscroll = None
            self._tc_geom    = None
            self._tc_set_repeat(ident is not None)
            if ident is None:
                self._tc_drag = False
                self._tc_pending_click = None
        elif ident is not None:
            n = len(self._tc_get(ident))
            self._tc_pos = min(self._tc_pos, n)
            if self._tc_anchor is not None:
                self._tc_anchor = min(self._tc_anchor, n)
        return ident

    def _tc_has_sel(self):
        return self._tc_anchor is not None and self._tc_anchor != self._tc_pos

    def _tc_sel_range(self):
        a, b = self._tc_anchor, self._tc_pos
        return (a, b) if a <= b else (b, a)

    def _tc_delete_sel(self, ident):
        """Remove the selected text (if any). True if anything was removed."""
        if not self._tc_has_sel():
            return False
        s, e = self._tc_sel_range()
        buf = self._tc_get(ident)
        self._tc_set(ident, buf[:s] + buf[e:])
        self._tc_pos = s
        self._tc_anchor = None
        return True

    def _tc_insert(self, ident, text):
        """Type / paste *text* at the caret, replacing the selection.  Pasted
        text is sanitised per field, and the dialogue field is trimmed to what
        still fits the dialogue box (a no-op insert never eats the selection)."""
        multiline = self._tc_is_multiline(ident)
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        if multiline:
            text = ''.join(ch for ch in text if ch == '\n' or ch.isprintable())
        else:
            text = ''.join(' ' if ch in '\n\t' else ch for ch in text)
            text = ''.join(ch for ch in text if ch.isprintable())
        if ident == ('new_name',):
            text = ''.join(ch for ch in text if ch not in self._NAME_BAD_CHARS)
        if not text:
            return

        buf = self._tc_get(ident)
        lo, hi = self._tc_sel_range() if self._tc_has_sel() else (self._tc_pos, self._tc_pos)

        if multiline and self.dialogue_box is not None:
            portrait = self._form_params.get('portrait') or None

            def fits(t):
                return self.dialogue_box.fits_box(buf[:lo] + t + buf[hi:], portrait_key=portrait)

            if fits(text):
                self._text_limit_hit = False
            else:
                # Longest prefix of the inserted text that still fits.
                a, b = 0, len(text)
                while a < b:
                    mid = (a + b + 1) // 2
                    if fits(text[:mid]):
                        a = mid
                    else:
                        b = mid - 1
                text = text[:a]
                self._text_limit_hit = True
                if not text:
                    return
        else:
            self._text_limit_hit = False

        if ident == ('new_name',):
            self._list_msg = ''
        self._tc_set(ident, buf[:lo] + text + buf[hi:])
        self._tc_pos = lo + len(text)
        self._tc_anchor = None

    def _tc_line_of(self, ranges, pos):
        for i, (s, e) in enumerate(ranges):
            if s <= pos <= e:
                return i
        return len(ranges) - 1

    def _tc_lines(self, text):
        """Visual line ranges of the focused multiline field (a single range
        when its geometry isn't known yet)."""
        g = self._tc_geom
        if g and g.get('kind') == 'multi' and g.get('ident') == self._tc_ident:
            return self._wrap_ranges(g['font'], text, g['wrap_w'])
        return [(0, len(text))]

    @staticmethod
    def _tc_col_from_x(font, text, rel_x):
        """Caret index in *text* whose position is closest to rel_x pixels."""
        if not text or rel_x <= 0:
            return 0
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi) // 2
            if font.size(text[:mid])[0] >= rel_x:
                hi = mid
            else:
                lo = mid + 1
        i = lo
        if i > 0 and abs(font.size(text[:i - 1])[0] - rel_x) <= abs(font.size(text[:i])[0] - rel_x):
            i -= 1
        return i

    def _tc_index_at(self, pos):
        """Map a screen position to a caret index in the focused field using
        the geometry its last draw recorded (None if unknown)."""
        g = self._tc_geom
        if not g or g.get('ident') != self._tc_ident or self._tc_ident is None:
            return None
        text = self._tc_get(self._tc_ident)
        font = g['font']
        x = max(g['rect'].left, min(pos[0], g['rect'].right))
        if g['kind'] == 'single':
            return self._tc_col_from_x(font, text, x - g['x0'])
        ranges = self._wrap_ranges(font, text, g['wrap_w'])
        row = int((pos[1] - g['y0']) // g['line_h']) + g['start']
        row = max(0, min(len(ranges) - 1, row))
        s, e = ranges[row]
        return s + self._tc_col_from_x(font, text[s:e], x - g['x0'])

    def _tc_set_geom(self, **geom):
        """Called by the field draw code for the focused field: records its
        geometry and turns a pending click into a caret / selection anchor."""
        geom['ident'] = self._tc_ident
        self._tc_geom = geom
        pc = self._tc_pending_click
        if pc is None:
            return
        self._tc_pending_click = None
        idx = self._tc_index_at(pc)
        if idx is None:
            return
        if pygame.key.get_mods() & pygame.KMOD_SHIFT:
            if self._tc_anchor is None:
                self._tc_anchor = self._tc_pos
        else:
            self._tc_anchor = idx
        self._tc_pos = idx
        self._blink = 0.0

    def _tc_drag_to(self, pos):
        """Mouse moved with the button held after clicking into a field."""
        if self._tc_ident is None:
            return
        idx = self._tc_index_at(pos)
        if idx is not None:
            self._tc_pos = idx
            self._blink = 0.0

    def _tc_text_x(self, font, text, inner):
        """X where a focused single-line field's text starts, scrolled just
        enough to keep the caret visible (long text starts out tail-first, as
        before, because the caret starts at the end)."""
        vis = inner.w - 4
        w = font.size(text)[0]
        cp = font.size(text[:min(self._tc_pos, len(text))])[0]
        off = self._tc_scroll
        if cp - off > vis:
            off = cp - vis
        if cp - off < 0:
            off = cp
        off = max(0, min(off, max(0, w - vis)))
        self._tc_scroll = off
        return inner.x - off

    def _tc_draw_single(self, screen, font, text, x, cy, field_rect):
        """Selection highlight + caret for a focused single-line field whose
        text was just drawn at x (vertically centred on cy)."""
        h = font.get_height()
        top = cy - h // 2
        self._tc_set_geom(kind='single', rect=pygame.Rect(field_rect), x0=x, font=font)
        n = len(text)
        if self._tc_has_sel():
            a, b = self._tc_sel_range()
            a, b = min(a, n), min(b, n)
            sx = x + font.size(text[:a])[0]
            ex = x + font.size(text[:b])[0]
            uk.draw_rect_on(screen, (*uk.Theme.KI_BLUE, 90),
                            pygame.Rect(sx, top, max(1, ex - sx), h), 0, 0)
        self._caret(screen, x + font.size(text[:min(self._tc_pos, n)])[0], top, h)

    def _tc_edit_key(self, ident, event):
        """Apply one KEYDOWN to the focused field's text (caret movement,
        selection, clipboard, deletion, typing)."""
        key   = event.key
        ctrl  = bool(event.mod & (pygame.KMOD_CTRL | pygame.KMOD_META))
        shift = bool(event.mod & pygame.KMOD_SHIFT)
        multiline = self._tc_is_multiline(ident)
        buf = self._tc_get(ident)
        n   = len(buf)
        self._blink = 0.0

        def move(p):
            if shift:
                if self._tc_anchor is None:
                    self._tc_anchor = self._tc_pos
            else:
                self._tc_anchor = None
            self._tc_pos = max(0, min(n, p))

        if ctrl and key == pygame.K_a:
            self._tc_anchor = 0
            self._tc_pos = n
        elif ctrl and key in (pygame.K_c, pygame.K_x):
            if self._tc_has_sel():
                s, e = self._tc_sel_range()
                uk.clipboard_set_text(buf[s:e])
                if key == pygame.K_x:
                    self._tc_delete_sel(ident)
                    self._text_limit_hit = False
        elif ctrl and key == pygame.K_v:
            self._tc_insert(ident, uk.clipboard_get_text() or '')
        elif key == pygame.K_LEFT:
            if not shift and self._tc_has_sel():
                self._tc_pos = self._tc_sel_range()[0]
                self._tc_anchor = None
            else:
                move(self._tc_pos - 1)
        elif key == pygame.K_RIGHT:
            if not shift and self._tc_has_sel():
                self._tc_pos = self._tc_sel_range()[1]
                self._tc_anchor = None
            else:
                move(self._tc_pos + 1)
        elif key in (pygame.K_HOME, pygame.K_END):
            if multiline:
                ranges = self._tc_lines(buf)
                s, e = ranges[self._tc_line_of(ranges, self._tc_pos)]
                move(s if key == pygame.K_HOME else e)
            else:
                move(0 if key == pygame.K_HOME else n)
        elif key in (pygame.K_UP, pygame.K_DOWN) and multiline:
            ranges = self._tc_lines(buf)
            li = self._tc_line_of(ranges, self._tc_pos)
            g = self._tc_geom
            font = g['font'] if g else self.font_medium
            s, e = ranges[li]
            x = font.size(buf[s:max(s, min(self._tc_pos, e))])[0]
            tgt = li - 1 if key == pygame.K_UP else li + 1
            if tgt < 0:
                move(0)
            elif tgt >= len(ranges):
                move(n)
            else:
                ts, te = ranges[tgt]
                move(ts + self._tc_col_from_x(font, buf[ts:te], x))
        elif key == pygame.K_BACKSPACE:
            self._text_limit_hit = False
            self._list_msg = ''
            if not self._tc_delete_sel(ident) and self._tc_pos > 0:
                p = self._tc_pos
                self._tc_set(ident, buf[:p - 1] + buf[p:])
                self._tc_pos = p - 1
        elif key == pygame.K_DELETE:
            self._text_limit_hit = False
            self._list_msg = ''
            if not self._tc_delete_sel(ident) and self._tc_pos < n:
                p = self._tc_pos
                self._tc_set(ident, buf[:p] + buf[p + 1:])
        elif key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            if multiline:           # hard line break (single-line fields never get here)
                self._tc_insert(ident, '\n')
        elif event.unicode and event.unicode.isprintable():
            self._tc_insert(ident, event.unicode)

    def _handle_text_field(self, attr, event, field_key=None, actor_key=None):
        """Apply one KEYDOWN event to whichever text buffer currently has focus.

        Three routing modes (exactly one should be set):
          attr      — a direct attribute on self (e.g. '_new_name_buf')
          field_key — a key in _form_params, or 'time' for _form_time_buf
          actor_key — 'id' for the actor-add form's Actor ID buffer

        Tab always clears focus. Return clears focus for single-line
        fields; for the dialogue text field it inserts a hard newline
        (Discord-style multiline) instead, so the designer can break lines
        without leaving the box.  In the new-cutscene name row Return creates
        the cutscene.
        """
        key = event.key
        is_dialogue_text = (field_key == 'text' and self._form_type == 'dialogue')

        if key == pygame.K_TAB:
            self._new_name_focus = False
            self._form_focus     = None
            self._actor_focus    = None
            return
        if key in (pygame.K_RETURN, pygame.K_KP_ENTER) and attr == '_new_name_buf':
            self._submit_new_name()
            return
        if key in (pygame.K_RETURN, pygame.K_KP_ENTER) and not is_dialogue_text:
            self._new_name_focus = False
            self._form_focus     = None
            self._actor_focus    = None
            return

        ident = self._tc_sync()
        if ident is not None:
            self._tc_edit_key(ident, event)

    def _handle_duration_field(self, event):
        key = event.key
        if key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_TAB, pygame.K_ESCAPE):
            self._commit_duration()
            self._duration_focus = False
            return
        ident = self._tc_sync()
        if ident is not None:
            self._tc_edit_key(ident, event)

    def _commit_duration(self):
        """Parse _duration_buf and write it back to cutscene_data['duration']."""
        if not self.cutscene_data:
            return
        try:
            val = float(self._duration_buf)
            if val > 0 and round(val, 2) != self.cutscene_data.get('duration'):
                self._push_undo()
                self.cutscene_data['duration'] = round(val, 2)
                self.unsaved = True
        except ValueError:
            pass
        # Re-sync buf to whatever is actually stored (rolls back bad input)
        self._duration_buf = str(self.cutscene_data.get('duration', 10.0))

    def _on_mouse_motion(self, pos):
        """Drag playhead when scrubbing; also keep mouse position for hover."""
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
            self._apply_kf_drag(pos[0])
            self._tl_auto_scroll = self._calc_tl_auto_scroll(pos[0])
            return

        # ── Rubber-band selection ─────────────────────────────────────────────
        if self._tl_marquee is not None:
            m = self._tl_marquee
            if not m['active'] and (abs(pos[0] - m['px0']) > 4 or abs(pos[1] - m['py0']) > 4):
                m['active'] = True
            self._tl_auto_scroll = self._calc_tl_auto_scroll(pos[0]) if m['active'] else 0.0
            return

        # ── Actor initial-position drag ───────────────────────────────────────
        if self._actor_drag_idx >= 0 and self.cutscene_data:
            actors = self.cutscene_data.get('actors', [])
            if self._actor_drag_idx < len(actors):
                wx, wy = self._mouse_world()
                sx, sy = self._snap_actor_xy(wx + self._actor_drag_offset_x,
                                              wy + self._actor_drag_offset_y)
                actors[self._actor_drag_idx]['x'] = sx
                actors[self._actor_drag_idx]['y'] = sy
                self.unsaved = True
            return

        if not self._tl_play_drag:
            self._tl_auto_scroll = 0.0
            return
        tl = self._tl_panel_rect()
        mx = pos[0]
        label_end_x = tl.x + self._tl_label_w
        t = (mx - label_end_x + self._tl_scroll_x) / self._tl_time_zoom
        dur = self.cutscene_data.get('duration', 10.0) if self.cutscene_data else 10.0
        # Update the playhead position instantly so it renders at the cursor
        # without waiting for the expensive seek().  The actual scene scrub
        # is deferred to update() so it runs at most once per frame.
        self._tl_playhead_t  = _clamp(t, self._tl_min_t(), dur)
        self._scrub_pending  = True
        self._tl_auto_scroll = self._calc_tl_auto_scroll(mx)

    def _apply_kf_drag(self, mouse_x):
        """Move the dragged keyframe — and every other keyframe in the drag
        group — so the grabbed one follows the cursor.  The whole group shifts
        by the same delta (keeping their relative spacing), clamped so none of
        them leaves [0, duration].  Snapping is applied to the grabbed keyframe
        and the rest follow it."""
        if not self.cutscene_data or not self._kf_drag_group:
            return
        actions = self.cutscene_data.get('actions', [])
        if not (0 <= self._kf_drag_idx < len(actions)):
            return
        tl          = self._tl_panel_rect()
        label_end_x = tl.x + self._tl_label_w
        dur         = self.cutscene_data.get('duration', 10.0)
        raw_t       = (mouse_x - label_end_x + self._tl_scroll_x) / self._tl_time_zoom
        min_t       = self._tl_min_t()
        new_t       = round(_clamp(raw_t + self._kf_drag_offset, min_t, dur), 3)
        new_t       = self._snap_time(new_t)
        delta       = new_t - self._kf_drag_orig
        starts      = [t for _a, t in self._kf_drag_group]
        delta       = max(min_t - min(starts), min(delta, dur - max(starts)))
        for act, t0 in self._kf_drag_group:
            act['time'] = round(t0 + delta, 3)
        # Keep the inspector time field in sync while dragging
        self._form_time_buf = f'{actions[self._kf_drag_idx]["time"]:.2f}'
        self.unsaved        = True

    def _calc_tl_auto_scroll(self, mouse_x):
        """Return px/sec scroll speed based on how close mouse_x is to the
        left/right edge of the timeline time area.  Positive = scroll right."""
        tl          = self._tl_panel_rect()
        label_end_x = tl.x + self._tl_label_w
        edge_zone   = 60   # px from edge that triggers auto-scroll
        if mouse_x > tl.right - edge_zone:
            return ((mouse_x - (tl.right - edge_zone)) / edge_zone) * 400
        if mouse_x < label_end_x + edge_zone:
            return -((label_end_x + edge_zone - mouse_x) / edge_zone) * 400
        return 0.0

    # ── Snapping helpers ──────────────────────────────────────────────────────

    def _snap_actor_xy(self, x, y):
        """Snap a world-space actor position to the actor placement grid, if
        enabled. Used both for the initial pick_actor placement click and for
        live dragging, so an actor always lands on the same grid either way."""
        if not self._actor_snap_enabled:
            return round(x, 1), round(y, 1)
        size = self._actor_snap_sizes[self._actor_snap_idx]
        return round(round(x / size) * size, 1), round(round(y / size) * size, 1)

    def _snap_time(self, t):
        """Snap a keyframe time (seconds) to the timeline grid, if enabled."""
        if not self._tl_grid_enabled:
            return t
        interval = self._tl_grid_intervals[self._tl_grid_idx]
        return round(round(t / interval) * interval, 3)

    def _cycle_actor_snap_size(self):
        self._actor_snap_idx = (self._actor_snap_idx + 1) % len(self._actor_snap_sizes)

    def _cycle_tl_grid_interval(self):
        self._tl_grid_idx = (self._tl_grid_idx + 1) % len(self._tl_grid_intervals)

    # ── Clicks ────────────────────────────────────────────────────────────────

    def _on_click(self, pos):
        self._mouse_pos = pos

        # Open dropdown swallows every click (pick an item, or dismiss).
        if self._dd is not None:
            self._click_dropdown(pos)
            return None

        top = None
        for rect, action, arg, full in reversed(self._hits):   # last drawn = on top
            if rect.collidepoint(pos):
                top = (full, action, arg)
                break

        # Clicking anywhere that isn't a text field takes focus off the fields.
        # (The list view's new-name row is only ended by Enter / Esc.)
        if not (top and top[1] == 'field'):
            self._blur_text()

        if top is not None:
            if top[1] is not None:
                return self._on_action(top[1], top[2], top[0])
            return None

        if self.view == 'edit':
            if self._vp_rect.collidepoint(pos):
                wx, wy = self._mouse_world()
                return self._on_viewport_click(wx, wy)
            tl = self._tl_panel_rect()
            if tl.collidepoint(pos):
                self._on_tl_click(pos[0], pos[1], tl)
                return None
        elif self.view == 'list':
            self._list_sel = -1
        return None

    def _on_viewport_click(self, wx, wy):
        if self._pick_mode == 'pick_pan_to_start':
            self._form_params['start_x'] = f'{wx:.1f}'
            self._form_params['start_y'] = f'{wy:.1f}'
            self._pick_mode = None
            return None
        if self._pick_mode in _ACTOR_PICKS:
            wx, wy = self._snap_actor_xy(wx, wy)   # honours the actor grid snap
        if self._pick_mode in ('pick_pan_to', 'pick_snap_to',
                               'pick_move_to', 'pick_fly_to', 'pick_teleport',):
            self._form_params['x'] = f'{wx:.1f}'
            self._form_params['y'] = f'{wy:.1f}'
            self._pick_mode = None
            return None
        if self._pick_mode == 'pick_attack_target':
            # A beam can only ever travel along the actor's facing axis, so
            # only the matching coordinate of the click is meaningful — the
            # runtime (_beam_stop_distance_px in cutscene_actor.py) only
            # ever reads that one axis based on the current `direction`.
            # Deliberately NOT clearing the other field here: if it's ever
            # stale from an earlier pick under a different direction, it's
            # simply ignored, not a way to silently disable the cap the
            # way requiring both used to be.
            direction = self._form_params.get('direction', 'down')
            if direction in ('up', 'down'):
                self._form_params['target_y'] = f'{wy:.1f}'
            else:
                self._form_params['target_x'] = f'{wx:.1f}'
            self._pick_mode = None
            return None
        if self._pick_mode == 'pick_actor':
            self._place_actor_def['x'], self._place_actor_def['y'] = self._snap_actor_xy(wx, wy)
            self._push_undo()
            self.cutscene_data['actors'].append(dict(self._place_actor_def))
            self._pick_mode  = None
            self.unsaved     = True
            self._actor_form = False
            self._runtime    = None  # runtime.actors is stale; force full rebuild
            return None

        # ── Actor drag: hit-test each actor's initial position ────────────────
        # Only blocked while actively playing. Scrubbing the timeline (e.g.
        # touching a fade_in/fade_out keyframe) creates/keeps a paused
        # CutsceneRuntime alive (see _scrub_to / _stop_preview) so playback
        # can resume instantly — that runtime staying around must NOT lock
        # out actor dragging. Hit-testing still uses each actor's stored
        # spawn position (actor.get('x')/('y')).
        if not self._playing and self.cutscene_data:
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

    def _tl_geometry(self):
        """(tl, label_end_x, ruler_y, tracks_y) for the timeline's inner rect."""
        tl = self._tl_panel_rect()
        ruler_y = tl.y + self._tl_hdr_h
        return tl, tl.x + self._tl_label_w, ruler_y, ruler_y + self._tl_ruler_h

    def _on_tl_click(self, mx, my, tl):
        """Handle clicks in the graphical timeline (ruler scrub, caret toggle,
        keyframe select + drag)."""
        _, label_end_x, ruler_y, tracks_y = self._tl_geometry()
        time_area_x = label_end_x
        row_h = self._tl_row_h

        # Click in ruler → start playhead drag
        if ruler_y <= my < tracks_y and mx >= time_area_x:
            t = (mx - time_area_x + self._tl_scroll_x) / self._tl_time_zoom
            dur = self.cutscene_data.get('duration', 10.0) if self.cutscene_data else 10.0
            t = _clamp(t, self._tl_min_t(), dur)
            self._tl_playhead_t = t
            self._scrub_to(t)
            self._tl_play_drag  = True
            return

        if my < tracks_y or not self.cutscene_data:
            return

        # Click in a track row → find nearest keyframe on that track
        rows = self._tl_visible_rows()
        for i, row in enumerate(rows):
            row_y = tracks_y + i * row_h - self._tl_scroll_y
            if not (row_y <= my < row_y + row_h):
                continue

            # Label column: a parent row with >1 action type toggles its
            # sub-lanes (caret or label — the whole cell is the target).
            if mx < label_end_x:
                if row['kind'] == 'parent' and row['expandable']:
                    target = row['target']
                    self._tl_expanded[target] = not self._tl_expanded.get(target, False)
                return

            target      = row['target']
            action_type = row['action_type']  # None for parent rows
            all_actions = self.cutscene_data.get('actions', [])
            best_idx   = -1
            best_dist  = 12  # pixel hit tolerance
            for ai, action in enumerate(all_actions):
                if action.get('target') != target:
                    continue
                if action_type is not None and action.get('type') != action_type:
                    continue
                if action.get('_prefix'):
                    continue          # fail-sequence view: pre-QTE context is locked
                kf_x = time_area_x + action['time'] * self._tl_time_zoom - self._tl_scroll_x
                dist = abs(mx - kf_x)
                if dist < best_dist:
                    best_dist = dist
                    best_idx  = ai

            if best_idx >= 0:
                hit = all_actions[best_idx]
                # Ctrl / Shift-click adds or removes a keyframe from the
                # selection without starting a drag.
                if pygame.key.get_mods() & (pygame.KMOD_CTRL | pygame.KMOD_SHIFT):
                    self._toggle_selection(hit)
                    return
                if self._tl_multi_ids and id(hit) in self._tl_multi_ids:
                    # Grabbing a member of the group: keep the whole selection
                    # and drag it together.
                    group = [(a, a['time']) for a in all_actions
                             if id(a) in self._tl_multi_ids]
                else:
                    self._tl_multi_ids.clear()
                    self._tl_sel = best_idx
                    self._open_action_form(best_idx)
                    group = [(hit, hit['time'])]
                # Begin drag — store the sub-pixel offset so the keyframe
                # doesn't jump on the very first motion event.
                clicked_t = (mx - time_area_x + self._tl_scroll_x) / self._tl_time_zoom
                self._push_undo()
                self._kf_drag_idx    = best_idx
                self._kf_drag_offset = hit['time'] - clicked_t
                self._kf_drag_orig   = hit['time']
                self._kf_drag_group  = group
            else:
                self._kf_drag_idx = -1
                self._begin_marquee(mx, my, in_row=True)
            return

        # Below the last row: still allow rubber-banding on the empty space.
        if mx >= label_end_x:
            self._begin_marquee(mx, my, in_row=False)

    # ── Clipboard ─────────────────────────────────────────────────────────────

    def _copy_selection(self):
        """Copy the selected keyframe(s) — a single one or a whole group —
        keeping their relative timing."""
        sel = self._sel_actions()
        if not sel:
            return
        t0 = min(a['time'] for a in sel)
        CutsceneEditor._clipboard = [(round(a['time'] - t0, 3), copy.deepcopy(a)) for a in sel]

    def _delete_selection(self):
        actions = self.cutscene_data.get('actions', []) if self.cutscene_data else []
        sel = self._sel_actions()
        if not sel:
            return
        self._push_undo()
        ids = {id(a) for a in sel}
        first = min(i for i, a in enumerate(actions) if id(a) in ids)
        actions[:] = [a for a in actions if id(a) not in ids]
        self._tl_multi_ids.clear()
        self._tl_sel      = (_clamp(first - 1, -1, len(actions) - 1)
                             if len(sel) == 1 else -1)
        self._form_active = False
        self.unsaved      = True
        self._runtime     = None

    def _paste_clipboard(self):
        """Paste the clipboard group so its first keyframe lands on the
        playhead.  The pasted keyframes become the selection, ready to drag.
        Keyframes aimed at an actor this cutscene doesn't have are skipped."""
        clip = CutsceneEditor._clipboard
        if not clip or not self.cutscene_data:
            return
        actor_ids = {a.get('id') for a in self.cutscene_data.get('actors', [])}
        items = [(rt, a) for rt, a in clip
                 if a.get('target') in _TARGET_FIXED or a.get('target') in actor_ids]
        if not items:
            return
        dur  = self.cutscene_data.get('duration', 10.0)
        span = max(rt for rt, _a in items)
        t0   = self._tl_playhead_t
        self._push_undo()
        if span > dur:
            dur = round(span, 3)
            self.cutscene_data['duration'] = dur
            self._duration_buf = str(dur)
        t0 = max(self._tl_min_t(), _clamp(t0, 0.0, dur - span))
        new = []
        for rt, a in items:
            c = copy.deepcopy(a)
            c['time'] = round(t0 + rt, 3)
            new.append(c)
        actions = self.cutscene_data.setdefault('actions', [])
        actions.extend(new)
        actions.sort(key=lambda a: a['time'])
        self._set_selection(new)
        self.unsaved  = True
        self._runtime = None

    # ── Destination helpers ───────────────────────────────────────────────────

    def _actor_pos_before(self, actor_id, t):
        """Where *actor_id* stands just before time *t*: its spawn position,
        moved by every earlier move_to / fly_to / teleport.  The action being
        edited is ignored so it doesn't move its own starting point."""
        actors = (self.cutscene_data or {}).get('actors', [])
        actor = next((a for a in actors if a.get('id') == actor_id), None)
        if actor is None:
            return None
        pos = (float(actor.get('x', 0)), float(actor.get('y', 0)))
        acts = (self.cutscene_data or {}).get('actions', [])
        cur = (acts[self._tl_sel] if (not self._form_new and self._form_active
                                      and 0 <= self._tl_sel < len(acts)) else None)
        for a in acts:   # sorted by time
            if a is cur or a.get('target') != actor_id or a.get('time', 0.0) >= t:
                continue
            if a.get('type') in ('move_to', 'fly_to', 'teleport'):
                p = a.get('params', {})
                try:
                    pos = (float(p['x']), float(p['y']))
                except (KeyError, TypeError, ValueError):
                    pass
        return pos

    def _form_time(self):
        try:
            return float(self._form_time_buf)
        except (ValueError, TypeError):
            return self._tl_playhead_t

    def _nudge_form_xy(self, dx, dy):
        p = self._form_params
        try:
            x = float(p.get('x', ''))
            y = float(p.get('y', ''))
        except ValueError:
            base = self._actor_pos_before(self._form_target, self._form_time())
            x, y = base if base else (0.0, 0.0)
        p['x'] = f'{x + dx:.1f}'
        p['y'] = f'{y + dy:.1f}'

    def _dest_preview(self):
        """Describe the destination ghost to draw, or None.  Active while an
        actor move / fly / teleport form is open or being picked."""
        if self.view != 'edit' or not self.cutscene_data:
            return None
        ftype = self._form_type
        if ftype not in ('move_to', 'fly_to', 'teleport'):
            return None
        picking = _ACTOR_PICKS.get(self._pick_mode) == ftype
        if not (picking or self._form_active):
            return None
        actor = next((a for a in self.cutscene_data.get('actors', [])
                      if a.get('id') == self._form_target), None)
        if actor is None:
            return None
        dest = None
        if picking and self._dd is None and self._vp_rect.collidepoint(self._mouse_pos):
            dest = self._snap_actor_xy(*self._mouse_world())
        else:
            try:
                dest = (float(self._form_params.get('x', '')),
                        float(self._form_params.get('y', '')))
            except ValueError:
                return None
        start = self._actor_pos_before(actor['id'], self._form_time())
        idx = self.cutscene_data['actors'].index(actor)
        return {'actor': actor, 'idx': idx, 'type': ftype, 'start': start, 'dest': dest}

    def _draw_dest_ghost(self, inter):
        """World-pass preview: dashed path start→destination and the actor's
        sprite standing at the destination, exactly where the runtime would
        put it (same x/y the action stores)."""
        pv = self._dest_preview()
        if not pv:
            return
        cam_x, cam_y = int(self.camera.x), int(self.camera.y)
        col = _ACTOR_COLORS[pv['idx'] % len(_ACTOR_COLORS)]
        dx, dy = pv['dest']
        ex, ey = int(dx * RENDER_SCALE - cam_x), int(dy * RENDER_SCALE - cam_y)
        if pv['start']:
            sx, sy = int(pv['start'][0] * RENDER_SCALE - cam_x), int(pv['start'][1] * RENDER_SCALE - cam_y)
            length = math.hypot(ex - sx, ey - sy)
            if length > 1:
                ux, uy = (ex - sx) / length, (ey - sy) / length
                pos, dash, gap = 0.0, 8, 6
                while pos < length:
                    end = min(pos + dash, length)
                    inter.draw_line(col, (int(sx + ux * pos), int(sy + uy * pos)),
                                    (int(sx + ux * end), int(sy + uy * end)), 2)
                    pos += dash + gap
            inter.draw_circle(col, (sx, sy), 5, 1)
        entity = self._get_or_create_actor_entity(pv['actor'])
        if entity is not None:
            ox, oy = entity.x, entity.y
            p = self._form_params
            state = p.get('anim_state') or 'idle'
            facing = p.get('direction') or 'down'
            try:
                entity.x, entity.y = float(dx), float(dy)
                if getattr(entity, 'sprite', None):
                    entity.sprite.set_animation(state, facing)
                entity.in_cutscene = True
                entity.draw(inter, self.camera, {})
            except Exception:
                pass
            finally:
                entity.in_cutscene = False
                entity.x, entity.y = ox, oy
                try:
                    if getattr(entity, 'sprite', None):
                        entity.sprite.set_animation('idle', 'down')
                except Exception:
                    pass
        inter.draw_circle(col, (ex, ey), 12, 2)
        inter.draw_circle(_WHITE, (ex, ey), 3)

    def _draw_impact_marker(self, screen, pos, col):
        x, y = pos
        uk.draw_circle_on(screen, col, (x, y), 9, 2)
        uk.draw_circle_on(screen, _WHITE, (x, y), 3, 0)
        for ddx, ddy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            uk.draw_line_on(screen, col, (x + ddx * 12, y + ddy * 12), (x + ddx * 19, y + ddy * 19), 2)

    # ── Multi-selection ───────────────────────────────────────────────────────

    def _sel_actions(self):
        """The currently selected action dicts (0, 1 or many), in time order."""
        if not self.cutscene_data:
            return []
        actions = self.cutscene_data.get('actions', [])
        if self._tl_multi_ids:
            return [a for a in actions if id(a) in self._tl_multi_ids]
        if 0 <= self._tl_sel < len(actions):
            return [actions[self._tl_sel]]
        return []

    def _set_selection(self, acts):
        """Replace the selection with *acts* (action dicts).  One action uses
        the regular single selection; two or more use the multi set.  Closes the
        inspector form — it only ever edits a single keyframe."""
        actions = self.cutscene_data.get('actions', []) if self.cutscene_data else []
        uniq, seen = [], set()
        for a in acts:
            if id(a) not in seen:
                seen.add(id(a))
                uniq.append(a)
        self._tl_multi_ids.clear()
        self._tl_sel = -1
        if len(uniq) == 1:
            self._tl_sel = next((i for i, a in enumerate(actions) if a is uniq[0]), -1)
        elif len(uniq) > 1:
            self._tl_multi_ids = {id(a) for a in uniq}
        self._form_active = False
        self._form_focus  = None

    def _toggle_selection(self, act):
        cur = self._sel_actions()
        if any(a is act for a in cur):
            cur = [a for a in cur if a is not act]
        else:
            cur.append(act)
        self._set_selection(cur)

    def _begin_marquee(self, mx, my, in_row):
        """Arm a rubber-band selection at the click point.  It only becomes a
        real box once the mouse has moved a few pixels; a plain click on empty
        space instead clears the selection (like before)."""
        _, label_end_x, _ruler_y, tracks_y = self._tl_geometry()
        self._tl_marquee = {
            'px0': mx, 'py0': my, 'active': False, 'in_row': in_row,
            'additive': bool(pygame.key.get_mods() & (pygame.KMOD_CTRL | pygame.KMOD_SHIFT)),
            't0':  (mx - label_end_x + self._tl_scroll_x) / self._tl_time_zoom,
            'y0':  my - tracks_y + self._tl_scroll_y,
        }

    def _marquee_bounds(self, m=None):
        """Current rubber-band as (t_min, t_max, y_min, y_max) in content space.
        *m* lets _finish_marquee pass the marquee it already detached from
        self._tl_marquee (which it clears first)."""
        if m is None:
            m = self._tl_marquee
        _, label_end_x, _ruler_y, tracks_y = self._tl_geometry()
        mx, my = self._mouse_pos
        t1 = (mx - label_end_x + self._tl_scroll_x) / self._tl_time_zoom
        y1 = my - tracks_y + self._tl_scroll_y
        return (min(m['t0'], t1), max(m['t0'], t1), min(m['y0'], y1), max(m['y0'], y1))

    def _finish_marquee(self):
        m = self._tl_marquee
        self._tl_marquee = None
        self._tl_auto_scroll = 0.0
        if not self.cutscene_data:
            return
        if not m['active']:
            # Plain click on empty lane space → deselect and close the form.
            if m['in_row'] and not m['additive']:
                self._set_selection([])
            return
        t_min, t_max, y_min, y_max = self._marquee_bounds(m)
        pad_t = 6.0 / self._tl_time_zoom
        rh = self._tl_row_h
        picked = []
        for i, row in enumerate(self._tl_visible_rows()):
            cy = i * rh + rh / 2
            if not (y_min - 6 <= cy <= y_max + 6):
                continue
            for act in self.cutscene_data.get('actions', []):
                if act.get('target') != row['target']:
                    continue
                if row['action_type'] is not None and act.get('type') != row['action_type']:
                    continue
                if act.get('_prefix'):
                    continue          # locked pre-QTE context
                if t_min - pad_t <= act['time'] <= t_max + pad_t:
                    picked.append(act)
        if m['additive']:
            picked = self._sel_actions() + picked
        self._set_selection(picked)

    def _on_scroll(self, event):
        dy = event.y
        mx, my = self._mouse_pos

        # Open dropdown owns the wheel (scrolls its own list, never the viewport)
        if self._dd is not None:
            rect = self._dd.get('rect')
            if rect is not None and rect.collidepoint(mx, my):
                max_scroll = max(0, len(self._dd['items']) - self._dd['visible'])
                self._dd['scroll'] = int(_clamp(self._dd['scroll'] - dy, 0, max_scroll))
            return

        if self.view == 'list':
            self._list_scroll = _clamp(self._list_scroll - dy * 40, 0, 99999)
            return

        # ── Viewport: Ctrl+scroll → zoom (same as the room editor) ────────────
        if self._vp_rect.collidepoint(mx, my):
            if pygame.key.get_mods() & pygame.KMOD_CTRL:
                vx = mx - self._vp_rect.x
                vy = my - self._vp_rect.y
                zoom_old = self._vp_zoom
                zoom_new = round(_clamp(zoom_old + dy * self._vp_zoom_step,
                                        self._vp_zoom_min, self._vp_zoom_max), 2)
                if zoom_new != zoom_old:
                    self._vp_zoom = zoom_new
                    # Keep the world point under the mouse fixed (room-editor formula).
                    self.camera.x += vx / zoom_old - vx / zoom_new
                    self.camera.y += vy / zoom_old - vy / zoom_new
                    self._sync_camera_view()
                    self._clamp_camera()
            return

        # ── Timeline scroll ────────────────────────────────────────────────────
        tl, label_end_x, ruler_y, tracks_y = self._tl_geometry()
        if self._tl_frame.collidepoint(mx, my):
            keys = pygame.key.get_pressed()
            if keys[pygame.K_LCTRL] or keys[pygame.K_RCTRL]:
                pivot_t = (mx - label_end_x + self._tl_scroll_x) / self._tl_time_zoom
                self._tl_time_zoom = _clamp(
                    self._tl_time_zoom * (1.12 ** dy),
                    self._tl_zoom_min, self._tl_zoom_max)
                self._tl_scroll_x = pivot_t * self._tl_time_zoom - (mx - label_end_x)
                self._tl_scroll_x = max(self._tl_min_scroll(), self._tl_scroll_x)
            elif keys[pygame.K_LSHIFT] or keys[pygame.K_RSHIFT]:
                self._tl_scroll_x = max(self._tl_min_scroll(), self._tl_scroll_x - dy * 30)
            else:
                # Vertical scroll through the track list.
                visible_h    = tl.bottom - tracks_y
                content_h    = len(self._tl_visible_rows()) * self._tl_row_h
                max_scroll_y = max(0.0, content_h - visible_h)
                self._tl_scroll_y = _clamp(
                    self._tl_scroll_y - dy * self._tl_row_h, 0.0, max_scroll_y)
            return

        if self._left_frame.collidepoint(mx, my):
            self._left_scroll = _clamp(self._left_scroll - dy * 30, 0, 99999)
        elif self._right_frame.collidepoint(mx, my):
            self._insp_scroll = _clamp(self._insp_scroll - dy * 30, 0, 99999)

    # ══════════════════════════════════════════════════════════════════════════
    # Action dispatcher — every clickable widget registers (rect, action, arg)
    # ══════════════════════════════════════════════════════════════════════════

    def _on_action(self, action, arg, rect):
        # ── Text-field focus ──────────────────────────────────────────────────
        if action == 'field':
            kind, key = arg
            self._blink = 0.0
            if kind == 'form':
                self._form_focus, self._actor_focus = key, None
            elif kind == 'actor':
                self._actor_focus, self._form_focus = key, None
            elif kind == 'new_name':
                self._new_name_focus = True
            elif kind == 'duration':
                if self.cutscene_data:
                    if not self._duration_focus:   # re-clicking must not discard typed text
                        self._duration_buf = str(self.cutscene_data.get('duration', 10.0))
                    self._duration_focus = True
                    self._form_focus     = None
                    self._actor_focus    = None
            if self._tc_ident_now() is not None:
                # Place the caret / start a drag-selection at the click on the next draw.
                self._tc_pending_click = tuple(self._mouse_pos)
                self._tc_drag          = True
            return

        # ── Global / header ───────────────────────────────────────────────────
        if action == 'close':
            # List view's back arrow — the Dev Menu closed itself when it
            # launched this editor (see DevMenu._activate_selected), so
            # signal the game loop to reopen it instead of just vanishing
            # into gameplay. Mirrors every other dev-tools editor's
            # 'back_to_dev_menu' convention.
            self.toggle()
            return 'back_to_dev_menu'
        elif action == 'back':
            self._leave_edit()
        elif action == 'save':
            self._save_cutscene()
        elif action == 'play':
            if self._playing:
                self._stop_preview()
            else:
                self._start_preview()
        elif action == 'undo':
            self._undo()
        elif action == 'redo':
            self._redo()
        elif action == 'grid_toggle':
            self._show_grid = not self._show_grid

        # ── List view ─────────────────────────────────────────────────────────
        elif action == 'list_open':
            self._load_cutscene(arg)
        elif action == 'list_new_start':
            self._new_name_focus = True
            self._new_name_buf   = ''
            self._list_msg       = ''
            self._list_confirm   = None
            self._list_scroll    = 99999   # clamped in draw → jump to the new row
            self._blink          = 0.0
        elif action == 'list_delete_ask':
            self._list_confirm = arg
        elif action == 'list_delete_no':
            self._list_confirm = None
        elif action == 'list_delete_yes':
            path = _cutscene_path(arg)
            if os.path.exists(path):
                os.remove(path)
            self._list_confirm = None
            self._list_sel = -1
            self._refresh_file_list()

        # ── Scene panel ───────────────────────────────────────────────────────
        elif action == 'room_prev':
            self._cycle_room(-1)
        elif action == 'room_next':
            self._cycle_room(1)
        elif action == 'room_pick':
            rooms = [r.name for r in self.room_manager.rooms
                     if not getattr(r, 'is_transient', False)]
            self._open_dropdown(rect, rooms, self.cutscene_data.get('room', ''),
                                self._pick_scene_room, accent=_ROOM_COLOR,
                                empty='No rooms loaded')
        elif action == 'actor_row':
            # Selecting an actor row clears the inspector form
            self._actor_sel   = arg
            self._form_active = False
        elif action == 'actor_add':
            # Pre-fill the actor form with a sensible default ID
            self._actor_form   = True
            self._actor_focus  = None
            self._actor_id_buf = f'actor_{len(self.cutscene_data["actors"])}'
            self._sync_actor_etype_default()
        elif action == 'actor_form_close':
            self._actor_form = False
            self._actor_focus = None
        elif action == 'actor_del':
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
                self._tl_sel    = -1
                self._tl_multi_ids.clear()
                self._form_active = False
                self.unsaved    = True
                self._runtime   = None  # runtime.actors is stale; force full rebuild
        elif action == 'actor_snap_toggle':
            self._actor_snap_enabled = not self._actor_snap_enabled
        elif action == 'actor_snap_size':
            self._cycle_actor_snap_size()
        elif action in ('actor_type_prev', 'actor_type_next'):
            step = -1 if action.endswith('prev') else 1
            self._actor_type_idx = (self._actor_type_idx + step) % len(_ACTOR_TYPES)
            self._sync_actor_etype_default()
        elif action == 'actor_type_pick':
            self._open_dropdown(rect, list(_ACTOR_TYPES),
                                _ACTOR_TYPES[self._actor_type_idx % len(_ACTOR_TYPES)],
                                self._pick_actor_type, accent=_ROOM_COLOR)
        elif action == 'actor_etype_pick':
            atype = _ACTOR_TYPES[self._actor_type_idx % len(_ACTOR_TYPES)]
            self._open_dropdown(rect, self._actor_asset_ids(atype), self._actor_etype_buf,
                                self._pick_actor_etype, accent=_ROOM_COLOR,
                                empty='None found in entity catalogue')
        elif action == 'actor_place_confirm':
            # Build the actor def and enter pick mode so the next viewport
            # click sets the spawn position.
            atype = _ACTOR_TYPES[self._actor_type_idx % len(_ACTOR_TYPES)]
            actor_def = {
                'id':      self._actor_id_buf.strip() or f'actor_{len(self.cutscene_data["actors"])}',
                'type':    atype,
                'variant': 'default',
                'x': 100.0, 'y': 100.0,
            }
            if atype in ('enemy', 'boss', 'npc'):
                actor_def['enemy_type'] = self._actor_etype_buf.strip()
            elif atype == 'player':
                actor_def['character'] = self._actor_etype_buf.strip()
            self._place_actor_def = actor_def
            self._pick_mode = 'pick_actor'

        # ── Timeline toolbar ──────────────────────────────────────────────────
        elif action == 'tl_add':
            self._open_new_action_form()
        elif action == 'fail_edit':
            self._enter_fail_edit()
        elif action == 'fail_done':
            self._exit_fail_edit(True)
        elif action == 'fail_discard':
            self._exit_fail_edit(False)
        elif action == 'tl_del':
            self._delete_selection()
        elif action == 'tl_copy':
            self._copy_selection()
        elif action == 'tl_paste':
            self._paste_clipboard()
        elif action == 'tl_dup':
            actions = self.cutscene_data.get('actions', [])
            sel = self._sel_actions()
            if sel:
                self._push_undo()
                dups = []
                for src_act in sel:
                    dup = copy.deepcopy(src_act)
                    # Nudge the duplicate forward 0.1 s so it doesn't sit on top
                    # of the original in the timeline and is immediately visible.
                    dup['time'] = round(dup['time'] + 0.1, 3)
                    actions.append(dup)
                    dups.append(dup)
                actions.sort(key=lambda a: a['time'])
                if len(sel) > 1:
                    # Leave the copies selected so the group can be dragged
                    # straight to where it should go.
                    self._set_selection(dups)
                self.unsaved = True
                self._runtime = None
        elif action == 'tl_zoom_in':
            self._tl_time_zoom = min(self._tl_zoom_max, self._tl_time_zoom * 1.25)
        elif action == 'tl_zoom_out':
            self._tl_time_zoom = max(self._tl_zoom_min, self._tl_time_zoom / 1.25)
        elif action == 'tl_grid_toggle':
            self._tl_grid_enabled = not self._tl_grid_enabled
        elif action == 'tl_grid_interval':
            self._cycle_tl_grid_interval()
        elif action == 'vp_zoom_reset':
            self._vp_zoom = 1.0
            self._sync_camera_view()
            self._clamp_camera()
        elif action == 'cam_track_toggle':
            self._cam_track = not self._cam_track
            if self._cam_track and not self._playing and self.cutscene_data:
                self._scrub_to(self._tl_playhead_t)   # re-snap to the cutscene camera

        # ── Inspector ─────────────────────────────────────────────────────────
        elif action == 'form_commit':
            self._commit_form()
            self._stop_preview_sound()
            self._runtime = None
        elif action == 'form_cancel':
            self._form_active = False
            self._form_focus  = None
            self._stop_preview_sound()
        elif action == 'form_target_prev':
            self._cycle_form_target(-1)
        elif action == 'form_target_next':
            self._cycle_form_target(1)
        elif action == 'form_target_pick':
            self._open_dropdown(rect, self._form_targets(), self._form_target,
                                self._pick_form_target, accent=self._target_color(self._form_target),
                                color_fn=self._target_color)
        elif action == 'form_type_prev':
            self._cycle_form_type(-1)
        elif action == 'form_type_next':
            self._cycle_form_type(1)
        elif action == 'form_type_pick':
            self._open_dropdown(rect, self._form_type_pool(), self._form_type,
                                self._pick_form_type, accent=self._target_color(self._form_target),
                                label_fn=lambda v: v.replace('_', ' '))
        elif action in _PICK_ACTIONS:
            self._pick_mode = action
        elif action == 'pick':
            # Toggle: clicking the armed button again cancels the pick.
            self._pick_mode = None if self._pick_mode == arg else arg
        elif action == 'param_prev':
            self._cycle_param(arg[0], arg[1], -1)
        elif action == 'param_next':
            self._cycle_param(arg[0], arg[1], 1)
        elif action == 'param_pick':
            key, hint = arg
            pool = self._param_pool(key, hint)
            if pool is not None:
                items, label_fn, empty, color_fn = pool
                self._open_dropdown(rect, items, self._form_params.get(key, ''),
                                    lambda v, k=key: self._form_params.__setitem__(k, v),
                                    label_fn=label_fn, empty=empty, color_fn=color_fn,
                                    accent=self._target_color(self._form_target))
        elif action == 'param_toggle':
            cur = self._form_params.get(arg, 'True')
            self._form_params[arg] = 'False' if cur == 'True' else 'True'
        elif action in ('room_group_prev', 'room_group_next'):
            self._cycle_room_group(-1 if action.endswith('prev') else 1)
        elif action in ('room_name_prev', 'room_name_next'):
            self._cycle_room_in_group(-1 if action.endswith('prev') else 1)
        elif action == 'room_group_pick':
            groups = [''] + list(self.room_manager.groups)
            self._open_dropdown(rect, groups, self._form_room_group,
                                self._pick_room_group, accent=_ROOM_COLOR,
                                label_fn=lambda v: v or 'All Groups')
        elif action == 'room_name_pick':
            rooms = self._rooms_for_group(self._form_room_group)
            self._open_dropdown(rect, rooms, self._form_params.get('room_name', ''),
                                lambda v: self._form_params.__setitem__('room_name', v),
                                accent=_ROOM_COLOR, empty='No rooms in this group')
        elif action == 'preview_sound':
            self._preview_sound()
        elif action == 'summary_edit':
            if self.cutscene_data and 0 <= self._tl_sel < len(self.cutscene_data.get('actions', [])):
                self._open_action_form(self._tl_sel)

    # ══════════════════════════════════════════════════════════════════════════
    # Form helpers
    # ══════════════════════════════════════════════════════════════════════════

    def _open_new_action_form(self):
        self._form_active   = True
        self._form_new      = True
        self._form_focus    = None
        self._form_time_buf = f'{max(self._tl_playhead_t, self._tl_min_t()):.2f}'
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
            if key == 'color' and val != '':
                # Saved as an [r,g,b] list — map back to whichever preset
                # name it's closest to so the cycle button has something
                # sane to show (handles hand-edited JSON too, not just
                # colors this editor itself wrote).
                self._form_params[key] = self._rgb_to_color_name(val)
            elif (key == 'direction' and val == ''
                  and self._form_type in ('move_to', 'fly_to')):
                # Empty direction means "auto-derive from the movement vector"
                # and is omitted from the saved JSON by _commit_form. Keep it
                # empty on reload instead of falling back to _default_param's
                # 'down', which silently turned auto into an explicit 'down'.
                self._form_params[key] = ''
            else:
                self._form_params[key] = str(val) if val != '' else self._default_param(key)
        # Sync the room-group browser to whichever group the saved room belongs to.
        if self._form_type == 'change_room':
            rname = self._form_params.get('room_name', '')
            room  = self.room_manager.get_room_by_name(rname) if rname else None
            self._form_room_group = room.group if room else ''

    def _rgb_to_color_name(self, rgb):
        """Map an [r,g,b] triple (as stored in an action's 'color' param)
        back to the closest _COLOR_PRESETS name, so the cycle button always
        has something sensible to display — including for colors saved
        before this preset list existed, or nudged by hand in the JSON."""
        try:
            r, g, b = rgb
        except (TypeError, ValueError):
            return 'black'
        best_name, best_dist = 'black', None
        for name, (pr, pg, pb) in _COLOR_PRESETS.items():
            dist = (r - pr) ** 2 + (g - pg) ** 2 + (b - pb) ** 2
            if best_dist is None or dist < best_dist:
                best_dist, best_name = dist, name
        return best_name

    def _set_form_target(self, target):
        self._form_target = target
        actors = self.cutscene_data.get('actors', []) if self.cutscene_data else []
        all_targets = ['camera', 'screen', 'room', 'sound'] + [a['id'] for a in actors]
        self._form_target_idx = all_targets.index(target) if target in all_targets else 0
        self._reset_form_params()

    def _set_form_type(self, atype):
        self._form_type = atype
        self._reset_form_params()

    def _cycle_form_target(self, delta):
        actors = self.cutscene_data.get('actors', []) if self.cutscene_data else []
        all_targets = ['camera', 'screen', 'room', 'sound'] + [a['id'] for a in actors]
        self._form_target_idx = (self._form_target_idx + delta) % len(all_targets)
        self._form_target = all_targets[self._form_target_idx]
        if self._form_target == 'camera':
            self._set_form_type('pan_to')
        elif self._form_target == 'screen':
            self._set_form_type('fade_in')
        elif self._form_target == 'room':
            self._set_form_type('change_room')
        elif self._form_target == 'sound':
            self._set_form_type('play_music')
        else:
            self._set_form_type('set_animation')

    def _cycle_form_type(self, delta):
        if self._form_target == 'camera':
            pool = _CAMERA_ACTIONS
        elif self._form_target == 'screen':
            pool = _SCREEN_ACTIONS
        elif self._form_target == 'room':
            pool = _ROOM_ACTIONS
        elif self._form_target == 'sound':
            pool = _SOUND_ACTIONS
        else:
            pool = _ACTOR_ACTIONS
        idx = pool.index(self._form_type) if self._form_type in pool else 0
        self._form_type = pool[(idx + delta) % len(pool)]
        self._reset_form_params()


    def _form_targets(self):
        actors = self.cutscene_data.get('actors', []) if self.cutscene_data else []
        return list(_TARGET_FIXED) + [a['id'] for a in actors]

    def _form_type_pool(self):
        if self._form_target == 'camera':
            return list(_CAMERA_ACTIONS)
        if self._form_target == 'screen':
            return list(_SCREEN_ACTIONS)
        if self._form_target == 'room':
            return list(_ROOM_ACTIONS)
        if self._form_target == 'sound':
            return list(_SOUND_ACTIONS)
        return list(_ACTOR_ACTIONS)

    def _target_color(self, target):
        """Track colour for a target id — fixed tracks have their own, actors
        take their slot in the palette (same order the timeline uses)."""
        fixed = {'camera': _CAMERA_COLOR, 'screen': _SCREEN_COLOR,
                 'room': _ROOM_COLOR, 'sound': _SOUND_COLOR}
        if target in fixed:
            return fixed[target]
        actors = self.cutscene_data.get('actors', []) if self.cutscene_data else []
        for i, a in enumerate(actors):
            if a.get('id') == target:
                return _ACTOR_COLORS[i % len(_ACTOR_COLORS)]
        return uk.Theme.GOLD

    # ── Dropdown callbacks (the one shared dropdown calls these on pick) ─────

    def _apply_form_target(self, target):
        """Pick a target directly (the old editor could only step through them
        with < >) and reset the action type to that target's first type."""
        targets = self._form_targets()
        self._form_target_idx = targets.index(target) if target in targets else 0
        self._form_target = targets[self._form_target_idx]
        default = {'camera': 'pan_to', 'screen': 'fade_in',
                   'room': 'change_room', 'sound': 'play_music'}.get(self._form_target, 'set_animation')
        self._set_form_type(default)

    def _pick_form_target(self, target):
        self._apply_form_target(target)

    def _pick_form_type(self, atype):
        self._form_type = atype
        self._reset_form_params()

    def _pick_actor_type(self, atype):
        if atype in _ACTOR_TYPES:
            self._actor_type_idx = _ACTOR_TYPES.index(atype)
            self._sync_actor_etype_default()

    def _pick_actor_etype(self, value):
        self._actor_etype_buf = value

    def _pick_room_group(self, group):
        self._form_room_group = group
        rooms = self._rooms_for_group(group)
        self._form_params['room_name'] = rooms[0] if rooms else ''

    # ── Enum-style params ─────────────────────────────────────────────────────
    # The old editor dispatched on the param *key* (state, loop, fade_in, …) in
    # _cycle_dropdown and had a separate popup for portrait / character /
    # costume / track / sfx.  Everything now goes through _param_pool(), keyed
    # on the type *hint* from _ACTION_PARAMS, and picked either with the < >
    # steppers or the shared dropdown.  (Dispatching on the hint also fixes
    # stop_music's Fade Out toggle, which the key-based lookup never matched.)

    @staticmethod
    def _empty_label(hint):
        if hint == 'portrait':
            return 'narrator'
        if hint in ('music_track', 'sfx_name'):
            return '(none)'
        if hint == 'sfx_name_any':
            return 'all looping'
        return 'auto'

    def _param_pool(self, key, hint):
        """(items, label_fn, empty_text, swatch_fn) for an enum-style param, or
        None for free-text / numeric / bool params."""
        empty_lbl = self._empty_label(hint)
        label_fn = lambda v: v if v != '' else empty_lbl

        if hint == 'anim':
            actor_def = None
            if self._form_target not in _TARGET_FIXED:
                for a in (self.cutscene_data or {}).get('actors', []):
                    if a['id'] == self._form_target:
                        actor_def = a
                        break
            return self._get_actor_anim_states(actor_def), label_fn, 'No animations found', None
        if hint == 'scroll_dir':
            return (['right', 'left', 'down', 'up', 'down_right', 'down_left', 'up_right', 'up_left'],
                    label_fn, None, None)
        if hint == 'dir':
            # For move_to / fly_to the first slot is '' (auto-derive from the
            # movement vector).  For all other actions direction is explicit.
            if self._form_type in ('move_to', 'fly_to'):
                return ['', 'down', 'up', 'left', 'right'], label_fn, None, None
            return list(_DIRECTIONS), label_fn, None, None
        if hint == 'invert_mode':
            return list(_INVERT_MODES), label_fn, None, None
        if hint == 'qte_mode':
            return ['spam', 'timing'], label_fn, None, None
        if hint == 'attack_type':
            return list(discover_attacks() or _ATTACK_TYPES), label_fn, None, None
        if hint == 'weather_type':
            try:
                files = sorted(glob.glob(os.path.join('assets', 'weather', '*.png')))
                pool = [os.path.splitext(os.path.basename(f))[0] for f in files]
            except Exception:
                pool = []
            return (pool or ['rain', 'snow', 'fog']), label_fn, None, None
        if hint == 'color':
            return list(_COLOR_PRESETS.keys()), label_fn, None, (lambda v: _COLOR_PRESETS.get(v))
        if hint == 'portrait':
            # '' (shown as "narrator") = no face art — full-width text box.
            files = sorted(glob.glob(os.path.join('assets', 'portraits', '*.png')))
            return [''] + [os.path.splitext(os.path.basename(f))[0] for f in files], label_fn, None, None
        if hint == 'character':
            return self._discover_player_characters(), label_fn, 'No characters found in assets/sprites/player/', None
        if hint == 'costume':
            return self._discover_costumes_for_actor(self._form_target), label_fn, 'No costumes found for this actor', None
        if hint == 'music_track':
            return self._available_music_tracks(), label_fn, 'No music tracks loaded', None
        if hint == 'sfx_name':
            return self._available_sfx_names(), label_fn, 'No sound effects loaded', None
        if hint == 'sfx_name_any':
            return [''] + self._available_sfx_names(), label_fn, None, None
        return None

    def _cycle_param(self, key, hint, delta):
        pool = self._param_pool(key, hint)
        if pool is None or not pool[0]:
            return
        items = pool[0]
        cur = self._form_params.get(key, '')
        if cur in items:
            new = items[(items.index(cur) + delta) % len(items)]
        else:
            new = items[0] if delta > 0 else items[-1]
        self._form_params[key] = new



    # ══════════════════════════════════════════════════════════════════════════
    # Asset discovery / sound preview
    # ══════════════════════════════════════════════════════════════════════════

    def _discover_player_characters(self):
        """Return every player character ID, one sub-folder per character
        under assets/sprites/player/ — the same source of truth used by
        character_creator.py's own discover_characters()."""
        players_dir = os.path.join('assets', 'sprites', 'player')
        try:
            return sorted(
                d for d in os.listdir(players_dir)
                if os.path.isdir(os.path.join(players_dir, d))
            )
        except OSError:
            return []

    def _default_character(self):
        chars = self._discover_player_characters()
        return chars[0] if chars else 'goku'

    def _discover_costumes_for_actor(self, actor_id: str) -> list:
        """Return the costume folder names for the character currently used by
        *actor_id* at the playhead position.

        Resolves the character in priority order:
          1. The last set_character action targeting this actor whose time is
             <= the current form's time — so the dropdown reflects the character
             after a mid-scene swap, not the original spawn character.
          2. The actor_def's 'character' field (spawn character).
          3. The live entity's .character attribute (fallback).

        Then scans assets/sprites/player/{character}/ for sub-directories that
        contain at least one *.png file directly (same logic as
        discover_costumes() in character_creator.py).
        """
        import glob as _glob

        # Current form time — used to find the last set_character before this action.
        try:
            form_t = float(self._form_time_buf)
        except (ValueError, AttributeError):
            form_t = 0.0

        # Walk actions up to form_t and track the last set_character for this actor.
        character = ''
        for action in sorted(self.cutscene_data.get('actions', []),
                             key=lambda a: a.get('time', 0.0)):
            if action.get('time', 0.0) > form_t:
                break
            if (action.get('target') == actor_id
                    and action.get('type') == 'set_character'):
                character = action.get('params', {}).get('character', '') or character

        # Fall back to the actor_def's initial character if no override was found.
        if not character:
            actor_def = next(
                (a for a in self.cutscene_data.get('actors', [])
                 if a.get('id') == actor_id),
                None,
            )
            character = (actor_def or {}).get('character', '')

        # Last resort: live entity attribute.
        if not character:
            entity = self._actor_entities.get(actor_id)
            if entity:
                character = getattr(entity, 'character', '')

        if not character:
            return ['base']

        char_dir = os.path.join('assets', 'sprites', 'player', character)
        if not os.path.isdir(char_dir):
            return ['base']

        costumes = []
        for entry in sorted(os.scandir(char_dir), key=lambda e: e.name):
            if (entry.is_dir()
                    and not entry.name.startswith('.')
                    and entry.name != 'transformations'
                    and _glob.glob(os.path.join(entry.path, '*.png'))):
                costumes.append(entry.name)
        return costumes if costumes else ['base']

    def _actor_asset_ids(self, atype):
        """Return the placeable ids for the actor-add form's current Type.

        Sourced from entity_editor's discover_*_ids() (the same catalogue
        EntityEditor's own placement palette reads from, so this list never
        drifts out of sync with what's actually placeable) or the player
        character folder for 'player' — never something the designer has to
        type in and hope it's spelled the same as the real asset.
        """
        if atype == 'boss':
            return discover_boss_ids()
        if atype == 'npc':
            return discover_npc_ids()
        if atype == 'player':
            return self._discover_player_characters()
        # 'enemy' — regular enemies only; bosses have their own Type entry.
        bosses = set(discover_boss_ids())
        return [eid for eid in discover_enemy_ids() if eid not in bosses]

    def _sync_actor_etype_default(self):
        """Reset the actor-add form's etype buffer to the first id available
        for the currently-selected Type, called whenever Type changes so the
        buffer never holds a stale id from a different type (e.g. an enemy
        id left over after switching Type to 'npc')."""
        atype_names = ['enemy', 'boss', 'npc', 'player']
        atype = atype_names[self._actor_type_idx % len(atype_names)]
        ids = self._actor_asset_ids(atype)
        self._actor_etype_buf = ids[0] if ids else ''

    def _available_music_tracks(self):
        """Return every music track name loaded by the SoundEngine, sorted.

        Sourced from sound_manager.sound_engine.music_tracks — the same dict
        AudioAssetLoader.load_from_directory() populates from assets/audio/music/.
        Empty if no sound_manager was given to the editor.
        """
        sm = self.sound_manager
        if sm is None or getattr(sm, 'sound_engine', None) is None:
            return []
        return sorted(sm.sound_engine.music_tracks.keys())

    def _available_sfx_names(self):
        """Return every sound effect name loaded by the SoundEngine, sorted.

        Sourced from sound_manager.sound_engine.sound_effects — populated the
        same way as _available_music_tracks() above.
        """
        sm = self.sound_manager
        if sm is None or getattr(sm, 'sound_engine', None) is None:
            return []
        return sorted(sm.sound_engine.sound_effects.keys())

    def _preview_sound(self):
        """Instantly play/stop the currently-selected track or sfx through the
        real SoundManager, so a dev can audition a play_music / play_sfx /
        stop_music action from the inspector without running the whole
        cutscene. No-op if no sound_manager was given to the editor.
        """
        if self.sound_manager is None:
            return
        if self._form_type == 'play_music':
            track = self._form_params.get('track', '')
            if track:
                fade_in = self._form_params.get('fade_in', 'True') != 'False'
                self.sound_manager.play_music(track, fade_in=fade_in)
        elif self._form_type == 'play_sfx':
            sfx = self._form_params.get('sfx', '')
            if sfx:
                from core.cutscene_runtime import play_sfx_on, stop_sfx_on
                stop_sfx_on(self.sound_manager, sfx)   # never stack preview loops
                play_sfx_on(self.sound_manager, sfx,
                            loop=self._form_params.get('loop', 'False') == 'True')
        elif self._form_type == 'stop_sfx':
            from core.cutscene_runtime import stop_sfx_on
            stop_sfx_on(self.sound_manager, self._form_params.get('sfx', '') or None)
        elif self._form_type == 'stop_music':
            fade_out = self._form_params.get('fade_out', 'True') != 'False'
            self.sound_manager.stop_music(fade_out=fade_out)

    def _stop_preview_sound(self):
        """Stop any music started via the inspector's Preview button.

        Called when the action form is committed (✓ OK) or dismissed
        (✕ CANCEL) so a previewed track never keeps playing in the
        background after the dev is done auditioning it. Cuts instantly
        (no fade) — same as the music stop on editor close in toggle().
        No-op if no sound_manager was given to the editor.
        """
        if self.sound_manager is not None:
            self.sound_manager.stop_music(fade_out=False)
            from core.cutscene_runtime import stop_sfx_on
            stop_sfx_on(self.sound_manager, None)   # also cut any previewed SFX loop

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


    # ══════════════════════════════════════════════════════════════════════════
    # Form params
    # ══════════════════════════════════════════════════════════════════════════

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
            'room_name': '', 'character': self._default_character(),
            'loop': 'True', 'fade_in': 'True', 'fade_out': 'True',
            'attack_type': (discover_attacks() or _ATTACK_TYPES)[0], 'deal_damage': 'False',
            'visible': 'True',
            'fill_per_press': '0.08', 'drain_rate': '0.15',
            'sweep_speed': '0.9', 'max_attempts': '0', 'fail_hold': '0.5',
        }
        if key == 'mode' and self._form_type == 'qte':
            return 'spam'
        if key == 'color':
            # flash defaults to a white pop; fade_in/fade_out default to a
            # plain black fade (matches CutsceneRuntime's own [0,0,0]
            # fallback for actions saved before this param existed).
            return 'white' if self._form_type == 'flash' else 'black'
        if key == 'track':
            tracks = self._available_music_tracks()
            return tracks[0] if tracks else ''
        if key == 'sfx':
            if self._form_type == 'stop_sfx':
                return ''            # blank = stop every looping SFX
            names = self._available_sfx_names()
            return names[0] if names else ''
        if key == 'loop' and self._form_type == 'play_sfx':
            return 'False'           # SFX are one-shots unless Loop is switched on
        # scroll uses a much slower default speed than weather
        return base.get(key, '')

    def _commit_form(self):
        try:
            t = float(self._form_time_buf)
        except ValueError:
            t = 0.0
        t = max(t, self._tl_min_t())   # fail-sequence view: nothing before the QTE

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
                elif hint == 'bool':  params[key] = (raw == 'True')
                elif hint == 'color': params[key] = list(_COLOR_PRESETS.get(raw, (0, 0, 0)))
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
                if actions[self._tl_sel].get('_prefix'):
                    action['_prefix'] = True     # fail-sequence view: still pre-QTE context
                    if action['type'] == 'qte':
                        action['params']['_fail_edit'] = True
                actions[self._tl_sel] = action

        actions.sort(key=lambda a: a['time'])
        # Locate the action we just committed by object identity, not by
        # matching time+type — value-matching ignored `target`, so adding or
        # editing an action at the same timestamp/type as another action
        # (e.g. two actions both at t=0.0) could resolve _tl_sel to that
        # OTHER action instead of the one just committed. Since identity
        # survives both the append/replace above and sort() (sort reorders
        # the list but never copies the dicts), `a is action` always finds
        # exactly the entry we just wrote, regardless of any ties.
        self._tl_sel = next(
            (i for i, a in enumerate(actions) if a is action), 0)
        self._tl_multi_ids.clear()
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
                self._vp_zoom  = _clamp(float(state.get('zoom', self._vp_zoom)),
                                        self._vp_zoom_min, self._vp_zoom_max)
                self._sync_camera_view()
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
        self._files = discover_cutscene_ids()
        self._list_msg = ''
        # Light metadata for the list cards (room · length · action count).
        self._file_meta = {}
        for name in self._files:
            try:
                with open(_cutscene_path(name), 'r') as f:
                    d = json.load(f)
                self._file_meta[name] = (str(d.get('room', '')),
                                         float(d.get('duration', 0.0)),
                                         len(d.get('actions', [])),
                                         len(d.get('actors', [])))
            except (OSError, ValueError, TypeError, AttributeError):
                pass
        self._list_confirm = None
        if not (0 <= self._list_sel < len(self._files)):
            self._list_sel = -1

    def _reset_edit_state(self, data):
        """Clear every piece of transient state tied to the previously open
        cutscene (selections, undo history, cached sprites, playback, scroll)
        so the editor starts fresh for *data*."""
        self.cutscene_data  = data
        self._fail_edit     = None
        self.unsaved        = False
        self._tl_sel        = -1
        self._tl_multi_ids  = set()
        self._kf_drag_group = []
        self._tl_marquee    = None
        self._actor_sel     = -1
        self._form_active   = False
        self._form_focus    = None
        self._actor_form    = False
        self._actor_focus   = None
        self._pick_mode     = None
        self._dd            = None
        self._actor_entities.clear()
        self._undo_stack.clear()
        self._redo_stack.clear()
        self.view           = 'edit'
        self._new_name_focus = False
        self._new_name_buf   = ''
        self._stop_preview()
        self._runtime       = None   # force fresh runtime for the new cutscene
        self._duration_buf   = str(data.get('duration', 10.0))
        self._duration_focus = False
        self._tl_playhead_t = 0.0
        self._tl_scroll_x   = 0.0
        self._tl_scroll_y   = 0.0
        self._tl_expanded   = {}
        self._left_scroll   = 0
        self._insp_scroll   = 0
        self._autosave_t    = 0.0
        self._vp_tile_surfaces.clear()
        self._vp_animated_tiles.clear()

    def _create_and_open(self, name):
        _ensure_dir()
        rooms = [r.name for r in self.room_manager.rooms
                 if not getattr(r, 'is_transient', False)]
        data = {'id': name, 'room': rooms[0] if rooms else '',
                'duration': 10.0, 'actors': [], 'actions': []}
        with open(_cutscene_path(name), 'w') as f:
            json.dump(data, f, indent=2)
        self.cutscene_name = name
        self._reset_edit_state(data)
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
        self.cutscene_name = name
        self._reset_edit_state(data)
        # Drop any cached tile data so the new room reloads from disk
        te = getattr(self.room_editor, 'tileset_editor', None)
        if te is not None and hasattr(te, 'room_tiles'):
            te.room_tiles.pop(data.get('room', ''), None)
        # Restore saved viewport (falls back to centring on the room)
        self._restore_viewport_state()


    # ── QTE fail-sequence view ───────────────────────────────────────────────
    # Select a timing-mode 'qte' keyframe and click "Edit fail sequence": the
    # timeline switches to a copy holding (a) every normal action up to the QTE
    # (shown dimmed, read-only in effect) so the scene is exactly as it is at
    # that point, and (b) the QTE's existing fail actions as ordinary actions.
    # Edit them like any timeline; "Done" writes them back tagged with
    # params['qte_fail'] = qte_id (the runtime plays them after each miss, with
    # times measured from the QTE keyframe) and restores the normal timeline.

    def _tl_min_t(self):
        """Earliest time the timeline can show / edit: the QTE keyframe in the
        fail-sequence view (it IS the start of that timeline), else 0."""
        return self._fail_edit['T'] if self._fail_edit else 0.0

    def _tl_min_scroll(self):
        """Smallest horizontal scroll (px). In the fail view the left edge is
        the QTE keyframe, minus a few px so its diamond isn't clipped."""
        if not self._fail_edit:
            return 0.0
        return max(0.0, self._fail_edit['T'] * self._tl_time_zoom - 12.0)

    def _fail_merged_actions(self):
        """Full action list for the real cutscene: the original actions with
        this QTE's old fail actions replaced by what's in the working copy."""
        import copy
        fe, qid, T = self._fail_edit, self._fail_edit['qte_id'], self._fail_edit['T']
        kept = [a for a in fe['orig'].get('actions', [])
                if str((a.get('params') or {}).get('qte_fail') or '') != qid]
        new = []
        for a in self.cutscene_data.get('actions', []):
            if a.get('_prefix'):
                continue                      # pre-QTE context: edits discarded
            if a.get('target') == 'screen' and a.get('type') == 'qte':
                continue                      # no QTE inside a fail sequence
            b = copy.deepcopy(a)
            b.pop('_prefix', None)
            b.setdefault('params', {})['qte_fail'] = qid
            b['params'].pop('_fail_edit', None)
            b['time'] = round(max(T, float(b.get('time', T))), 3)
            new.append(b)
        return sorted(kept + new, key=lambda a: a['time'])

    def _fail_edit_reset_view(self, t):
        self._tl_sel = -1
        self._tl_multi_ids.clear()
        self._kf_drag_group = []
        self._form_active = False
        self._form_focus = None
        self._actor_sel = -1
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._stop_preview()
        self._runtime = None
        self._duration_buf = str(self.cutscene_data.get('duration', 10.0))
        self._tl_playhead_t = t
        self._tl_scroll_x = max(self._tl_min_scroll(), t * self._tl_time_zoom - 120.0)
        self._scrub_to(t)

    def _enter_fail_edit(self):
        import copy
        data = self.cutscene_data
        actions = data.get('actions', []) if data else []
        if self._fail_edit or not (0 <= self._tl_sel < len(actions)):
            return
        q = actions[self._tl_sel]
        qid = str((q.get('params') or {}).get('qte_id') or '').strip()
        if q.get('type') != 'qte' or q.get('target') != 'screen' or not qid:
            return
        T = float(q.get('time', 0.0))
        work = copy.deepcopy(data)
        kept, fails = [], []
        for a in work.get('actions', []):
            p = a.get('params') or {}
            tag = p.get('qte_fail')
            if tag:
                if str(tag) == qid:
                    p.pop('qte_fail', None)
                    a['params'] = p
                    a['time'] = max(T, float(a.get('time', T)))
                    fails.append(a)
                continue                      # other QTEs' fail actions stay out of view
            if float(a.get('time', 0.0)) <= T:
                a['_prefix'] = True
                if a.get('type') == 'qte' and a.get('target') == 'screen':
                    a.setdefault('params', {})['_fail_edit'] = True   # inert marker in preview
                kept.append(a)
        work['actions'] = sorted(kept + fails, key=lambda a: a['time'])
        last = max([a['time'] for a in fails], default=T)
        work['duration'] = max(T + 10.0, last + 3.0)
        self._fail_edit = {'orig': data, 'qte_id': qid, 'T': T}
        self.cutscene_data = work
        self._fail_edit_reset_view(T)

    def _exit_fail_edit(self, commit):
        fe = self._fail_edit
        if not fe:
            return
        if commit:
            fe['orig']['actions'] = self._fail_merged_actions()
            self.unsaved = True
        self.cutscene_data = fe['orig']
        self._fail_edit = None
        self._fail_edit_reset_view(fe['T'])

    def _draw_fail_banner(self, screen, x, y, W):
        fe = self._fail_edit
        pad = 12
        lines = self._wrap_text(
            self.font_small,
            'This timeline starts at the QTE keyframe: everything before it is locked. '
            'Add actions from here on - they play after each miss, then the bar comes '
            'back. Hit the bar and the normal timeline continues instead.', W - pad * 2)
        lh = self._fh('s') + 4
        H = pad * 2 + self._fh('m') + 6 + lh + 8 + lh * len(lines)
        card = pygame.Rect(x, y, W, H)
        uk.draw_panel(screen, card, bg=(*_ROW_BASE, 255), border=uk.Theme.GOLD,
                      border_width=1, radius=8, shadow=False)
        cy = card.y + pad
        self._txt(screen, self.font_medium, 'Editing FAIL sequence', (card.x + pad, cy),
                  uk.Theme.GOLD, dynamic=True)
        cy += self._fh('m') + 6
        self._txt(screen, self.font_small, f"QTE '{fe['qte_id']}'  at  {fe['T']:.2f} s",
                  (card.x + pad, cy), uk.Theme.TEXT_SECONDARY, dynamic=True)
        cy += lh + 8
        for line in lines:
            self._txt(screen, self.font_small, line, (card.x + pad, cy), uk.Theme.TEXT_MUTED)
            cy += lh
        y = card.bottom + 10
        self._button(screen, pygame.Rect(x, y, W, 34), 'Done - back to main timeline', None,
                     'fail_done', primary=True, key='insp:fail_done')
        y += 34 + 6
        self._button(screen, pygame.Rect(x, y, W, 30), 'Discard fail changes', None,
                     'fail_discard', danger=True, key='insp:fail_discard')
        return y + 30 + 10

    def _save_cutscene(self):
        """Write cutscene_data to disk and persist the viewport state."""
        if not self.cutscene_data:
            return
        _ensure_dir()
        data = self.cutscene_data
        if self._fail_edit:
            # Mid fail-sequence edit: write the real cutscene with the sequence
            # merged in, never the temporary working copy.
            data = dict(self._fail_edit['orig'])
            data['actions'] = self._fail_merged_actions()
        with open(_cutscene_path(self.cutscene_name), 'w') as f:
            json.dump(data, f, indent=2)
        self.unsaved     = False
        self._autosave_t = 0.0   # reset the autosave debounce timer
        self._save_viewport_state()


    # ══════════════════════════════════════════════════════════════════════════
    # Preview / entity factory
    # ══════════════════════════════════════════════════════════════════════════

    def _scrub_to(self, t):
        """Seek the scene to time *t*, creating a paused runtime if needed."""
        if self._runtime is None and self.cutscene_data:
            try:
                from core.cutscene_runtime import CutsceneRuntime
                self._runtime = CutsceneRuntime(
                    self.cutscene_data, self.camera, self._entity_factory,
                    dialogue_box=self.dialogue_box, sound_manager=self.sound_manager,
                    qte_bar=self._qte_bar)
            except Exception as e:
                print(f'[CutsceneEditor] _scrub_to runtime error: {e}')
                return
        if self._runtime:
            hold = None if self._cam_track else (self.camera.x, self.camera.y)
            self._runtime.seek(t)
            from config.settings import RENDER_SCALE
            import math
            if hold is not None:
                # Tracking off: seek() may have moved the camera; put it back.
                self.camera.x, self.camera.y = hold
            else:
                # Snap the editor camera directly to the camera_target world position.
                # Don't call camera.update() here — that tweens, causing the camera to
                # keep drifting after scrub ends. Instead set x/y directly.
                ct = self._runtime.camera_target
                self.camera.x = ct.x * RENDER_SCALE - self.camera.screen_width  // 2
                self.camera.y = ct.y * RENDER_SCALE - self.camera.screen_height // 2
                self._clamp_camera()
            # Scrub view is a static snapshot — clear any live shake that seek()
            # may have applied (shake inside its window) so the camera doesn't
            # wobble while the playhead is stationary.  A deterministic jitter
            # offset is added below purely for the scrub preview frame.
            self._runtime._clear_camera_shake()
            # Apply a deterministic shake preview when the playhead is inside a
            # shake window.  Uses the camera_target position (already set above)
            # as the base; the jitter is overwritten on the next scrub call so it
            # never bleeds into camera state or playback.
            for _action in ((self.cutscene_data or {}).get('actions', [])
                            if self._cam_track else []):
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
                    dialogue_box=self.dialogue_box, sound_manager=self.sound_manager,
                    qte_bar=self._qte_bar)
            hold = None if self._cam_track else (self.camera.x, self.camera.y)
            self._runtime.seek(self._tl_playhead_t)
            if hold is not None:
                self.camera.x, self.camera.y = hold
            else:
                # Snap the camera to the correct start position so there is no
                # one-frame jump when playback begins (same logic as _scrub_to).
                from config.settings import RENDER_SCALE
                ct = self._runtime.camera_target
                self.camera.x = ct.x * RENDER_SCALE - self.camera.screen_width  // 2
                self.camera.y = ct.y * RENDER_SCALE - self.camera.screen_height // 2
                self._clamp_camera()
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
        self._redo_stack.append(copy.deepcopy(self.cutscene_data))
        self.cutscene_data = self._undo_stack.pop()
        self._after_history_jump()

    def _redo(self):
        """Reapply the snapshot at the top of the redo stack."""
        if not self._redo_stack or self.cutscene_data is None:
            return
        self._undo_stack.append(copy.deepcopy(self.cutscene_data))
        self.cutscene_data = self._redo_stack.pop()
        self._after_history_jump()


    def _after_history_jump(self):
        """Tidy up editor state after an undo or redo."""
        self.unsaved      = True
        self._tl_sel      = -1
        self._tl_multi_ids.clear()
        self._kf_drag_group = []
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
        # Likewise a QTE bar / fail sequence left running when playback is
        # stopped mid-step. abort_qte() also rewinds the clock to the QTE.
        if self._qte_bar is not None:
            self._qte_bar.stop()
        if self._runtime is not None:
            self._runtime.abort_qte()
            self._tl_playhead_t = self._runtime.elapsed
        # Cut any music the cutscene started (play_music action) or that was
        # left over from a Preview button click — otherwise it keeps playing
        # indefinitely after playback stops, with no fade-out ever fired.
        # fade_out=False so there's no lingering fade-out delay.
        if self.sound_manager is not None:
            self.sound_manager.stop_music(fade_out=False)
        if self._runtime is not None:
            self._runtime.stop_looping_sfx()
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
                boss = BossEnemy(x, y, boss_id=actor_def.get('enemy_type', 'pui_pui'),
                                 variant=actor_def.get('variant', 'default'))
                # Every real spawn site sets this right after construction
                # (see game.py's _spawn_room_entities) — BossEnemy defaults
                # to inactive otherwise, which made cutscene bosses build
                # fine but draw as nothing.
                boss.active = True
                return boss
            elif atype == 'npc':
                from entities.npc import NPC
                npc_id  = actor_def.get('enemy_type', 'generic')
                variant = actor_def.get('variant', 'default')
                npc = NPC(x, y, None)
                npc.npc_id  = npc_id
                npc.variant = variant
                # See the boss branch above — NPC also defaults to inactive
                # until a spawn site flips this on.
                npc.active  = True
                try:
                    from core.sprite_system import create_npc_sprite
                    npc.sprite     = create_npc_sprite(npc_id, variant, npc.width, npc.height)
                    npc.has_sprite = npc.sprite is not None
                except Exception:
                    npc.has_sprite = False
                return npc
            else:
                from entities.enemy import Enemy
                enemy = Enemy(x, y, enemy_type=actor_def.get('enemy_type', 'tiger_bandit'),
                             variant=actor_def.get('variant', 'default'))
                # See the boss branch above — Enemy also defaults to
                # inactive until a spawn site flips this on.
                enemy.active = True
                return enemy
        except Exception as ex:
            print(f'[CutsceneEditor] entity_factory error: {ex}')
            return None


    # ══════════════════════════════════════════════════════════════════════════
    # Camera / room helpers
    # ══════════════════════════════════════════════════════════════════════════

    def _reset_camera_for_room(self):
        """Reset zoom and position the camera at the room's top-left corner.

        We do NOT call _clamp_camera here: for rooms smaller than the viewport,
        that would produce a negative camera.x/y which some rendering paths
        don't handle gracefully.  Clamping happens naturally as the user pans.
        """
        self._vp_zoom = 1.0
        self._sync_camera_view()
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
        # room.tiles is the on-disk source - the same one _enter_view_room()
        # reads - but it holds raw dicts (Tile.to_dict() shape) until an
        # editor session converts them. Without converting here too, every
        # tile downstream (_build_tile_surface) would try tile.layer /
        # tile.tileset_name / etc. on a plain dict and blow up - or, since
        # room.tiles is truthy, silently shadow whatever real Tile data
        # already sits in te.room_tiles. Mirrors _enter_view_room()'s own
        # dict-guard so both code paths agree on what a "tile" is.
        if not te.room_tiles.get(room.name):
            from dev_tools.room_editor.room_editor_tools.tileset_editor import Tile
            raw = getattr(room, 'tiles', None) or []
            te.room_tiles[room.name] = [
                Tile.from_dict(t) if isinstance(t, dict) else t
                for t in raw
            ]

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
    # Panel rect + track helpers
    # ══════════════════════════════════════════════════════════════════════════

    def _tl_panel_rect(self):
        """Inner (content) rect of the timeline panel — the frame minus its
        padding, so nothing drawn in it can touch the rounded corners."""
        return self._tl_frame.inflate(-16, -16)


    def _tl_tracks(self):
        """Return list of (label, color, target_id) for all tracks."""
        tracks = [
            ('Camera', _CAMERA_COLOR, 'camera'),
            ('Screen', _SCREEN_COLOR, 'screen'),
            ('Room',   _ROOM_COLOR,   'room'),
            ('Sound',  _SOUND_COLOR,  'sound'),
        ]
        actors = self.cutscene_data.get('actors', []) if self.cutscene_data else []
        for i, a in enumerate(actors):
            tracks.append((a['id'], _ACTOR_COLORS[i % len(_ACTOR_COLORS)], a['id']))
        return tracks

    def _tl_visible_rows(self):
        """Expanded row list for the graphical AE-style timeline (used by
        _draw_timeline / _on_tl_click / the scroll-clamp math), as opposed to
        _tl_tracks() above which stays flat and only feeds the LAYERS list in
        the left panel.

        Any track that currently has more than one distinct action *type*
        on it (e.g. Screen carrying both fade_in and fade_out actions) gets
        an expand caret. Collapsed (default): behaves exactly like before —
        one row, every action on that track drawn on it. Expanded: the
        parent row stays as a merged overview (still shows every keyframe,
        same as collapsed) and a child row is inserted per action type
        directly below it, each only showing keyframes of that one type —
        this is the "Fade In" / "Fade Out" sub-lane split.

        Returns a list of dicts:
          kind         'parent' | 'child'
          label        display text
          color        (r,g,b) track colour
          target       action target id this row filters on
          action_type  None for parent rows; the specific action 'type' for
                       child rows (used as an additional keyframe filter)
          expandable   True if this parent has >1 distinct action type
          expanded     current expand state (parents only)
        """
        rows    = []
        actions = self.cutscene_data.get('actions', []) if self.cutscene_data else []
        for label, color, target in self._tl_tracks():
            # Distinct action types present on this track, in first-seen
            # order so the sub-lane order matches the order events were
            # first added rather than jumping around alphabetically.
            types = []
            for a in actions:
                if a.get('target') == target and a.get('type') not in types:
                    types.append(a.get('type'))
            expandable = len(types) > 1
            expanded   = expandable and self._tl_expanded.get(target, False)
            rows.append({
                'kind': 'parent', 'label': label, 'color': color,
                'target': target, 'action_type': None,
                'expandable': expandable, 'expanded': expanded,
            })
            if expanded:
                for atype in types:
                    rows.append({
                        'kind': 'child',
                        'label': (atype or '').replace('_', ' ').title(),
                        'color': color, 'target': target,
                        'action_type': atype,
                        'expandable': False, 'expanded': False,
                    })
        return rows


    # ══════════════════════════════════════════════════════════════════════════
    # Room browsing (scene room + change_room action)
    # ══════════════════════════════════════════════════════════════════════════

    def _set_scene_room(self, new_name):
        """Point the cutscene at *new_name*.

        Also invalidates baked tile surfaces so the viewport doesn't flash the
        old room's tiles for one frame before the new ones load.
        """
        if not self.cutscene_data or new_name == self.cutscene_data.get('room', ''):
            return
        self._push_undo()
        self.cutscene_data['room'] = new_name
        self.unsaved = True
        self._invalidate_tile_cache(new_name)
        # Drop the tileset_editor's cached tile list so _ensure_room_tiles
        # reseeds it from room.tiles on the next draw call.
        te = getattr(self.room_editor, 'tileset_editor', None)
        if te is not None and hasattr(te, 'room_tiles'):
            te.room_tiles.pop(new_name, None)

    def _pick_scene_room(self, name):
        self._set_scene_room(name)

    def _cycle_room(self, delta):
        """Advance (+1) or reverse (-1) through the room list."""
        rooms = [r.name for r in self.room_manager.rooms
                 if not getattr(r, 'is_transient', False)]
        if not rooms:
            return
        cur      = self.cutscene_data.get('room', '')
        idx      = rooms.index(cur) if cur in rooms else 0
        self._set_scene_room(rooms[(idx + delta) % len(rooms)])


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


    # ══════════════════════════════════════════════════════════════════════════
    # Viewport world rendering  (tiles, decorations, actors, weather, overlay)
    # ══════════════════════════════════════════════════════════════════════════

    def _draw_viewport(self, screen, vp):
        """Render the room and actors into viewport rect *vp* on the real
        GPUScreen, with zoom support.

        This used to render at RENDER_SCALE into a smaller CPU-side
        intermediate Surface (iw x ih = vw/zoom x vh/zoom) cached across
        frames, then pygame.transform.scale() that up to fill *vp* every
        frame. That's the same "offscreen canvas area grows as 1/zoom^2,
        plus a CPU-bound full-frame transform.scale() on top" shape that
        was tanking room_editor.py's FPS when zoomed out (see
        room_editor.py's _ZoomedScreen / _draw_view_room) -- zooming out
        here grows iw/ih the same way, so it was only a matter of time
        before it hit the same wall.

        Fixed the same way room_editor.py was: draw calls below target a
        _ZoomedViewport (a private, self-contained copy of the same idea
        room_editor.py's _ZoomedScreen uses, plus a screen-space origin
        offset since this viewport is a sub-panel, not the whole window)
        wrapping the real screen, so each one lands on the real GPUScreen
        already scaled by SDL's GPU-side stretched blit, instead of
        accumulating into a big CPU surface that then needs one expensive
        software scale per frame. Tile/sprite drawing code below is
        unchanged -- it still works in RENDER_SCALE, unscaled, "world"
        coordinates; the _ZoomedViewport is what makes zoom purely visual,
        same contract as before.
        """
        zoom = self._vp_zoom
        vscreen = _ZoomedViewport(screen, zoom, vp.width, vp.height, origin=(vp.x, vp.y))
        vscreen.fill(_CANVAS)

        # How much world we see at base scale -- vscreen.get_size() already
        # does exactly the iw/ih = real_size/zoom division internally.
        iw, ih = vscreen.get_size()
        inter = vscreen  # kept as `inter` below to minimize the diff

        room = self._get_current_room()
        te   = getattr(self.room_editor, 'tileset_editor', None)

        # Ensure tiles are seeded into the tileset_editor even if the room
        # editor has never opened this room.
        self._ensure_room_tiles(room)

        # camera.x/y are stored at base scale (= world_x * RENDER_SCALE).
        cam_x = int(self.camera.x)
        cam_y = int(self.camera.y)

        if room:
            self._blit_room_layer(inter, room, cam_x, cam_y, foreground=False)

        if room and self._vp_zoom >= 0.4 and self._show_grid:
            self._draw_viewport_grid(inter, room)
            # Room boundary outline
            rx = -cam_x
            ry = -cam_y
            inter.draw_rect(uk.Theme.GOLD,
                            (rx, ry, room.width * RENDER_SCALE, room.height * RENDER_SCALE), 2)

        # Actor placement snap grid — shown only while it's actually relevant
        # (placing a new actor or dragging an existing one) so it doesn't
        # clutter the viewport the rest of the time. Own colour/spacing from
        # the tile grid above so the two are never confused for each other.
        if (room and self._vp_zoom >= 0.4 and self._actor_snap_enabled
                and (self._pick_mode == 'pick_actor' or self._pick_mode in _ACTOR_PICKS
                     or self._actor_drag_idx >= 0)):
            snap_size = self._actor_snap_sizes[self._actor_snap_idx]
            self._draw_viewport_grid(inter, room, cell_size=snap_size,
                                     grid_col=(90, 150, 230))

        # ── Decorations (trees, etc.) — own Y-sorted pass, drawn before actors.
        # Decorations are a separate object system from tiles (see
        # objects/decoration_objects.py / room.decorations), so the tile-baking
        # fix alone never surfaces them — nothing here ever asked room.decorations
        # for anything. game.py's own cutscene-draw path (the branch this
        # viewport is meant to mirror) draws these the same way: their own
        # y-sorted pass via layer_manager, before draw_actors(), before fg
        # tiles. The editor has no LayerManager instance, so this reimplements
        # just the sort + blit using Decoration.get_render_info(), the same
        # helper _draw_player_silhouette_if_occluded() uses in game.py — it
        # already returns a pre-scaled surface and screen-space (x, y) for a
        # given camera, so no extra scale/offset math is needed here.
        if room and getattr(room, 'decorations', None):
            for decoration in sorted(room.decorations, key=lambda d: d.get_sort_key()):
                if not getattr(decoration, 'active', True):
                    continue
                scaled, dx, dy = decoration.get_render_info(self.camera, RENDER_SCALE)
                if scaled:
                    inter.blit(scaled, (dx, dy))

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
            # Must match game.py's _cs_colors exactly — several attacks'
            # fallback rendering (used whenever their sprite sheet fails to
            # load, e.g. Projectile/GenkidamaBlast/BurningChargeEffect/
            # BeamAttack's no-sprite rects) reaches for colors['CYAN']/
            # colors['YELLOW']. Missing keys here throw a KeyError that
            # _RealAttackEffect's update()/draw() then silently swallows —
            # the effect just never appears, with no error anywhere.
            colors = {'WHITE': (255, 255, 255), 'RED': (220, 60, 60),
                      'CYAN': (80, 220, 220), 'YELLOW': (255, 220, 80)}
            self._runtime.draw_actors(inter, self.camera, colors)  # Y-sorted, before fg
        else:
            # Static editor path — Y-sort actor markers so deeper ones draw behind.
            actors = self.cutscene_data.get('actors', []) if self.cutscene_data else []
            actors_sorted = sorted(enumerate(actors), key=lambda t: t[1].get('y', 0))
            for i, actor in actors_sorted:
                self._draw_actor_marker(inter, actor, i, i == self._actor_sel)

        # Destination preview (move / fly / teleport) — under fg tiles so it
        # is occluded exactly like the real actor will be.
        self._draw_dest_ghost(inter)

        # Foreground tile layer — drawn after actors so it occludes them correctly.
        if room:
            self._blit_room_layer(inter, room, cam_x, cam_y, foreground=True)

        if self._runtime:
            # Weather before the overlay — same layering as game.py's own
            # cutscene draw path (world → actors → weather → overlay/dialogue).
            # Was missing entirely, so weather_start/weather_stop tracked
            # state on self._runtime but nothing ever blitted it here.
            self._runtime.draw_weather(inter, iw, ih)
            self._runtime.draw_overlay(inter, iw, ih)

    def _draw_viewport_grid(self, surf, room, cell_size=None, grid_col=(52, 66, 98)):
        """Draw a world-unit grid onto the intermediate surface using
        base-scale camera coords. Defaults to the room's TILE_SIZE (the
        [G]-toggled tile grid); pass cell_size explicitly to draw a
        different-spaced grid, e.g. the actor placement snap grid."""
        from config.settings import TILE_SIZE
        size     = cell_size or TILE_SIZE
        cx, cy   = int(self.camera.x), int(self.camera.y)
        vw, vh   = surf.get_size()

        xs = (cx // RENDER_SCALE // size) * size
        x  = xs
        while x * RENDER_SCALE - cx <= vw:
            sx = x * RENDER_SCALE - cx
            if 0 <= sx <= vw:
                surf.draw_line(grid_col, (sx, 0), (sx, vh), 1)
            x += size

        ys = (cy // RENDER_SCALE // size) * size
        y  = ys
        while y * RENDER_SCALE - cy <= vh:
            sy = y * RENDER_SCALE - cy
            if 0 <= sy <= vh:
                surf.draw_line(grid_col, (0, sy), (vw, sy), 1)
            y += size

    def _invalidate_tile_cache(self, room_name=None):
        """Drop baked tile surfaces so they are rebuilt on the next draw.

        Call with no argument to clear everything (e.g. tileset change), or
        pass room_name to invalidate only that room (e.g. after _cycle_room).
        """
        if room_name is None:
            self._vp_tile_surfaces.clear()
            self._vp_animated_tiles.clear()
        else:
            self._vp_tile_surfaces.pop((room_name, True),  None)
            self._vp_tile_surfaces.pop((room_name, False), None)
            self._vp_animated_tiles.pop((room_name, True),  None)
            self._vp_animated_tiles.pop((room_name, False), None)

    def _blit_room_layer(self, inter, room, cam_x, cam_y, foreground):
        """Draw one tile layer for *room* onto *inter*, animated content included.

        Prefers self.room_editor.blit_tiles_callback - game.py wires this to
        Game.blit_room_tiles, the exact same call the Room Editor's own
        viewport uses (see room_editor.py's _enter_view_room draw path). That
        one function is the actual single source of truth for a room's full
        visual layer: the baked static tile surface, the per-tile animated-
        tile overlay (water/flags/etc.), AND algorithmic AnimatedRegions
        (grass/water/lava - objects/animated_region.py, drawn underneath the
        bg bake). This editor has no way to reproduce AnimatedRegions itself
        - that's ~150 lines of patch-cropping/frame-sheet/scroll logic that
        only exists on Game - so placed grass etc. would stay invisible here
        forever without going through the callback.

        Falls back to the local bake-and-cache path below (tiles + per-tile
        animation only, no AnimatedRegions) if the callback was never wired
        up - e.g. this editor being exercised outside a full Game instance.
        """
        cb = getattr(self.room_editor, 'blit_tiles_callback', None)
        if cb:
            # blit_tiles_callback's own 'bg' flag is background=True, the
            # opposite sense of this method's 'foreground' flag.
            cb(inter, room.name, cam_x, cam_y, not foreground)
        else:
            inter.blit(self._get_baked_tile_surface(room, foreground), (-cam_x, -cam_y))
            self._draw_viewport_animated_tiles(inter, room, cam_x, cam_y, foreground)

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
        # Prefer the editor's live cache (real Tile objects, kept up to date
        # by every paint/erase this session) and only fall back to the raw
        # on-disk room.tiles - converting dicts to Tile the same way
        # _enter_view_room()/_render_room_tile_preview() do - when that cache
        # hasn't been populated for this room yet. Checking room.tiles first
        # (as this used to) meant a non-empty raw-dict list would win over a
        # perfectly good live cache, and either shadow real edits or crash on
        # tile.layer since dicts don't have attributes.
        if te.room_tiles.get(room.name):
            tiles = te.room_tiles[room.name]
        else:
            from dev_tools.room_editor.room_editor_tools.tileset_editor import Tile
            raw = getattr(room, 'tiles', None) or []
            tiles = [Tile.from_dict(t) if isinstance(t, dict) else t for t in raw]
        # Sort by layer so multiple stacked layers on the same side (bg or fg)
        # bake bottom-to-top in the correct order — mirrors
        # game._build_room_tile_surface. Without this, tiles blit in
        # whatever order they sit in the list (paint order), so a higher
        # layer can end up hidden underneath a lower one that happened to
        # be blitted after it.
        animated_tiles = []
        for tile in sorted(tiles, key=lambda t: t.layer):
            is_fg = tile.layer >= 0
            if foreground != is_fg:
                continue
            tileset = te.tileset_manager.get_tileset(tile.tileset_name)
            if not tileset:
                continue
            # Animated tiles (water, flags, rotors, etc.) are excluded from
            # the static bake — this surface is built once and cached, so a
            # cycling tile would freeze on its anchor frame forever. Mirrors
            # game._build_room_tile_surface: pull them out here and redraw
            # them fresh every frame via _draw_viewport_animated_tiles instead.
            if tileset.is_tile_animated(tile.tile_x, tile.tile_y):
                animated_tiles.append(tile)
                continue
            scaled = tileset.get_scaled_tile_surface(tile.tile_x, tile.tile_y, RENDER_SCALE)
            if scaled:
                surf.blit(scaled, (int(tile.x * RENDER_SCALE), int(tile.y * RENDER_SCALE)))
        self._vp_animated_tiles[(room.name, foreground)] = animated_tiles
        # Convert to display format so every subsequent blit (camera pan,
        # tiled scroll) is hardware-accelerated rather than per-pixel software.
        # Background layer: no transparency needed → convert() (faster, no alpha).
        # Foreground layer: has transparent gaps → convert_alpha().
        return surf.convert() if not foreground else surf.convert_alpha()

    def _draw_viewport_animated_tiles(self, inter, room, cam_x, cam_y, foreground: bool):
        """Blit this room/layer's animated tiles on top of the baked static
        surface, picking each one's current frame from the shared clock.

        Mirrors game._draw_animated_tile_overlay — kept separate from the
        static bake for the same reason: these tiles cycle frames every
        tick and can't be pre-rendered once.
        """
        animated_tiles = self._vp_animated_tiles.get((room.name, foreground))
        if not animated_tiles:
            return
        te = getattr(self.room_editor, 'tileset_editor', None)
        if not te:
            return
        tick_ms = pygame.time.get_ticks()
        tileset_mgr = te.tileset_manager
        iw, ih = inter.get_size()
        for tile in animated_tiles:
            tileset = tileset_mgr.get_tileset(tile.tileset_name)
            if not tileset or not tileset.image:
                continue
            screen_x = (tile.x * RENDER_SCALE) - cam_x
            screen_y = (tile.y * RENDER_SCALE) - cam_y
            scaled_w = tileset.tile_width * RENDER_SCALE
            scaled_h = tileset.tile_height * RENDER_SCALE
            if not (-scaled_w <= screen_x <= iw and -scaled_h <= screen_y <= ih):
                continue
            disp_x, disp_y = tileset.get_animated_coords(tile.tile_x, tile.tile_y, tick_ms)
            scaled = tileset.get_scaled_tile_surface(disp_x, disp_y, RENDER_SCALE)
            if scaled:
                inter.blit(scaled, (int(screen_x), int(screen_y)))

    # ══════════════════════════════════════════════════════════════════════════
    # UI primitives  (immediate-mode: draw + register hit rect in one call)
    # ══════════════════════════════════════════════════════════════════════════

    _ENUM_HINTS = frozenset({'dir', 'anim', 'portrait', 'invert_mode', 'qte_mode', 'weather_type',
                             'scroll_dir', 'character', 'costume', 'music_track',
                             'sfx_name', 'sfx_name_any', 'attack_type', 'color'})

    def _fh(self, size):
        """Measured line height of font 's' / 'm' / 'l' (cached)."""
        cache = self.__dict__.setdefault('_fh_cache', {})
        if size not in cache:
            font = {'s': self.font_small, 'm': self.font_medium, 'l': self.font_large}[size]
            cache[size] = font.get_height()
        return cache[size]

    def _anim(self, key, target, speed=12.0):
        """Eased 0..1 value per widget key, snapped to 21 steps so ui_kit's
        rounded-rect / glow caches keep hitting instead of rebuilding."""
        v = self._anims.get(key, 0.0)
        v += (target - v) * min(1.0, self._dt * speed)
        if abs(target - v) < 0.004:
            v = float(target)
        self._anims[key] = v
        return round(v * 20) / 20.0

    def _push_clip(self, screen, rect):
        prev = self._clip_rect
        r = pygame.Rect(rect)
        if prev is not None:
            r = r.clip(prev)
        self._clip_stack.append(prev)
        self._clip_rect = r
        screen.set_clip(r)

    def _pop_clip(self, screen):
        prev = self._clip_stack.pop()
        self._clip_rect = prev
        screen.set_clip(prev)

    def _hit(self, rect, action, arg=None):
        """Register a clickable rect (clipped to whatever panel is currently
        being drawn, so scrolled-away widgets can't steal clicks) and return
        True if the mouse is over it."""
        full = pygame.Rect(rect)
        r = full.clip(self._clip_rect) if self._clip_rect is not None else full
        if r.w <= 0 or r.h <= 0:
            return False
        self._hits.append((r, action, arg, full))
        m = self._mouse_pos
        if not r.collidepoint(m):
            return False
        if not self._in_overlay and any(b.collidepoint(m) for b in self._blocks_prev):
            return False
        return True

    def _hover(self, rect):
        """Hover test only — does NOT register a hit, so it never swallows the
        click (use for regions whose click is resolved elsewhere, e.g. the
        timeline, which does its own hit-testing)."""
        r = pygame.Rect(rect)
        if self._clip_rect is not None:
            r = r.clip(self._clip_rect)
        m = self._mouse_pos
        if r.w <= 0 or r.h <= 0 or not r.collidepoint(m):
            return False
        return self._in_overlay or not any(b.collidepoint(m) for b in self._blocks_prev)

    def _block(self, rect):
        """Shield a floating card: swallows clicks and stops hover on whatever
        it covers."""
        self._blocks.append(pygame.Rect(rect))
        self._hit(rect, None)

    def _fit(self, font, text, max_w):
        """Trim *text* with '..' so it fits max_w pixels."""
        if max_w <= 0:
            return ''
        if font.size(text)[0] <= max_w:
            return text
        key = (id(font), text, max_w)
        hit = self._fit_cache.get(key)
        if hit is not None:
            return hit
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if font.size(text[:mid].rstrip() + '..')[0] <= max_w:
                lo = mid
            else:
                hi = mid - 1
        res = text[:lo].rstrip() + '..'
        if len(self._fit_cache) > 800:
            self._fit_cache.clear()
        self._fit_cache[key] = res
        return res

    def _txt(self, screen, font, text, pos, color, anchor='topleft', dynamic=False, max_w=None):
        if max_w is not None:
            text = self._fit(font, text, max_w)
        surf = font.render(text, True, color)
        r = surf.get_rect(**{anchor: (int(pos[0]), int(pos[1]))})
        uk.blit_surface(screen, surf, r.topleft, transient=dynamic)
        return r

    def _caret(self, screen, x, y, h, color=None):
        """Blinking vertical caret bar (same convention RoomEditor uses)."""
        if int(self._blink * 2) % 2 != 0:
            return
        uk.draw_rect_on(screen, color or uk.Theme.TEXT_PRIMARY,
                        pygame.Rect(int(x), int(y), 2, int(h)), 0, 0)

    def _backdrop(self, screen, top, bottom):
        w, h = self.screen_width, self.screen_height
        uk.draw_rect_on(screen, _BG_BASE, pygame.Rect(0, 0, w, h), 0, 0)
        uk.draw_rect_on(screen, _BG_CONTENT, pygame.Rect(0, top, w, h - top - bottom), 0, 0)

    def _bar(self, screen, rect, line_at_bottom):
        rect = pygame.Rect(rect)
        uk.draw_rect_on(screen, _BAR_BG, rect, 0, 0)
        ly = rect.bottom - 1 if line_at_bottom else rect.y
        uk.draw_rect_on(screen, _BAR_LINE, pygame.Rect(rect.x, ly, rect.w, 1), 0, 0)

    def _panel(self, screen, rect, border=None, border_width=1, radius=10, shadow=False):
        uk.draw_panel(screen, rect, bg=uk.Theme.PANEL_BG, border=border or uk.Theme.PANEL_BORDER,
                      border_width=border_width, radius=radius, shadow=shadow)

    # ── Widgets ───────────────────────────────────────────────────────────────

    def _draw_icon_in(self, screen, icon, rect, color):
        if isinstance(icon, pygame.Surface):
            uk.blit_surface(screen, icon, icon.get_rect(center=rect.center), transient=False)
        else:
            icon(screen, rect, color)

    def _btn_width(self, label, icon=False, font=None, pad=14):
        font = font or self.font_small
        w = pad * 2
        if icon:
            w += 18 + (6 if label else 0)
        if label:
            w += font.size(label)[0]
        return max(w, 32)

    def _button(self, screen, rect, label=None, icon=None, action=None, arg=None,
                accent=None, danger=False, active=False, primary=False,
                enabled=True, font=None, key=None, radius=8):
        """Pill / icon button in the DevMenu-RoomEditor style: dark card body,
        hairline border that lights toward the accent on hover, soft glow.

        icon may be a vector fn(surface, rect, color) or a pre-baked PNG Surface.
        primary=True gives the gold-tinted "main action" look; active=True is
        the latched/toggled-on look.  Returns True while hovered."""
        rect = pygame.Rect(rect)
        col = uk.Theme.DANGER_BRIGHT if danger else (accent or uk.Theme.GOLD)
        hov = bool(enabled and action is not None and self._hit(rect, action, arg))
        t = self._anim(key or f'b:{action}:{arg}', 1.0 if (hov or active) and enabled else 0.0)

        if not enabled:
            base, border, fg = _FIELD_BASE, uk.Theme.CARD_BORDER, uk.Theme.TEXT_DIM
        elif primary:
            base = uk.lerp_color((36, 30, 17), (52, 42, 21), t)
            border = uk.lerp_color(uk.lerp_color(uk.Theme.CARD_BORDER, col, 0.55), col, t)
            fg = uk.lerp_color(col, uk.Theme.GOLD_BRIGHT if not danger else col, t)
        elif danger:
            base = uk.lerp_color(_ROW_BASE, (34, 22, 22), t)
            border = uk.lerp_color(uk.Theme.CARD_BORDER, col, t)
            fg = uk.lerp_color(uk.Theme.TEXT_SECONDARY, col, t)
        else:
            base = uk.lerp_color(_ROW_BASE, _ROW_HOVER, t)
            border = uk.lerp_color(uk.Theme.CARD_BORDER, col, t if active else t * 0.78)
            fg = uk.lerp_color(uk.Theme.TEXT_SECONDARY, col, t)
            if active:
                base = uk.lerp_color(base, col, 0.13)
        bw = 1 + (1 if (t > 0.5 and not primary) else 0)
        uk.draw_panel(screen, rect, bg=(*base, 255), border=border, border_width=bw,
                      radius=radius, shadow=False)
        if enabled and t > 0.01:
            uk.draw_soft_glow(screen, rect.center, min(34, max(rect.w, rect.h) // 2 + 4),
                              col, max_alpha=int(26 * t))

        font = font or self.font_small
        if label is None and icon is not None:
            box = pygame.Rect(0, 0, min(rect.w, rect.h) - 10, min(rect.w, rect.h) - 10)
            box.center = rect.center
            self._draw_icon_in(screen, icon, box, fg)
        elif label is not None:
            lab = font.render(label, True, fg)
            if icon is not None:
                gap, isz = 6, 18
                total = isz + gap + lab.get_width()
                x0 = rect.centerx - total // 2
                self._draw_icon_in(screen, icon, pygame.Rect(x0, rect.centery - isz // 2, isz, isz), fg)
                uk.blit_surface(screen, lab, lab.get_rect(midleft=(x0 + isz + gap, rect.centery)).topleft)
            else:
                uk.blit_surface(screen, lab, lab.get_rect(center=rect.center).topleft)
        return hov

    def _field(self, screen, rect, text, focused, field_arg, placeholder='', font=None):
        """Single-line text box.  Shows the tail of long text with the caret
        after it (like any normal input) instead of overflowing."""
        rect = pygame.Rect(rect)
        font = font or self.font_medium
        hov = self._hit(rect, 'field', field_arg)
        r = rect.clip(self._clip_rect) if self._clip_rect is not None else rect
        if r.w > 0 and r.h > 0:
            self._text_rects.append(r)
        t = self._anim(f'f:{field_arg}', 1.0 if hov else 0.0)
        base = uk.lerp_color(_FIELD_BASE, _FIELD_HOVER, t)
        border = uk.Theme.GOLD if focused else uk.lerp_color(uk.Theme.CARD_BORDER, uk.Theme.GOLD, t * 0.78)
        uk.draw_panel(screen, rect, bg=(*base, 255), border=border,
                      border_width=2 if focused else 1 + round(t), radius=8, shadow=False)

        inner = rect.inflate(-24, -4)
        self._push_clip(screen, inner)
        shown = text if text else ''
        color = uk.Theme.TEXT_PRIMARY if (shown or focused) else uk.Theme.TEXT_DIM
        if not shown and not focused and placeholder:
            shown = placeholder
        surf = font.render(shown, True, color)
        editing = bool(focused) and self._tc_ident is not None
        if editing:
            x = self._tc_text_x(font, text or '', inner)
        else:
            x = inner.x if surf.get_width() <= inner.w - 4 else inner.right - surf.get_width() - 4
        pos = (x, rect.centery - surf.get_height() // 2)
        uk.blit_surface(screen, surf, pos, transient=True)
        if editing:
            self._tc_draw_single(screen, font, text or '', x, rect.centery, rect)
        self._pop_clip(screen)
        return hov

    def _wrap_ranges(self, font, text, max_w):
        """Same wrapping as _wrap_text, but returns each visual line as a
        (start, end) index range into *text* — what caret / selection code
        needs.  text[s:e] is exactly the line _wrap_text would produce; the
        one character between consecutive lines (the dropped space or the
        newline) belongs to no line."""
        max_w = max(1, max_w)
        text = text or ''
        ranges = []
        base = 0
        for paragraph in text.split('\n'):
            plen = len(paragraph)
            if paragraph == '':
                ranges.append((base, base))
                base += 1
                continue
            cs = ce = None            # current line (None == empty)
            pos = base
            for word in paragraph.split(' '):
                ws, we = pos, pos + len(word)
                pos = we + 1
                cur_empty = cs is None or ce == cs
                cand_s, cand_e = (ws, we) if cur_empty else (cs, we)
                if font.size(text[cand_s:cand_e])[0] <= max_w:
                    cs, ce = cand_s, cand_e
                    continue
                if not cur_empty:
                    ranges.append((cs, ce))
                cs = ce = None
                if font.size(text[ws:we])[0] <= max_w:
                    cs, ce = ws, we
                else:
                    chs, che = ws, ws
                    for k in range(ws, we):
                        if font.size(text[chs:k + 1])[0] <= max_w:
                            che = k + 1
                        else:
                            if che > chs:
                                ranges.append((chs, che))
                            chs, che = k, k + 1
                    cs, ce = chs, che
            if cs is None:
                cs = ce = base + plen
            ranges.append((cs, ce))
            base += plen + 1
        return ranges or [(0, 0)]

    def _wrap_text(self, font, text, max_w):
        """Word-wrap *text* to fit *max_w* pixels.  Preserves explicit '\\n'
        hard breaks; oversized single words are hard-broken character by
        character so nothing can exceed max_w.  Always returns >= 1 line."""
        max_w = max(1, max_w)
        lines = []
        for paragraph in (text.split('\n') if text is not None else ['']):
            if paragraph == '':
                lines.append('')
                continue
            current = ''
            for word in paragraph.split(' '):
                candidate = word if current == '' else current + ' ' + word
                if font.size(candidate)[0] <= max_w:
                    current = candidate
                    continue
                if current != '':
                    lines.append(current)
                    current = ''
                if font.size(word)[0] <= max_w:
                    current = word
                else:
                    chunk = ''
                    for ch in word:
                        if font.size(chunk + ch)[0] <= max_w:
                            chunk += ch
                        else:
                            if chunk:
                                lines.append(chunk)
                            chunk = ch
                    current = chunk
            lines.append(current)
        return lines or ['']

    def _text_area(self, screen, x, y, W, key, buf, focused, max_lines):
        """Discord-style multiline box that grows downward as the text wraps
        (dialogue lines).  Returns the y just below it."""
        font = self.font_medium
        pad_x, pad_y = 12, 8
        line_h = self._fh('m') + 4
        text = buf or ''
        wrap_w = W - pad_x * 2
        ranges = self._wrap_ranges(font, text, wrap_w)
        n_rows = int(_clamp(len(ranges), 2, max(2, max_lines)))
        rect = pygame.Rect(x, y, W, pad_y * 2 + n_rows * line_h)

        hov = self._hit(rect, 'field', ('form', key))
        r = rect.clip(self._clip_rect) if self._clip_rect is not None else rect
        if r.w > 0 and r.h > 0:
            self._text_rects.append(r)
        t = self._anim(f'ta:{key}', 1.0 if hov else 0.0)
        base = uk.lerp_color(_FIELD_BASE, _FIELD_HOVER, t)
        border = uk.Theme.GOLD if focused else uk.lerp_color(uk.Theme.CARD_BORDER, uk.Theme.GOLD, t * 0.78)
        uk.draw_panel(screen, rect, bg=(*base, 255), border=border,
                      border_width=2 if focused else 1 + round(t), radius=8, shadow=False)

        self._push_clip(screen, rect.inflate(-pad_x, -pad_y))
        max_start = max(0, len(ranges) - n_rows)
        editing = bool(focused) and self._tc_ident == ('form', key)
        x0 = rect.x + pad_x
        if editing:
            # Scroll just enough to keep the caret's line visible (a fresh
            # focus starts at the bottom, where the caret is).
            vs = max_start if self._tc_vscroll is None else self._tc_vscroll
            vs = int(_clamp(vs, 0, max_start))
            self._tc_set_geom(kind='multi', rect=rect, x0=x0, y0=rect.y + pad_y,
                              line_h=line_h, start=vs, font=font, wrap_w=wrap_w)
            n = len(text)
            pos = min(self._tc_pos, n)
            car_line = self._tc_line_of(ranges, pos)
            if car_line < vs:
                vs = car_line
            elif car_line > vs + n_rows - 1:
                vs = car_line - n_rows + 1
            self._tc_vscroll = vs
            self._tc_geom['start'] = vs
            start = vs
            sel = None
            if self._tc_has_sel():
                a, b = self._tc_sel_range()
                sel = (min(a, n), min(b, n))
        else:
            start = max_start
        for i, (ls, le) in enumerate(ranges[start:start + n_rows]):
            ly = rect.y + pad_y + i * line_h
            line = text[ls:le]
            if line:
                surf = font.render(line, True, uk.Theme.TEXT_PRIMARY)
                uk.blit_surface(screen, surf, (x0, ly), transient=True)
            if not editing:
                continue
            if sel is not None:
                a, b = sel
                if a <= le and b >= ls:
                    lo, hi = max(a, ls), min(b, le)
                    sx = x0 + font.size(text[ls:lo])[0]
                    ex = x0 + font.size(text[ls:hi])[0]
                    if a <= le < b and le < n:       # selection covers the line break
                        ex += font.size(' ')[0]
                    if ex > sx:
                        uk.draw_rect_on(screen, (*uk.Theme.KI_BLUE, 90),
                                        pygame.Rect(sx, ly, ex - sx, self._fh('m')), 0, 0)
            if start + i == car_line:
                self._caret(screen, x0 + font.size(text[ls:max(ls, min(pos, le))])[0],
                            ly, self._fh('m'))
        self._pop_clip(screen)
        return rect.bottom

    def _select(self, screen, rect, text, pick_action, pick_arg=None, prev=None, nxt=None,
                dim=False, swatch=None, dot=None, key=None, arrow_w=32, caret=True):
        """Value picker:  [ ‹ ] [  value  v ] [ › ]   — the arrows step through
        the options, the middle opens the shared dropdown.  prev/nxt are
        (action, arg) tuples, or None for a dropdown-only picker."""
        rect = pygame.Rect(rect)
        az = arrow_w if prev else 0
        left = pygame.Rect(rect.x, rect.y, az, rect.h)
        right = pygame.Rect(rect.right - az, rect.y, az, rect.h)
        mid = pygame.Rect(rect.x + az, rect.y, rect.w - 2 * az, rect.h)

        k = key or f'sel:{pick_action}:{pick_arg}'
        hm = self._hit(mid, pick_action, pick_arg)
        hl = bool(prev) and self._hit(left, prev[0], prev[1])
        hr = bool(nxt) and self._hit(right, nxt[0], nxt[1])
        tm = self._anim(k + ':m', 1.0 if hm else 0.0)
        tl_ = self._anim(k + ':l', 1.0 if hl else 0.0)
        tr_ = self._anim(k + ':r', 1.0 if hr else 0.0)
        t_any = max(tm, tl_, tr_)

        base = uk.lerp_color(_FIELD_BASE, _FIELD_HOVER, t_any)
        border = uk.lerp_color(uk.Theme.CARD_BORDER, uk.Theme.GOLD, t_any * 0.78)
        uk.draw_panel(screen, rect, bg=(*base, 255), border=border,
                      border_width=1 + round(t_any), radius=8, shadow=False)

        if prev:
            for zone, tz, fn in ((left, tl_, _icon_chev_l), (right, tr_, _icon_chev_r)):
                if tz > 0.01:
                    uk.draw_rect_on(screen, (255, 255, 255, int(16 * tz)), zone.inflate(-6, -6), 0, 6)
                fn(screen, zone, uk.lerp_color(uk.Theme.TEXT_MUTED, uk.Theme.GOLD, tz))
            for x in (left.right, right.x):
                uk.draw_rect_on(screen, uk.Theme.CARD_BORDER,
                                pygame.Rect(x, rect.y + 7, 1, rect.h - 14), 0, 0)

        # value (+ optional swatch / track dot), centred in the middle zone
        cw = 14 if caret else 0
        avail = mid.w - 16 - cw
        color = uk.Theme.TEXT_DIM if dim else uk.lerp_color(uk.Theme.TEXT_PRIMARY, uk.Theme.GOLD_BRIGHT, tm)
        lead = 0
        if swatch is not None:
            lead = 22
        elif dot is not None:
            lead = 16
        surf = self.font_medium.render(self._fit(self.font_medium, text, max(10, avail - lead)), True, color)
        total = lead + surf.get_width()
        x0 = mid.centerx - cw // 2 - total // 2
        if swatch is not None:
            sw = pygame.Rect(x0, mid.centery - 7, 16, 14)
            uk.draw_rect_on(screen, swatch, sw, 0, 4)
            uk.draw_rect_on(screen, uk.Theme.CARD_BORDER, sw, 1, 4)
        elif dot is not None:
            uk.draw_circle_on(screen, dot, (x0 + 5, mid.centery), 5)
        uk.blit_surface(screen, surf, surf.get_rect(midleft=(x0 + lead, mid.centery)).topleft)
        if caret:
            _icon_chev_d(screen, pygame.Rect(mid.right - cw - 8, mid.centery - 8, cw, 16),
                         uk.lerp_color(uk.Theme.TEXT_DIM, uk.Theme.GOLD, tm))
        return hm

    def _switch_row(self, screen, rect, text, checked, action, arg):
        """Full-width toggle row: value text on the left, iOS-style switch right."""
        rect = pygame.Rect(rect)
        hov = self._hit(rect, action, arg)
        t = self._anim(f'sw:{action}:{arg}', 1.0 if hov else 0.0)
        on = self._anim(f'swv:{action}:{arg}', 1.0 if checked else 0.0, speed=16.0)
        base = uk.lerp_color(_FIELD_BASE, _FIELD_HOVER, t)
        border = uk.lerp_color(uk.Theme.CARD_BORDER, uk.Theme.GOLD, t * 0.78)
        uk.draw_panel(screen, rect, bg=(*base, 255), border=border,
                      border_width=1 + round(t), radius=8, shadow=False)
        self._txt(screen, self.font_medium, text, (rect.x + 12, rect.centery),
                  uk.lerp_color(uk.Theme.TEXT_SECONDARY, uk.Theme.TEXT_PRIMARY, on), anchor='midleft')
        track = pygame.Rect(0, 0, 40, 20)
        track.midright = (rect.right - 12, rect.centery)
        uk.draw_rect_on(screen, uk.lerp_color((44, 49, 63), uk.Theme.GOLD, on), track, 0, 10)
        kx = int(track.x + 10 + on * (track.w - 20))
        uk.draw_circle_on(screen, uk.lerp_color(uk.Theme.TEXT_MUTED, (24, 20, 10), on), (kx, track.centery), 7)
        return hov

    def _label(self, screen, x, y, text, color=None, max_w=None):
        self._txt(screen, self.font_small, text.upper(), (x, y),
                  color or uk.Theme.TEXT_MUTED, max_w=max_w)
        return y + self._fh('s') + 5

    def _section(self, screen, x, y, W, text):
        """Small dim caption with a hairline running out to the right edge."""
        r = self._txt(screen, self.font_small, text.upper(), (x, y), uk.Theme.TEXT_DIM)
        uk.draw_rect_on(screen, uk.Theme.CARD_BORDER,
                        pygame.Rect(r.right + 8, r.centery, max(0, x + W - r.right - 8), 1), 0, 0)
        return y + self._fh('s') + 9

    def _chip(self, screen, x, y, text, color, font=None, anchor='topleft', border=None,
              bg=(10, 12, 18, 220), dynamic=True):
        """Small rounded label plate (viewport HUD, badges).  Width snaps to
        a multiple of 8 so live-changing text doesn't fill ui_kit's shape
        cache with one entry per pixel width."""
        font = font or self.font_small
        surf = font.render(text, True, color)
        w = int(math.ceil((surf.get_width() + 20) / 8.0) * 8)
        h = surf.get_height() + 12
        r = pygame.Rect(0, 0, w, h)
        setattr(r, anchor, (int(x), int(y)))
        uk.draw_panel(screen, r, bg=bg, border=border or uk.Theme.CARD_BORDER,
                      border_width=1, radius=7, shadow=False)
        uk.blit_surface(screen, surf, surf.get_rect(center=r.center).topleft, transient=dynamic)
        return r

    def _scrollbar(self, screen, track, scroll, content_h, view_h):
        """Slim thumb along the right edge of a scrolling area."""
        if content_h <= view_h + 1:
            return
        track = pygame.Rect(track)
        th = max(24, int(track.h * view_h / content_h))
        span = max(1, content_h - view_h)
        ty = track.y + int((track.h - th) * (scroll / span))
        uk.draw_rect_on(screen, (255, 255, 255, 34), pygame.Rect(track.x, ty, 4, th), 0, 2)


    # ══════════════════════════════════════════════════════════════════════════
    # Edit view
    # ══════════════════════════════════════════════════════════════════════════

    def _draw_edit(self, screen):
        self._backdrop(screen, self.header_h, 0)
        self._draw_viewport_panel(screen)
        self._draw_left_panel(screen)
        self._draw_right_panel(screen)
        self._draw_timeline(screen)
        self._draw_edit_header(screen)
        self._draw_actor_form(screen)

    # ── Header ────────────────────────────────────────────────────────────────

    def _draw_edit_header(self, screen):
        w, hh = self.screen_width, self.header_h
        self._bar(screen, pygame.Rect(0, 0, w, hh), line_at_bottom=True)
        pad, bs = 12, hh - 16

        # Back (same PNG icon the dev menu / room editor headers use)
        self._button(screen, pygame.Rect(pad, 8, bs, bs), icon=self._icon_back_sm,
                     action='back', key='hdr:back')

        # ── right cluster: grid · save ─────────────────────────────────────────
        xr = w - pad
        sw = self._btn_width('Save', True)
        save_r = pygame.Rect(xr - sw, 8, sw, bs)
        xr = save_r.x - 8
        gw = self._btn_width('Grid', True)
        grid_r = pygame.Rect(xr - gw, 8, gw, bs)
        self._button(screen, grid_r, 'Grid', _icon_grid, 'grid_toggle', active=self._show_grid,
                     key='hdr:grid')
        cw = self._btn_width('Cam Track', True)
        cam_r = pygame.Rect(grid_r.x - 8 - cw, 8, cw, bs)
        self._button(screen, cam_r, 'Cam Track', _icon_camera_track, 'cam_track_toggle',
                     active=self._cam_track, key='hdr:camtrack')
        self._button(screen, save_r, 'Save', self._icon_save_png, 'save', primary=self.unsaved,
                     key='hdr:save')

        # ── centre block: timecode + play ─────────────────────────────────────
        tc_w = 172
        pw = max(self._btn_width('Play', True), self._btn_width('Stop', True)) + 10
        block_w = tc_w + 10 + pw
        left_end = pad + bs + 14
        cl = (w - block_w) // 2
        cl = min(cl, cam_r.x - block_w - 24)
        cl = max(cl, left_end + 150)

        tc = pygame.Rect(cl, 8, tc_w, bs)
        uk.draw_panel(screen, tc, bg=(*_FIELD_BASE, 255),
                      border=uk.Theme.GOLD if self._playing else uk.Theme.CARD_BORDER,
                      border_width=1, radius=8, shadow=False)
        dur = self.cutscene_data.get('duration', 10.0) if self.cutscene_data else 10.0
        cur = self.font_medium.render(f'{self._tl_playhead_t:05.2f}', True,
                                      uk.Theme.GOLD_BRIGHT if self._playing else uk.Theme.TEXT_PRIMARY)
        tot = self.font_small.render(f' / {dur:.2f}', True, uk.Theme.TEXT_DIM)
        gx = tc.centerx - (cur.get_width() + tot.get_width()) // 2
        uk.blit_surface(screen, cur, (gx, tc.centery - cur.get_height() // 2), transient=True)
        uk.blit_surface(screen, tot, (gx + cur.get_width(), tc.centery - tot.get_height() // 2 + 2),
                        transient=True)

        play_r = pygame.Rect(tc.right + 10, 8, pw, bs)
        if self._playing:
            self._button(screen, play_r, 'Stop', _icon_stop, 'play', danger=True, key='hdr:play')
        else:
            self._button(screen, play_r, 'Play', _icon_play, 'play', primary=True, key='hdr:play')

        # ── left block: name · unsaved dot · length ───────────────────────────
        len_w, len_lbl = 84, 'LENGTH'
        lbl_w = self.font_small.size(len_lbl)[0] + 10
        x = left_end
        if self.unsaved:
            uk.draw_circle_on(screen, uk.Theme.GOLD, (x + 4, hh // 2), 4)
            x += 16
        name = self.cutscene_name or 'untitled'
        name_max = max(40, (cl - 24) - x - (lbl_w + len_w + 28))
        name_r = self._txt(screen, self.font_large, name, (x, hh // 2), uk.Theme.TEXT_PRIMARY,
                           anchor='midleft', max_w=name_max)
        len_x = name_r.right + 28 + lbl_w
        self._txt(screen, self.font_small, len_lbl, (len_x - lbl_w + 2, hh // 2),
                  uk.Theme.TEXT_MUTED, anchor='midleft')
        field = pygame.Rect(len_x, 10, len_w, hh - 20)
        shown = self._duration_buf if self._duration_focus else f'{dur:g}s'
        self._field(screen, field, shown, self._duration_focus, ('duration', None),
                    font=self.font_small)

    # ── Scene panel (left) ────────────────────────────────────────────────────

    def _draw_left_panel(self, screen):
        fr = self._left_frame
        self._panel(screen, fr)
        pad = 12
        x, W = fr.x + pad, fr.w - pad * 2
        view_h = fr.h - 20
        self._left_scroll = int(_clamp(self._left_scroll, 0, max(0, self._left_content_h - view_h)))
        ch = self._ctl_h

        self._push_clip(screen, pygame.Rect(fr.x + 3, fr.y + 6, fr.w - 6, fr.h - 12))
        y0 = fr.y + 12 - self._left_scroll
        y = y0

        # Room — dropdown-only middle keeps room names readable; the arrows
        # still step through the list one room at a time.
        y = self._section(screen, x, y, W, 'Room')
        room = self.cutscene_data.get('room', '') if self.cutscene_data else ''
        self._select(screen, pygame.Rect(x, y, W, ch), room or '(none)', 'room_pick',
                     prev=('room_prev', None), nxt=('room_next', None),
                     dot=_ROOM_COLOR, key='sel:scene_room', arrow_w=28, caret=False)
        y += ch + 18

        # Actor tools sit above the (long, scrolling) layer list so the
        # controls you reach for most never need scrolling to.
        y = self._section(screen, x, y, W, 'Actors')
        half = (W - 8) // 2
        self._button(screen, pygame.Rect(x, y, half, ch), 'Actor', _icon_plus, 'actor_add',
                     accent=_ROOM_COLOR, active=self._actor_form, key='left:add')
        can_del = 0 <= self._actor_sel < len(self.cutscene_data.get('actors', []) if self.cutscene_data else [])
        self._button(screen, pygame.Rect(x + half + 8, y, W - half - 8, ch), 'Delete', self._icon_trash_png,
                     'actor_del', danger=True, enabled=can_del, key='left:del')
        y += ch + 8
        snap_w = int(W * 0.62)
        self._button(screen, pygame.Rect(x, y, snap_w, ch), 'Snap', _icon_grid, 'actor_snap_toggle',
                     active=self._actor_snap_enabled, key='left:snap')
        self._button(screen, pygame.Rect(x + snap_w + 8, y, W - snap_w - 8, ch),
                     f'{self._actor_snap_sizes[self._actor_snap_idx]} px', None,
                     'actor_snap_size', key='left:snapsz')
        y += ch + 18

        # Layers — the four fixed tracks are read-only, so they collapse into a
        # 2 x 2 chip grid; the interactive actor rows follow right under it.
        y = self._section(screen, x, y, W, 'Layers')
        actions = self.cutscene_data.get('actions', []) if self.cutscene_data else []
        tracks = self._tl_tracks()
        cw = (W - 6) // 2
        for k, (label, color, target) in enumerate(tracks[:4]):
            chip = pygame.Rect(x + (k % 2) * (cw + 6), y + (k // 2) * 30, cw, 26)
            uk.draw_panel(screen, chip, bg=(*_FIELD_BASE, 255), border=uk.Theme.CARD_BORDER,
                          border_width=1, radius=8, shadow=False)
            uk.draw_circle_on(screen, color, (chip.x + 12, chip.centery), 4)
            n = sum(1 for a in actions if a.get('target') == target)
            nw = 0
            if n:
                nr = self._txt(screen, self.font_small, str(n), (chip.right - 9, chip.centery),
                               uk.Theme.TEXT_MUTED, anchor='midright')
                nw = nr.w + 6
            self._txt(screen, self.font_small, label, (chip.x + 24, chip.centery),
                      uk.Theme.TEXT_MUTED, anchor='midleft', max_w=cw - 24 - nw - 8)
        y += 62

        for ti, (label, color, target) in enumerate(tracks[4:], start=4):
            aidx = ti - 4
            selected = aidx == self._actor_sel
            row = pygame.Rect(x, y, W, 30)
            hov = self._hit(row, 'actor_row', aidx)
            t = self._anim(f'layer:{ti}', 1.0 if hov else 0.0)
            base = uk.lerp_color(_ROW_BASE, _ROW_HOVER, t)
            if selected:
                base = uk.Theme.CARD_BG_SELECTED[:3]
            border = uk.Theme.GOLD if selected else uk.lerp_color(uk.Theme.CARD_BORDER, uk.Theme.GOLD, t * 0.78)
            uk.draw_panel(screen, row, bg=(*base, 255), border=border,
                          border_width=1 + (1 if selected else 0), radius=8, shadow=False)
            uk.draw_circle_on(screen, color, (row.x + 15, row.centery), 5)
            n = sum(1 for a in actions if a.get('target') == target)
            badge_w = 0
            if n:
                bs_ = self.font_small.render(str(n), True, uk.Theme.TEXT_MUTED)
                badge = pygame.Rect(0, 0, max(20, bs_.get_width() + 10), 18)
                badge.midright = (row.right - 8, row.centery)
                uk.draw_rect_on(screen, uk.Theme.CHIP_BG[:3], badge, 0, 6)
                uk.blit_surface(screen, bs_, bs_.get_rect(center=badge.center).topleft)
                badge_w = badge.w + 8
            self._txt(screen, self.font_small, label, (row.x + 28, row.centery),
                      uk.Theme.TEXT_PRIMARY if (selected or hov) else uk.Theme.TEXT_SECONDARY,
                      anchor='midleft', max_w=W - 28 - badge_w - 8)
            y += 34
        if not (self.cutscene_data and self.cutscene_data.get('actors')):
            y += 2
            for line in self._wrap_text(self.font_small, 'No actors yet. Add one to give it a track.', W):
                self._txt(screen, self.font_small, line, (x, y), uk.Theme.TEXT_DIM)
                y += self._fh('s') + 3
        else:
            y += 4
            for line in self._wrap_text(self.font_small, 'Drag an actor in the viewport to move it.', W):
                self._txt(screen, self.font_small, line, (x, y), uk.Theme.TEXT_DIM)
                y += self._fh('s') + 3

        self._left_content_h = (y + 6) - (y0 - 12) + 8
        self._pop_clip(screen)
        self._scrollbar(screen, pygame.Rect(fr.right - 8, fr.y + 8, 4, fr.h - 16),
                        self._left_scroll, self._left_content_h, view_h)

    # ── Floating "add actor" card ─────────────────────────────────────────────

    def _draw_actor_form(self, screen):
        if not self._actor_form or self._pick_mode == 'pick_actor' or not self.cutscene_data:
            return
        vf = self._vp_frame
        ch, fs = 32, self._fh('s')
        pad, gap = 12, 8
        W = min(300, vf.w - 24)
        row_h = fs + 5 + ch + gap
        # Size the card to its content.  The one-line hint is the first thing
        # to go on a short viewport; the card itself is never clipped, because
        # its primary button ("Place in viewport") must always be reachable.
        avail = vf.h - 16
        with_hint = True
        H = pad + 34 + row_h * 3 + (fs + 8) + 36 + pad
        if H > avail:
            with_hint = False
            H -= fs + 8
        rect = pygame.Rect(vf.x + 12, vf.y + 8, W, H)
        rect.bottom = min(rect.bottom, self.screen_height - 6)

        self._in_overlay = True
        self._block(rect)
        uk.draw_panel(screen, rect, bg=uk.Theme.PANEL_BG, border=uk.Theme.GOLD,
                      border_width=1, radius=10, shadow=True)
        x, iw, y = rect.x + pad, rect.w - pad * 2, rect.y + pad

        self._txt(screen, self.font_medium, 'ADD ACTOR', (x, y + 13), uk.Theme.GOLD, anchor='midleft')
        self._button(screen, pygame.Rect(rect.right - pad - 26, y, 26, 26), icon=_icon_close,
                     action='actor_form_close', key='af:close')
        y += 34

        atype = _ACTOR_TYPES[self._actor_type_idx % len(_ACTOR_TYPES)]
        y = self._label(screen, x, y, 'Type')
        self._select(screen, pygame.Rect(x, y, iw, ch), atype, 'actor_type_pick',
                     prev=('actor_type_prev', None), nxt=('actor_type_next', None), key='sel:af_type')
        y += ch + gap

        y = self._label(screen, x, y, 'Actor ID')
        self._field(screen, pygame.Rect(x, y, iw, ch), self._actor_id_buf,
                    self._actor_focus == 'id', ('actor', 'id'), placeholder='actor_0')
        y += ch + gap

        # Which entity to spawn — always picked from the entity catalogue
        # (or the player character folder), never typed by hand.
        etype_labels = {'enemy': 'Enemy', 'boss': 'Boss', 'npc': 'NPC', 'player': 'Character'}
        y = self._label(screen, x, y, etype_labels[atype])
        self._select(screen, pygame.Rect(x, y, iw, ch), self._actor_etype_buf or '(none found)',
                     'actor_etype_pick', dim=not self._actor_etype_buf, key='sel:af_etype')
        y += ch + gap

        if with_hint:
            self._txt(screen, self.font_small, 'Then click the viewport.', (x, y), uk.Theme.TEXT_DIM,
                      max_w=iw)
            y += fs + 8
        self._button(screen, pygame.Rect(x, y, iw, 36), 'Place in viewport', self._icon_pick_png,
                     'actor_place_confirm', primary=True, key='af:place')
        self._in_overlay = False

    # ── Inspector (right) ─────────────────────────────────────────────────────

    def _draw_right_panel(self, screen):
        fr = self._right_frame
        self._panel(screen, fr)
        pad = 14
        x, W = fr.x + pad, fr.w - pad * 2
        fs = self._fh('s')

        y = fr.y + 14
        self._txt(screen, self.font_small, 'INSPECTOR', (x, y), uk.Theme.TEXT_DIM)
        if self._form_active:
            new = self._form_new
            self._chip(screen, fr.right - pad, y - 5, 'NEW' if new else 'EDIT',
                       uk.Theme.GOLD_BRIGHT if new else uk.Theme.KI_BLUE, anchor='topright',
                       border=uk.Theme.GOLD if new else uk.Theme.KI_BLUE, dynamic=False)
        y += fs + 10
        uk.draw_rect_on(screen, uk.Theme.CARD_BORDER, pygame.Rect(x, y, W, 1), 0, 0)
        y += 10

        actions_h = 56 if self._form_active else 0
        body = pygame.Rect(fr.x + 3, y, fr.w - 6, fr.bottom - 8 - actions_h - y)

        # A different form (new action / another keyframe) starts scrolled to the top
        form_key = (self._form_active, self._form_new, self._tl_sel)
        if form_key != getattr(self, '_insp_key', None):
            self._insp_key = form_key
            self._insp_scroll = 0
        self._insp_scroll = int(_clamp(self._insp_scroll, 0, max(0, self._insp_content_h - body.h)))

        self._push_clip(screen, body)
        top = body.y - self._insp_scroll
        if self._form_active:
            end = self._draw_action_form(screen, x, top, W)
        else:
            end = self._draw_inspector_idle(screen, x, top, W)
        self._insp_content_h = (end - top) + 6
        self._pop_clip(screen)
        self._scrollbar(screen, pygame.Rect(fr.right - 8, body.y + 2, 4, body.h - 4),
                        self._insp_scroll, self._insp_content_h, body.h)

        if self._form_active:
            fy = fr.bottom - 12 - 34
            uk.draw_rect_on(screen, uk.Theme.CARD_BORDER, pygame.Rect(x, fy - 11, W, 1), 0, 0)
            ok_w = int((W - 8) * 0.56)
            self._button(screen, pygame.Rect(x, fy, ok_w, 34), 'Apply', None, 'form_commit',
                         primary=True, key='insp:ok')
            self._button(screen, pygame.Rect(x + ok_w + 8, fy, W - ok_w - 8, 34), 'Cancel', None,
                         'form_cancel', danger=True, key='insp:cancel')

    def _draw_inspector_idle(self, screen, x, y, W):
        if self._fail_edit:
            y = self._draw_fail_banner(screen, x, y, W)
        return self._draw_inspector_idle_body(screen, x, y, W)

    def _draw_inspector_idle_body(self, screen, x, y, W):
        """Nothing being edited: a gentle empty state, plus a read-only summary
        of the selected action if there is one."""
        actions = self.cutscene_data.get('actions', []) if self.cutscene_data else []
        sel = actions[self._tl_sel] if 0 <= self._tl_sel < len(actions) else None

        if self._tl_multi_ids:
            group = self._sel_actions()
            y += 18
            self._txt(screen, self.font_medium, f'{len(group)} keyframes selected',
                      (x + W // 2, y), uk.Theme.TEXT_PRIMARY, anchor='midtop', dynamic=True)
            y += self._fh('m') + 8
            if group:
                span = f'{group[0]["time"]:.2f}s  to  {group[-1]["time"]:.2f}s'
                self._txt(screen, self.font_small, span, (x + W // 2, y),
                          uk.Theme.TEXT_SECONDARY, anchor='midtop', dynamic=True)
                y += self._fh('s') + 8
            hint = ('Drag any selected keyframe to move them all together. '
                    'Ctrl/Shift-click to add or remove one. Esc clears the selection.')
            for line in self._wrap_text(self.font_small, hint, W - 8):
                self._txt(screen, self.font_small, line, (x + W // 2, y), uk.Theme.TEXT_MUTED, anchor='midtop')
                y += self._fh('s') + 4
            y += 14
            self._button(screen, pygame.Rect(x, y, W, 36), 'New action', _icon_plus, 'tl_add',
                         primary=True, key='insp:new_multi')
            return y + 36

        if sel is None:
            y += 18
            self._txt(screen, self.font_medium, 'Nothing selected', (x + W // 2, y),
                      uk.Theme.TEXT_SECONDARY, anchor='midtop')
            y += self._fh('m') + 8
            for line in self._wrap_text(self.font_small,
                                        'Click a keyframe on the timeline to edit it (Ctrl/Shift-click or drag a box to select several), or add a new action.', W - 8):
                self._txt(screen, self.font_small, line, (x + W // 2, y), uk.Theme.TEXT_MUTED, anchor='midtop')
                y += self._fh('s') + 4
            y += 14
            self._button(screen, pygame.Rect(x, y, W, 36), 'New action', _icon_plus, 'tl_add',
                         primary=True, key='insp:new')
            return y + 36

        # QTE step selected: the fail-sequence button goes FIRST, above the
        # (tall) summary card, so it never ends up scrolled out of view. It is
        # always shown for a QTE step, greyed out with the reason when unusable.
        if not self._fail_edit and sel.get('type') == 'qte' and sel.get('target') == 'screen':
            sp = sel.get('params') or {}
            ok_mode = sp.get('mode') == 'timing'
            ok_id   = bool(str(sp.get('qte_id') or '').strip())
            self._button(screen, pygame.Rect(x, y, W, 36), 'Edit fail sequence', None,
                         'fail_edit', primary=True, enabled=(ok_mode and ok_id),
                         key='insp:fail_edit')
            y += 36 + 6
            why = None
            if not ok_mode:
                why = 'Fail sequences need Mode = timing. Click "Edit action" and set Mode to timing.'
            elif not ok_id:
                why = 'Give this QTE an id first (Edit action > QTE Id).'
            if why:
                for line in self._wrap_text(self.font_small, why, W - 8):
                    self._txt(screen, self.font_small, line, (x + W // 2, y),
                              uk.Theme.TEXT_MUTED, anchor='midtop')
                    y += self._fh('s') + 4
            y += 10

        # Selected-action summary card
        color = self._target_color(sel.get('target', ''))
        card_pad = 12
        params = _ACTION_PARAMS.get(sel.get('type', ''), [])
        rows = [('Time', f'{sel.get("time", 0.0):.2f} s'),
                ('Target', sel.get('target', '')),
                ('Action', str(sel.get('type', '')).replace('_', ' '))]
        for key, label, hint in params:
            val = str(sel.get('params', {}).get(key, ''))
            rows.append((label, val if val != '' else self._empty_label(hint)))
        line_h = self._fh('s') + 8
        H = card_pad * 2 + line_h * len(rows)
        card = pygame.Rect(x, y, W, H)
        uk.draw_panel(screen, card, bg=(*_ROW_BASE, 255), border=uk.Theme.CARD_BORDER,
                      border_width=1, radius=8, shadow=False)
        uk.draw_rect_on(screen, color, pygame.Rect(card.x + 1, card.y + 10, 3, card.h - 20), 0, 1)
        cy = card.y + card_pad
        for k, v in rows:
            self._txt(screen, self.font_small, k, (card.x + card_pad + 4, cy), uk.Theme.TEXT_MUTED,
                      max_w=W // 2 - 20)
            self._txt(screen, self.font_small, v, (card.right - card_pad, cy), uk.Theme.TEXT_PRIMARY,
                      anchor='topright', max_w=W // 2 - 4)
            cy += line_h
        y = card.bottom + 12
        self._button(screen, pygame.Rect(x, y, W, 34), 'Edit action', self._icon_pick_png, 'summary_edit',
                     key='insp:edit')
        y += 34 + 8
        self._button(screen, pygame.Rect(x, y, W, 34), 'New action', _icon_plus, 'tl_add',
                     primary=True, key='insp:new2')
        return y + 34

    # ── Action form ───────────────────────────────────────────────────────────

    _PICK_FOR_PAIR = {
        ('x', 'y'): (('pan_to', 'snap_to', 'move_to', 'fly_to', 'teleport'), None,
                     'Pick X, Y in viewport'),
        ('start_x', 'start_y'): (('pan_to',), 'pick_pan_to_start', 'Pick start X, Y in viewport'),
        ('target_x', 'target_y'): (('attack',), 'pick_attack_target', 'Pick beam stop in viewport'),
    }

    def _draw_action_form(self, screen, x, y, W):
        ch, gap = self._ctl_h, 12
        tcol = self._target_color(self._form_target)

        y = self._label(screen, x, y, 'Time (sec)')
        self._field(screen, pygame.Rect(x, y, W, ch), self._form_time_buf,
                    self._form_focus == 'time', ('form', 'time'), placeholder='0.0')
        y += ch + gap

        y = self._label(screen, x, y, 'Target')
        self._select(screen, pygame.Rect(x, y, W, ch), self._form_target, 'form_target_pick',
                     prev=('form_target_prev', None), nxt=('form_target_next', None),
                     dot=tcol, key='sel:form_target')
        y += ch + gap

        y = self._label(screen, x, y, 'Action')
        self._select(screen, pygame.Rect(x, y, W, ch), self._form_type.replace('_', ' '),
                     'form_type_pick', prev=('form_type_prev', None), nxt=('form_type_next', None),
                     key='sel:form_type')
        y += ch + gap + 2

        y = self._section(screen, x, y, W, 'Parameters')

        params = _ACTION_PARAMS.get(self._form_type, [])
        if not params:
            for line in self._wrap_text(self.font_small, 'This action has no parameters.', W):
                self._txt(screen, self.font_small, line, (x, y), uk.Theme.TEXT_DIM)
                y += self._fh('s') + 3
            y += 6

        # Adjacent X / Y params share one row.
        pair_of = {'x': 'y', 'start_x': 'start_y', 'target_x': 'target_y'}
        items, i = [], 0
        while i < len(params):
            k = params[i][0]
            if k in pair_of and i + 1 < len(params) and params[i + 1][0] == pair_of[k]:
                items.append(('pair', params[i], params[i + 1]))
                i += 2
            else:
                items.append(('single', params[i]))
                i += 1

        for item in items:
            if item[0] == 'pair':
                (kx, lx, _hx), (ky, ly, _hy) = item[1], item[2]
                half = (W - 8) // 2
                self._label(screen, x, y, lx, max_w=half)
                y = self._label(screen, x + half + 8, y, ly, max_w=half)
                self._field(screen, pygame.Rect(x, y, half, ch), self._form_params.get(kx, ''),
                            self._form_focus == kx, ('form', kx))
                self._field(screen, pygame.Rect(x + half + 8, y, W - half - 8, ch),
                            self._form_params.get(ky, ''), self._form_focus == ky, ('form', ky))
                y += ch + 8
                types, pick_name, pick_label = self._PICK_FOR_PAIR.get((kx, ky), ((), None, ''))
                if self._form_type in types:
                    name = pick_name or f'pick_{self._form_type}'
                    self._button(screen, pygame.Rect(x, y, W, 30), pick_label, self._icon_pick_png, 'pick', name,
                                 active=(self._pick_mode == name), key=f'pick:{name}')
                    y += 30 + 8
                y += gap - 8
                continue

            key, label, hint = item[1]
            buf = self._form_params.get(key, '')
            if hint == 'room':
                y = self._label(screen, x, y, 'Room group')
                self._select(screen, pygame.Rect(x, y, W, ch), self._form_room_group or 'All groups',
                             'room_group_pick', prev=('room_group_prev', None),
                             nxt=('room_group_next', None), key='sel:rgroup')
                y += ch + 8
                y = self._label(screen, x, y, label)
                self._select(screen, pygame.Rect(x, y, W, ch), buf or '(none)', 'room_name_pick',
                             prev=('room_name_prev', None), nxt=('room_name_next', None),
                             dim=not buf, dot=_ROOM_COLOR, key='sel:rname')
                y += ch + gap
                continue

            y = self._label(screen, x, y, label, max_w=W)
            if hint == 'bool':
                on = buf != 'False'
                self._switch_row(screen, pygame.Rect(x, y, W, ch), 'On' if on else 'Off', on,
                                 'param_toggle', key)
                y += ch + gap
            elif hint in self._ENUM_HINTS:
                shown = buf if buf != '' else self._empty_label(hint)
                swatch = _COLOR_PRESETS.get(buf) if hint == 'color' else None
                self._select(screen, pygame.Rect(x, y, W, ch), shown, 'param_pick', (key, hint),
                             prev=('param_prev', (key, hint)), nxt=('param_next', (key, hint)),
                             dim=(buf == ''), swatch=swatch, key=f'sel:p:{key}')
                y += ch + gap
            elif key == 'text' and self._form_type == 'dialogue':
                max_lines = getattr(self.dialogue_box, 'MAX_LINES', None) if self.dialogue_box else None
                y = self._text_area(screen, x, y, W, key, buf, self._form_focus == key,
                                    max_lines if isinstance(max_lines, int) else 6)
                if self.dialogue_box is not None:
                    y = self._draw_dialogue_capacity_hint(screen, x, y, W, buf)
                y += gap - 2
            else:
                self._field(screen, pygame.Rect(x, y, W, ch), buf, self._form_focus == key, ('form', key))
                y += ch + gap

        if self._form_type in ('play_music', 'play_sfx', 'stop_sfx', 'stop_music') and self.sound_manager is not None:
            label = 'Preview stop' if self._form_type in ('stop_music', 'stop_sfx') else 'Preview'
            self._button(screen, pygame.Rect(x, y, W, ch), label, _icon_speaker, 'preview_sound',
                         accent=_SOUND_COLOR, key='insp:preview')
            y += ch + gap
        return y

    def _draw_dialogue_capacity_hint(self, screen, x, y, W, buf):
        """Small 'lines used' readout under the dialogue text field, so the
        designer can see how much room is left instead of only discovering
        the limit when a keystroke gets refused."""
        portrait_key = self._form_params.get('portrait') or None
        lines_used   = len(self.dialogue_box.wrap_text(buf, portrait_key=portrait_key)) if buf else 0
        max_lines    = self.dialogue_box.MAX_LINES
        at_limit     = self._text_limit_hit and self._form_focus == 'text'

        label = f'Lines {lines_used} / {max_lines}'
        if at_limit:
            label += '  -  box is full'
        col = (uk.Theme.DANGER_BRIGHT if at_limit
               else uk.Theme.GOLD if lines_used >= max_lines else uk.Theme.TEXT_MUTED)
        self._txt(screen, self.font_small, label, (x, y + 5), col, dynamic=True, max_w=W)
        return y + 5 + self._fh('s') + 2


    # ══════════════════════════════════════════════════════════════════════════
    # Viewport panel
    # ══════════════════════════════════════════════════════════════════════════

    def _draw_viewport_panel(self, screen):
        vf, vp = self._vp_frame, self._vp_rect
        picking = bool(self._pick_mode)
        uk.draw_panel(screen, vf, bg=(8, 10, 16, 255),
                      border=uk.Theme.GOLD if picking else uk.Theme.PANEL_BORDER,
                      border_width=2 if picking else 1, radius=10, shadow=False)

        self._marker_labels = []
        self._push_clip(screen, vp)
        self._draw_viewport(screen, vp)
        self._draw_marker_labels(screen, vp)
        self._draw_dialogue_overlay(screen, vp)
        self._draw_viewport_hud(screen, vp)
        self._pop_clip(screen)

    def _draw_marker_labels(self, screen, vp):
        """Actor id tags — drawn in screen space (not inside the zoomed canvas)
        so the pixel font stays crisp at any viewport zoom."""
        z = self._vp_zoom
        for text, col, sx, sy in self._marker_labels:
            px, py = vp.x + sx * z + 14, vp.y + sy * z
            surf = self.font_small.render(text, True, col)
            plate = pygame.Rect(0, 0, surf.get_width() + 10, surf.get_height() + 6)
            plate.midleft = (int(px), int(py))
            uk.draw_rect_on(screen, (8, 10, 16, 190), plate, 0, 4)
            uk.blit_surface(screen, surf, surf.get_rect(center=plate.center).topleft)

    def _draw_dialogue_overlay(self, screen, vp):
        # DialogueBox sizes itself from screen_width/screen_height (integer
        # frame scale, pixel-perfect). Drawing at full game resolution then
        # pygame.transform.scale-ing down into the smaller viewport reintroduced
        # the uneven-pixel-row artifact we fixed in dialogue.py — non-integer
        # scale maps some source rows to N dest pixels and others to N+1.
        #
        # Instead, temporarily tell the box the viewport's size so it builds
        # at the size it will actually occupy, then blit 1:1. Capacity checks
        # (fits_box / wrap_text) still use the real game resolution because
        # we only swap the dimensions for this draw call.
        #
        # Draws straight onto `screen` at vp.topleft via DialogueBox.draw's
        # `offset` param, rather than through an intermediate viewport-sized
        # SRCALPHA surface. That intermediate surface used to be allocated
        # and — on the GPU backend — uploaded as a brand-new texture
        # (blit_surface(..., transient=True)) every single frame the box was
        # active, sized to the *whole editor viewport* rather than the box
        # itself (often far bigger than the box content), which is what
        # made the open/close animation so slow. DialogueBox.draw() already
        # builds its own tightly-sized internal surface (and, as of the
        # dialogue.py caching fix, reuses it across frames when the content
        # hasn't changed) — no need to wrap it in another one.
        if self.dialogue_box and getattr(self.dialogue_box, 'active', False):
            _dlg_colors = {
                'WHITE': (255, 255, 255), 'RED': (220, 60, 60),
                'DARK_GRAY': (40, 40, 40), 'CYAN': (80, 220, 220),
            }
            _ow = self.dialogue_box.screen_width
            _oh = self.dialogue_box.screen_height
            self.dialogue_box.screen_width  = vp.width
            self.dialogue_box.screen_height = vp.height
            try:
                self.dialogue_box.draw(screen, _dlg_colors, offset=vp.topleft)
            finally:
                self.dialogue_box.screen_width  = _ow
                self.dialogue_box.screen_height = _oh

        # QTE bar for 'qte' steps — laid out inside the viewport rect, on top
        # of the dialogue overlay, using the editor's per-frame blit helper.
        if self._qte_bar is not None and self._qte_bar.active:
            self._qte_bar.draw(
                screen, offset=vp.topleft, size=vp.size,
                blit_fn=lambda surf, pos: uk.blit_surface(screen, surf, pos))

    def _draw_viewport_hud(self, screen, vp):
        """Everything painted over the world: mode banner, drag hint ring,
        pick-mode ghosts, cursor coordinates and the zoom chip."""
        mx, my = self._mouse_pos
        z = self._vp_zoom
        inside = vp.collidepoint(mx, my) and self._dd is None
        actors = self.cutscene_data.get('actors', []) if self.cutscene_data else []

        # ── Mode banner ───────────────────────────────────────────────────────
        banner, bcol = None, uk.Theme.GOLD_BRIGHT
        if self._pick_mode:
            if self._pick_mode == 'pick_attack_target':
                direction = self._form_params.get('direction', 'down')
                axis = 'height (Y)' if direction in ('up', 'down') else 'distance (X)'
                banner = f'Click where the beam should stop  -  {axis} only  -  Esc to cancel'
            elif self._pick_mode == 'pick_actor':
                banner = 'Click to place the actor  -  Esc to cancel'
            elif self._pick_mode in _ACTOR_PICKS:
                banner = ('Click to set the destination  -  '
                          + ('snapping to grid  -  ' if self._actor_snap_enabled else '')
                          + 'Esc to cancel')
            else:
                banner = f'Click to pick a position  -  {self._pick_mode}  -  Esc to cancel'
        elif self._actor_drag_idx >= 0 and self._actor_drag_idx < len(actors):
            a = actors[self._actor_drag_idx]
            banner = (f'Dragging {a.get("id", "actor")}  -  '
                      f'X {a.get("x", 0):.1f}   Y {a.get("y", 0):.1f}')
            bcol = uk.Theme.TEXT_PRIMARY
        if banner:
            self._chip(screen, vp.x + 10, vp.y + 10, banner, bcol, font=self.font_medium,
                       border=uk.Theme.GOLD, bg=(10, 12, 18, 235))

        # ── Hover ring: this actor can be dragged ─────────────────────────────
        if (inside and not self._playing and not self._pick_mode
                and self._actor_drag_idx < 0 and self.cutscene_data and not self._actor_form):
            hwx, hwy = self._mouse_world()
            for i, actor in enumerate(actors):
                ax, ay = float(actor.get('x', 0)), float(actor.get('y', 0))
                if abs(hwx - ax) <= 20.0 and abs(hwy - ay) <= 20.0:
                    col = _ACTOR_COLORS[i % len(_ACTOR_COLORS)]
                    scr_x, scr_y = self._world_to_screen(ax, ay)
                    ring_r = max(6, int(16 * z))
                    uk.draw_circle_on(screen, col, (scr_x, scr_y), ring_r, 2)
                    uk.draw_circle_on(screen, _WHITE, (scr_x, scr_y), ring_r, 1)
                    self._chip(screen, scr_x + ring_r + 6, scr_y, 'drag to move', col,
                               anchor='midleft', dynamic=False)
                    break

        # ── Pick-mode ghosts ──────────────────────────────────────────────────
        if self._pick_mode and inside:
            wx, wy = self._mouse_world()
            acc = uk.Theme.GOLD
            if self._pick_mode == 'pick_actor':
                ghost = _ACTOR_COLORS[len(actors) % len(_ACTOR_COLORS)]
                uk.draw_circle_on(screen, ghost, (mx, my), 13, 2)
                uk.draw_circle_on(screen, _WHITE, (mx, my), 13, 1)
                uk.draw_line_on(screen, ghost, (mx - 18, my), (mx + 18, my), 1)
                uk.draw_line_on(screen, ghost, (mx, my - 18), (mx, my + 18), 1)
                self._chip(screen, mx + 22, my - 8, self._place_actor_def.get('id', 'actor'), ghost,
                           anchor='bottomleft')
                self._chip(screen, mx + 22, my - 4, f'({wx:.1f}, {wy:.1f})', uk.Theme.TEXT_SECONDARY,
                           anchor='topleft')
            elif self._pick_mode in ('pick_pan_to', 'pick_snap_to', 'pick_move_to',
                                     'pick_fly_to', 'pick_teleport', 'pick_pan_to_start'):
                if self._pick_mode in _ACTOR_PICKS:
                    # Show the crosshair where the click will really land.
                    wx, wy = self._snap_actor_xy(wx, wy)
                    mx, my = self._world_to_screen(wx, wy)
                arm = 22
                uk.draw_line_on(screen, acc, (mx - arm, my), (mx + arm, my), 1)
                uk.draw_line_on(screen, acc, (mx, my - arm), (mx, my + arm), 1)
                uk.draw_circle_on(screen, acc, (mx, my), 5, 1)
                self._chip(screen, mx + arm + 6, my, f'({wx:.1f}, {wy:.1f})', acc, anchor='midleft')
            elif self._pick_mode == 'pick_attack_target':
                # The beam only ever travels along the actor's facing axis, so
                # only one coordinate of the click matters: draw a guide locked
                # to that axis, running from the actor to the cursor.
                direction = self._form_params.get('direction', 'down')
                actor_def = next((a for a in actors if a.get('id') == self._form_target), None)
                start = (self._actor_pos_before(actor_def['id'], self._form_time())
                         if actor_def else None)
                ax = start[0] if start else wx
                ay = start[1] if start else wy
                a_x, a_y = self._world_to_screen(ax, ay)
                if direction in ('up', 'down'):
                    uk.draw_line_on(screen, acc, (a_x, a_y), (a_x, my), 2)
                    self._draw_impact_marker(screen, (a_x, my), acc)
                    self._chip(screen, a_x + 24, my, f'stop Y = {wy:.1f}', acc, anchor='midleft')
                else:
                    uk.draw_line_on(screen, acc, (a_x, a_y), (mx, a_y), 2)
                    self._draw_impact_marker(screen, (mx, a_y), acc)
                    self._chip(screen, mx + 24, a_y, f'stop X = {wx:.1f}', acc, anchor='midleft')

        # ── Destination readout (actor move / fly / teleport) ─────────────────
        pv = self._dest_preview()
        if pv:
            dx, dy = pv['dest']
            col = _ACTOR_COLORS[pv['idx'] % len(_ACTOR_COLORS)]
            label = f'{pv["actor"].get("id", "actor")}  ->  ({dx:.1f}, {dy:.1f})'
            if pv['start']:
                dist = math.hypot(dx - pv['start'][0], dy - pv['start'][1])
                label += f'   dist {dist:.0f}'
                try:
                    dur = float(self._form_params.get('duration', ''))
                except ValueError:
                    dur = 0.0
                if pv['type'] != 'teleport' and dur > 0:
                    label += f'   {dist / dur:.0f} u/s'
            px, py = self._world_to_screen(dx, dy)
            self._chip(screen, px + 16, py + 20, label, col, anchor='topleft')

        # Persistent beam-stop preview while an attack form is open.
        if (not self._pick_mode and self._form_active and self._form_type == 'attack'):
            actor_def = next((a for a in actors if a.get('id') == self._form_target), None)
            start = (self._actor_pos_before(actor_def['id'], self._form_time())
                     if actor_def else None)
            if start:
                direction = self._form_params.get('direction', 'down')
                key = 'target_y' if direction in ('up', 'down') else 'target_x'
                try:
                    tv = float(self._form_params.get(key, ''))
                except ValueError:
                    tv = None
                if tv is not None:
                    a_x, a_y = self._world_to_screen(*start)
                    end = (a_x, self._world_to_screen(0, tv)[1]) if key == 'target_y' \
                        else (self._world_to_screen(tv, 0)[0], a_y)
                    uk.draw_line_on(screen, uk.Theme.GOLD, (a_x, a_y), end, 2)
                    self._draw_impact_marker(screen, end, uk.Theme.GOLD)
                    self._chip(screen, end[0] + 24, end[1], f'beam stops {key[-1].upper()} = {tv:.1f}',
                               uk.Theme.GOLD, anchor='midleft')

        # ── Cursor coordinates (bottom-left) ──────────────────────────────────
        if inside and not self._pick_mode:
            wx, wy = self._mouse_world()
            self._chip(screen, vp.x + 8, vp.bottom - 8, f'X {wx:.1f}   Y {wy:.1f}',
                       uk.Theme.TEXT_SECONDARY, anchor='bottomleft')

        # ── Zoom chip (bottom-right) — click to snap back to 1.00x ────────────
        default_zoom = abs(z - 1.0) < 0.01
        label = f'{z:.2f}x' if default_zoom else f'{z:.2f}x  -  click to reset'
        surf = self.font_small.render(label, True, uk.Theme.TEXT_SECONDARY)
        w = int(math.ceil((surf.get_width() + 24) / 8.0) * 8)
        rect = pygame.Rect(0, 0, w, surf.get_height() + 14)
        rect.bottomright = (vp.right - 8, vp.bottom - 8)
        hov = self._hit(rect, 'vp_zoom_reset')
        t = self._anim('vp:zoom', 1.0 if hov else 0.0)
        col = uk.Theme.GOLD if not default_zoom else uk.Theme.TEXT_MUTED
        uk.draw_panel(screen, rect, bg=(10, 12, 18, 220),
                      border=uk.lerp_color(uk.Theme.CARD_BORDER, uk.Theme.GOLD, max(t, 0.0 if default_zoom else 0.6)),
                      border_width=1, radius=7, shadow=False)
        surf = self.font_small.render(label, True, uk.lerp_color(col, uk.Theme.GOLD_BRIGHT, t))
        uk.blit_surface(screen, surf, surf.get_rect(center=rect.center).topleft, transient=True)

    def _draw_actor_marker(self, inter_surf, actor, idx, selected):
        """Draw actor onto the intermediate surface.

        Tries to show the real sprite (via a cached entity).  Falls back to a
        coloured circle if the entity/sprite couldn't be created.
        Drawn at base RENDER_SCALE on the intermediate surface; zoom is handled
        by the caller scaling the whole surface afterward.  The id label is
        queued instead of drawn here so it renders crisp in screen space.
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
                inter_surf.draw_circle(col, (sx, sy), 14, 2)
                inter_surf.draw_circle(_WHITE, (sx, sy), 14, 1)
            else:
                inter_surf.draw_circle(col, (sx, sy), 10, 1)
        else:
            # Fallback: plain coloured circle
            r = 10 if selected else 7
            inter_surf.draw_circle(col, (sx, sy), r)
            if selected:
                inter_surf.draw_circle(_WHITE, (sx, sy), r, 2)

        # Label (always drawn so the actor is identifiable)
        self._marker_labels.append((actor.get('id', '?'), col, sx, sy))

    # ══════════════════════════════════════════════════════════════════════════
    # Timeline
    # ══════════════════════════════════════════════════════════════════════════

    def _ruler_steps(self):
        """(label_step, subdivisions) so ruler labels stay >= ~56 px apart at
        any timeline zoom."""
        z = self._tl_time_zoom
        step = 60
        for s in (0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60):
            if s * z >= 56:
                step = s
                break
        sub = {0.1: 2, 0.25: 5, 0.5: 5, 1: 2, 2: 4, 5: 5, 10: 5, 15: 3, 30: 3, 60: 4}[step]
        return step, sub

    def _draw_tl_toolbar(self, screen, tl, dur, actions):
        y, h = tl.y + 2, 32
        x = tl.x
        sel_ok = bool(self._tl_multi_ids) or 0 <= self._tl_sel < len(actions)
        interval = self._tl_grid_intervals[self._tl_grid_idx]
        int_lbl = f'{interval:g}s'
        total_lbl = f'{dur:.1f}s total'
        if self._tl_multi_ids:
            total_lbl = f'{len(self._tl_multi_ids)} selected  -  {total_lbl}'

        # Measure the full layout first; if it can't fit beside the "total"
        # readout, fall back to icon-only Duplicate / Delete and drop the
        # zoom caption so nothing ever overprints (narrow windows).
        w_dup, w_del = self._btn_width('Duplicate', True), self._btn_width('Delete', True)
        w_snap, w_int = self._btn_width('Snap', True), self._btn_width(int_lbl, False)
        zoom_cap_w = self.font_small.size('ZOOM')[0] + 10
        full_w = (w_dup + 8 + w_del + 6 + (h + 6) * 2 + 8 + 15 + zoom_cap_w + h + 6 + h + 8 + 62 + 12
                  + 15 + w_snap + 6 + w_int)
        total_w = self.font_small.size(total_lbl)[0]
        compact = full_w + total_w + 24 > tl.w
        if compact:
            w_dup = w_del = h

        self._button(screen, pygame.Rect(x, y, w_dup, h), None if compact else 'Duplicate', self._icon_dup_png,
                     'tl_dup', enabled=sel_ok, key='tl:dup')
        x += w_dup + 8
        self._button(screen, pygame.Rect(x, y, w_del, h), None if compact else 'Delete', self._icon_trash_png,
                     'tl_del', danger=True, enabled=sel_ok, key='tl:del')
        x += w_del + 6
        self._button(screen, pygame.Rect(x, y, h, h), icon=_icon_dup, action='tl_copy',
                     enabled=sel_ok, key='tl:copy')
        x += h + 6
        self._button(screen, pygame.Rect(x, y, h, h), icon=_icon_paste, action='tl_paste',
                     enabled=bool(CutsceneEditor._clipboard), key='tl:paste')
        x += h + 8

        def sep(px):
            uk.draw_rect_on(screen, uk.Theme.CARD_BORDER, pygame.Rect(px, y + 6, 1, h - 12), 0, 0)
            return px + 15

        x = sep(x)
        if not compact:
            self._txt(screen, self.font_small, 'ZOOM', (x, y + h // 2), uk.Theme.TEXT_MUTED, anchor='midleft')
            x += zoom_cap_w
        self._button(screen, pygame.Rect(x, y, h, h), icon=_icon_minus, action='tl_zoom_out', key='tl:zo')
        x += h + 6
        self._button(screen, pygame.Rect(x, y, h, h), icon=_icon_plus, action='tl_zoom_in', key='tl:zi')
        x += h + 8
        if not compact:
            r = self._txt(screen, self.font_small, f'{self._tl_time_zoom:.0f} px/s', (x, y + h // 2),
                          uk.Theme.TEXT_DIM, anchor='midleft', dynamic=True)
            x = max(r.right, x + 62) + 12
        x = sep(x)

        self._button(screen, pygame.Rect(x, y, w_snap, h), 'Snap', _icon_grid, 'tl_grid_toggle',
                     active=self._tl_grid_enabled, key='tl:grid')
        x += w_snap + 6
        self._button(screen, pygame.Rect(x, y, w_int, h), int_lbl, None, 'tl_grid_interval', key='tl:gridint')
        x += w_int

        if x + 16 + total_w <= tl.right:
            self._txt(screen, self.font_small, total_lbl, (tl.right - 4, y + h // 2),
                      uk.Theme.TEXT_MUTED, anchor='midright', dynamic=True)

    def _draw_timeline(self, screen):
        """Track-based timeline: toolbar, time ruler, one lane per track with
        keyframe diamonds + duration bars, and the playhead."""
        self._panel(screen, self._tl_frame)
        if not self.cutscene_data:
            return

        tl, label_end_x, ruler_y, tracks_y = self._tl_geometry()
        dur      = self.cutscene_data.get('duration', 10.0)
        zoom     = self._tl_time_zoom
        scroll_x = self._tl_scroll_x
        time_w   = tl.right - label_end_x
        rh       = self._tl_row_h
        ruler_h  = self._tl_ruler_h
        rows     = self._tl_visible_rows()
        actions  = self.cutscene_data.get('actions', [])

        self._draw_tl_toolbar(screen, tl, dur, actions)

        # Re-clamp here (not just on wheel events) — e.g. deleting an actor
        # while scrolled down would otherwise leave _tl_scroll_y past the
        # new, shorter content height with nothing visible.
        visible_h = tl.bottom - tracks_y
        content_h = len(rows) * rh
        self._tl_scroll_y = _clamp(self._tl_scroll_y, 0.0, max(0.0, content_h - visible_h))
        scroll_y = self._tl_scroll_y

        # ── Ruler ─────────────────────────────────────────────────────────────
        uk.draw_rect_on(screen, (12, 15, 22), pygame.Rect(tl.x, ruler_y, tl.w, ruler_h), 0, 0)
        uk.draw_rect_on(screen, _BAR_LINE, pygame.Rect(tl.x, tracks_y - 1, tl.w, 1), 0, 0)

        step, sub = self._ruler_steps()
        minor = step / sub
        first = max(0, int(scroll_x / zoom / minor) - 1)
        last = int((scroll_x + time_w) / zoom / minor) + 2
        last = min(last, int((dur + step) / minor) + 1)
        major_xs = []
        self._push_clip(screen, pygame.Rect(label_end_x, ruler_y, time_w, ruler_h))
        for i in range(first, last + 1):
            t = i * minor
            tx = int(label_end_x + t * zoom - scroll_x)
            if i % sub == 0:
                major_xs.append(tx)
                uk.draw_rect_on(screen, uk.Theme.TEXT_DIM, pygame.Rect(tx, tracks_y - 11, 1, 10), 0, 0)
                self._txt(screen, self.font_small, f'{t:g}', (tx + 5, ruler_y + 4), uk.Theme.TEXT_MUTED)
            elif minor * zoom >= 6:
                uk.draw_rect_on(screen, uk.Theme.CARD_BORDER, pygame.Rect(tx, tracks_y - 6, 1, 5), 0, 0)
        self._pop_clip(screen)

        # Grid-snap guide lines — x positions precomputed once rather than
        # per-row. Skipped when the interval would be so dense at the current
        # zoom it'd just paint a solid block (snapping itself still works).
        grid_xs = []
        if self._tl_grid_enabled:
            interval = self._tl_grid_intervals[self._tl_grid_idx]
            if interval * zoom >= 4:
                gi0 = max(0, int(scroll_x / zoom / interval) - 1)
                gi1 = min(int((scroll_x + time_w) / zoom / interval) + 2, int((dur + interval) / interval) + 1)
                for gi in range(gi0, gi1 + 1):
                    gx = int(label_end_x + gi * interval * zoom - scroll_x)
                    if label_end_x <= gx <= tl.right:
                        grid_xs.append(gx)

        # ── Track lanes ───────────────────────────────────────────────────────
        rows_rect = pygame.Rect(label_end_x, tracks_y, time_w, visible_h)
        self._push_clip(screen, rows_rect)
        row_bgs = []
        for i, row in enumerate(rows):
            row_y = tracks_y + i * rh - scroll_y
            if row_y + rh < tracks_y or row_y > tl.bottom:
                row_bgs.append(None)
                continue
            bg = (14, 17, 25) if i % 2 == 0 else (17, 20, 29)
            if row['kind'] == 'child':
                bg = (11, 14, 20)
            row_bgs.append(bg)
            uk.draw_rect_on(screen, bg, pygame.Rect(label_end_x, int(row_y), time_w, rh), 0, 0)

        # Vertical guides: major ruler ticks (faint) + snap grid (brighter)
        for gx in major_xs:
            uk.draw_rect_on(screen, (24, 28, 38), pygame.Rect(gx, tracks_y, 1, visible_h), 0, 0)
        for gx in grid_xs:
            uk.draw_rect_on(screen, (52, 60, 88), pygame.Rect(gx, tracks_y, 1, visible_h), 0, 0)

        # Past-the-end shade + end marker
        end_x = int(label_end_x + dur * zoom - scroll_x)
        if end_x < tl.right:
            sx0 = max(end_x, label_end_x)
            uk.draw_rect_on(screen, (0, 0, 0, 96), pygame.Rect(sx0, tracks_y, tl.right - sx0, visible_h), 0, 0)
            if end_x >= label_end_x:
                uk.draw_rect_on(screen, uk.lerp_color(uk.Theme.CARD_BORDER, uk.Theme.GOLD, 0.6),
                                pygame.Rect(end_x, tracks_y, 1, visible_h), 0, 0)

        bar_h = 14
        r_cap = bar_h // 2
        multi_ids = self._tl_multi_ids
        drag_ids  = ({id(a) for a, _t in self._kf_drag_group}
                     if self._kf_drag_idx >= 0 else set())
        for i, row in enumerate(rows):
            row_bg = row_bgs[i]
            if row_bg is None:
                continue
            row_y  = int(tracks_y + i * rh - scroll_y)
            color  = row['color']
            target = row['target']
            kf_cy  = row_y + rh // 2

            for ai, action in enumerate(actions):
                if action.get('target') != target:
                    continue
                if row['action_type'] is not None and action.get('type') != row['action_type']:
                    continue
                kf_t     = action['time']
                kf_x     = label_end_x + kf_t * zoom - scroll_x
                selected = (ai == self._tl_sel) or (id(action) in multi_ids)

                # Duration bar — actions with a 'duration' param span time, so
                # show the window they occupy as a capsule from the keyframe.
                try:
                    kf_dur = float(action.get('params', {}).get('duration', 0.0))
                except (TypeError, ValueError):
                    kf_dur = 0.0
                if kf_dur > 0:
                    bar_end_x = label_end_x + (kf_t + kf_dur) * zoom - scroll_x
                    x0 = max(label_end_x, kf_x)
                    x1 = min(tl.right, bar_end_x)
                    if x1 - x0 >= 1:
                        # Opaque pre-blended colours: capsule ends overlap the
                        # body, which would double-blend if we used alpha.
                        fill = uk.lerp_color(row_bg, color, 0.60 if selected else 0.30)
                        body = pygame.Rect(int(x0), kf_cy - bar_h // 2, int(x1 - x0), bar_h)
                        if body.w >= bar_h:
                            uk.draw_rect_on(screen, fill, body, 0, 0)
                            if kf_x >= label_end_x:
                                uk.draw_circle_on(screen, fill, (body.x + r_cap - 1, kf_cy), r_cap)
                            if bar_end_x <= tl.right:
                                uk.draw_circle_on(screen, fill, (body.right - r_cap, kf_cy), r_cap)
                        else:
                            uk.draw_rect_on(screen, fill, body, 0, 0)
                        if bar_end_x <= tl.right:
                            cap = uk.lerp_color(row_bg, color, 0.95 if selected else 0.6)
                            uk.draw_rect_on(screen, cap, pygame.Rect(int(x1) - 2, kf_cy - 4, 2, 8), 0, 1)

                if kf_x < label_end_x - 12 or kf_x > tl.right + 12:
                    continue
                dragging = (ai == self._kf_drag_idx) or (id(action) in drag_ids)
                if selected:
                    uk.draw_soft_glow(screen, (int(kf_x), kf_cy), 18, uk.Theme.GOLD, max_alpha=52)
                    _draw_diamond(screen, kf_x, kf_cy, 8 if dragging else 7, color, _WHITE, 2)
                else:
                    _kcol = uk.lerp_color(row_bg, color, 0.3) if action.get('_prefix') else color
                    _draw_diamond(screen, kf_x, kf_cy, 8 if dragging else 6, _kcol, (8, 10, 15), 1)

            uk.draw_rect_on(screen, (26, 30, 41), pygame.Rect(label_end_x, row_y + rh - 1, time_w, 1), 0, 0)
        self._pop_clip(screen)

        # ── Rubber-band selection box ─────────────────────────────────────────
        if self._tl_marquee is not None and self._tl_marquee['active']:
            t_min, t_max, y_min, y_max = self._marquee_bounds()
            bx0 = label_end_x + t_min * zoom - scroll_x
            bx1 = label_end_x + t_max * zoom - scroll_x
            by0 = tracks_y + y_min - scroll_y
            by1 = tracks_y + y_max - scroll_y
            box = pygame.Rect(int(bx0), int(by0), max(1, int(bx1 - bx0)), max(1, int(by1 - by0)))
            box = box.clip(rows_rect)
            if box.w > 0 and box.h > 0:
                self._push_clip(screen, rows_rect)
                uk.draw_rect_on(screen, (*uk.Theme.GOLD[:3], 40), box, 0, 0)
                edge = uk.Theme.GOLD
                uk.draw_rect_on(screen, edge, pygame.Rect(box.x, box.y, box.w, 1), 0, 0)
                uk.draw_rect_on(screen, edge, pygame.Rect(box.x, box.bottom - 1, box.w, 1), 0, 0)
                uk.draw_rect_on(screen, edge, pygame.Rect(box.x, box.y, 1, box.h), 0, 0)
                uk.draw_rect_on(screen, edge, pygame.Rect(box.right - 1, box.y, 1, box.h), 0, 0)
                self._pop_clip(screen)

        # ── Playhead (spans ruler + rows) ─────────────────────────────────────
        self._push_clip(screen, pygame.Rect(label_end_x, ruler_y, time_w + 1, tl.bottom - ruler_y))
        ph_x = int(label_end_x + self._tl_playhead_t * zoom - scroll_x)
        if label_end_x - 8 <= ph_x <= tl.right + 8:
            uk.draw_rect_on(screen, _PLAYHEAD, pygame.Rect(ph_x - 1, ruler_y + 8, 2, tl.bottom - ruler_y - 8), 0, 0)
            _blit_poly(screen, pygame.Rect(ph_x - 7, ruler_y + 2, 14, 16), _PLAYHEAD,
                       [(0, 0), (1, 0), (1, 0.55), (0.5, 1), (0, 0.55)])
        self._pop_clip(screen)

        # ── Label column ──────────────────────────────────────────────────────
        uk.draw_rect_on(screen, (*_BAR_BG,), pygame.Rect(tl.x, tracks_y, self._tl_label_w, visible_h), 0, 0)
        uk.draw_rect_on(screen, _BAR_LINE, pygame.Rect(label_end_x - 1, ruler_y, 1, tl.bottom - ruler_y), 0, 0)
        self._txt(screen, self.font_small, f'{self._tl_playhead_t:.2f}s', (tl.x + 10, ruler_y + ruler_h // 2),
                  uk.Theme.GOLD_BRIGHT, anchor='midleft', dynamic=True)

        sel_target = actions[self._tl_sel].get('target') if 0 <= self._tl_sel < len(actions) else None
        self._push_clip(screen, pygame.Rect(tl.x, tracks_y, self._tl_label_w - 1, visible_h))
        for i, row in enumerate(rows):
            row_y = int(tracks_y + i * rh - scroll_y)
            if row_y + rh < tracks_y or row_y > tl.bottom:
                continue
            color, child = row['color'], row['kind'] == 'child'
            cell = pygame.Rect(tl.x, row_y, self._tl_label_w - 1, rh)
            hov = False
            if row['kind'] == 'parent' and row['expandable']:
                hov = self._hover(cell)   # click itself is resolved by _on_tl_click
            t = self._anim(f'tll:{i}', 1.0 if hov else 0.0)
            bg = uk.lerp_color((11, 14, 20) if child else _BAR_BG, _ROW_HOVER, t)
            uk.draw_rect_on(screen, bg, cell, 0, 0)
            uk.draw_rect_on(screen, color, pygame.Rect(tl.x, row_y + 5, 3, rh - 10), 0, 1)

            text_x = tl.x + 14
            if child:
                text_x += 14
            elif row['expandable']:
                cx, cy = tl.x + 20, row_y + rh // 2
                if row['expanded']:
                    _blit_poly(screen, pygame.Rect(cx - 5, cy - 3, 10, 7), uk.Theme.TEXT_MUTED,
                               [(0, 0), (1, 0), (0.5, 1)])
                else:
                    _blit_poly(screen, pygame.Rect(cx - 3, cy - 5, 7, 10), uk.Theme.TEXT_MUTED,
                               [(0, 0), (1, 0.5), (0, 1)])
                text_x += 14
            is_sel_track = (row['kind'] == 'parent' and row['target'] == sel_target)
            text_col = (uk.Theme.TEXT_DIM if child
                        else uk.Theme.TEXT_PRIMARY if (is_sel_track or hov) else uk.Theme.TEXT_SECONDARY)
            self._txt(screen, self.font_small, row['label'], (text_x, row_y + rh // 2), text_col,
                      anchor='midleft', max_w=self._tl_label_w - (text_x - tl.x) - 8)
            uk.draw_rect_on(screen, (26, 30, 41), pygame.Rect(tl.x, row_y + rh - 1, self._tl_label_w - 1, 1), 0, 0)
        self._pop_clip(screen)

        if not actions:
            self._txt(screen, self.font_medium, 'No actions yet - press Add to create one.',
                      (label_end_x + 22, tracks_y + max(len(rows), 1) * rh // 2 - 2),
                      uk.Theme.TEXT_DIM, anchor='midleft', max_w=max(40, time_w - 44))

        self._scrollbar(screen, pygame.Rect(tl.right - 5, tracks_y + 2, 4, visible_h - 4),
                        scroll_y, content_h, visible_h)

    # ══════════════════════════════════════════════════════════════════════════
    # List view
    # ══════════════════════════════════════════════════════════════════════════

    def _draw_list(self, screen):
        w, h = self.screen_width, self.screen_height
        self._backdrop(screen, self.list_header_h, self.list_footer_h)

        # Header — same bar + centred title as the dev menu
        self._bar(screen, pygame.Rect(0, 0, w, self.list_header_h), line_at_bottom=True)
        self._txt(screen, self.font_title, 'CUTSCENE EDITOR', (w // 2, self.list_header_h // 2),
                  uk.Theme.TEXT_PRIMARY, anchor='center')
        n = len(self._files)
        self._button(screen, self._list_back_rect, icon=self._icon_back, action='close', key='list:back')

        # Footer (purely visual, like the dev menu's)
        self._bar(screen, pygame.Rect(0, h - self.list_footer_h, w, self.list_footer_h), line_at_bottom=False)

        # ── Cards ─────────────────────────────────────────────────────────────
        mx_, gap, ch = self.margin_x, 12, self.card_h
        top = self.list_header_h + 24
        add = self._list_add_rect
        creating = self._new_name_focus
        bottom = add.top - 16
        view = pygame.Rect(mx_ - 6, top - 6, w - 2 * mx_ + 12, max(60, bottom - top + 6))
        card_w = w - 2 * mx_

        total = n + (1 if creating else 0)
        content_h = total * (ch + gap)
        self._list_scroll = _clamp(self._list_scroll, 0, max(0, content_h - view.h + 6))
        scroll = int(self._list_scroll)

        self._push_clip(screen, view)
        for i, name in enumerate(self._files):
            rect = pygame.Rect(mx_, top + i * (ch + gap) - scroll, card_w, ch)
            if rect.bottom < view.top or rect.top > view.bottom:
                continue
            self._draw_list_card(screen, rect, i, name)

        if creating:
            rect = pygame.Rect(mx_, top + n * (ch + gap) - scroll, card_w, ch)
            uk.draw_panel(screen, rect, bg=(*uk.Theme.CARD_BG_HOVER[:3], 255), border=uk.Theme.GOLD,
                          border_width=2, radius=12, shadow=False)
            icon = pygame.Rect(0, 0, 26, 26)
            icon.center = (rect.x + 40, rect.centery)
            _icon_plus(screen, icon, uk.Theme.GOLD, 3)
            inner = pygame.Rect(rect.x + 74, rect.y, rect.w - 74 - 20, rect.h)
            self._hit(rect, 'field', ('new_name', None))
            self._text_rects.append(rect.clip(view))
            self._push_clip(screen, inner)
            shown = self._new_name_buf
            nfont = self.font_large
            surf = nfont.render(shown if shown else 'Cutscene name', True,
                                uk.Theme.TEXT_PRIMARY if shown else uk.Theme.TEXT_DIM)
            editing = self._tc_ident == ('new_name',)
            tx = self._tc_text_x(nfont, shown, inner) if editing else inner.x
            uk.blit_surface(screen, surf, (tx, rect.centery - surf.get_height() // 2), transient=True)
            if editing:
                self._tc_draw_single(screen, nfont, shown, tx, rect.centery, rect)
            self._pop_clip(screen)
            hint = self.font_small.render('ENTER to create  -  ESC to cancel', True, uk.Theme.TEXT_DIM)
            uk.blit_surface(screen, hint, hint.get_rect(midright=(rect.right - 20, rect.centery)).topleft)
        self._pop_clip(screen)

        if not self._files and not creating:
            self._txt(screen, self.font_large, 'No cutscenes yet', (w // 2, top + 70),
                      uk.Theme.TEXT_SECONDARY, anchor='midtop')
            self._txt(screen, self.font_small, 'Press the + button to create your first one.',
                      (w // 2, top + 70 + self._fh('l') + 10), uk.Theme.TEXT_MUTED, anchor='midtop')

        self._scrollbar(screen, pygame.Rect(w - mx_ + 18, view.y + 4, 4, view.h - 8),
                        scroll, content_h, view.h)

        # Add button (bottom-left) — hidden while the inline name row is open
        if not creating:
            self._button(screen, add, icon=self._icon_plus_png, action='list_new_start', key='list:add')
        if self._list_msg:
            self._txt(screen, self.font_medium, self._list_msg,
                      (add.right + 18, add.centery), uk.Theme.DANGER_BRIGHT, anchor='midleft',
                      max_w=w - add.right - 2 * mx_)

    def _draw_list_card(self, screen, rect, i, name):
        """One cutscene card — same shell as the room editor's list cards."""
        confirming = (self._list_confirm == name)
        hov = self._hit(rect, 'list_open', name) if not confirming else False
        focus = (self._last_input == 'keyboard' and i == self._list_sel)
        t = self._anim(f'card:{name}', 1.0 if (hov or focus) else 0.0)
        accent = _ACTOR_COLORS[i % len(_ACTOR_COLORS)]

        lift = round(2 * t)
        r = rect.move(0, -lift)
        bg = uk.lerp_color(uk.Theme.CARD_BG[:3], uk.Theme.CARD_BG_HOVER[:3], t)
        if confirming:
            border, bw = uk.Theme.DANGER_BRIGHT, 2
            bg = uk.lerp_color(bg, (60, 24, 24), 0.35)
        else:
            border, bw = uk.lerp_color(uk.Theme.CARD_BORDER, uk.Theme.GOLD, t * 0.78), 1 + round(t)
        uk.draw_panel(screen, r, bg=(*bg, 255), border=border, border_width=bw, radius=12, shadow=False)
        if t > 0.01 and not confirming:
            uk.draw_soft_glow(screen, r.center, max(r.w, r.h) // 2, uk.Theme.GOLD, max_alpha=int(14 * t))

        uk.draw_circle_on(screen, accent, (r.x + 40, r.centery), 8)
        tx = r.x + 74

        if confirming:
            self._txt(screen, self.font_large, f'Delete {name}?', (tx, r.centery - 9),
                      uk.Theme.DANGER_BRIGHT, anchor='midleft', max_w=r.w - 74 - 260)
            self._txt(screen, self.font_small, 'This removes the file and cannot be undone.',
                      (tx, r.centery + 15), uk.Theme.TEXT_MUTED, anchor='midleft')
            bh = 36
            cw = self._btn_width('Cancel', False, pad=18)
            dw = self._btn_width('Delete', True, pad=18)
            cancel = pygame.Rect(r.right - 20 - cw, r.centery - bh // 2, cw, bh)
            dele = pygame.Rect(cancel.x - 10 - dw, r.centery - bh // 2, dw, bh)
            self._button(screen, dele, 'Delete', self._icon_trash_png, 'list_delete_yes', name, danger=True,
                         primary=True, key=f'ld:{name}')
            self._button(screen, cancel, 'Cancel', None, 'list_delete_no', key='ld:no')
            return

        meta = self._file_meta.get(name)
        title_w = r.w - 74 - 90
        self._txt(screen, self.font_large, name, (tx, r.centery - (9 if meta else 0)),
                  uk.lerp_color(uk.Theme.TEXT_PRIMARY, uk.Theme.GOLD_BRIGHT, t), anchor='midleft', max_w=title_w)
        if meta:
            room, dur, n_act, n_actors = meta
            parts = [room or 'no room', f'{dur:g}s', f'{n_act} action{"s" if n_act != 1 else ""}']
            if n_actors:
                parts.append(f'{n_actors} actor{"s" if n_actors != 1 else ""}')
            self._txt(screen, self.font_small, '   -   '.join(parts), (tx, r.centery + 15),
                      uk.Theme.TEXT_MUTED, anchor='midleft', max_w=title_w)
        trash = pygame.Rect(0, 0, 42, 36)
        trash.midright = (r.right - 20, r.centery)
        self._button(screen, trash, icon=self._icon_trash_png_sm, action='list_delete_ask', arg=name,
                     danger=True, key=f'lt:{name}')

    # ══════════════════════════════════════════════════════════════════════════
    # Shared dropdown overlay
    # ══════════════════════════════════════════════════════════════════════════

    def _open_dropdown(self, anchor, items, current, on_pick, label_fn=None, accent=None,
                       empty=None, color_fn=None):
        """Open the one shared picker under *anchor* (a field rect).  on_pick is
        called with the chosen value; clicking anywhere else just dismisses."""
        items = list(items)
        vis = max(1, min(8, len(items)))
        scroll = 0
        if current in items:
            scroll = int(_clamp(items.index(current) - vis // 2, 0, max(0, len(items) - vis)))
        self._dd = {
            'anchor': pygame.Rect(anchor), 'items': items, 'current': current,
            'on_pick': on_pick, 'label_fn': label_fn or (lambda v: v if v != '' else '(none)'),
            'accent': accent or uk.Theme.GOLD, 'empty': empty, 'color_fn': color_fn,
            'scroll': scroll, 'visible': vis, 'rect': None, 'rows': [],
        }

    def _click_dropdown(self, pos):
        dd, self._dd = self._dd, None
        for rect, value in dd['rows']:
            if rect.collidepoint(pos):
                dd['on_pick'](value)
                return

    def _draw_dropdown(self, screen):
        dd = self._dd
        if dd is None:
            return
        w, h = self.screen_width, self.screen_height
        item_h = 30
        items = dd['items']
        n_rows = max(1, min(dd['visible'], len(items)))
        anchor = dd['anchor']
        width = int(_clamp(max(anchor.w, 200), 120, w - 16))
        height = n_rows * item_h + 8
        x = int(_clamp(anchor.x, 8, w - width - 8))
        limit = h - 8
        y = anchor.bottom + 6
        if y + height > limit:
            y = anchor.top - 6 - height
            if y < 8:
                y = max(8, limit - height)
        rect = pygame.Rect(x, y, width, height)
        dd['rect'] = rect

        uk.draw_panel(screen, rect, bg=uk.Theme.PANEL_BG, border=dd['accent'],
                      border_width=1, radius=8, shadow=True)
        rows = []
        if not items:
            self._txt(screen, self.font_small, dd['empty'] or 'Nothing to choose from',
                      (rect.centerx, rect.centery), uk.Theme.TEXT_MUTED, anchor='center',
                      max_w=width - 16)
        else:
            dd['scroll'] = int(_clamp(dd['scroll'], 0, max(0, len(items) - n_rows)))
            mouse = self._mouse_pos
            self._push_clip(screen, rect.inflate(-2, -2))
            for i in range(n_rows):
                value = items[dd['scroll'] + i]
                row = pygame.Rect(rect.x + 4, rect.y + 4 + i * item_h, width - 8, item_h)
                hov = row.collidepoint(mouse)
                cur = (value == dd['current'])
                if cur:
                    uk.draw_rect_on(screen, uk.Theme.CARD_BG_SELECTED, row, 0, 6)
                elif hov:
                    uk.draw_rect_on(screen, uk.Theme.CARD_BG_HOVER, row, 0, 6)
                tx = row.x + 10
                sw = dd['color_fn'](value) if dd['color_fn'] else None
                if isinstance(sw, tuple) and len(sw) >= 3:
                    sr = pygame.Rect(tx, row.centery - 6, 16, 12)
                    uk.draw_rect_on(screen, sw[:3], sr, 0, 3)
                    uk.draw_rect_on(screen, uk.Theme.CARD_BORDER, sr, 1, 3)
                    tx += 24
                col = (uk.Theme.GOLD_BRIGHT if cur else
                       uk.Theme.TEXT_PRIMARY if hov else uk.Theme.TEXT_SECONDARY)
                self._txt(screen, self.font_small, dd['label_fn'](value), (tx, row.centery), col,
                          anchor='midleft', max_w=row.right - tx - (26 if cur else 10))
                if cur:
                    _icon_check(screen, pygame.Rect(row.right - 26, row.centery - 8, 18, 16), uk.Theme.GOLD)
                rows.append((row, value))
            self._pop_clip(screen)
            self._scrollbar(screen, pygame.Rect(rect.right - 7, rect.y + 6, 4, rect.h - 12),
                            dd['scroll'], len(items), n_rows)
        dd['rows'] = rows