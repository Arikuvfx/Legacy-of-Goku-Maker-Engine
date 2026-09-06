"""
ui/scouter_menu.py

Standalone overlay opened with ENTER (see Game._handle_game_keydown's
K_RETURN branch and Game._open_scouter_menu). Kept separate from
PauseMenu on purpose — different key, different freeze behaviour, and a
totally different set of sections — but mirrors PauseMenu's public shape
(active / open() / close() / handle_input(event) / update(dt) / draw(...))
so it slots into Game's existing overlay-priority event loop and draw
order the same way every other menu does.

Sections, and how you move between them:

    MAP -----Q----> SCOUTER  (freezes the frame, lets you crosshair-browse
                               every entity currently on screen)
    MAP -----E----> WORLD_MAP (shows the world map + this room's pin)
    SCOUTER --E---> MAP   (back)
    WORLD_MAP --Q-> MAP   (back)
    ESC or ENTER, from any section -> closes the whole menu.

    The back key in SCOUTER/WORLD_MAP is deliberately the *other* key from
    the one that got you there — Q takes you left into Scouter, so once
    you're in there Q has nothing left to do and E is what returns you to
    MAP; E takes you right into World Map, so Q is what returns you from
    there. The L/R button prompts (see _l_button_available() /
    _r_button_available()) follow the same rule: whichever key would do
    nothing in the current section has its button hidden.

The MAP section auto-renders the current zone's silhouette from each room's
map_paint cells (painted once in the room editor's Map Paint tool) plus
room_transitions (already authored for gameplay) — see
ui/scouter_room_map.py. No hand-authored map art per room.

The SCOUTER section freezes gameplay (Game does this by early-returning
out of Game.update() while self.scouter_menu.active — see game.py) and
snapshots every on-screen entity's screen position the instant you press
Q, so browsing with the crosshair is against a still frame rather than
fighting live movement. Draw order back-to-front is grid.png, then the
frozen entity snapshots (each entity's real sprite — see
_capture_entity_sprite), then the crosshair, then map_overlay.png on top
of all of it — see draw() — so the overlay's HUD bezel caps the scan
instead of the scan floating in front of it. The crosshair itself moves
freely over that still frame — held arrow keys/WASD (see
_update_crosshair_position(), driven every frame from update(dt), not
per-keypress) or the mouse (see handle_input()'s pygame.MOUSEMOTION
branch) — rather than snapping between entities. Whatever entity it ends
up centred over (within _CROSSHAIR_TARGET_RADIUS px — see
_find_target_entity()) is shown with a highlight ring and is what SPACE
(or a left mouse click — see handle_input()'s pygame.MOUSEBUTTONDOWN
branch, wired to the same _start_crosshair_charge()) will lock onto.
Holding SPACE (or holding the left mouse button) plays the crosshair
sprite (crosshair.png) through a charge animation and parks it at its
"charged" frame for as long as it stays down; releasing plays it back
down to rest, and only then does handle_input()'s caller learn about it
— via update()'s return value, as ('inspect', entity_obj, entity_kind),
not handle_input() itself (see _update_crosshair_anim()). Game doesn't
do anything with that yet (the actual per-entity info panel is a later
task), but the hook is here so wiring it up won't require touching this
file again. E is reserved for backing out to MAP (see the module
docstring above), so Inspect had to move off of it.
"""

import math
import os

import pygame

from config.settings import RENDER_SCALE
from core.sprite_system import (create_character_sprite, create_enemy_sprite,
                                 create_npc_sprite, create_boss_sprite)


class _BlitScaledShim:
    """Wraps a plain pygame.Surface so it also answers to GPUScreen's
    blit_scaled(surface, dst_rect, area=None) — see gpu_renderer.py.

    AnimatedSprite.draw() (core/sprite_system.py) was migrated to call
    screen.blit_scaled(...) directly, on the assumption that every
    "screen" it's ever handed is now a GPUScreen (true for the main game
    surface, which game.py wraps once at startup). This module still
    draws onto plain throwaway pygame.Surface scratch canvases for
    off-screen snapshotting (see _capture_entity_sprite,
    _capture_data_viewer_frame), so a raw Surface has to be dressed up
    with the same interface here rather than assuming AnimatedSprite
    will ever fall back to plain .blit() again.

    This only wraps the *target* passed into draw() calls — the
    underlying pygame.Surface is unchanged and still what every other
    line in this file (subsurface, pygame.mask.from_surface, etc.)
    keeps operating on directly.
    """

    def __init__(self, surface):
        self._surface = surface

    def blit(self, source, dest, area=None, special_flags=0):
        return self._surface.blit(source, dest, area, special_flags)

    def blit_scaled(self, surface, dst_rect, area=None):
        if area is not None and not isinstance(area, pygame.Rect):
            area = pygame.Rect(area)
        src = surface.subsurface(area) if area is not None else surface
        if isinstance(dst_rect, pygame.Rect):
            pos, size = dst_rect.topleft, (dst_rect.width, dst_rect.height)
        else:
            pos, size = dst_rect, src.get_size()
        size = (max(1, size[0]), max(1, size[1]))
        if size != src.get_size():
            # Nearest-neighbor, not smoothscale — GPUScreen.blit_scaled
            # goes through SDL's texture draw, which (with no
            # SDL_HINT_RENDER_SCALE_QUALITY override in gpu_renderer.py)
            # defaults to nearest-neighbor sampling. smoothscale's
            # bilinear filtering blurs pixel art that's meant to stay
            # crisp when scaled, so this has to match, not just "scale".
            src = pygame.transform.scale(src, size)
        self._surface.blit(src, pos)

    def __getattr__(self, name):
        # Anything else (get_size, fill, etc.) falls straight through to
        # the real Surface.
        return getattr(self._surface, name)


class _LetterSpriteFont:
    """Loads per-letter (a-z) PNG glyphs from a folder, keyed by
    lowercase filename stem — e.g. assets/ui/fonts/scouter_stats/h.png,
    p.png, s.png, t.png, r.png, o.png, w.png, e.png, n.png, d.png. Used
    to assemble the Scouter Data stat labels (HP/STR/POW/END) letter by
    letter rather than as a single pre-rendered word image per stat.
    Sprites are assumed to be plain white (or greyscale) art so a
    caller can recolor the assembled word with .fill(color,
    special_flags=pygame.BLEND_RGBA_MULT) — the same tint trick
    dialogue.py's _BitmapFont uses — without needing separate
    pre-colored glyphs per stat."""

    def __init__(self, folder):
        self.folder = folder
        self.glyphs = {}
        self._load()

    def _load(self):
        if not os.path.exists(self.folder):
            return
        # Assets are authored as A.png..Z.png (uppercase stems). Try
        # uppercase first, then lowercase, so this works on both
        # case-sensitive (Linux) and case-insensitive (Windows) filesystems.
        # Glyphs are always keyed by lowercase so render()'s ch.lower()
        # lookup stays case-insensitive against the caller's text.
        for letter in 'abcdefghijklmnopqrstuvwxyz':
            path = None
            for stem in (letter.upper(), letter):
                candidate = os.path.join(self.folder, f'{stem}.png')
                if os.path.exists(candidate):
                    path = candidate
                    break
            if path is None:
                continue
            try:
                self.glyphs[letter] = pygame.image.load(path).convert_alpha()
            except Exception as e:
                print(f'[scouter_menu] could not load {path}: {e}')

    def height(self):
        if not self.glyphs:
            return 0
        return max(g.get_height() for g in self.glyphs.values())

    def render(self, word, gap=1):
        """Composites `word`'s letters left-to-right onto a single new
        Surface, bottom-aligned so glyphs of differing heights still
        sit flush along a shared baseline. Returns None if any letter
        of `word` has no glyph on disk — the caller falls back to a
        plain-text render for the whole label rather than a row with
        holes punched in it."""
        letters = [self.glyphs.get(ch.lower()) for ch in word]
        if not letters or any(g is None for g in letters):
            return None
        gap_total = gap * (len(letters) - 1)
        total_w = sum(g.get_width() for g in letters) + gap_total
        h = self.height()
        surf = pygame.Surface((total_w, h), pygame.SRCALPHA)
        x = 0
        for g in letters:
            surf.blit(g, (x, h - g.get_height()))
            x += g.get_width() + gap
        return surf


class _MixedCaseSpriteFont:
    """Loads two separate per-letter PNG glyph sets — an uppercase set
    (assets/ui/fonts/uppercase/A.png..Z.png) and a lowercase set
    (assets/ui/fonts/lowercase/a.png..z.png) — keyed by the literal
    character rather than case-folded to one shape like
    _LetterSpriteFont does. That case-folding is fine for the all-caps
    HP/STR/POW/END stat labels, but the Scouter Data description is a
    real mixed-case sentence, so it needs the actual uppercase glyph
    art for capitals and the actual lowercase glyph art for the rest
    rather than picking one case's shape for both. Sprites are assumed
    plain white/greyscale, same BLEND_RGBA_MULT tint convention as the
    other sprite fonts here."""

    def __init__(self, upper_folder, lower_folder):
        self.upper_glyphs = {}
        self.lower_glyphs = {}
        self.punct_glyphs = {}
        self._load(upper_folder, self.upper_glyphs, str.upper)
        self._load(lower_folder, self.lower_glyphs, str.lower)
        # Punctuation has no case, so it isn't covered by the a-z loop
        # above — loaded separately, keyed by the literal character.
        # Tries both folders (without overwriting whichever is found
        # first) since punctuation could reasonably have been dropped
        # in either one.
        self._load_punctuation(lower_folder)
        self._load_punctuation(upper_folder)

    def _load(self, folder, dest, case_fn):
        if not os.path.exists(folder):
            return
        for letter in 'abcdefghijklmnopqrstuvwxyz':
            target = case_fn(letter)
            # Try the expected case first, then the other case, so this
            # still works if a folder was authored with the "wrong"
            # filename case for its own purpose (mirrors the tolerant
            # lookup _LetterSpriteFont._load uses).
            path = None
            for stem in (target, letter, letter.upper()):
                candidate = os.path.join(folder, f'{stem}.png')
                if os.path.exists(candidate):
                    path = candidate
                    break
            if path is None:
                continue
            try:
                dest[target] = pygame.image.load(path).convert_alpha()
            except Exception as e:
                print(f'[scouter_menu] could not load {path}: {e}')

    # Maps each supported punctuation character to a descriptive
    # fallback filename stem, in case the art wasn't authored under the
    # bare character itself (a filename that's just '.png' or '-.png'
    # is awkward on some filesystems/tools) — see _load_punctuation().
    _PUNCTUATION_FILENAMES = {
        '-': 'hyphen',
        ',': 'comma',
        '.': 'period',
    }

    def _load_punctuation(self, folder):
        """Loads the hyphen/comma/period glyphs (see
        _PUNCTUATION_FILENAMES) out of `folder`, keyed by the literal
        character rather than case-folded like the a-z glyphs. Tries
        the literal character as the filename stem first (e.g.
        '-.png'), then the descriptive fallback name (e.g.
        'hyphen.png', 'Hyphen.png') — same tolerant multi-stem lookup
        style as _load(). Never overwrites a glyph already found (this
        gets called once per folder), so it doesn't matter which of
        upper_folder/lower_folder the art actually landed in."""
        if not os.path.exists(folder):
            return
        for ch, name in self._PUNCTUATION_FILENAMES.items():
            if ch in self.punct_glyphs:
                continue
            path = None
            for stem in (ch, name, name.capitalize()):
                candidate = os.path.join(folder, f'{stem}.png')
                if os.path.exists(candidate):
                    path = candidate
                    break
            if path is None:
                continue
            try:
                self.punct_glyphs[ch] = pygame.image.load(path).convert_alpha()
            except Exception as e:
                print(f'[scouter_menu] could not load {path}: {e}')

    def glyph(self, ch):
        """The raw (untinted) glyph Surface for `ch`, or None if `ch`
        isn't a character this font has art for (digits, unsupported
        punctuation, space, or a missing file)."""
        if ch.isupper():
            return self.upper_glyphs.get(ch)
        if ch.islower():
            return self.lower_glyphs.get(ch)
        return self.punct_glyphs.get(ch)

    def height(self):
        glyphs = (list(self.upper_glyphs.values()) + list(self.lower_glyphs.values())
                  + list(self.punct_glyphs.values()))
        return max((g.get_height() for g in glyphs), default=0)

    # Characters with a descender — their glyph art is drawn extending
    # below the shared baseline. g/j/p/q/y all dip below the x-height
    # row other lowercase letters sit on; the comma is given the SAME
    # offset as 'y' since its art hangs its tail below the baseline the
    # same way. Bottom-aligning every glyph flush to the same row (what
    # a plain shared-baseline composite does — see
    # _LetterSpriteFont.render, which is correct for the all-caps
    # scouter_stats set with no descenders at all) would either clip
    # these characters' tails or, if their art is trimmed tight with no
    # headroom, drag the WHOLE glyph down and throw off alignment with
    # the characters around it. descender_offset() below is the extra
    # downward nudge these need on top of normal baseline placement so
    # the tail hangs past the baseline instead of the glyph itself
    # sitting too high or too low.
    _DESCENDER_LETTERS = set('gjpqy,')
    _DESCENDER_OFFSET = 3   # native (pre-scale) px

    def descender_offset(self, ch):
        return self._DESCENDER_OFFSET if ch in self._DESCENDER_LETTERS else 0

    def space_width(self):
        """No dedicated space glyph on disk, so approximate one from the
        average lowercase glyph width — good enough for word-wrap math
        and inter-word gaps without needing a blank asset."""
        glyphs = list(self.lower_glyphs.values()) or list(self.upper_glyphs.values())
        if not glyphs:
            return 0
        avg = sum(g.get_width() for g in glyphs) / len(glyphs)
        return max(1, round(avg * 0.6))


class _DigitSpriteFont:
    """Loads per-digit (0-9) PNG glyphs from assets/ui/fonts/dmg_font.
    Those sprites are authored for damage-number popups over gameplay
    and carry a black outline stroke around each digit — dropped
    straight over the Scouter Data panel that outline just reads as a
    solid black block, so every near-black pixel is stripped to fully
    transparent once at load time (see _strip_outline) rather than
    drawn as-is. Stripping the outline empties out its 1px column on
    each side of the glyph but doesn't shrink the surface itself — the
    source art is 6px wide (1px outline + 4px digit + 1px outline), so
    without trimming that now-transparent margin every glyph would
    still report its old 6px width to the slot-alignment math in
    _draw_data_stat_number(). _trim_horizontal_margin() crops that
    margin away right after stripping so glyphs report their real
    (~4px) content width."""

    # Highest weighted r/g/b distance from pure black still treated as
    # "the outline" and stripped (0..255 scale, see _strip_outline).
    # Real black is 0/0/0; a small margin above that catches anti-
    # aliased near-black edge pixels too without eating into whatever
    # (much brighter) color makes up the digit's actual fill.
    _OUTLINE_THRESHOLD = 40

    def __init__(self, folder):
        self.folder = folder
        self.glyphs = {}
        self._load()

    def _load(self):
        if not os.path.exists(self.folder):
            return
        for d in '0123456789':
            path = os.path.join(self.folder, f'{d}.png')
            if not os.path.exists(path):
                continue
            try:
                img = pygame.image.load(path).convert_alpha()
                self.glyphs[d] = self._trim_horizontal_margin(self._strip_outline(img))
            except Exception as e:
                print(f'[scouter_menu] could not load {path}: {e}')

    @classmethod
    def _strip_outline(cls, surf):
        """Return a copy of surf with every near-black pixel forced to
        alpha 0, removing the sprite's black outline stroke. Uses
        PixelArray.replace's weighted color-distance match (a fast bulk
        op) rather than a per-pixel get_at/set_at loop."""
        out = surf.copy()
        px = pygame.PixelArray(out)
        px.replace((0, 0, 0), (0, 0, 0, 0), distance=cls._OUTLINE_THRESHOLD / 255.0)
        px.close()
        return out

    @staticmethod
    def _trim_horizontal_margin(surf):
        """Crop away the transparent left/right margin _strip_outline
        leaves behind once the outline columns are gone, so a glyph
        that was authored 6px wide (1px outline + 4px digit + 1px
        outline) reports the real ~4px content width instead of the
        original 6px. Uses get_bounding_rect (alpha-aware) rather than
        a hardcoded 1px-each-side trim so it isn't thrown off if a
        particular digit's outline ends up asymmetric after
        thresholding. Only the x-extent is trimmed — height is left
        exactly as-is, so vertical alignment between glyphs is
        unaffected."""
        bounds = surf.get_bounding_rect(min_alpha=1)
        if bounds.width <= 0:
            return surf
        trimmed = pygame.Surface((bounds.width, surf.get_height()), pygame.SRCALPHA)
        trimmed.blit(surf, (-bounds.x, 0))
        return trimmed

    def max_width(self):
        if not self.glyphs:
            return 0
        return max(g.get_width() for g in self.glyphs.values())

    def height(self):
        if not self.glyphs:
            return 0
        return max(g.get_height() for g in self.glyphs.values())


class _NameDigitSpriteFont:
    """Loads per-digit (0-9) PNG glyphs from assets/ui/fonts/numbers,
    keyed by the literal digit character. Used only for digits that
    show up inside an entity's Scouter Data display name (e.g. "Android
    19", "Cell Jr. 3") so they render in the same pixel-art family as
    the rest of the name instead of falling through to the plain
    pygame fallback font — see _render_data_name_word(). Unlike
    _DigitSpriteFont (assets/ui/fonts/dmg_font), this art isn't
    authored with an outline stroke to strip, so glyphs are loaded
    as-is, the same plain convention _LetterSpriteFont uses."""

    def __init__(self, folder):
        self.folder = folder
        self.glyphs = {}
        self._load()

    def _load(self):
        if not os.path.exists(self.folder):
            return
        for d in '0123456789':
            path = os.path.join(self.folder, f'{d}.png')
            if not os.path.exists(path):
                continue
            try:
                self.glyphs[d] = pygame.image.load(path).convert_alpha()
            except Exception as e:
                print(f'[scouter_menu] could not load {path}: {e}')

    def height(self):
        if not self.glyphs:
            return 0
        return max(g.get_height() for g in self.glyphs.values())


class ScouterMenu:
    SECTION_MAP = 'map'
    SECTION_SCOUTER = 'scouter'
    SECTION_WORLD_MAP = 'world_map'
    # Entered from SCOUTER once the SPACE/click charge-hold-release
    # animation finishes over a locked-in target (see _update_crosshair_anim
    # / _consume_crosshair_target) — a full-screen readout for whichever
    # entity was inspected, rather than something reached via Q/E like the
    # other three sections. See _enter_data_section().
    SECTION_DATA = 'data'

    # How far off-screen (in px) an entity can still be and count as
    # "in frame" for the Scouter snapshot — a small margin so entities
    # whose sprite is partially visible at the screen edge still show up.
    _FRAME_MARGIN = 32

    # crosshair.png is a horizontal strip of 32x32 frames: 1 = idle/rest,
    # 2/3 = the space-bar charge poses. See _update_crosshair_anim().
    _CROSSHAIR_FRAME_PX = 32
    _CROSSHAIR_STEP_SECONDS = 0.06   # time each charge/release frame is held

    # player.png (Map section "you are here" marker) is a horizontal strip
    # of 8x8 frames, cycled continuously as a little idle animation rather
    # than tied to any input state — see _get_player_marker_frame_number()
    # / _draw_map_player_marker(). Drawn at _get_design_v_scale() like the
    # crosshair/scroll-arrow HUD icons (not at the zone map's own per-cell
    # scale — see _draw_map_player_marker docstring for why), so it reads
    # as a consistent-size pin regardless of how zoomed in/out the current
    # zone's map happens to be.
    _PLAYER_MARKER_FRAME_PX = 8
    _PLAYER_MARKER_FRAME_COUNT = 4
    _PLAYER_MARKER_FRAME_SECONDS = 0.2   # time each idle frame is held

    # Free-roam crosshair movement (keyboard) — px/second, held-key based
    # rather than the old one-entity-per-keypress jump.
    _CROSSHAIR_MOVE_SPEED = 480

    # Map section camera — the zone silhouette is still drawn across the
    # same on-screen area it always has (see _draw_map_section), just at a
    # fixed zoomed-in pixels-per-cell scale instead of shrunk to fit the
    # whole zone at once, panned with WASD/arrows. See _update_map_camera.
    _MAP_ZOOM_SCALE = 6               # fixed px-per-cell, not fit-to-screen
    _MAP_PAN_SPEED = 100.0              # grid cells/second
    _MAP_CAMERA_MARGIN_CELLS = 4      # how far past the zone edge you can pan

    # Map section directional pan-availability prompts (room_arrow.png),
    # one flush against each edge of the screen, drawn on top of
    # map_overlay.png's bezel — see _draw_room_arrows(). The sheet is a
    # horizontal strip of frames, each _ROOM_ARROW_FRAME_PX wide (frame
    # count is auto-detected from the sheet's own width at load time —
    # see _load_room_arrow() — rather than hardcoded here). Frame 1 is
    # the static/nothing-more-that-way pose; the strip cycles at
    # _ROOM_ARROW_FRAME_SECONDS per frame ONLY while that edge's
    # direction still has more zone map hidden past the fixed-size map
    # viewport — i.e. while the camera can still be panned further that
    # way (see _update_map_camera / _map_camera_bounds) — same "only
    # animate what's actionable" convention as the Scouter Data
    # description's scroll arrows (arrow.png).
    _ROOM_ARROW_FRAME_PX = 16          # frame width
    _ROOM_ARROW_FRAME_HEIGHT_PX = 8    # frame height — NOT assumed to equal the sheet's full raw height (see _get_room_arrow_frame)
    _ROOM_ARROW_FRAME_SECONDS = 0.08

    # How close the crosshair's centre has to be to an entity's screen
    # position for that entity to count as "targeted" — used both for the
    # highlight ring drawn every frame and for what SPACE actually locks
    # onto when the charge starts. Roughly matches the crosshair sprite's
    # own on-screen radius (_CROSSHAIR_FRAME_PX / 2) plus a little slack
    # so it doesn't feel pixel-perfect finicky.
    _CROSSHAIR_TARGET_RADIUS = 24

    # Target-glow flicker pulse (see _draw_scouter_entities /
    # _get_glow_tinted_sprite) — fast flash between no tint and half
    # strength, cycling every _GLOW_PULSE_PERIOD_SECONDS.
    _GLOW_PULSE_MIN = 0.0          # tint strength at the dimmest point
    _GLOW_PULSE_MAX = 0.5          # tint strength at the brightest point
    _GLOW_PULSE_PERIOD_SECONDS = 0.5   # one full dim -> bright -> dim cycle
    # Colour added at full (1.0) pulse strength (the pulse itself only
    # ever reaches 0.5 of this — see _GLOW_PULSE_MAX — so the actual peak
    # colour added is half of this).
    _GLOW_TINT_COLOR = (80, 220, 120)

    # ---- Scouter Data section (see SECTION_DATA) ----
    # How long the fade-in from black takes once the section is entered
    # (see _enter_data_section() / update()) — a quick fade rather than a
    # hard cut, matching the "menu fades" behaviour asked for.
    _DATA_FADE_SECONDS = 0.35

    # Facing order the Data section's idle-viewer sprite cycles through
    # (see _update_data_viewer()). down -> right -> up -> left -> down is
    # counter-clockwise as seen on screen: think of it as a clock face
    # (12=up, 3=right, 6=down, 9=left) run backwards — 6 -> 3 -> 12 -> 9
    # -> 6 — rather than as raw dx/dy, since screen y grows downward and
    # would flip the sense of "clockwise" if reasoned about numerically.
    _DATA_FACING_ORDER = ('down', 'right', 'up', 'left')
    _DATA_FACING_INTERVAL = 2.0   # seconds between facing swaps

    # Square scratch canvas (screen px) the viewer sprite is rendered
    # into before being cropped to its visible pixels — see
    # _capture_data_viewer_frame(). Must comfortably exceed any single
    # character frame's on-screen footprint (32px sprite * RENDER_SCALE),
    # same margin-of-safety reasoning as _SPRITE_CAPTURE_PAD above.
    # Calibrated for RENDER_SCALE == _CAPTURE_REFERENCE_RENDER_SCALE and
    # scaled proportionally at capture time so it doesn't clip the sprite
    # when the real RENDER_SCALE is higher than that.
    _DATA_VIEWER_SCRATCH_SIZE = 160

    # Straight scale multiplier for the rotating character, on top of
    # however big AnimatedSprite.draw() already renders him at
    # (32px sprite * RENDER_SCALE) — THIS is the knob to turn to resize
    # him (see _draw_data_viewer()). 1.0 = native size, 2.0 = double,
    # 1.5 = one and a half, etc. Not clamped to the grid box's own
    # width/height, so a large enough value will deliberately overflow
    # past the box edges — only the CENTER point is drawn from the box.
    # Independent of the grid box's own size (_get_data_grid_box()) and
    # of the separate static-portrait sizing (_DATA_PORTRAIT_* /
    # _get_data_portrait_box()).
    _DATA_VIEWER_SCALE = 1.5

    # The Data section's viewer character is meant to always look like it
    # was rendered at RENDER_SCALE == 4, no matter what config.settings.
    # RENDER_SCALE actually is right now — see _draw_data_viewer(), which
    # multiplies _DATA_VIEWER_SCALE by (this / real RENDER_SCALE) to cancel
    # out AnimatedSprite.draw()'s real-RENDER_SCALE sizing before reapplying
    # this fixed one. (_capture_data_viewer_frame()'s centering math still
    # has to divide by the REAL RENDER_SCALE, since that's inverting the
    # actual multiply AnimatedSprite.draw() performs — only the apparent
    # on-screen SIZE is pinned here, not that centering step.)
    _DATA_VIEWER_FIXED_RENDER_SCALE = 4

    # Simple procedural shadow ellipse drawn under the viewer sprite's
    # feet — see _draw_data_viewer_shadow() for why this can't just
    # reuse LayerManager._draw_shadow() the way Scouter's frozen-frame
    # entity snapshots do.
    _DATA_VIEWER_SHADOW_COLOR = (0, 0, 0, 110)
    _DATA_VIEWER_SHADOW_WIDTH_RATIO = 0.7   # shadow width, as a fraction of the on-screen sprite width
    _DATA_VIEWER_SHADOW_HEIGHT_PX = 6       # flat ellipse height — shouldn't grow taller as the character scales up, just wider

    # Stat readout layout — four rows stacked top to bottom (HP / STR /
    # POW / END), each a sprite word (assets/ui/fonts/scouter_stats) on
    # the left and its number (assets/ui/fonts/dmg_font) right-aligned
    # on the right. See _draw_data_stats() / _draw_data_stat_number().
    # LEFT/TOP are in scouter_background.png RAW pixels (same space as
    # _DATA_PORTRAIT_RAW_RECT) so they track the integer-scaled panel
    # instead of assuming a full-screen stretch at a fixed resolution.
    # Tuned to sit in the right-hand column near/below the portrait frame.
    _DATA_STAT_RAW_LEFT = 112       # x of the label column's left edge (raw px)
    _DATA_STAT_RAW_TOP = 15         # y of the first (HP) row (raw px)
    _DATA_STAT_ROW_GAP = 12          # vertical gap between rows (screen px)
    _DATA_STAT_LABEL_NUMBER_GAP = 100 # gap between label and number columns (screen px)
    # Fixed per-digit slot pitch the number column is built from, right
    # to left — every row uses the same slot pitch, so whichever digit
    # lands in the rightmost slot (a stat's 1s place) sits at the exact
    # same x on every row, and the same goes for the 10s/100s slots
    # going left from there, regardless of how many digits any one
    # row's number has. Shares _DATA_STAT_GLYPH_GAP with the label's
    # letter-to-letter spacing (see _LetterSpriteFont.render's `gap`
    # param) so the number column reads with the same rhythm as the
    # word beside it rather than a different, uncoordinated gap — see
    # _scaled_digit_gap().
    _DATA_STAT_GLYPH_GAP = 1   # native (pre-scale) px between glyphs

    # Size knobs — scale factors applied on top of the sprites' native
    # pixel size (1.0 = draw at native resolution). Kept separate so the
    # word labels and the digits can be resized independently if they
    # ever need to read at different weights; set both the same to scale
    # the whole stat readout uniformly. Uses pygame.transform.scale
    # (nearest-neighbour, not smoothscale) so pixel art stays crisp
    # rather than blurring at non-1.0 factors. See
    # _get_data_stat_label_sprite() / _scaled_digit_glyph().
    _DATA_STAT_LABEL_SCALE = 6.0
    _DATA_STAT_DIGIT_SCALE = 6.0

    # Per-stat label tint — sprites in scouter_stats are plain white/
    # greyscale art, recolored at draw time via BLEND_RGBA_MULT (see
    # _LetterSpriteFont / _draw_data_stats).
    _DATA_STAT_COLORS = {
        'HP': (255, 0, 0),       # red   (FF0000)
        'STR': (0, 255, 255),    # blue  (00FFFF)
        'POW': (0, 255, 0),      # green (00FF00)
        'END': (255, 255, 148),  # beige (FFFF94)
    }

    # Portrait's target rectangle as authored in scouter_background.png's
    # RAW pixel space (x0, y0, x1, y1) — the actual gold-framed slot drawn
    # into the background art. Mapped to on-screen coordinates by
    # _bg_raw_rect_to_screen(), which replicates the exact same scale +
    # 9-slice gap-shift as _get_scaled_scouter_background() so this always
    # lines up with the frame regardless of screen size.
    # x0 nudged 168 -> 169: the box/scale math is confirmed symmetric
    # left-to-right (see _bg_raw_rect_to_screen — both corners of this
    # rect go through the identical branch/scale), so a left-only gap
    # means this eyeballed pixel coordinate itself was 1px into the
    # border. Re-nudge by eye again if this overshoots/undershoots.
    _DATA_PORTRAIT_RAW_RECT = (169, 8, 231, 71)

    # Description box (bottom-right) — exact raw-art rect, same corner-
    # pixel convention as _DATA_PORTRAIT_RAW_RECT/_DATA_NAME_RAW_RECT
    # (mapped via _bg_raw_rect_to_screen(), x1+1/y1+1 passed at the call
    # site so the last row/column isn't clipped). Previously this box was
    # derived from the portrait box + _DATA_SIDE_MARGIN, which drifted
    # from where the panel art actually frames the description text.
    _DATA_DESCRIPTION_RAW_RECT = (112, 80, 231, 151)

    # Two solid bars baked into the original game's Scouter Data panel,
    # overlapping the top and bottom of the description text — not part
    # of scouter_background.png here, so drawn explicitly. Same raw-pixel
    # space/convention as the rect above.
    _DATA_DESCRIPTION_BAR_COLOR = (0x18, 0x39, 0x00)
    _DATA_DESCRIPTION_TOP_BAR_RAW_RECT = (112, 80, 231, 87)
    _DATA_DESCRIPTION_BOTTOM_BAR_RAW_RECT = (112, 144, 231, 151)

    # Scroll-availability arrow, baked into the right edge of each bar —
    # assets/ui/scouter/arrow.png (same folder as crosshair.png/grid.png/
    # map_overlay.png), a 2-frame horizontal strip. Same raw-pixel
    # space/convention as the bar rects above: these ARE the on-screen
    # destination rects (mapped via _bg_raw_rect_to_screen), not a crop
    # out of the sheet — see _get_arrow_frame() for the source slicing.
    # The bottom placement is the SAME sprite, just vertically
    # flipped (see _draw_data_description_scroll_arrows()).
    _DATA_DESC_SCROLL_ARROW_TOP_RAW_RECT = (220, 81, 227, 85)
    _DATA_DESC_SCROLL_ARROW_BOTTOM_RAW_RECT = (220, 146, 227, 150)
    # Frame 1 = static "nothing more to scroll this way" pose. Frame 2
    # only appears while blinking (see _update_data_description_scroll).
    _DATA_DESC_ARROW_BLINK_INTERVAL = 0.5

    # Extra left padding (on top of the base .inflate() margin below) for
    # the description text specifically — nudges it right, off the bars'
    # left edge, without affecting the box/bars themselves.
    _DATA_DESCRIPTION_TEXT_LEFT_PAD_PX = 34

    # Extra right-side margin subtracted ONLY from the width used for
    # word-wrap decisions (see _measure_data_description_layout) — NOT
    # from the box/clip_rect itself, so the box/bars stay exactly where
    # they were authored and only the wrap gets a bit more conservative.
    # The original game's lines break noticeably earlier than a bare
    # edge-to-edge fit would (see the reference vs. current-game
    # comparison this was tuned against), so this reserves a strip of
    # unused space on the right rather than letting text pack all the
    # way to the box edge. Tune this up/down to nudge every line's
    # break point without touching box geometry at all.
    _DATA_DESCRIPTION_TEXT_RIGHT_PAD_PX = 50

    # Kept only as the gap below the portrait for other layout that may
    # still reference it; no longer used to derive the description box
    # itself (see _DATA_DESCRIPTION_RAW_RECT above).
    _DATA_SIDE_MARGIN = 40

    # Name — centred in this raw-art rect (x0, y0, x1, y1 inclusive).
    # Rendered with the same scouter_stats letter sprites as the HP/STR/
    # POW/END labels (see _get_data_name_sprite / _draw_data_name).
    _DATA_NAME_RAW_RECT = (8, 128, 103, 151)
    _DATA_NAME_COLOR = (255, 255, 255)

    # scouter_background.png presentation (see _get_scaled_scouter_background).
    # Same philosophy as PauseMenu: scale the art by an *integer* multiple
    # of its native size so every source pixel maps to an identical block
    # of screen pixels (no fractional scale → no pixel-row inconsistencies),
    # centre the result on screen, and fill the remaining letterbox /
    # pillarbox with a solid colour. No 9-slice gap insertion, no
    # independent x/y scales, no stretch-to-fill.
    _DATA_BG_LETTERBOX_COLOR = (0, 74, 0)  # #004A00

    # Character-viewer placement in the Data section, authored in
    # scouter_background.png RAW pixel space (x0, y0, x1, y1 inclusive)
    # — same convention as _DATA_PORTRAIT_RAW_RECT. Mapped through
    # _bg_raw_rect_to_screen(); the rotating entity is centred on this
    # rect. Matches the grid band of the source art: left edge at the
    # old left-cap (56px), right edge just before the portrait frame
    # (169), top at the grid top margin (8), bottom above the bottom
    # border band (raw_h - 57 ≈ 103 on a 160-tall source).
    _DATA_VIEWER_RAW_RECT = (8, 8, 103, 103)


    def __init__(self, screen_width, screen_height):
        self.screen_width = screen_width
        self.screen_height = screen_height

        self.active = False
        self.section = self.SECTION_MAP

        self._font = pygame.font.Font(None, 20)
        self._title_font = pygame.font.Font(None, 28)

        # ---- Map section state ----
        # Loaded lazily (first draw) and cached — same convention as the
        # game's own asset caches (e.g. _world_map_hud in game.py): try
        # once, remember the Surface (or the failure) so a missing file
        # doesn't retry a disk read every single frame.
        self._map_overlay_raw = None      # original, unscaled Surface
        self._map_overlay_scaled = None   # cached fit-to-screen Surface
        self._map_overlay_load_attempted = False

        # ---- Grid background (Scouter + World Map sections only) ----
        self._grid_raw = None
        self._grid_tiled = None           # cached full-screen tiled Surface
        self._grid_load_attempted = False

        # ---- L/R button prompts (top-left / top-right, all sections) ----
        self._button_load_attempted = False
        self._button_raw = {}             # name -> raw Surface | None
        self._button_scaled = {}          # name -> (cache_key, scaled Surface)
        # Whether Q/E was already being held *while its button was still
        # available* — see _draw_button_prompts. Lets a hold-through-
        # transition (e.g. Q on MAP -> SCOUTER) keep showing pressed, while
        # a fresh press of Q/E that starts after the button's already gone
        # (unused in the new section) does not.
        self._l_held_active = False
        self._r_held_active = False

        # ---- Scouter section state ----
        # Each entry: {'label': str, 'kind': 'player'|'npc'|'enemy',
        #              'obj': <entity>, 'x': int, 'y': int}  (x/y are the
        # frozen screen-space position captured at snapshot time).
        self._entities = []

        # ---- Crosshair sprite (Scouter section only) ----
        # Free-floating screen-space position (floats, so slow held-key
        # movement doesn't get rounded away) rather than an index into
        # self._entities — the crosshair can now sit anywhere on screen,
        # not just snapped onto an entity. See _update_crosshair_position()
        # and _find_target_entity().
        self._crosshair_x = 0.0
        self._crosshair_y = 0.0
        self._crosshair_raw = None
        self._crosshair_frames = {}       # frame_number (1-based) -> Surface | None
        self._crosshair_scaled_frames = {}  # frame_number -> (cache_key, scaled Surface)
        self._crosshair_load_attempted = False

        # ---- Player location marker (Map section only, player.png) ----
        self._player_marker_raw = None
        self._player_marker_frames = {}          # frame_number (1-based) -> Surface | None
        self._player_marker_scaled_frames = {}    # frame_number -> (cache_key, scaled Surface)
        self._player_marker_load_attempted = False

        # ---- Map section object markers (flyingpad.png / worldmap.png /
        # savepad.png) — same lazy/cached/fixed-HUD-size convention as the
        # Map section's player.png marker above (see _load_player_marker /
        # _get_scaled_player_marker_frame), just without the frame-strip
        # slicing since these are plain single-frame icons, one per stem.
        self._map_marker_icons_raw = {}               # icon stem -> Surface | None
        self._map_marker_icons_load_attempted = set()  # stems already loaded/tried
        self._map_marker_icons_scaled = {}             # icon stem -> (cache_key, scaled Surface)

        # ---- Map section camera (WASD/arrow panning) ----
        # In zone grid-cell space (same units world_to_grid returns) — the
        # cell currently centred in the viewport. Reset to the current
        # room's centre whenever the zone/room changes (see
        # _draw_map_section's cache_key check).
        self._map_camera_gx = 0.0
        self._map_camera_gy = 0.0
        self._map_camera_bounds = None   # (min_gx, min_gy, max_gx, max_gy) for the current zone
        self._map_zone_grid_size = None  # (grid_w, grid_h), unpadded — see _draw_map_section

        # ---- Map section directional pan arrows (room_arrow.png) ----
        # Same lazy/cached/design-v-scale convention as crosshair.png/
        # player.png above — see _load_room_arrow() /
        # _get_scaled_room_arrow_frame(). Base (unrotated, unflipped)
        # frames only; the per-edge flip/rotation is applied at draw
        # time in _draw_room_arrows(), not baked into the cache, so all
        # four edges share one set of scaled frames.
        self._room_arrow_raw = None
        self._room_arrow_frame_count = 0
        self._room_arrow_frames = {}          # frame_number (1-based) -> Surface | None
        self._room_arrow_scaled_frames = {}   # frame_number -> (cache_key, scaled Surface)
        self._room_arrow_load_attempted = False

        # ---- World Map section (see _draw_world_map_section) ----
        # world_map_surface_provider (passed into draw()) returns the raw,
        # presumably full-resolution composite for whatever map is active;
        # scaling that down to fit the screen is real per-pixel work
        # (pygame.transform.smoothscale), so it's cached here keyed on
        # (map name, the provider's own Surface identity, target size)
        # instead of redone every frame. If the provider itself rebuilds a
        # fresh Surface object on every call rather than caching internally,
        # this cache still helps (cuts the smoothscale cost) but can't do
        # anything about that rebuild cost — that lives outside this file.
        self._wm_scaled_cache = None   # (cache_key, scaled Surface) | None

        # ---- Scouter Data description scroll arrows (arrow.png) ----
        self._arrow_raw = None
        self._arrow_frames = {}           # frame_number (1-based) -> Surface | None
        self._arrow_load_attempted = False
        # Blink state — advanced only while SECTION_DATA is active, see
        # _update_data_description_scroll(). frame index is 0 or 1
        # (0 -> frame 1, 1 -> frame 2); which one actually gets drawn
        # per-arrow additionally depends on whether that arrow's
        # direction has anything left to scroll — see
        # _draw_data_description_scroll_arrows().
        self._data_desc_arrow_blink_timer = 0.0
        self._data_desc_arrow_blink_index = 0

        # Space-bar charge/hold/release animation — see
        # _update_crosshair_anim() for the full state machine. Frame 1 is
        # the idle/rest pose shown whenever the animation isn't running.
        self._crosshair_anim = 'idle'     # 'idle' | 'charging' | 'holding' | 'releasing'
        self._crosshair_frame = 1
        self._crosshair_step_queue = []
        self._crosshair_step_timer = 0.0
        self._space_held = False
        # Entity locked in when charging started — an ('obj', 'kind') pair
        # (or None if nothing was in range), not an index, since the
        # crosshair is no longer guaranteed to be sitting on an entity at
        # all. See _find_target_entity() / _start_crosshair_charge().
        self._crosshair_charge_target = None

        # Running clock purely for the target-glow pulse (see
        # _get_glow_tinted_sprite / _draw_scouter_entities) — advanced in
        # update(), not tied to any particular animation state.
        self._glow_time = 0.0

        # ---- Scouter Data section state (see SECTION_DATA) ----
        self._data_entity = None          # the inspected entity object
        self._data_kind = None            # 'player' | 'npc' | 'enemy'
        self._data_fade = 0.0             # 0 (black) -> 1 (fully faded in)
        # True while _data_fade is counting back DOWN to 0 after E/SPACE/
        # click dismissed the section — see _start_exit_data_section() /
        # update(). The actual section switch back to SCOUTER is deferred
        # until the fade-out reaches black, mirroring how the outer menu's
        # own close holds on black before fading back in (see Game's
        # 'close' handling for pause_menu/scouter_menu).
        self._data_exiting = False

        # ---- Data section rotating idle-sprite viewer ----
        # A standalone AnimatedSprite built fresh for whichever entity is
        # being inspected (see _build_data_viewer_sprite) — deliberately
        # NOT the live entity's own .sprite. self._data_entity is the
        # actual in-game player/npc/enemy instance; forcing IT through a
        # direction/idle spin here would leave that entity's real
        # animation state stomped on (wrong direction, mid-cycle frame)
        # the instant you back out to Scouter/gameplay, since .sprite is
        # shared with whatever's still rendering underneath. A clone
        # sidesteps that — nothing about the live entity is ever touched.
        self._data_viewer_sprite = None
        # The REAL shadow — same shadow_sprite Scouter itself draws for
        # this entity, captured via LayerManager._draw_shadow() back in
        # build_scouter_snapshot() (see self._entities). Reused as-is
        # rather than redrawn, since a shadow doesn't change shape as
        # the character turns to face a different direction — it can
        # just sit under whichever facing the viewer is currently on.
        # shadow_ox/oy are the capture-time top-left of that surface
        # relative to the entity's screen-centre (sx, sy) — the same
        # origin LayerManager._draw_shadow used — so the Data section
        # can place it with the exact in-game offset rather than
        # re-deriving a feet-anchored guess (see _draw_data_viewer_shadow).
        self._data_viewer_shadow = None
        self._data_viewer_shadow_ox = 0
        self._data_viewer_shadow_oy = 0
        # Seconds since the last facing swap, and an index into
        # _DATA_FACING_ORDER — both reset in _enter_data_section() so
        # every inspection starts facing down, mid-cycle. Advanced in
        # _update_data_viewer(), called from update() only while
        # section == SECTION_DATA.
        self._data_facing_timer = 0.0
        self._data_facing_index = 0

        # Smooth vertical scroll offset (px) for the description text —
        # see _update_data_description_scroll() / _draw_data_description().
        # 0 = top of the text. Reset to 0 in _enter_data_section() so
        # every inspection starts scrolled to the top. Clamped every
        # frame against that entity's own text content height (via
        # _get_data_description_scroll_bounds()), not just once on
        # entry, since it's cheap and keeps this robust even if
        # something upstream changes obj.description while the section
        # is open.
        self._data_desc_scroll_px = 0.0

        self._scouter_bg_raw = None
        self._scouter_bg_scaled = None
        self._scouter_bg_load_attempted = False

        # entity name -> {'name'|'kind'} title font, kept separate from the
        # HUD's smaller _font/_title_font so the data readout can use its
        # own sizes without affecting the other three sections.
        self._data_name_font = pygame.font.Font(None, 32)
        # Fallback only — used if a scouter_stats/dmg_font sprite is
        # missing on disk, so the row still shows *something* readable
        # instead of silently vanishing. Not used when the real sprite
        # fonts below loaded successfully.
        self._data_stat_label_font = pygame.font.Font(None, 18)
        self._data_stat_value_font = pygame.font.Font(None, 30)
        self._data_desc_font = pygame.font.Font(None, 20)

        # Scouter Data description text — separate upper/lowercase sprite
        # glyph sets (see _MixedCaseSpriteFont) so the description reads
        # in the same pixel-art font family as the rest of the panel
        # instead of a plain pygame font. self._data_desc_font is kept
        # only as an inline fallback for characters neither glyph set
        # covers (digits, punctuation, etc.) — see
        # _blit_data_description_text().
        self._data_desc_sprite_font = _MixedCaseSpriteFont(
            os.path.join('assets', 'ui', 'fonts', 'uppercase'),
            os.path.join('assets', 'ui', 'fonts', 'lowercase'))

        # Scouter Data stat readout sprite fonts — see _LetterSpriteFont /
        # _DigitSpriteFont above and _draw_data_stats() below.
        self._data_stat_word_font = _LetterSpriteFont(
            os.path.join('assets', 'ui', 'fonts', 'scouter_stats'))
        self._data_stat_digit_font = _DigitSpriteFont(
            os.path.join('assets', 'ui', 'fonts', 'dmg_font'))
        # Digits inside an entity's Scouter Data display name (e.g.
        # "Android 19") — separate from _data_stat_digit_font above,
        # which is the dmg_font popup-numbers art used for the HP/STR/
        # POW/END stat values, not the name. See _render_data_name_word().
        self._data_name_digit_font = _NameDigitSpriteFont(
            os.path.join('assets', 'ui', 'fonts', 'numbers'))
        # Cache for _scaled_digit_glyph() — scaling a glyph via
        # pygame.transform.scale isn't free, and there are only ever 10
        # possible digits, so scale each one once on first use rather
        # than every frame every row draws.
        self._data_stat_digit_scaled_cache = {}

        # Portrait art — keyed by the resolved (id, costume, form) tuple
        # (see _resolve_portrait_path) rather than by entity object, since
        # a player's costume/transformation can change between snapshots.
        # Value is a Surface or None (missing-asset, still cached so a
        # repeat lookup doesn't hit disk every frame).
        self._portrait_cache = {}

        self.sound_engine = None

    def set_sound_engine(self, sound_engine):
        self.sound_engine = sound_engine

    def _play(self, sound_id):
        # Best-effort — mirrors PauseMenu's own tolerance for a sound id
        # that isn't loaded rather than crashing the menu over an sfx.
        if self.sound_engine:
            try:
                self.sound_engine.play_sfx(sound_id)
            except Exception:
                pass

    # ----------------------------------------------------------------
    # Lifecycle
    # ----------------------------------------------------------------
    def open(self, player):
        """Called once the pre-menu fade-out reaches full black — see
        Game._open_scouter_menu(), which mirrors _open_pause_menu()."""
        self.active = True
        self.section = self.SECTION_MAP
        self._entities = []
        self._crosshair_x = self.screen_width / 2
        self._crosshair_y = self.screen_height / 2
        self._reset_crosshair_anim()
        self._data_entity = None
        self._data_kind = None
        self._data_fade = 0.0
        self._data_exiting = False

    def close(self):
        self.active = False
        self._reset_crosshair_anim()
        self._data_entity = None
        self._data_kind = None
        self._data_fade = 0.0
        self._data_exiting = False
        return 'close'

    def _reset_crosshair_anim(self):
        self._crosshair_anim = 'idle'
        self._crosshair_frame = 1
        self._crosshair_step_queue = []
        self._crosshair_step_timer = 0.0
        self._space_held = False
        self._crosshair_charge_target = None

    def update(self, dt):
        """Returns whatever _update_crosshair_anim() returns — None most
        frames, or ('inspect', entity_obj, kind) on the frame the
        charge/hold/release animation finishes back over a target. Game
        needs to check this return value the same way it already checks
        handle_input()'s, since the actual inspect trigger no longer
        happens synchronously on the SPACE keypress (see
        _update_crosshair_anim())."""
        if not self.active:
            return None
        self._glow_time += dt
        if self.section == self.SECTION_SCOUTER:
            self._update_crosshair_position(dt)
        if self.section == self.SECTION_MAP:
            self._update_map_camera(dt)
        result = self._update_crosshair_anim(dt)
        if result is not None:
            # ('inspect', obj, kind) — the charge/hold/release animation
            # just finished over a locked-in target. Open Scouter Data
            # ourselves (see _enter_data_section) rather than waiting on
            # Game to do something with the return value — the fade +
            # full-screen readout lives entirely in this file, same as
            # every other section. Still returned below so a caller that
            # *does* want to react (sfx, stat tracking, whatever) still
            # can, same contract as before.
            self._enter_data_section(result[1], result[2])
        if self.section == self.SECTION_DATA:
            if self._data_exiting:
                # Counting back down to black — same duration as the
                # entrance fade, just run in reverse. Once it hits 0 the
                # section is fully black, so swap back to SCOUTER right
                # then (see _exit_data_section) rather than waiting for
                # anything else; SCOUTER was already fully visible before
                # Data was entered, so nothing needs to fade back in on
                # this side — the outer black frame IS the seam.
                self._data_fade = max(0.0, self._data_fade - dt / self._DATA_FADE_SECONDS)
                if self._data_fade <= 0.0:
                    self._exit_data_section()
            elif self._data_fade < 1.0:
                self._data_fade = min(1.0, self._data_fade + dt / self._DATA_FADE_SECONDS)
        if self.section == self.SECTION_DATA:
            self._update_data_viewer(dt)
            self._update_data_description_scroll(dt)
        return result

    def _update_data_viewer(self, dt):
        """Ticks the Data section's rotating idle-sprite viewer forward —
        advances the idle animation every frame, and swaps facing
        direction counter-clockwise (see _DATA_FACING_ORDER) every
        _DATA_FACING_INTERVAL seconds. No-ops if _enter_data_section()
        couldn't build a viewer sprite for this entity (see
        _build_data_viewer_sprite) — _draw_data_portrait() falls back to
        the static portrait art in that case."""
        sprite = self._data_viewer_sprite
        if sprite is None:
            return

        self._data_facing_timer += dt
        if self._data_facing_timer >= self._DATA_FACING_INTERVAL:
            # Subtract rather than reset to 0 so a frame that overshoots
            # the interval (a hitch/large dt) doesn't lose that overshoot
            # — it just carries into the next facing's timer instead.
            self._data_facing_timer -= self._DATA_FACING_INTERVAL
            self._data_facing_index = (self._data_facing_index + 1) % len(self._DATA_FACING_ORDER)
            sprite.set_animation('idle', self._DATA_FACING_ORDER[self._data_facing_index])

        sprite.update(dt)

    # ----------------------------------------------------------------
    # Scouter snapshot
    # ----------------------------------------------------------------
    def build_scouter_snapshot(self, player, npcs, enemies, camera, render_scale, colors,
                                layer_manager):
        """Capture every on-screen entity's current screen position (and a
        cropped snapshot of its actual sprite, shadow included — see
        _capture_entity_sprite) the instant this is called. Call this once,
        right when transitioning MAP -> SCOUTER (Game does this in response
        to handle_input() returning 'enter_scouter') — NOT every frame,
        since the whole point of the Scouter section is that the frame
        stays frozen while you browse it. colors is passed straight through
        to each entity's own draw(screen, camera, colors) — same signature
        LayerManager.draw_all() calls it with every frame (see
        draw_layers.py) — so the captured sprite matches exactly what was
        on screen, tint/direction/animation frame and all. layer_manager is
        needed too, purely to reuse its _draw_shadow() the same way
        draw_all() does, so the ground shadow gets captured along with the
        sprite instead of being silently dropped."""
        self._entities = []

        px = int(player.x * render_scale - camera.x)
        py = int(player.y * render_scale - camera.y)
        captured = self._capture_entity_sprite(player, camera, colors, layer_manager, px, py)
        self._entities.append({
            'kind': 'player', 'obj': player, 'x': px, 'y': py,
            'sprite': captured['body'] if captured else None,
            'shadow_sprite': captured['shadow'] if captured else None,
            'shadow_ox': captured['shadow_ox'] if captured else 0,
            'shadow_oy': captured['shadow_oy'] if captured else 0,
        })

        m = self._FRAME_MARGIN
        for npc in npcs:
            sx = int(npc.x * render_scale - camera.x)
            sy = int(npc.y * render_scale - camera.y)
            if -m <= sx <= self.screen_width + m and -m <= sy <= self.screen_height + m:
                captured = self._capture_entity_sprite(npc, camera, colors, layer_manager, sx, sy)
                self._entities.append({
                    'kind': 'npc', 'obj': npc, 'x': sx, 'y': sy,
                    'sprite': captured['body'] if captured else None,
                    'shadow_sprite': captured['shadow'] if captured else None,
                    'shadow_ox': captured['shadow_ox'] if captured else 0,
                    'shadow_oy': captured['shadow_oy'] if captured else 0,
                })

        for enemy in enemies:
            sx = int(enemy.x * render_scale - camera.x)
            sy = int(enemy.y * render_scale - camera.y)
            if -m <= sx <= self.screen_width + m and -m <= sy <= self.screen_height + m:
                captured = self._capture_entity_sprite(enemy, camera, colors, layer_manager, sx, sy)
                self._entities.append({
                    'kind': 'enemy', 'obj': enemy, 'x': sx, 'y': sy,
                    'sprite': captured['body'] if captured else None,
                    'shadow_sprite': captured['shadow'] if captured else None,
                    'shadow_ox': captured['shadow_ox'] if captured else 0,
                    'shadow_oy': captured['shadow_oy'] if captured else 0,
                })

        # Start the crosshair parked on the player if they're on screen
        # (they always are — always the first entry, see above), so
        # opening Scouter doesn't drop you somewhere empty.
        if self._entities:
            self._crosshair_x = float(self._entities[0]['x'])
            self._crosshair_y = float(self._entities[0]['y'])
        else:
            self._crosshair_x = self.screen_width / 2
            self._crosshair_y = self.screen_height / 2
        self._reset_crosshair_anim()

    # Generous box (px, pre-render_scale) around an entity's screen position
    # that _capture_entity_sprite renders into before cropping — comfortably
    # bigger than any single character frame so nothing gets clipped.
    # Calibrated for RENDER_SCALE == _CAPTURE_REFERENCE_RENDER_SCALE — scaled
    # proportionally at capture time (see _capture_entity_sprite) so it stays
    # generous instead of clipping the sprite when the real RENDER_SCALE is
    # higher than that.
    _SPRITE_CAPTURE_PAD = 64
    _CAPTURE_REFERENCE_RENDER_SCALE = 4

    def _capture_entity_sprite(self, obj, camera, colors, layer_manager, sx, sy):
        """Get the entity's *real* current frame, split into its shadow and
        its body as two separate Surfaces sharing one coordinate frame —
        {'shadow': Surface|None, 'body': Surface|None} — by calling
        layer_manager._draw_shadow(...) and obj.draw(screen, camera,
        colors) (the same two calls in the same order
        draw_layers.LayerManager.draw_all() makes every frame) onto two
        separate throwaway transparent surfaces, then cropping both to the
        same bounds. They're kept apart (rather than flattened into one
        image the way earlier versions of this did) specifically so the
        Scouter's target glow (see _get_glow_tinted_sprite) can be applied
        to the body only — a tinted shadow blob doesn't read as part of
        the original game's lock-on effect, it just looks like a green
        smudge on the ground.

        Both crops are trimmed to the *union* of the shadow's and body's
        visible pixels rather than each independently — trimming them
        separately would give each its own tight bounding box and lose
        their relative offset (a shadow ellipse and a tall character
        sprite don't cover the same rectangle), so _draw_scouter_entities
        wouldn't be able to blit them back on top of each other correctly.

        This sidesteps needing to know each entity/sprite class's internal
        frame storage (Player routes through CharacterSprite, which only
        exposes a draw() that composites hurt-tint/flash/direction itself
        and blits straight to the target surface, not a plain stored
        .image) and the shadow's own drawing details (size, eligibility by
        class name, big/small variant — see _draw_shadow in
        draw_layers.py), and it happens once per entity at snapshot time,
        not per frame, so two full-screen scratch surfaces per entity is
        cheap enough here. Returns None (fail soft — see the dot fallback
        in _draw_scouter_entities) if the entity has no draw() or drawing
        it produces no visible pixels on either layer."""
        if not hasattr(obj, 'draw'):
            return None

        shadow_scratch = pygame.Surface((self.screen_width, self.screen_height), pygame.SRCALPHA)
        body_scratch = pygame.Surface((self.screen_width, self.screen_height), pygame.SRCALPHA)
        try:
            if layer_manager is not None:
                layer_manager._draw_shadow(_BlitScaledShim(shadow_scratch), obj, camera)
            obj.draw(_BlitScaledShim(body_scratch), camera, colors)
        except Exception as e:
            print(f'[scouter_menu] could not snapshot sprite for {type(obj).__name__}: {e}')
            return None

        pad = int(self._SPRITE_CAPTURE_PAD * max(1, RENDER_SCALE) / self._CAPTURE_REFERENCE_RENDER_SCALE)
        box = pygame.Rect(sx - pad, sy - pad, pad * 2, pad * 2).clip(
            pygame.Rect(0, 0, self.screen_width, self.screen_height))
        if box.width <= 0 or box.height <= 0:
            return None

        shadow_crop = shadow_scratch.subsurface(box).copy()
        body_crop = body_scratch.subsurface(box).copy()

        # See the docstring above — union, not each layer's own bounds, so
        # the two crops stay aligned to one shared coordinate frame.
        shadow_rects = pygame.mask.from_surface(shadow_crop).get_bounding_rects()
        body_rects = pygame.mask.from_surface(body_crop).get_bounding_rects()
        all_rects = shadow_rects + body_rects
        if not all_rects:
            return None
        bounds = all_rects[0].unionall(all_rects[1:])
        if bounds.width <= 0 or bounds.height <= 0:
            return None

        # Origin of the shared union crop relative to the entity's screen
        # centre (sx, sy). LayerManager._draw_shadow and obj.draw both
        # place their pixels relative to that centre, so this is the
        # exact top-left the union surface should be blitted at to land
        # on the same screen pixels it was captured from. The Data
        # section uses these offsets (scaled by _DATA_VIEWER_SCALE) so
        # the reused shadow sits under the viewer character with the
        # same relative placement the in-game shadow has — rather than
        # a feet-anchored re-guess that ignores how far the shadow
        # actually extends past the sprite's own bounding box.
        shadow_ox = (box.x + bounds.x) - sx
        shadow_oy = (box.y + bounds.y) - sy

        return {
            'shadow': shadow_crop.subsurface(bounds).copy() if shadow_rects else None,
            'body': body_crop.subsurface(bounds).copy() if body_rects else None,
            'shadow_ox': shadow_ox if shadow_rects else 0,
            'shadow_oy': shadow_oy if shadow_rects else 0,
        }

    def _update_crosshair_position(self, dt):
        """Free keyboard movement — held arrow keys / WASD, resolved every
        frame rather than jumping between entities on each keypress (see
        handle_input()'s pygame.MOUSEMOTION branch for the mouse half of
        this). Diagonal input is normalised so it isn't faster than
        cardinal movement."""
        keys = pygame.key.get_pressed()
        dx = (keys[pygame.K_RIGHT] or keys[pygame.K_d]) - (keys[pygame.K_LEFT] or keys[pygame.K_a])
        dy = (keys[pygame.K_DOWN] or keys[pygame.K_s]) - (keys[pygame.K_UP] or keys[pygame.K_w])
        if dx == 0 and dy == 0:
            return

        length = (dx * dx + dy * dy) ** 0.5
        step = self._CROSSHAIR_MOVE_SPEED * dt / length
        self._crosshair_x = min(max(self._crosshair_x + dx * step, 0), self.screen_width)
        self._crosshair_y = min(max(self._crosshair_y + dy * step, 0), self.screen_height)

    def _update_map_camera(self, dt):
        """Held WASD/arrow-key panning for the Map section's zone camera
        (see _draw_map_section) — same held-key, diagonal-normalised
        convention as _update_crosshair_position above, just moving in
        zone grid-cell space instead of screen pixels. Clamped to
        self._map_camera_bounds, which _draw_map_section (re)computes
        whenever the current zone/room changes."""
        keys = pygame.key.get_pressed()
        dx = (keys[pygame.K_d] or keys[pygame.K_RIGHT]) - (keys[pygame.K_a] or keys[pygame.K_LEFT])
        dy = (keys[pygame.K_s] or keys[pygame.K_DOWN]) - (keys[pygame.K_w] or keys[pygame.K_UP])
        if dx == 0 and dy == 0:
            return

        length = (dx * dx + dy * dy) ** 0.5
        step = self._MAP_PAN_SPEED * dt / length
        self._map_camera_gx += dx * step
        self._map_camera_gy += dy * step

        bounds = self._map_camera_bounds
        if bounds is not None:
            min_gx, min_gy, max_gx, max_gy = bounds
            self._map_camera_gx = min(max(self._map_camera_gx, min_gx), max_gx)
            self._map_camera_gy = min(max(self._map_camera_gy, min_gy), max_gy)

    def _find_target_entity(self):
        """Nearest entity to the crosshair's current position, provided
        it's within _CROSSHAIR_TARGET_RADIUS — or None if the crosshair
        isn't close enough to anything. Shared by the per-frame highlight
        ring (_draw_scouter_entities) and by what SPACE actually locks onto
        (_start_crosshair_charge), so the ring is always an honest preview
        of what pressing SPACE would do."""
        best = None
        best_dist = None
        for e in self._entities:
            dx = e['x'] - self._crosshair_x
            dy = e['y'] - self._crosshair_y
            dist = dx * dx + dy * dy
            if best_dist is None or dist < best_dist:
                best_dist = dist
                best = e
        if best is not None and best_dist <= self._CROSSHAIR_TARGET_RADIUS ** 2:
            return best
        return None

    def _start_crosshair_charge(self):
        """Called on SPACE KEYDOWN or left MOUSEBUTTONDOWN (see
        handle_input()) — either one starts the same charge/hold/release
        cycle. Doesn't fire the inspect itself — it just kicks off the
        charge animation (frames 2 -> 3); the actual ('inspect', obj,
        kind) result comes later, out of update(), once the animation
        finishes (see _update_crosshair_anim() and the module
        docstring)."""
        self._space_held = True
        if self._crosshair_anim != 'idle' or not self._entities:
            return
        target = self._find_target_entity()
        self._crosshair_charge_target = (target['obj'], target['kind']) if target else None
        self._crosshair_anim = 'charging'
        self._crosshair_step_queue = [2, 3]
        self._crosshair_step_timer = 0.0

    def _consume_crosshair_target(self):
        """The target was locked in back when charging started
        (_start_crosshair_charge) rather than re-read here, so moving the
        crosshair mid-animation can't change what gets inspected."""
        target = self._crosshair_charge_target
        self._crosshair_charge_target = None
        if target is None:
            return None
        obj, kind = target
        return ('inspect', obj, kind)

    def _update_crosshair_anim(self, dt):
        """Drives which crosshair frame is showing:

            press (quick tap):  1 -> 2 -> 3 -> 2 -> 1, then inspect fires
            press and hold:     1 -> 2 -> 3, parked at 3 for as long as
                                 SPACE stays down
            release after hold: 3 -> 2 -> 1, then inspect fires

        In every case the charge-up (2, 3) always plays out in full
        before anything ramps back down — a quick tap doesn't skip
        frame 3, it just doesn't linger there (see _start_crosshair_charge
        / the 'charging' branch below checking self._space_held only
        once the queue is empty, i.e. only once 3 has actually been
        reached)."""
        if self._crosshair_anim == 'idle':
            return None

        if self._crosshair_anim == 'holding':
            if self._space_held:
                return None   # parked at frame 3 until SPACE is released
            self._crosshair_anim = 'releasing'
            self._crosshair_step_queue = [2, 1]
            self._crosshair_step_timer = 0.0
            return None

        # 'charging' or 'releasing': step through the queue at a fixed
        # interval, one frame per _CROSSHAIR_STEP_SECONDS.
        result = None
        self._crosshair_step_timer += dt
        if self._crosshair_step_queue and self._crosshair_step_timer >= self._CROSSHAIR_STEP_SECONDS:
            self._crosshair_step_timer = 0.0
            self._crosshair_frame = self._crosshair_step_queue.pop(0)

            if not self._crosshair_step_queue:
                if self._crosshair_anim == 'charging':
                    if self._space_held:
                        self._crosshair_anim = 'holding'
                    else:
                        self._crosshair_anim = 'releasing'
                        self._crosshair_step_queue = [2, 1]
                        self._crosshair_step_timer = 0.0
                else:  # 'releasing'
                    self._crosshair_anim = 'idle'
                    result = self._consume_crosshair_target()
        return result

    def _enter_data_section(self, obj, kind):
        """Switches into SECTION_DATA for the just-inspected entity and
        starts the fade-in from black (see _DATA_FADE_SECONDS / update()).
        The crosshair/anim state is left alone — _reset_crosshair_anim()
        already ran as part of the release finishing, and SCOUTER's own
        state (crosshair position, snapshot entities) needs to survive
        underneath so E from here can drop straight back into browsing
        without re-snapshotting."""
        self.section = self.SECTION_DATA
        self._data_entity = obj
        self._data_kind = kind
        self._data_fade = 0.0
        self._data_exiting = False

        # Fresh rotating idle-viewer sprite for this entity (see
        # _build_data_viewer_sprite) — always start mid-cycle at index 0
        # (facing down, _DATA_FACING_ORDER[0]) with a clean timer, so
        # every inspection begins the same way rather than carrying over
        # wherever the previous entity's cycle happened to stop.
        self._data_facing_timer = 0.0
        self._data_facing_index = 0
        self._data_desc_scroll_px = 0.0
        self._data_desc_arrow_blink_timer = 0.0
        self._data_desc_arrow_blink_index = 0
        self._data_viewer_sprite = self._build_data_viewer_sprite(obj, kind)
        if self._data_viewer_sprite is not None:
            self._data_viewer_sprite.set_animation('idle', self._DATA_FACING_ORDER[0])

        # Reuse the SAME shadow_sprite Scouter already captured for this
        # entity (see self._entities / build_scouter_snapshot) rather
        # than drawing a new one — that's the game's real shadow asset,
        # sized/positioned via LayerManager._draw_shadow() the same way
        # it is everywhere else. obj is matched by identity, not value
        # equality, since two different entities could otherwise compare
        # equal. Also pull the capture-time offset of that surface's
        # top-left relative to the entity centre so _draw_data_viewer_shadow
        # can place it with the same relative offset the in-game shadow
        # has (see _capture_entity_sprite).
        self._data_viewer_shadow = None
        self._data_viewer_shadow_ox = 0
        self._data_viewer_shadow_oy = 0
        for entry in self._entities:
            if entry.get('obj') is obj:
                self._data_viewer_shadow = entry.get('shadow_sprite')
                self._data_viewer_shadow_ox = entry.get('shadow_ox', 0)
                self._data_viewer_shadow_oy = entry.get('shadow_oy', 0)
                break

        self._play('menu_move')

    def _start_exit_data_section(self):
        """Kicks off the fade-to-black on the way OUT of SECTION_DATA —
        called from handle_input()'s SECTION_DATA branch (E/SPACE) and
        the MOUSEBUTTONDOWN click-to-dismiss branch, instead of switching
        section immediately. Mirrors _enter_data_section() starting the
        fade-IN: same _DATA_FADE_SECONDS duration, just counting down
        instead of up (see update()). No-ops if a fade-out is already
        running so mashing E/SPACE can't restart the timer mid-fade."""
        if self._data_exiting:
            return
        self._data_exiting = True
        self._play('menu_move')

    def _exit_data_section(self):
        """Actually switches back to SCOUTER browsing — called from
        update() once the fade-out started by _start_exit_data_section()
        reaches black. Doesn't touch self._entities/_crosshair_x/_y, so
        browsing resumes exactly where it left off."""
        self.section = self.SECTION_SCOUTER
        self._data_entity = None
        self._data_kind = None
        self._data_viewer_sprite = None
        self._data_viewer_shadow = None
        self._data_viewer_shadow_ox = 0
        self._data_viewer_shadow_oy = 0
        self._data_exiting = False

    def _build_data_viewer_sprite(self, obj, kind):
        """Fresh, standalone AnimatedSprite for the Data section's
        rotating idle viewer — see the state comment on
        self._data_viewer_sprite in __init__ for why this is a brand new
        sprite rather than obj.sprite itself.

        Player routes through create_character_sprite(obj.character,
        obj.costume) — both fields Player.__init__ always sets (see
        player.py), same source _resolve_portrait_key() already reads.
        NPC/enemy classes aren't in this module, so their type/variant
        are pulled via getattr with fallbacks rather than a hard
        attribute reference — same fail-soft spirit as the rest of this
        method and of _resolve_portrait_key(). Returns None (fail soft —
        _draw_data_portrait() falls back to the static portrait art) if
        the kind is unrecognised or the loader raises."""
        try:
            if kind == 'player':
                character = getattr(obj, 'character', None) or 'goku'
                costume = getattr(obj, 'costume', None) or 'base'
                return create_character_sprite(character, costume, 32, 32)
            elif kind == 'enemy':
                variant = getattr(obj, 'variant', None) or 'default'
                # Bosses (BossEnemy, is_boss=True) don't load their sprite
                # via create_enemy_sprite() — see boss_enemy.py, which
                # pulls from a separate assets/sprites/enemies/boss/{boss_id}/
                # folder via create_boss_sprite(), since boss frames aren't
                # necessarily 32x32 like regular enemies. Using
                # create_enemy_sprite() for a boss looks in the wrong
                # folder, fails, and falls back to the static portrait
                # (no rotation) — hence the branch here matching
                # boss_enemy.py's own loader exactly, frame size included.
                if getattr(obj, 'is_boss', False):
                    boss_id = getattr(obj, 'boss_id', None) or getattr(obj, 'enemy_type', None)
                    if not boss_id:
                        return None
                    fw = getattr(obj, '_frame_width', 32)
                    fh = getattr(obj, '_frame_height', 32)
                    return create_boss_sprite(boss_id, variant, fw, fh)

                enemy_type = (getattr(obj, 'enemy_type', None) or getattr(obj, 'id', None)
                              or getattr(obj, 'character', None))
                if not enemy_type:
                    return None
                return create_enemy_sprite(enemy_type, variant, 32, 32)
            elif kind == 'npc':
                npc_type = (getattr(obj, 'npc_type', None) or getattr(obj, 'id', None)
                            or getattr(obj, 'character', None) or 'generic')
                variant = getattr(obj, 'variant', None) or 'default'
                return create_npc_sprite(npc_type, variant, 32, 32)
        except Exception as e:
            print(f'[scouter_menu] could not build data viewer sprite for '
                  f'{kind} {type(obj).__name__}: {e}')
        return None

    def _capture_data_viewer_frame(self):
        """Render self._data_viewer_sprite's current frame onto a small
        scratch surface and crop it to its own visible pixels — same
        crop-to-bounds approach _capture_entity_sprite uses for Scouter's
        entity snapshots, just against a blank scratch canvas instead of
        the live game frame since there's no world position to capture
        from here. Returns (surface, ox, oy) where ox/oy are the
        crop's top-left relative to the scratch centre (i.e. the same
        entity-centre origin AnimatedSprite.draw used), so the caller
        can place the tight crop back on that centre rather than
        re-centring the bounding box itself — keeping the body aligned
        with the shadow, which is also placed relative to entity
        centre. Returns None if there's no viewer sprite, or its
        current frame is fully transparent (e.g. this particular
        animation/direction combo failed to load — see AnimatedSprite's
        placeholder-magenta-rect branch in sprite_system.py, which
        pygame.mask would see as fully opaque, not transparent, so this
        deliberately doesn't try to distinguish that case here — it'll
        just show the placeholder rect like everywhere else in the game
        would)."""
        sprite = self._data_viewer_sprite
        if sprite is None:
            return None

        size = int(self._DATA_VIEWER_SCRATCH_SIZE * max(1, RENDER_SCALE) / self._CAPTURE_REFERENCE_RENDER_SCALE)
        scratch = pygame.Surface((size, size), pygame.SRCALPHA)
        # camera=None -> AnimatedSprite.draw() centers the frame on
        # (x * RENDER_SCALE, y * RENDER_SCALE). Picking x=y=half the
        # scratch size in WORLD units (i.e. pre-RENDER_SCALE) lands that
        # center on the scratch surface's own center regardless of
        # whatever RENDER_SCALE actually is.
        c = (size / 2) / RENDER_SCALE
        try:
            sprite.draw(_BlitScaledShim(scratch), c, c, camera=None)
        except Exception as e:
            print(f'[scouter_menu] could not draw data viewer sprite: {e}')
            return None

        rects = pygame.mask.from_surface(scratch).get_bounding_rects()
        if not rects:
            return None
        bounds = rects[0].unionall(rects[1:])
        if bounds.width <= 0 or bounds.height <= 0:
            return None
        # Top-left of the tight crop relative to the entity centre
        # (scratch midpoint) — same coordinate frame the shadow's
        # shadow_ox/oy use.
        half = size / 2
        ox = bounds.x - half
        oy = bounds.y - half
        return scratch.subsurface(bounds).copy(), ox, oy

    # ----------------------------------------------------------------
    # Input
    # ----------------------------------------------------------------
    def handle_input(self, event):
        """Returns:
            None                              - nothing Game needs to act on
            'close'                           - menu just closed
            'enter_scouter'                   - Game should call
                                                 build_scouter_snapshot() now

        Note: the ('inspect', entity_obj, kind) result is NOT returned
        from here anymore. SPACE now kicks off a charge/hold/release
        animation on the crosshair sprite (see _start_crosshair_charge /
        _update_crosshair_anim) and the inspect result comes back out of
        update() once that animation finishes — see update()'s docstring.
        """
        if not self.active:
            return None

        if event.type == pygame.KEYUP:
            if event.key == pygame.K_SPACE:
                self._space_held = False
            return None

        if event.type == pygame.MOUSEBUTTONUP:
            if event.button == 1:
                self._space_held = False
            return None

        if event.type == pygame.MOUSEMOTION:
            # Mouse free-moves the crosshair directly, alongside the held-
            # key movement in _update_crosshair_position() — whichever the
            # player is using just works, no mode switch needed.
            if self.section == self.SECTION_SCOUTER:
                mx, my = event.pos
                self._crosshair_x = min(max(mx, 0), self.screen_width)
                self._crosshair_y = min(max(my, 0), self.screen_height)
            return None

        if event.type == pygame.MOUSEBUTTONDOWN:
            # Left click charges/inspects the same as SPACE (see
            # _start_crosshair_charge) — same charge/hold/release
            # animation either way, this just gives it a second trigger.
            # Only meaningful in SCOUTER (SPACE has no effect in MAP or
            # WORLD_MAP either — see the section branches below).
            if event.button == 1 and self.section == self.SECTION_SCOUTER:
                self._start_crosshair_charge()
            elif event.button == 1 and self.section == self.SECTION_DATA:
                # Click anywhere dismisses the data readout, same as SPACE
                # below — mirrors it being what opened this section.
                self._start_exit_data_section()
            return None

        if event.type != pygame.KEYDOWN:
            return None

        if event.key in (pygame.K_ESCAPE, pygame.K_RETURN):
            return self.close()

        if self.section == self.SECTION_MAP:
            if event.key == pygame.K_q:
                self.section = self.SECTION_SCOUTER
                self._play('menu_move')
                return 'enter_scouter'
            elif event.key == pygame.K_e:
                self.section = self.SECTION_WORLD_MAP
                self._play('menu_move')

        elif self.section == self.SECTION_SCOUTER:
            if event.key == pygame.K_e:
                self.section = self.SECTION_MAP
                self._play('menu_move')
            elif event.key == pygame.K_SPACE:
                self._start_crosshair_charge()

        elif self.section == self.SECTION_WORLD_MAP:
            if event.key == pygame.K_q:
                self.section = self.SECTION_MAP
                self._play('menu_move')

        elif self.section == self.SECTION_DATA:
            # Either SPACE (what opened this section) or E (the usual
            # "back" key everywhere else in Scouter) dismisses it back to
            # browsing — see _r_button_available(), which shows E's prompt
            # here for the same reason.
            if event.key in (pygame.K_SPACE, pygame.K_e):
                self._start_exit_data_section()

        return None

    # ----------------------------------------------------------------
    # Draw
    # ----------------------------------------------------------------
    def draw(self, surface, current_room, active_world_map_name, wm_locations,
              world_map_surface_provider, room_manager=None, player=None,
              world_map_lookup=None):
        """world_map_surface_provider: callable(map_name) -> Surface|None,
        e.g. Game._build_world_map_surface, so this module never has to
        know how the tile JSON is turned into a texture.

        world_map_lookup: optional callable(room_name) -> (map_name,
        locations) | None — "is this specific room a pinned location on
        some world map, and if so which map/location list?" When given
        (together with room_manager), the World Map section resolves
        attachment TRANSITIVELY: it BFS's every room reachable from
        current_room via room_transitions (see
        scouter_room_map.build_zone_layout — the exact same traversal
        the Map section already uses to lay out a zone) and asks
        world_map_lookup about each one in turn, so current_room counts
        as attached if it — or any room connected to it, or any room
        connected to THOSE rooms, and so on — is a pinned location. The
        first reachable room world_map_lookup recognizes wins, and its
        (map_name, locations) supersede the active_world_map_name/
        wm_locations arguments above. That same reachable-room set is
        also what decides which location pin(s) play the full idle
        animation in _draw_world_map_section, instead of only the one
        room matching current_room.name exactly.

        If world_map_lookup is omitted, none of the above runs — the
        section falls back to using active_world_map_name/wm_locations
        exactly as given, unchanged from before, so existing callers
        keep working until they wire up world_map_lookup. A minimal
        adapter usually looks like: given some existing per-room
        resolver `_resolve_world_map(room)` that only ever checked one
        room, `world_map_lookup=lambda name: _resolve_world_map(
        room_manager.get_room_by_name(name))`.

        room_manager: needed by the Map section (get_room_by_name + rooms)
        to walk room_transitions and lay out the current zone — see
        ui/scouter_room_map.py. Optional so callers/tests that never open
        the Map section aren't forced to provide one.

        player: needed by the Map section to draw the "you are here"
        marker (player.png) at the player's actual position within
        current_room — see _draw_map_player_marker(). Only .x/.y are
        read, same room-local units build_scouter_snapshot() already
        reads player.x/player.y in. Optional; the marker is simply
        skipped if not provided, same fail-soft tolerance as a missing
        room_manager."""
        if not self.active:
            return

        # Solid black backdrop behind everything else (fully opaque — no
        # peeking through to the frozen game frame underneath).
        surface.fill((0, 0, 0))

        if self.section == self.SECTION_DATA:
            # Its own full-screen background (scouter_background.png)
            # rather than grid.png + map_overlay.png — Data isn't a scan
            # over the frozen frame like Scouter/World Map, it's a
            # dedicated readout, so it gets a dedicated backdrop instead
            # of reusing the scan-bezel art. See _draw_data_section().
            self._draw_data_section(surface)
            self._draw_chrome(surface)
            self._draw_button_prompts(surface)
            self._draw_data_fade(surface)
            return

        # grid.png sits behind everything else in this frame, but only on
        # the Scouter and World Map sections — not Map.
        if self.section in (self.SECTION_SCOUTER, self.SECTION_WORLD_MAP):
            self._draw_grid_background(surface)

        # Scouter / Map / World Map content sits in front of the grid but
        # *behind* map_overlay.png — the HUD bezel frames them rather than
        # the map painting over the overlay art.
        if self.section == self.SECTION_SCOUTER:
            self._draw_scouter_entities(surface)
            self._draw_scouter_crosshair(surface)
        elif self.section == self.SECTION_MAP:
            self._draw_map_section(surface, current_room, room_manager, player)
        elif self.section == self.SECTION_WORLD_MAP:
            resolved_map_name = active_world_map_name
            resolved_locations = wm_locations
            connected_room_names = None
            if (world_map_lookup is not None and room_manager is not None
                    and current_room is not None):
                resolved_map_name, resolved_locations, connected_room_names = (
                    self._resolve_world_map_attachment(
                        current_room, room_manager, world_map_lookup))
            self._draw_world_map_section(
                surface, current_room, resolved_map_name, resolved_locations,
                world_map_surface_provider, connected_room_names)

        # map_overlay.png sits in front of the grid and section content,
        # behind chrome/button prompts — see _draw_overlay_background().
        self._draw_overlay_background(surface)
        if self._map_overlay_raw is None:
            # Asset missing — keep the sections usable instead of showing
            # nothing where the frame would normally be.
            msg = self._font.render('assets/ui/scouter/map_overlay.png not found',
                                     True, (200, 100, 100))
            surface.blit(msg, (self.screen_width // 2 - msg.get_width() // 2,
                                self.screen_height // 2))

        if self.section == self.SECTION_MAP:
            # room_arrow.png prompts still sit on top of everything,
            # including the overlay drawn just above — see
            # _draw_room_arrows(). The zone silhouette itself was
            # already drawn earlier, before the overlay (see above).
            self._draw_room_arrows(surface)

        self._draw_chrome(surface)
        self._draw_button_prompts(surface)

    def _draw_chrome(self, surface):
        # Title and bottom hint text are skipped entirely for SECTION_DATA,
        # SECTION_MAP, SECTION_WORLD_MAP, and SECTION_SCOUTER — the Scouter
        # Data panel's own art (portrait frame, name plate, stat rows) fills
        # that space and doesn't want the section label or key-hint line
        # drawn over/around it, the zone map is deliberately shown clean
        # with no title/tooltip chrome, the world map is shown the same
        # clean way, and the Scouter crosshair-browse section is now shown
        # clean too — no title label or key-hint line drawn over any of them.
        if self.section in (self.SECTION_DATA, self.SECTION_MAP,
                             self.SECTION_WORLD_MAP, self.SECTION_SCOUTER):
            return

        label = {
            self.SECTION_DATA: 'SCOUTER DATA',
        }[self.section]
        title_surf = self._title_font.render(label, True, (255, 220, 60))
        surface.blit(title_surf, (self.screen_width // 2 - title_surf.get_width() // 2, 10))

        hint = {
            self.SECTION_DATA: 'Space/Click/E: Back    ESC/Enter: Close',
        }[self.section]
        hint_surf = self._font.render(hint, True, (200, 200, 200))
        surface.blit(hint_surf, (self.screen_width // 2 - hint_surf.get_width() // 2,
                                  self.screen_height - 24))

    def _load_map_overlay(self):
        """Load assets/ui/scouter/map_overlay.png once and cache a
        letterboxed (aspect-preserved) scale-to-fit for the current screen
        size. Only re-derives the scaled copy if the screen size actually
        changed — matches the raw Surface once and reuses it."""
        self._map_overlay_load_attempted = True
        path = os.path.join('assets', 'ui', 'scouter', 'map_overlay.png')
        try:
            self._map_overlay_raw = pygame.image.load(path).convert_alpha()
        except Exception as e:
            print(f'[scouter_menu] could not load {path}: {e}')
            self._map_overlay_raw = None

    def _load_grid(self):
        """Load assets/ui/scouter/grid.png once — same lazy/cached
        convention as _load_map_overlay."""
        self._grid_load_attempted = True
        path = os.path.join('assets', 'ui', 'scouter', 'grid.png')
        try:
            self._grid_raw = pygame.image.load(path).convert_alpha()
        except Exception as e:
            print(f'[scouter_menu] could not load {path}: {e}')
            self._grid_raw = None

    def _load_crosshair(self):
        """Load assets/ui/scouter/crosshair.png once — same lazy/cached
        convention as the other Scouter assets. It's a horizontal strip
        of 32x32 frames (see _CROSSHAIR_FRAME_PX); individual frames are
        sliced out and cached on demand by _get_crosshair_frame()."""
        self._crosshair_load_attempted = True
        path = os.path.join('assets', 'ui', 'scouter', 'crosshair.png')
        try:
            self._crosshair_raw = pygame.image.load(path).convert_alpha()
        except Exception as e:
            print(f'[scouter_menu] could not load {path}: {e}')
            self._crosshair_raw = None

    def _get_crosshair_frame(self, frame_number):
        """frame_number is 1-based (1 = idle/rest, 2/3 = charge poses —
        see _update_crosshair_anim()). Returns the raw 32x32 slice out of
        the strip, cached but NOT scaled — draw code should call
        _get_scaled_crosshair_frame() instead so the crosshair matches
        the rest of the Scouter UI's scale."""
        if not self._crosshair_load_attempted:
            self._load_crosshair()
        if self._crosshair_raw is None:
            return None
        if frame_number in self._crosshair_frames:
            return self._crosshair_frames[frame_number]

        size = self._CROSSHAIR_FRAME_PX
        rect = pygame.Rect((frame_number - 1) * size, 0, size, size)
        raw_w, raw_h = self._crosshair_raw.get_size()
        if rect.right > raw_w or rect.bottom > raw_h:
            # Sheet doesn't actually have this frame — fail soft instead
            # of raising, same tolerance as the other asset loaders here.
            print(f'[scouter_menu] crosshair.png has no frame {frame_number} '
                  f'(sheet is {raw_w}x{raw_h}, needs at least '
                  f'{rect.right}x{rect.bottom})')
            self._crosshair_frames[frame_number] = None
            return None

        frame = self._crosshair_raw.subsurface(rect).copy()
        self._crosshair_frames[frame_number] = frame
        return frame

    def _get_scaled_crosshair_frame(self, frame_number):
        """Scales the crosshair frame by the same design_v_scale factor
        used for grid.png and the L/R button prompts (see
        _get_design_v_scale() / _get_scaled_button()) — that factor is
        derived from map_overlay.png's own height vs. the screen, and is
        what makes every other piece of Scouter UI read as one consistent
        "resolution" regardless of actual screen size. crosshair.png was
        being drawn at its native 32x32 with no such scaling, which is
        why it showed up tiny next to everything else."""
        raw = self._get_crosshair_frame(frame_number)
        if raw is None:
            return None

        v_scale = self._get_design_v_scale()
        cache_key = (frame_number, v_scale)
        cached = self._crosshair_scaled_frames.get(frame_number)
        if cached is not None and cached[0] == cache_key:
            return cached[1]

        w = max(1, round(raw.get_width() * v_scale))
        h = max(1, round(raw.get_height() * v_scale))
        scaled = pygame.transform.scale(raw, (w, h)) if v_scale != 1.0 else raw
        self._crosshair_scaled_frames[frame_number] = (cache_key, scaled)
        return scaled

    def _load_player_marker(self):
        """Load assets/ui/scouter/player.png once — same lazy/cached
        convention as crosshair.png/arrow.png. It's a horizontal strip of
        8x8 frames (see _PLAYER_MARKER_FRAME_PX/_PLAYER_MARKER_FRAME_COUNT);
        individual frames are sliced out and cached on demand by
        _get_player_marker_frame().

        Unlike crosshair.png/arrow.png, this sheet is authored with a
        flat color-key background rather than real per-pixel alpha, so a
        plain convert_alpha() leaves that background solid instead of
        transparent (showing up as a colored box around the sprite).
        Sampled from the sheet's own corner pixel (0, 0) — same spot the
        art is presumed to use as background everywhere — rather than
        hardcoded to a specific color, so this keeps working unchanged if
        the asset is ever re-authored on a different key color."""
        self._player_marker_load_attempted = True
        path = os.path.join('assets', 'ui', 'scouter', 'player.png')
        try:
            raw = pygame.image.load(path).convert()
            key_color = raw.get_at((0, 0))
            raw.set_colorkey(key_color)
            # convert_alpha() bakes the colorkey into a real per-pixel
            # alpha channel, so subsurface()'d frames blend cleanly
            # (colorkey alone doesn't survive scaling as well as a true
            # alpha channel does).
            self._player_marker_raw = raw.convert_alpha()
        except Exception as e:
            print(f'[scouter_menu] could not load {path}: {e}')
            self._player_marker_raw = None

    def _get_player_marker_frame(self, frame_number):
        """frame_number is 1-based. Returns the raw 8x8 slice out of the
        strip, cached but NOT scaled — draw code should go through
        _get_scaled_player_marker_frame() instead."""
        if not self._player_marker_load_attempted:
            self._load_player_marker()
        if self._player_marker_raw is None:
            return None
        if frame_number in self._player_marker_frames:
            return self._player_marker_frames[frame_number]

        size = self._PLAYER_MARKER_FRAME_PX
        rect = pygame.Rect((frame_number - 1) * size, 0, size, size)
        raw_w, raw_h = self._player_marker_raw.get_size()
        if rect.right > raw_w or rect.bottom > raw_h:
            # Sheet doesn't actually have this frame — fail soft instead
            # of raising, same tolerance as the other asset loaders here.
            print(f'[scouter_menu] player.png has no frame {frame_number} '
                  f'(sheet is {raw_w}x{raw_h}, needs at least '
                  f'{rect.right}x{rect.bottom})')
            self._player_marker_frames[frame_number] = None
            return None

        frame = self._player_marker_raw.subsurface(rect).copy()
        self._player_marker_frames[frame_number] = frame
        return frame

    def _get_scaled_player_marker_frame(self, frame_number):
        """Scaled by the same design_v_scale factor as the crosshair/
        scroll-arrow HUD icons (see _get_scaled_crosshair_frame) — NOT by
        the Map section's own per-cell zoom (see _draw_map_player_marker),
        so the marker reads as a fixed-size "you are here" pin no matter
        how large or small the current zone's silhouette ends up on
        screen."""
        raw = self._get_player_marker_frame(frame_number)
        if raw is None:
            return None

        v_scale = self._get_design_v_scale()
        cache_key = (frame_number, v_scale)
        cached = self._player_marker_scaled_frames.get(frame_number)
        if cached is not None and cached[0] == cache_key:
            return cached[1]

        w = max(1, round(raw.get_width() * v_scale))
        h = max(1, round(raw.get_height() * v_scale))
        scaled = pygame.transform.scale(raw, (w, h)) if v_scale != 1.0 else raw
        self._player_marker_scaled_frames[frame_number] = (cache_key, scaled)
        return scaled

    def _load_map_marker_icon(self, stem):
        """Load assets/ui/scouter/{stem}.png once per stem — same lazy/
        cached convention as player.png (see _load_player_marker), for the
        Map section's flying pad / world map / save pad object markers."""
        self._map_marker_icons_load_attempted.add(stem)
        path = os.path.join('assets', 'ui', 'scouter', f'{stem}.png')
        try:
            self._map_marker_icons_raw[stem] = pygame.image.load(path).convert_alpha()
        except Exception as e:
            print(f'[scouter_menu] could not load {path}: {e}')
            self._map_marker_icons_raw[stem] = None

    def _get_scaled_map_marker_icon(self, stem):
        """Scaled by the same design_v_scale factor as the player marker
        (see _get_scaled_player_marker_frame) — a fixed HUD size regardless
        of how large or small the zone silhouette itself is drawn on
        screen. Returns None if the art is missing so the caller can fall
        back to a plain dot, same tolerance as the player marker."""
        if stem not in self._map_marker_icons_load_attempted:
            self._load_map_marker_icon(stem)
        raw = self._map_marker_icons_raw.get(stem)
        if raw is None:
            return None

        v_scale = self._get_design_v_scale()
        cached = self._map_marker_icons_scaled.get(stem)
        if cached is not None and cached[0] == v_scale:
            return cached[1]

        w = max(1, round(raw.get_width() * v_scale))
        h = max(1, round(raw.get_height() * v_scale))
        scaled = pygame.transform.scale(raw, (w, h)) if v_scale != 1.0 else raw
        self._map_marker_icons_scaled[stem] = (v_scale, scaled)
        return scaled

    def _get_player_marker_frame_number(self, animating=True):
        """Ping-pong idle cycle driven off self._glow_time (already
        ticking every frame the menu is active — see update()) rather than
        its own dt-tracked timer, same "just derive it from the running
        clock" approach _draw_map_section takes for the current-room pulse.
        1-based. Bounces 1 -> 2 -> 3 -> 4 -> 4 -> 3 -> 2 -> 1 -> 1 -> ...,
        holding one step at each end before reversing, rather than
        wrapping straight from the last frame back to the first — the
        sheet is a walk/bob cycle, not a loop, so snapping 4 back to 1
        read as a visible pop.

        animating=False holds frame 2 instead — used by the World Map's
        location pins (see _draw_world_map_section): every pin shows a
        static idle pose, and only the pin for whatever location the
        player's current room is actually connected to plays the full
        cycle. Frame 2, not frame 1, matches the pose the pin is
        authored to rest on when idle."""
        if not animating:
            return 2
        n = self._PLAYER_MARKER_FRAME_COUNT
        step = int(self._glow_time / self._PLAYER_MARKER_FRAME_SECONDS)
        if n <= 1:
            return 1
        cycle = 2 * n
        pos = step % cycle
        index = pos if pos < n else (cycle - 1 - pos)   # 0-based, holds at both ends
        return index + 1

    def _load_room_arrow(self):
        """Load assets/ui/scouter/room_arrow.png once — same lazy/cached
        convention as arrow.png/player.png (see _load_player_marker). A
        horizontal strip of _ROOM_ARROW_FRAME_PX-wide frames; the frame
        count is derived from the sheet's own width divided by that,
        rather than a hardcoded count, so re-authoring the sheet with
        more/fewer frames doesn't need a matching code change here.
        Individual frames are sliced out and cached on demand by
        _get_room_arrow_frame()."""
        self._room_arrow_load_attempted = True
        path = os.path.join('assets', 'ui', 'scouter', 'room_arrow.png')
        try:
            self._room_arrow_raw = pygame.image.load(path).convert_alpha()
            self._room_arrow_frame_count = (
                self._room_arrow_raw.get_width() // self._ROOM_ARROW_FRAME_PX
            )
        except Exception as e:
            print(f'[scouter_menu] could not load {path}: {e}')
            self._room_arrow_raw = None
            self._room_arrow_frame_count = 0

    def _get_room_arrow_frame(self, frame_number):
        """frame_number is 1-based. Slices the frame_number-th
        _ROOM_ARROW_FRAME_PX x _ROOM_ARROW_FRAME_HEIGHT_PX cell out of
        the strip. Deliberately does NOT assume the frame height equals
        the sheet's own raw height — if the file has any extra rows
        (padding, margin, whatever) beyond the actual 8px-tall arrow
        art, slicing by the full raw height would pull that padding
        into the "frame" too. That padding would then sit on a
        different side of the sprite depending on which way it later
        gets rotated (_draw_room_arrows rotates left/right in opposite
        directions), throwing the visible art off from the edge
        differently per direction — exactly the kind of asymmetric
        misalignment reported against the previous raw_h-based version.
        Cached but NOT scaled — draw code should go through
        _get_scaled_room_arrow_frame() instead."""
        if not self._room_arrow_load_attempted:
            self._load_room_arrow()
        if self._room_arrow_raw is None:
            return None
        if frame_number in self._room_arrow_frames:
            return self._room_arrow_frames[frame_number]

        fw = self._ROOM_ARROW_FRAME_PX
        fh = self._ROOM_ARROW_FRAME_HEIGHT_PX
        raw_w, raw_h = self._room_arrow_raw.get_size()
        rect = pygame.Rect((frame_number - 1) * fw, 0, fw, fh)
        if rect.right > raw_w or rect.bottom > raw_h:
            # Sheet doesn't actually have this frame — fail soft instead
            # of raising, same tolerance as the other asset loaders here.
            print(f'[scouter_menu] room_arrow.png has no frame {frame_number} '
                  f'(sheet is {raw_w}x{raw_h}, needs at least '
                  f'{rect.right}x{rect.bottom})')
            self._room_arrow_frames[frame_number] = None
            return None

        frame = self._room_arrow_raw.subsurface(rect).copy()
        self._room_arrow_frames[frame_number] = frame
        return frame

    def _get_scaled_room_arrow_frame(self, frame_number):
        """Scaled by the same design_v_scale factor as the crosshair/
        player-marker/scroll-arrow HUD icons (see
        _get_scaled_crosshair_frame) so it reads at a consistent size
        regardless of actual screen resolution — but ROUNDED to the
        nearest whole number first, unlike those other icons.
        self._get_design_v_scale() is a raw float ratio (screen_height
        / map_overlay_raw.get_height()), and pygame.transform.scale
        can't map every source pixel to an identical-size block of
        screen pixels when the factor isn't a whole number — some
        rows/columns of the tiny (_ROOM_ARROW_FRAME_PX-wide) source art
        end up duplicated one extra time and others don't, which is
        what shows up as visible pixel inconsistencies on a simple
        flat-color arrow shape. Rounding first gives a clean NxN block
        per source pixel instead — same crisp-pixel approach
        _get_data_bg_layout() uses for the Data section's integer-
        scaled panel — at the cost of the icon's on-screen size no
        longer matching the crosshair/player marker's to the exact
        sub-pixel (visually indistinguishable in practice, and this
        icon isn't shown alongside those anyway).

        Returns the base (unrotated, unflipped) frame — orientation per
        edge is applied by the caller (_draw_room_arrows), not baked in
        here, so all four edges can share this one cache instead of
        needing four rotated copies of every frame."""
        raw = self._get_room_arrow_frame(frame_number)
        if raw is None:
            return None

        v_scale = max(1, round(self._get_design_v_scale()))
        cache_key = (frame_number, v_scale)
        cached = self._room_arrow_scaled_frames.get(frame_number)
        if cached is not None and cached[0] == cache_key:
            return cached[1]

        w = max(1, round(raw.get_width() * v_scale))
        h = max(1, round(raw.get_height() * v_scale))
        scaled = pygame.transform.scale(raw, (w, h)) if v_scale != 1.0 else raw
        self._room_arrow_scaled_frames[frame_number] = (cache_key, scaled)
        return scaled

    def _get_room_arrow_frame_number(self, animating):
        """1-based. Holds frame 1 when `animating` is False — that edge
        has nothing left to pan toward, same "static when there's
        nothing to scroll to" rule the Data description's scroll arrows
        use. Otherwise bounces back and forth through the sheet's
        frames (1, 2, 3, 4, 3, 2, 1, 2, 3, 4, ...) at
        _ROOM_ARROW_FRAME_SECONDS each, driven off self._glow_time so
        all four edges share one running clock.

        Unlike the player marker's ping-pong (_get_player_marker_frame_
        number), which holds each end frame for two consecutive steps
        (..., 1, 1, 2, ...) so a walk cycle doesn't visibly pop, this
        does NOT double up the end frames — frame 1 and the last frame
        are each shown for exactly one step before reversing, which is
        what actually produces the 1 2 3 4 3 2 1 2 3 4 sequence rather
        than 1 1 2 3 4 4 3 2 1 1."""
        if not animating:
            return 1
        n = self._room_arrow_frame_count
        if n <= 1:
            return 1
        cycle = 2 * (n - 1)
        if cycle <= 0:
            return 1
        step = int(self._glow_time / self._ROOM_ARROW_FRAME_SECONDS)
        pos = step % cycle
        index = pos if pos < n else cycle - pos   # 0-based, no end-hold
        return index + 1

    def _draw_room_arrows(self, surface):
        """Four directional "more zone map this way" pan-availability
        prompts, one flush against each edge of the screen: top and
        bottom centred horizontally, left and right centred vertically
        (see draw() — called right after _draw_map_section, so this
        sits above both map_overlay.png's bezel and the zone silhouette
        itself). Top/bottom are the SAME sprite, bottom just vertically
        flipped — same trick _draw_data_description_scroll_arrows uses
        so one strip serves an opposite-facing pair; left/right are
        rotated +/-90 degrees instead, since a horizontal strip's
        individual frames aren't symmetrical the way top/bottom's
        vertical mirror is.

        Each edge only animates while some of the zone's actual content
        (rooms) still falls outside the visible viewport on that side —
        i.e. that direction is genuinely covered by map_overlay.png's
        bezel rather than shown on screen — not merely while the camera
        hasn't yet hit its padded pan limit (self._map_camera_bounds
        pads a few cells past the zone's real edge so panning doesn't
        feel like it slams into a wall — see _MAP_CAMERA_MARGIN_CELLS —
        which would otherwise leave an arrow animating even once
        everything in that direction is already fully on screen).
        Compares the visible viewport's edges (derived the same way
        _draw_map_section computes avail_w/avail_h/scale) against the
        zone's real, unpadded content bounds
        (self._map_zone_grid_size — 0..grid_w, 0..grid_h) instead.
        Otherwise it sits on frame 1."""
        zone_size = self._map_zone_grid_size
        if zone_size is None:
            return
        grid_w, grid_h = zone_size

        # Same viewport geometry _draw_map_section derives every frame
        # (avail_w/avail_h from the screen size, scale from the fixed
        # zoom constant) — recomputed here rather than cached, since
        # both only ever depend on values already available on self.
        avail_w = self.screen_width - 80
        avail_h = self.screen_height - 100
        scale = self._MAP_ZOOM_SCALE
        half_w_cells = (avail_w / 2) / scale
        half_h_cells = (avail_h / 2) / scale

        gx, gy = self._map_camera_gx, self._map_camera_gy
        visible_left = gx - half_w_cells
        visible_right = gx + half_w_cells
        visible_top = gy - half_h_cells
        visible_bottom = gy + half_h_cells

        # Small epsilon so floating-point edges that merely touch the
        # content bound (rather than genuinely still hiding some of it)
        # don't read as "more to see" and animate forever.
        can_pan_left = visible_left > 0.01
        can_pan_right = visible_right < grid_w - 0.01
        can_pan_up = visible_top > 0.01
        can_pan_down = visible_bottom < grid_h - 0.01

        top = self._get_scaled_room_arrow_frame(
            self._get_room_arrow_frame_number(can_pan_up))
        bottom = self._get_scaled_room_arrow_frame(
            self._get_room_arrow_frame_number(can_pan_down))
        right = self._get_scaled_room_arrow_frame(
            self._get_room_arrow_frame_number(can_pan_right))
        left = self._get_scaled_room_arrow_frame(
            self._get_room_arrow_frame_number(can_pan_left))

        if top is not None:
            dest = top.get_rect(midtop=(self.screen_width // 2, 0 + 27))
            surface.blit(top, dest)

        if bottom is not None:
            flipped = pygame.transform.flip(bottom, False, True)
            dest = flipped.get_rect(midbottom=(self.screen_width // 2, self.screen_height - 26))
            surface.blit(flipped, dest)

        if right is not None:
            rotated = pygame.transform.rotate(right, -90)
            dest = rotated.get_rect(midright=(self.screen_width - 27, self.screen_height // 2))
            surface.blit(rotated, dest)

        if left is not None:
            rotated = pygame.transform.rotate(left, 90)
            dest = rotated.get_rect(midleft=(0 + 27, self.screen_height // 2 + 1))
            surface.blit(rotated, dest)

    def _get_design_v_scale(self):
        """Vertical scale factor used to blow map_overlay.png up to screen
        height (see _build_map_overlay_composite) — grid.png reuses the
        exact same factor so both assets read as the same "resolution"
        instead of the grid looking small next to the overlay."""
        if not self._map_overlay_load_attempted:
            self._load_map_overlay()
        if self._map_overlay_raw is None:
            return 1.0
        return self.screen_height / self._map_overlay_raw.get_height()

    def _get_tiled_grid(self):
        """Tiles grid.png across the full screen, first scaled up by the
        same factor as map_overlay.png (nearest-neighbour, so it stays
        sharp) so the two assets match in scale rather than the grid
        looking tiny next to the overlay art."""
        if not self._grid_load_attempted:
            self._load_grid()
        if self._grid_raw is None:
            return None

        raw = self._grid_raw
        v_scale = self._get_design_v_scale()
        cache_key = (raw.get_width(), raw.get_height(), self.screen_width, self.screen_height, v_scale)
        if self._grid_tiled is not None and self._grid_tiled[0] == cache_key:
            return self._grid_tiled[1]

        tile_w = max(1, round(raw.get_width() * v_scale))
        tile_h = max(1, round(raw.get_height() * v_scale))
        scaled_tile = pygame.transform.scale(raw, (tile_w, tile_h)) if v_scale != 1.0 else raw

        tiled = pygame.Surface((self.screen_width, self.screen_height), pygame.SRCALPHA)
        for ty in range(0, self.screen_height, tile_h):
            for tx in range(0, self.screen_width, tile_w):
                tiled.blit(scaled_tile, (tx, ty))
        self._grid_tiled = (cache_key, tiled)
        return tiled

    def _draw_grid_background(self, surface):
        """Scouter + World Map sections only — see draw()."""
        grid_img = self._get_tiled_grid()
        if grid_img is not None:
            surface.blit(grid_img, (0, 0))

    def _load_button_assets(self):
        """L.png / R.png / L_pressed.png / R_pressed.png — loaded together
        since they're always needed as a set."""
        self._button_load_attempted = True
        for name in ('L', 'R', 'L_pressed', 'R_pressed'):
            path = os.path.join('assets', 'ui', 'scouter', f'{name}.png')
            try:
                self._button_raw[name] = pygame.image.load(path).convert_alpha()
            except Exception as e:
                print(f'[scouter_menu] could not load {path}: {e}')
                self._button_raw[name] = None

    def _get_scaled_button(self, name):
        if not self._button_load_attempted:
            self._load_button_assets()
        raw = self._button_raw.get(name)
        if raw is None:
            return None

        v_scale = self._get_design_v_scale()
        cache_key = (raw.get_width(), raw.get_height(), v_scale)
        cached = self._button_scaled.get(name)
        if cached is not None and cached[0] == cache_key:
            return cached[1]

        w = max(1, round(raw.get_width() * v_scale))
        h = max(1, round(raw.get_height() * v_scale))
        scaled = pygame.transform.scale(raw, (w, h)) if v_scale != 1.0 else raw
        self._button_scaled[name] = (cache_key, scaled)
        return scaled

    def _l_button_available(self):
        """Whether Q currently does anything in the active section — see
        handle_input() for what each section's Q actually does."""
        if self.section == self.SECTION_MAP:
            return True                      # Q -> Scouter
        if self.section == self.SECTION_SCOUTER:
            return False                     # Q is unused here (Q already spent getting in)
        if self.section == self.SECTION_WORLD_MAP:
            return True                      # Q -> back to Map
        return False                         # SECTION_DATA: Q is unused here

    def _r_button_available(self):
        """Whether E currently does anything in the active section — see
        handle_input() for what each section's E actually does."""
        if self.section == self.SECTION_MAP:
            return True                      # E -> World Map
        if self.section == self.SECTION_SCOUTER:
            return True                      # E -> back to Map
        if self.section == self.SECTION_WORLD_MAP:
            return False                     # E is unused here (E already spent getting in)
        if self.section == self.SECTION_DATA:
            return False                     # R prompt hidden here (E still exits via handle_input())
        return False

    def _draw_button_prompts(self, surface):
        """L (top-left) shows L_pressed.png while Q is physically held,
        R (top-right) shows R_pressed.png while E is held — queried
        directly from pygame's live key state rather than tracked through
        handle_input(), since KEYUP isn't something handle_input() looks
        at (see its docstring) and this needs to reflect the key being
        held, not just the KEYDOWN moment.

        Either button is skipped entirely (not just un-pressed) when its
        key wouldn't do anything in the current section — see
        _l_button_available()/_r_button_available() — UNLESS that hold
        started back when the key *was* still available: a press of Q/E
        can itself be what just switched self.section (e.g. Q on MAP ->
        SCOUTER, where Q is no longer available), so by the time this
        draws, availability alone would make the button vanish without
        ever having shown its pressed frame. self._l_held_active /
        self._r_held_active track exactly that — "was this hold already
        in progress while available" — so a hold carries through that
        transition, but a *fresh* press of Q/E that starts only after the
        button's already gone (e.g. idly tapping Q while in SCOUTER, where
        it does nothing) does not resurrect the pressed sprite."""
        keys_held = pygame.key.get_pressed()

        l_held = keys_held[pygame.K_q]
        if self._l_button_available():
            self._l_held_active = l_held
            l_img = self._get_scaled_button('L_pressed' if l_held else 'L')
            if l_img is not None:
                surface.blit(l_img, (0, 0))
        elif l_held and self._l_held_active:
            l_img = self._get_scaled_button('L_pressed')
            if l_img is not None:
                surface.blit(l_img, (0, 0))
        else:
            self._l_held_active = False

        r_held = keys_held[pygame.K_e]
        if self._r_button_available():
            self._r_held_active = r_held
            r_img = self._get_scaled_button('R_pressed' if r_held else 'R')
            if r_img is not None:
                surface.blit(r_img, (self.screen_width - r_img.get_width(), 0))
        elif r_held and self._r_held_active:
            r_img = self._get_scaled_button('R_pressed')
            if r_img is not None:
                surface.blit(r_img, (self.screen_width - r_img.get_width(), 0))
        else:
            self._r_held_active = False

    def _build_map_overlay_composite(self):
        """3-slice horizontal stretch: since map_overlay.png is symmetrical
        left/right, scale it to fill the screen's *height* (aspect
        preserved, nearest-neighbour so it stays sharp), split the result
        down the middle into a left cap and a right cap, anchor those to
        the left/right edges of the screen untouched, and stretch a thin
        strip taken from right at the seam to fill whatever gap is left
        between them. Only that middle sliver gets stretched — the caps
        (where all the actual detail/symmetry lives) stay pixel-exact."""
        raw = self._map_overlay_raw
        raw_w, raw_h = raw.get_size()

        v_scale = self._get_design_v_scale()
        scaled_w = max(2, round(raw_w * v_scale))
        scaled_full = pygame.transform.scale(raw, (scaled_w, self.screen_height))

        half_w = scaled_full.get_width() // 2
        left_cap = scaled_full.subsurface(pygame.Rect(0, 0, half_w, self.screen_height)).copy()
        right_cap = scaled_full.subsurface(
            pygame.Rect(half_w, 0, scaled_full.get_width() - half_w, self.screen_height)).copy()

        gap = self.screen_width - left_cap.get_width() - right_cap.get_width()

        composite = pygame.Surface((self.screen_width, self.screen_height), pygame.SRCALPHA)
        composite.blit(left_cap, (0, 0))
        composite.blit(right_cap, (self.screen_width - right_cap.get_width(), 0))

        if gap > 0:
            # A couple of pixels either side of the seam, stretched to
            # fill the gap — same nearest-neighbour scale, so a flat/
            # repeating pattern at the seam stays clean rather than
            # smearing the way smoothscale would.
            _STRIP_PX = 4
            strip_x = max(0, half_w - _STRIP_PX)
            strip_w = min(_STRIP_PX * 2, scaled_full.get_width() - strip_x)
            seam_strip = scaled_full.subsurface(
                pygame.Rect(strip_x, 0, strip_w, self.screen_height)).copy()
            middle = pygame.transform.scale(seam_strip, (gap, self.screen_height))
            composite.blit(middle, (left_cap.get_width(), 0))
        elif gap < 0:
            # Screen narrower than the two caps combined — caps would
            # overlap. Caps were already blit'd edge-anchored above, which
            # is the least-bad option (still centred/symmetrical) rather
            # than shrinking them and losing sharpness.
            pass

        return composite

    def _get_scaled_map_overlay(self):
        if not self._map_overlay_load_attempted:
            self._load_map_overlay()
        if self._map_overlay_raw is None:
            return None

        raw = self._map_overlay_raw
        cache_key = (raw.get_width(), raw.get_height(), self.screen_width, self.screen_height)
        if self._map_overlay_scaled is not None and self._map_overlay_scaled[0] == cache_key:
            return self._map_overlay_scaled[1]

        composite = self._build_map_overlay_composite()
        self._map_overlay_scaled = (cache_key, composite)
        return composite

    def _draw_overlay_background(self, surface):
        """Shared background for all three sections — see draw()."""
        overlay_img = self._get_scaled_map_overlay()
        if overlay_img is None:
            return
        # Composite is already sized to the full screen (see
        # _build_map_overlay_composite), so no centring needed here.
        surface.blit(overlay_img, (0, 0))

    def _draw_map_section(self, surface, current_room, room_manager, player=None):
        """Zone map — auto-generated from each room's painted map_paint
        cells + room_transitions (see ui/scouter_room_map.py). Rendered
        once and cached (alongside origins/rooms_by_name) keyed on
        (zone group, current room name); only re-rendered when the player
        actually enters a different zone/room, not every frame — see the
        cache block below. render_zone_surface's pulse_t argument no
        longer drives anything (its own docstring: the room-bounds
        highlight it used to draw was removed), so there's nothing left
        that would need a per-frame re-render anyway.

        Drawn into a smaller, fixed-zoom viewport rather than auto-fit to
        show the whole zone at once — panned with WASD/arrows via
        self._map_camera_gx/gy (see _update_map_camera)."""
        if current_room is None or room_manager is None:
            msg = self._font.render('No room data available', True, (180, 180, 180))
            surface.blit(msg, (self.screen_width // 2 - msg.get_width() // 2,
                                self.screen_height // 2))
            return

        # Local import (matches this codebase's convention elsewhere, e.g.
        # room_editor.py's tool-module imports) rather than a top-of-file
        # import — avoids a circular-import NameError when ui/__init__.py
        # imports ScouterMenu before ui.scouter_room_map has a chance to
        # bind onto the partially-initialized ui package.
        from ui import scouter_room_map

        # Also keyed on id(current_room.map_paint), not just group/name:
        # room_editor never mutates map_paint in place — every live paint
        # stroke and every undo/redo reassigns it to a fresh list object
        # (see room_editor.py's paint-sync blocks) — so a genuine edit
        # changes this id even though the room name/group didn't. Same
        # identity-based busting _draw_world_map_section already does with
        # id(wm_surf); without it, painting more/fewer cells after the map
        # section has already been opened once for this room just kept
        # reusing the first render.
        cache_key = (
            getattr(current_room, 'group', None),
            current_room.name,
            id(getattr(current_room, 'map_paint', None)),
        )
        cache = getattr(self, '_map_section_cache', None)
        if cache is None or cache[0] != cache_key:
            origins, rooms_by_name = scouter_room_map.build_zone_layout(
                current_room, room_manager.get_room_by_name
            )

            # render_zone_surface's own docstring says its pulse_t argument
            # is accepted but no longer used to draw anything (the room-
            # bounds highlight it used to drive was removed) — so the
            # image it returns is identical every call until the zone/room
            # itself changes. Rendering isn't cheap: it's a real per-room
            # flood-fill pass (_nested_fill_depths) plus a large number of
            # individual Surface.set_at calls, which is fine once per
            # zone-entry but was previously being redone from scratch every
            # single frame the Map section was open (passing a fresh,
            # currently-inert pulse_t each time) — almost certainly the
            # actual cause of low/unsmooth FPS while panning, more than
            # the pan speed itself. Rendered once here and cached alongside
            # origins/rooms_by_name instead; the 0.0 below is just a
            # placeholder now that the argument does nothing.
            zone_surf = scouter_room_map.render_zone_surface(
                origins, rooms_by_name, current_room.name, 0.0
            )
            self._map_section_cache = (cache_key, origins, rooms_by_name, zone_surf)

            # New zone/room — recentre the camera on the current room and
            # recompute how far it's allowed to pan, rather than carrying
            # over a stale position/bounds from whatever zone was last
            # viewed.
            min_x, min_y, max_x, max_y, grid_w, grid_h = scouter_room_map._zone_bounds(
                origins, rooms_by_name
            )
            margin = self._MAP_CAMERA_MARGIN_CELLS
            self._map_camera_bounds = (-margin, -margin,
                                        grid_w + margin, grid_h + margin)
            # Unpadded content size (grid coords 0..grid_w, 0..grid_h) —
            # kept separate from _map_camera_bounds above, which is
            # padded by _MAP_CAMERA_MARGIN_CELLS so the camera can pan a
            # bit past the zone's edge. _draw_room_arrows() needs the
            # real, unpadded size: it should stop animating an edge the
            # moment every room in that direction is already on screen,
            # not only once the camera has hit the padded pan limit.
            self._map_zone_grid_size = (grid_w, grid_h)

            start_pos = scouter_room_map.world_to_grid(
                origins, rooms_by_name, current_room.name,
                current_room.width / 2, current_room.height / 2
            )
            if start_pos is None:
                start_pos = (grid_w / 2, grid_h / 2)
            self._map_camera_gx, self._map_camera_gy = start_pos
        else:
            _, origins, rooms_by_name, zone_surf = cache

        if not origins or not any(
            getattr(r, 'map_paint', None) for r in rooms_by_name.values()
        ):
            msg = self._font.render(
                'No map painted for this area yet — use the Map Paint tool in the room editor',
                True, (180, 180, 180))
            surface.blit(msg, (self.screen_width // 2 - msg.get_width() // 2,
                                self.screen_height // 2))
            return

        avail_w = self.screen_width - 80
        avail_h = self.screen_height - 100

        # Same on-screen area the map has always used (full avail_w/avail_h,
        # centred the same way as before) — only the fit-to-screen scaling
        # is gone, replaced with a fixed pixels-per-cell zoom. Panning (see
        # _update_map_camera) just slides which part of the zone lands in
        # that same area; the area itself never shrinks.
        viewport_x = self.screen_width // 2 - avail_w // 2
        viewport_y = 50

        scale = self._MAP_ZOOM_SCALE
        raw_w, raw_h = zone_surf.get_size()

        # Only scale the slice of zone_surf that can actually land inside
        # the viewport, instead of scaling the whole zone every frame and
        # relying on the clip below to throw most of it away — for a
        # large zone that was real, needless per-frame cost (pygame's
        # transform.scale is O(output pixels), and the output pixel count
        # scales with the WHOLE zone here, not just what's on screen).
        # Cropping first makes the cost proportional to the viewport size
        # instead, regardless of how big the zone is.
        visible_w_raw = avail_w / scale
        visible_h_raw = avail_h / scale
        raw_x0 = self._map_camera_gx - visible_w_raw / 2
        raw_y0 = self._map_camera_gy - visible_h_raw / 2

        # 1px margin on each side so rounding never leaves a 1px gap at
        # the viewport edge, then clamp to the surface's real bounds.
        crop_x0 = max(0, int(math.floor(raw_x0)) - 1)
        crop_y0 = max(0, int(math.floor(raw_y0)) - 1)
        crop_x1 = min(raw_w, int(math.ceil(raw_x0 + visible_w_raw)) + 1)
        crop_y1 = min(raw_h, int(math.ceil(raw_y0 + visible_h_raw)) + 1)
        crop_w = max(1, crop_x1 - crop_x0)
        crop_h = max(1, crop_y1 - crop_y0)

        cropped = zone_surf.subsurface(pygame.Rect(crop_x0, crop_y0, crop_w, crop_h))
        scaled = pygame.transform.scale(cropped, (crop_w * scale, crop_h * scale))

        # Position the (likely larger than the display area) scaled zone
        # image so the camera's grid-cell position lands dead centre in
        # that area, then clip to it — the same clip the old fit-to-screen
        # version implicitly got for free by never drawing anything larger
        # than avail_w/avail_h in the first place. Offset by crop_x0/y0
        # since `scaled` no longer starts at raw grid-coordinate 0 — this
        # is what `scaled` (which is crop-relative) needs to land in the
        # right place, but it is NOT what _draw_map_object_markers/
        # _draw_map_player_marker want: those position things as
        # origin + grid_x * scale using raw (uncropped) grid coordinates,
        # so folding crop_x0/y0 into the origin they use would double
        # count it — the offset would grow every time crop_x0/y0 changes,
        # i.e. every time the camera pans away from the zone's edge. Kept
        # as two separate origins so each caller gets the one that
        # actually matches the coordinates it's feeding in.
        ox = viewport_x + avail_w // 2 - (self._map_camera_gx - crop_x0) * scale
        oy = viewport_y + avail_h // 2 - (self._map_camera_gy - crop_y0) * scale
        marker_ox = viewport_x + avail_w // 2 - self._map_camera_gx * scale
        marker_oy = viewport_y + avail_h // 2 - self._map_camera_gy * scale

        prev_clip = surface.get_clip()
        surface.set_clip(pygame.Rect(viewport_x, viewport_y, avail_w, avail_h))

        surface.blit(scaled, (ox, oy))

        self._draw_map_object_markers(surface, origins, rooms_by_name,
                                       marker_ox, marker_oy, scale)

        if player is not None:
            self._draw_map_player_marker(
                surface, current_room, player, origins, rooms_by_name,
                marker_ox, marker_oy, scale
            )

        surface.set_clip(prev_clip)

    def _draw_map_object_markers(self, surface, origins, rooms_by_name,
                                  map_ox, map_oy, map_scale):
        """Flying pad / world map / save pad markers on the zone minimap —
        drawn before the player marker (see _draw_map_section) so the
        player's own marker stays the topmost, most legible thing on
        screen if it happens to land on the same spot as one of these.

        Walks every room already part of this zone's layout (not just
        current_room) so a pad/point in a neighbouring, already-visited
        room still shows up. Position conversion is the exact same
        world_to_grid → screen-space math _draw_map_player_marker uses for
        the player; icon size is the same fixed design_v_scale HUD size as
        player.png, not the map's own per-room zoom.
        """
        from ui import scouter_room_map

        _MARKERS = (
            ('flying_pads', 'flyingpad'),
            ('save_points', 'savepad'),
        )

        for room in rooms_by_name.values():
            for attr_name, icon_stem in _MARKERS:
                for obj in getattr(room, attr_name, None) or []:
                    self._draw_single_map_marker(
                        surface, origins, rooms_by_name, room.name,
                        obj.x, obj.y, icon_stem, map_ox, map_oy, map_scale
                    )

            for wmo in getattr(room, 'world_map_objects', None) or []:
                if getattr(wmo, 'variant', None) != 'world_map':
                    continue
                self._draw_single_map_marker(
                    surface, origins, rooms_by_name, room.name,
                    wmo.x, wmo.y, 'worldmap', map_ox, map_oy, map_scale
                )

    def _draw_single_map_marker(self, surface, origins, rooms_by_name, room_name,
                                 world_x, world_y, icon_stem,
                                 map_ox, map_oy, map_scale):
        from ui import scouter_room_map

        grid_pos = scouter_room_map.world_to_grid(
            origins, rooms_by_name, room_name, world_x, world_y
        )
        if grid_pos is None:
            return

        screen_x = map_ox + grid_pos[0] * map_scale
        screen_y = map_oy + grid_pos[1] * map_scale

        icon = self._get_scaled_map_marker_icon(icon_stem)
        if icon is None:
            # Art missing — same dot fallback the player marker uses when
            # player.png can't be loaded (see _draw_map_player_marker), so
            # the marker never just silently vanishes.
            surface.draw_circle((255, 255, 255), (int(screen_x), int(screen_y)), 3)
            surface.draw_circle((0, 0, 0), (int(screen_x), int(screen_y)), 3, 1)
            return

        surface.blit(icon, (screen_x - icon.get_width() / 2,
                             screen_y - icon.get_height() / 2))

    def _draw_map_player_marker(self, surface, current_room, player, origins,
                                 rooms_by_name, map_ox, map_oy, map_scale):
        """"You are here" marker (player.png), drawn as an overlay on top
        of the already-scaled zone map rather than baked into zone_surf
        itself.

        zone_surf is a 1-pixel-per-CELL_SIZE grid (see
        scouter_room_map.render_zone_surface) — fine for silhouette fill
        and the small entity/spawn "blobs" it already draws, but player.png
        is an 8x8 sprite; stamped directly into that grid at native size
        it would swallow several cells at once (a typical room is often
        only a handful of cells wider than that), completely out of
        proportion with the room it's meant to be pinpointing. So instead:
        find the player's position in the SAME grid space via
        scouter_room_map.world_to_grid, convert that to a screen position
        using the exact same map_ox/map_oy/map_scale the caller just used
        to place `scaled` on screen, and draw the actual sprite frame
        there at a fixed design_v_scale HUD size — same convention as the
        crosshair/scroll arrows — instead of at the map's own per-room
        zoom level.
        """
        from ui import scouter_room_map

        room_name = getattr(current_room, 'name', None)
        px = getattr(player, 'x', None)
        py = getattr(player, 'y', None)
        if room_name is None or px is None or py is None:
            return

        grid_pos = scouter_room_map.world_to_grid(
            origins, rooms_by_name, room_name, px, py
        )
        if grid_pos is None:
            # Player's current room isn't part of this cached layout —
            # e.g. the section was entered mid-transition. Fail soft and
            # just skip the marker for this frame rather than guessing.
            return

        screen_x = map_ox + grid_pos[0] * map_scale
        screen_y = map_oy + grid_pos[1] * map_scale

        frame = self._get_scaled_player_marker_frame(self._get_player_marker_frame_number())
        if frame is None:
            # player.png missing — fall back to the same style of dot the
            # zone map already draws for spawn points, so the marker never
            # just silently vanishes.
            surface.draw_circle((90, 255, 120), (int(screen_x), int(screen_y)), 3)
            surface.draw_circle((0, 0, 0), (int(screen_x), int(screen_y)), 3, 1)
            return

        surface.blit(frame, (screen_x - frame.get_width() / 2,
                              screen_y - frame.get_height() / 2))

    def _draw_scouter_entities(self, surface):
        """Player/NPC/enemy snapshots — drawn between the grid and
        map_overlay.png (see draw()), so the scan targets read as sitting
        on the grid, capped by the overlay's HUD bezel, rather than
        floating in front of it."""
        if not self._entities:
            msg = self._font.render('No entities on screen', True, (180, 180, 180))
            surface.blit(msg, (self.screen_width // 2 - msg.get_width() // 2,
                                self.screen_height // 2))
            return

        target = self._find_target_entity()
        # Flicker pulse for the target glow (see _get_glow_tinted_sprite) —
        # 0 -> 0.5 -> 0 tint strength, one full cycle every
        # _GLOW_PULSE_PERIOD_SECONDS (see the constants above).
        omega = 2 * math.pi / self._GLOW_PULSE_PERIOD_SECONDS
        mid = (self._GLOW_PULSE_MIN + self._GLOW_PULSE_MAX) / 2
        amp = (self._GLOW_PULSE_MAX - self._GLOW_PULSE_MIN) / 2
        pulse = mid + amp * math.sin(self._glow_time * omega)

        for e in self._entities:
            color = (255, 60, 60) if e['kind'] == 'enemy' else \
                (60, 220, 255) if e['kind'] == 'player' else (255, 255, 255)

            sprite = e.get('sprite')
            shadow = e.get('shadow_sprite')
            if e is target:
                # Whoever the crosshair is currently centred over glows
                # green — same idea as hurt_tint (see player.py's draw()):
                # an additive colour pass over the sprite's own pixels
                # rather than a shape drawn behind/around it. See
                # _get_glow_tinted_sprite(). Only the body gets tinted —
                # the shadow (blitted separately below, untouched) stays
                # its normal colour, since a green-tinted shadow blob just
                # reads as a smudge rather than part of the glow. The
                # no-sprite fallback dot below has no base pixels to add
                # onto, so it pulses by directly scaling a bright green
                # instead.
                if sprite is not None:
                    sprite = self._get_glow_tinted_sprite(e, sprite, pulse)
                else:
                    color = tuple(int(c * pulse) for c in (60, 255, 120))

            if sprite is not None:
                # Drawn at its native captured size, not rescaled — it was
                # snapshotted straight out of the entity's own draw() call
                # (see _capture_entity_sprite), which already used the
                # real camera/render_scale, so it's already the exact size
                # and position the entity appeared at in the frozen frame.
                # Scaling it further here just distorted proportions and
                # blew up small entities.
                ox = e['x'] - sprite.get_width() // 2
                oy = e['y'] - sprite.get_height() // 2
                # Shadow first, untinted, at the same offset — shadow and
                # body were cropped to one shared coordinate frame at
                # snapshot time (see _capture_entity_sprite), so this
                # offset is correct for both.
                if shadow is not None:
                    surface.blit(shadow, (ox, oy))
                surface.blit(sprite, (ox, oy))
            else:
                # Fail soft if the entity didn't yield a usable sprite at
                # snapshot time (see _capture_entity_sprite) — same
                # missing-asset tolerance as the rest of this file. Still
                # pulses if targeted, via the color swap above.
                surface.draw_circle(color, (e['x'], e['y']), 6)

    def _get_glow_tinted_sprite(self, entity, sprite, strength):
        """Green-tinted copy of a captured entity sprite, cached on the
        entity dict itself (pygame.Surface objects don't support arbitrary
        attribute assignment, so the cache can't live on the sprite) keyed
        by the rounded pulse strength — there are only a handful of
        distinct strengths across a pulse cycle at any reasonable frame
        rate, so this avoids re-tinting from scratch every frame. Uses the
        same trick hurt_tint-style effects rely on: Surface.fill(color,
        special_flags=BLEND_RGBA_ADD) with alpha delta 0 adds colour to
        every pixel's RGB but leaves alpha untouched — so fully
        transparent background pixels stay invisible and only the
        sprite's own opaque pixels pick up the added colour, instead of a
        shape being drawn on top of or behind the sprite."""
        key = round(strength, 2)
        cache = entity.get('_glow_cache')
        if cache is not None and cache[0] == key:
            return cache[1]

        tinted = sprite.copy()
        r, g, b = self._GLOW_TINT_COLOR
        tinted.fill((int(r * strength), int(g * strength), int(b * strength), 0),
                    special_flags=pygame.BLEND_RGBA_ADD)
        entity['_glow_cache'] = (key, tinted)
        return tinted

    def _draw_scouter_crosshair(self, surface):
        """The reticle itself — drawn after entities but still before
        map_overlay.png (see draw()), so it sits on top of whatever it's
        aimed at while still being capped by the overlay's HUD bezel."""
        cx, cy = int(self._crosshair_x), int(self._crosshair_y)
        frame_img = self._get_scaled_crosshair_frame(self._crosshair_frame)
        if frame_img is not None:
            surface.blit(frame_img, (cx - frame_img.get_width() // 2,
                                      cy - frame_img.get_height() // 2))
        else:
            # Fallback to a drawn reticle if crosshair.png is missing, so
            # Scouter stays usable without the asset — same missing-asset
            # tolerance as map_overlay.png/grid.png elsewhere in this file.
            r = 14
            col = (255, 220, 60)
            surface.draw_line(col, (cx - r, cy), (cx - r + 6, cy), 2)
            surface.draw_line(col, (cx + r, cy), (cx + r - 6, cy), 2)
            surface.draw_line(col, (cx, cy - r), (cx, cy - r + 6), 2)
            surface.draw_line(col, (cx, cy + r), (cx, cy + r - 6), 2)
            surface.draw_circle(col, (cx, cy), r, 1)

    def _resolve_world_map_attachment(self, current_room, room_manager, world_map_lookup):
        """BFS every room reachable from current_room via room_transitions
        — scouter_room_map.build_zone_layout, the exact same traversal
        the Map section uses to lay a zone out — and ask world_map_lookup
        about each visited room in turn. A room counts as "attached to a
        world map" if it itself is a pinned location OR it's connected,
        directly or through any chain of further connected rooms, to one
        that is — "every room that transitions with it, and every room
        that transitions with those, and so on" per spec, rather than
        only ever checking current_room in isolation (the bug this
        exists to fix: a room one or more doorways away from the actual
        pinned room used to report as unattached).

        Returns (map_name, locations, connected_room_names):
        map_name/locations are None/[] if nothing anywhere in the
        reachable graph is attached to any map. connected_room_names is
        the full BFS room-name set regardless of whether a map was
        found — _draw_world_map_section uses it to decide which pin(s)
        should play the full idle animation rather than sit static, so
        that also becomes "any room in the connected graph", not just
        an exact match on current_room.name."""
        from ui import scouter_room_map

        try:
            _origins, visited_rooms = scouter_room_map.build_zone_layout(
                current_room, room_manager.get_room_by_name
            )
        except Exception as e:
            print(f'[scouter_menu] world map attachment BFS failed: {e}')
            name = getattr(current_room, 'name', None)
            return None, [], ({name} if name else set())

        connected_room_names = set(visited_rooms.keys())

        for room_name in visited_rooms:
            hit = world_map_lookup(room_name)
            if hit:
                map_name, locations = hit
                return map_name, locations, connected_room_names

        return None, [], connected_room_names

    def _draw_world_map_section(self, surface, current_room, active_world_map_name,
                                 wm_locations, world_map_surface_provider,
                                 connected_room_names=None):
        """No panning and no room_arrow.png prompts here, unlike the Map
        section — the whole map is always shown fit-to the overlay's
        inner content window at once (see the scale computation below),
        drawn *behind* map_overlay.png so the bezel frames it and the
        silhouette never paints over the overlay art.

        Location pins are player.png, same sprite/frame-strip as the Map
        section's "you are here" marker (see _get_scaled_player_marker_
        frame) — every pin sits on its static idle pose (frame 2) except
        the one(s) whose location is connected to current_room, which
        play the marker's full idle animation instead.

        connected_room_names: the full set of rooms transitively
        reachable from current_room (see _resolve_world_map_attachment,
        which builds this via the same BFS the Map section already uses)
        — a pin animates if its loc['room'] is anywhere in that set, not
        only if it's an exact match on current_room.name. Optional:
        falls back to exact-match-only if the caller didn't resolve
        attachment transitively (draw() only does so when a
        world_map_lookup callable was provided)."""
        if not active_world_map_name:
            msg = self._font.render('This room is not attached to a world map', True, (180, 180, 180))
            surface.blit(msg, (self.screen_width // 2 - msg.get_width() // 2,
                                self.screen_height // 2))
            return

        wm_surf = world_map_surface_provider(active_world_map_name)
        if wm_surf is None:
            msg = self._font.render(f'Could not load world map "{active_world_map_name}"',
                                     True, (200, 100, 100))
            surface.blit(msg, (self.screen_width // 2 - msg.get_width() // 2,
                                self.screen_height // 2))
            return

        # Content window inside map_overlay.png's bezel — same idea as the
        # zone Map section's viewport. The map is drawn *behind* the overlay
        # (see draw()), so edges under the bezel are covered; these insets
        # keep the silhouette centred in the transparent inner frame and
        # stop it from reaching the bezel art in the first place.
        # Insets track design scale so they stay proportional on any
        # resolution (overlay is height-fitted via _get_design_v_scale).
        v_scale = self._get_design_v_scale()
        # Raw overlay bezel thickness roughly ~24–32 px on the source art;
        # scaled + a little extra breathing room so coastline never kisses
        # the frame.
        inset_x = max(40, int(round(32 * v_scale)))
        inset_y = max(40, int(round(36 * v_scale)))
        viewport_x = inset_x
        viewport_y = inset_y
        avail_w = max(1, self.screen_width - inset_x * 2)
        avail_h = max(1, self.screen_height - inset_y * 2)

        # 1.0 = fill the overlay content window; 1.5 = 50% larger (zoomed).
        # No set_clip: at zoom > 1 the map extends under the bezel, and
        # map_overlay.png (drawn after this in draw()) covers those edges
        # instead of a hard clip cutting the coastline mid-line.
        WORLD_MAP_ZOOM = 1.38
        fit_scale = min(avail_w / wm_surf.get_width(), avail_h / wm_surf.get_height())
        scale = fit_scale * WORLD_MAP_ZOOM
        draw_w = max(1, int(wm_surf.get_width() * scale))
        draw_h = max(1, int(wm_surf.get_height() * scale))

        # world_map_surface_provider's own Surface may well be large (a
        # whole authored world map), and scale is real per-pixel work —
        # cache the scaled result instead of redoing it every frame.
        # Keyed on the provider's Surface identity (not just the map
        # name) so a genuine map edit/reload — a new Surface object —
        # still invalidates the cache; if the provider hands back the
        # same object every call (the expected/cheap case), this only
        # ever runs once per map per screen size.
        cache_key = (active_world_map_name, id(wm_surf), draw_w, draw_h)
        cached = self._wm_scaled_cache
        if cached is not None and cached[0] == cache_key:
            scaled = cached[1]
        else:
            scaled = (pygame.transform.scale(wm_surf, (draw_w, draw_h))
                       if scale != 1.0 else wm_surf)
            self._wm_scaled_cache = (cache_key, scaled)

        # Centre inside the overlay content window (not the full screen).
        ox = viewport_x + (avail_w - draw_w) // 2
        oy = viewport_y + (avail_h - draw_h) // 2

        surface.blit(scaled, (ox, oy))

        current_room_name = getattr(current_room, 'name', None)
        if connected_room_names is not None:
            highlight_room_names = connected_room_names
        else:
            highlight_room_names = {current_room_name} if current_room_name else set()

        # Location pins are stored in *map-tile* coordinates (0..MAP_TILE_W-1,
        # 0..MAP_TILE_H-1) by the world map editor — same space as
        # scouter_paint and the flying scene's `_loc['x'] * (tex_w / 362)`.
        # The old `loc['x'] * scale` treated them as pixels on wm_surf, which
        # after the surface is upscaled to world_map.png×2 shoved every pin
        # into the top-left corner. Map tile → fraction of the drawn map
        # instead, and centre on the tile (+0.5) so the sprite sits on the
        # cell the designer clicked rather than its top-left corner.
        _MAP_TILE_W = 362
        _MAP_TILE_H = 263
        for loc in (wm_locations or []):
            tx = loc.get('x', 0)
            ty = loc.get('y', 0)
            lx = ox + (tx + 0.5) / _MAP_TILE_W * draw_w
            ly = oy + (ty + 0.5) / _MAP_TILE_H * draw_h
            is_current = loc.get('room') in highlight_room_names

            pin = self._get_scaled_player_marker_frame(
                self._get_player_marker_frame_number(animating=is_current))
            if pin is None:
                # player.png missing — same dot fallback the Map section's
                # own player marker uses, so a pin never just vanishes.
                color = (255, 220, 60) if is_current else (255, 255, 255)
                radius = 6 if is_current else 4
                surface.draw_circle(color, (int(lx), int(ly)), radius)
                surface.draw_circle((0, 0, 0), (int(lx), int(ly)), radius, 1)
            else:
                surface.blit(pin, (lx - pin.get_width() / 2, ly - pin.get_height() / 2))

    # ----------------------------------------------------------------
    # Scouter Data section (see SECTION_DATA / _enter_data_section)
    # ----------------------------------------------------------------
    def _load_scouter_background(self):
        """assets/ui/scouter/scouter_background.png — same lazy/cached
        convention as map_overlay.png/grid.png (see _load_map_overlay)."""
        self._scouter_bg_load_attempted = True
        path = os.path.join('assets', 'ui', 'scouter', 'scouter_background.png')
        try:
            self._scouter_bg_raw = pygame.image.load(path).convert_alpha()
        except Exception as e:
            print(f'[scouter_menu] could not load {path}: {e}')
            self._scouter_bg_raw = None

    def _get_data_bg_layout(self):
        """Integer-scale + centred layout for scouter_background.png.

        Returns (scale, ox, oy, scaled_w, scaled_h) or None if the art
        hasn't loaded. scale is the largest integer N such that
        N * raw_w <= screen_w and N * raw_h <= screen_h (at least 1),
        so every source pixel maps to an identical N×N block of screen
        pixels — same crisp-pixel philosophy as PauseMenu's font_scale
        tiling. ox/oy is where the scaled panel's top-left lands on
        screen (centred)."""
        if not self._scouter_bg_load_attempted:
            self._load_scouter_background()
        raw = self._scouter_bg_raw
        if raw is None:
            return None
        raw_w, raw_h = raw.get_size()
        if raw_w <= 0 or raw_h <= 0:
            return None
        scale = max(1, min(self.screen_width // raw_w, self.screen_height // raw_h))
        scaled_w = raw_w * scale
        scaled_h = raw_h * scale
        ox = (self.screen_width - scaled_w) // 2
        oy = (self.screen_height - scaled_h) // 2
        return scale, ox, oy, scaled_w, scaled_h

    def _get_scaled_scouter_background(self):
        """Integer-scaled scouter_background.png, cached. The surface is
        ONLY the panel itself (not a full-screen canvas) — the caller
        fills the letterbox and centres this surface. See
        _get_data_bg_layout() / _draw_data_section()."""
        if not self._scouter_bg_load_attempted:
            self._load_scouter_background()
        raw = self._scouter_bg_raw
        if raw is None:
            return None

        layout = self._get_data_bg_layout()
        if layout is None:
            return None
        scale, _ox, _oy, scaled_w, scaled_h = layout
        cache_key = (raw.get_width(), raw.get_height(), scale)
        if self._scouter_bg_scaled is not None and self._scouter_bg_scaled[0] == cache_key:
            return self._scouter_bg_scaled[1]

        if scale == 1:
            scaled = raw
        else:
            scaled = pygame.transform.scale(raw, (scaled_w, scaled_h))
        self._scouter_bg_scaled = (cache_key, scaled)
        return scaled

    def _get_data_grid_box(self):
        """On-screen rect of the character-viewer area — authored in
        scouter_background.png RAW pixels as _DATA_VIEWER_RAW_RECT and
        mapped through the same integer-scale + centre layout as the
        panel itself (see _bg_raw_rect_to_screen). Returns an empty
        Rect if the background art hasn't loaded."""
        x0, y0, x1, y1 = self._DATA_VIEWER_RAW_RECT
        return self._bg_raw_rect_to_screen(x0, y0, x1 + 1, y1 + 1)

    def _draw_data_viewer(self, surface):
        """Draws the rotating idle-viewer sprite (see _update_data_viewer
        / _capture_data_viewer_frame) centred in the tiled grid gap —
        see _get_data_grid_box(). Separate from _draw_data_portrait(),
        which still draws the static portrait art in its own gold-framed
        box; this is the live scanned character standing in the grid,
        not the portrait. No-ops (draws nothing) if there's no viewer
        sprite for the current entity or no grid box to draw into.

        Scales the captured frame by _DATA_VIEWER_SCALE — see that
        constant to resize the character (1.0 = native, 2.0 = double,
        1.5 = one and a half, etc). Not clamped to the grid box's own
        width/height, so a large enough scale will deliberately overflow
        past the box edges — only the entity CENTRE is taken from the
        box. Body and shadow are both placed relative to that centre
        using the offsets returned by their respective captures, so the
        shadow lands with the same relative offset it has in-game
        (LayerManager._draw_shadow relative to entity x/y) rather than
        a feet-anchored re-guess."""
        box = self._get_data_grid_box()
        if box.width <= 0 or box.height <= 0:
            return
        captured = self._capture_data_viewer_frame()
        if captured is None:
            return
        frame, body_ox, body_oy = captured

        fw, fh = frame.get_width(), frame.get_height()
        if fw <= 0 or fh <= 0:
            return
        # Cancel out AnimatedSprite.draw()'s real-RENDER_SCALE sizing (baked
        # into `frame`'s pixel dimensions) and reapply a fixed RENDER_SCALE
        # of 4 instead, so the viewer's apparent size never changes even if
        # config.settings.RENDER_SCALE does — see _DATA_VIEWER_FIXED_RENDER_SCALE.
        render_scale_compensation = self._DATA_VIEWER_FIXED_RENDER_SCALE / max(1, RENDER_SCALE)
        scale = max(0.01, self._DATA_VIEWER_SCALE * render_scale_compensation)
        scaled_w = max(1, round(fw * scale))
        scaled_h = max(1, round(fh * scale))
        # Place the tight body crop relative to the entity centre
        # (box.center), preserving the offset the uncropped frame had
        # when AnimatedSprite.draw centred it — matches how the shadow
        # is placed from its own capture-time offset.
        dest_x = box.centerx + round(body_ox * scale)
        dest_y = box.centery + round(body_oy * scale)

        self._draw_data_viewer_shadow(surface, box.centerx, box.centery, scaled_w, scale)

        scaled = pygame.transform.scale(frame, (scaled_w, scaled_h))
        surface.blit(scaled, (dest_x, dest_y))

    def _draw_data_viewer_shadow(self, surface, center_x, center_y, sprite_w, scale):
        """Draws self._data_viewer_shadow — the SAME shadow_sprite
        Scouter itself already draws for this entity, captured via
        LayerManager._draw_shadow() back in build_scouter_snapshot() and
        reused here as-is (see _enter_data_section) rather than
        redrawn — a shadow doesn't change shape as the character turns
        to face a different direction, so it can just sit under
        whichever facing the viewer is currently on.

        Scaled by the SAME `scale` multiplier _draw_data_viewer() used
        for the character, since the shadow was captured at the same
        real RENDER_SCALE pixel size the viewer sprite is (both
        ultimately come from AnimatedSprite.draw()'s own scaling), so
        this keeps the two proportional to each other. Positioned via
        the capture-time shadow_ox/oy relative to the entity centre —
        the same origin LayerManager._draw_shadow used in-game — so the
        shadow lands with the exact in-game offset instead of being
        re-derived as "centred on the body, bottom-aligned to its feet"
        (which ignored how far the real shadow extends past the sprite
        bounds and how it sits relative to the entity centre).

        Falls back to a plain procedural ellipse only if this entity had
        no real shadow captured (e.g. it was off-screen or had no
        drawable shadow at snapshot time) — not a pixel-match for the
        game's own shadow art, just enough that he doesn't float."""
        shadow = self._data_viewer_shadow
        if shadow is not None and shadow.get_width() > 0 and shadow.get_height() > 0:
            sw = max(1, round(shadow.get_width() * scale))
            sh = max(1, round(shadow.get_height() * scale))
            scaled_shadow = pygame.transform.scale(shadow, (sw, sh))
            dest_x = center_x + round(self._data_viewer_shadow_ox * scale)
            dest_y = center_y + round(self._data_viewer_shadow_oy * scale)
            surface.blit(scaled_shadow, (dest_x, dest_y))
            return

        # Fallback: approximate a ground shadow under the body. Without
        # a real capture we don't know the in-game offset, so sit a
        # flat ellipse near the bottom of the body bounds.
        w = max(2, int(sprite_w * self._DATA_VIEWER_SHADOW_WIDTH_RATIO))
        h = self._DATA_VIEWER_SHADOW_HEIGHT_PX
        fallback = pygame.Surface((w, h), pygame.SRCALPHA)
        pygame.draw.ellipse(fallback, self._DATA_VIEWER_SHADOW_COLOR, fallback.get_rect())
        feet_y = center_y + round(sprite_w * 0.35)
        surface.blit(fallback, (center_x - w // 2, feet_y - h // 2))

    def _bg_raw_rect_to_screen(self, x0, y0, x1, y1):
        """Maps a rectangle authored in scouter_background.png's RAW pixel
        space to final on-screen coordinates via the integer-scale +
        centred layout in _get_data_bg_layout(). Uniform scale on both
        axes (no independent x/y stretch, no 9-slice gap shift), so a
        raw pixel lands on an exact multiple of `scale` screen pixels.

        Takes both corners (x0,y0)/(x1,y1) rather than a position+size —
        each corner is mapped independently, then width/height are
        derived from the two results, so edges stay pinned to the
        integer pixel grid."""
        layout = self._get_data_bg_layout()
        if layout is None:
            return pygame.Rect(0, 0, 0, 0)
        scale, ox, oy, _sw, _sh = layout

        sx0 = ox + x0 * scale
        sx1 = ox + x1 * scale
        sy0 = oy + y0 * scale
        sy1 = oy + y1 * scale
        return pygame.Rect(sx0, sy0, sx1 - sx0, sy1 - sy0)

    def _draw_data_section(self, surface):
        # Letterbox / pillarbox fill first, then the integer-scaled panel
        # centred on top — same "panel floats on a solid field" look as
        # PauseMenu, rather than stretching the art to every screen edge.
        surface.fill(self._DATA_BG_LETTERBOX_COLOR)
        bg = self._get_scaled_scouter_background()
        layout = self._get_data_bg_layout()
        if bg is not None and layout is not None:
            _scale, ox, oy, _sw, _sh = layout
            surface.blit(bg, (ox, oy))
        else:
            # Missing-asset tolerance, same as every other Scouter asset.
            msg = self._font.render('assets/ui/scouter/scouter_background.png not found',
                                     True, (200, 100, 100))
            surface.blit(msg, (self.screen_width // 2 - msg.get_width() // 2,
                                self.screen_height // 2))

        obj = self._data_entity
        if obj is None:
            return

        self._draw_data_stats(surface, obj)
        self._draw_data_name(surface, obj)
        self._draw_data_viewer(surface)
        self._draw_data_portrait(surface, obj)
        self._draw_data_description(surface, obj)

    def _draw_data_fade(self, surface):
        """Fade in from black over _DATA_FADE_SECONDS (see update(),
        which advances self._data_fade). Drawn last, over the chrome and
        button prompts too, so the whole section reads as materialising
        out of black rather than just its background layer fading while
        the HUD text pops in instantly."""
        if self._data_fade >= 1.0:
            return
        alpha = int(255 * (1.0 - self._data_fade))
        veil = pygame.Surface((self.screen_width, self.screen_height))
        veil.set_alpha(alpha)
        veil.fill((0, 0, 0))
        surface.blit(veil, (0, 0))

    def _lookup_entity_value(self, obj, candidates):
        """First match among candidates, checked against obj.stats (if
        it's a dict) and then against obj's own attributes directly —
        covers either convention an entity might expose its stats under
        without this file needing to know which one the rest of the
        project actually settled on. Returns None if nothing matched."""
        stats = getattr(obj, 'stats', None)
        if isinstance(stats, dict):
            for c in candidates:
                if c in stats:
                    return stats[c]
        for c in candidates:
            if hasattr(obj, c):
                return getattr(obj, c)
        return None

    def _get_data_stats(self, obj):
        """Resolves the four Scouter Data stats as (label, display_string)
        pairs. Field names follow the "stats" schema used by
        character_creator.py / entity_creator.py's DEFAULT_CONFIGs — but
        that schema is NOT the same shape for player vs. enemy, so the
        candidate order below matters:

        Player: a single "power" field (there's no "strength" field at
        all) doubles as STR, "ki_power" is POW, "vitality" is END.

        Enemy (post STR/POW-split — see entity_creator.py's load_config()
        migration comment): "strength" is STR, "power" is POW, "defense"
        is END. Enemies have no "ki_power"/"vitality" fields.

        Because "power" means STR for a player but POW for an enemy,
        "strength" has to be checked BEFORE "power" for str_val — that
        way an enemy's real strength field wins, and "power" only gets
        used as the STR fallback for player objects that don't have a
        "strength" field to begin with. pow_val checks "ki_power" first
        (the player field) and only falls back to "power" once str_val
        has already had first claim on it for enemies, so the two never
        collide and pull the same field into both stats. HP shows
        max_hp only (not current/max) — falls back to current hp if
        max_hp isn't set on the entity at all."""
        max_hp = self._lookup_entity_value(obj, ('max_hp',))
        cur_hp = self._lookup_entity_value(obj, ('hp', 'current_hp'))
        if max_hp is not None:
            hp_text = str(int(max_hp))
        elif cur_hp is not None:
            hp_text = str(int(cur_hp))
        else:
            hp_text = '—'

        str_val = self._lookup_entity_value(obj, ('strength', 'power', 'str'))
        pow_val = self._lookup_entity_value(obj, ('ki_power', 'power', 'pow'))
        end_val = self._lookup_entity_value(obj, ('vitality', 'end', 'endurance', 'defense'))

        def fmt(v):
            if v is None:
                return '—'
            return str(int(v)) if isinstance(v, (int, float)) else str(v)

        return [('HP', hp_text), ('STR', fmt(str_val)), ('POW', fmt(pow_val)), ('END', fmt(end_val))]

    def _get_data_stat_label_sprite(self, label):
        """Tinted scouter_stats word render (assembled letter by letter
        — see _LetterSpriteFont.render) for one stat row, or None if
        any letter's glyph failed to load (caller falls back to plain
        text)."""
        sprite = self._data_stat_word_font.render(label, gap=self._DATA_STAT_GLYPH_GAP)
        if sprite is None:
            return None
        tinted = sprite.copy()
        color = self._DATA_STAT_COLORS.get(label, (255, 255, 255))
        tinted.fill(color, special_flags=pygame.BLEND_RGBA_MULT)
        scale = self._DATA_STAT_LABEL_SCALE
        if scale != 1.0:
            w = max(1, round(tinted.get_width() * scale))
            h = max(1, round(tinted.get_height() * scale))
            tinted = pygame.transform.scale(tinted, (w, h))
        return tinted

    def _scaled_digit_glyph(self, ch):
        """Digit glyph scaled by _DATA_STAT_DIGIT_SCALE, cached per
        character so the transform.scale call only ever runs once per
        digit rather than every frame every row is drawn."""
        cached = self._data_stat_digit_scaled_cache.get(ch)
        if cached is not None:
            return cached
        glyph = self._data_stat_digit_font.glyphs.get(ch)
        if glyph is None:
            return None
        scale = self._DATA_STAT_DIGIT_SCALE
        if scale == 1.0:
            scaled = glyph
        else:
            w = max(1, round(glyph.get_width() * scale))
            h = max(1, round(glyph.get_height() * scale))
            scaled = pygame.transform.scale(glyph, (w, h))
        self._data_stat_digit_scaled_cache[ch] = scaled
        return scaled

    def _scaled_digit_max_width(self):
        return max(1, round(self._data_stat_digit_font.max_width() * self._DATA_STAT_DIGIT_SCALE))

    def _scaled_digit_height(self):
        return round(self._data_stat_digit_font.height() * self._DATA_STAT_DIGIT_SCALE)

    def _scaled_digit_gap(self):
        """Gap between adjacent digit slots, in final screen px. Uses
        the same native _DATA_STAT_GLYPH_GAP as the label's letter
        spacing (see _get_data_stat_label_sprite), scaled by the
        digit's own scale factor the same way the label's gap gets
        scaled along with the rest of its word sprite — so the number
        column reads with the same letter-to-letter rhythm as the word
        beside it rather than a mismatched gap."""
        return max(1, round(self._DATA_STAT_GLYPH_GAP * self._DATA_STAT_DIGIT_SCALE))

    def _draw_data_stat_number(self, surface, text, right_x, center_y):
        """Right-aligns `text` against right_x, one character per fixed-
        width slot, filled right to left — see _scaled_digit_gap().
        Because every row calls this with the same right_x and the same
        slot font, a row's 1s digit (the last character) always lands in
        the same rightmost slot as every other row's 1s digit, the 10s
        digit in the slot left of that, and so on — the numbers read as
        one aligned column no matter how many digits each row has."""
        digit_gap = self._scaled_digit_gap()
        slot_w = self._scaled_digit_max_width() + digit_gap
        row_h = self._scaled_digit_height()
        if slot_w <= digit_gap or row_h == 0:
            # dmg_font sprites missing entirely — fall back to the plain
            # pygame font rather than drawing nothing.
            surf = self._data_stat_value_font.render(text, True, (255, 255, 255))
            surface.blit(surf, (right_x - surf.get_width(), center_y - surf.get_height() // 2))
            return

        cx = right_x
        for ch in reversed(text):
            cx -= slot_w
            glyph = self._scaled_digit_glyph(ch)
            if glyph is not None:
                gx = cx + (slot_w - glyph.get_width()) // 2
                gy = center_y - row_h // 2 + (row_h - glyph.get_height())
                surface.blit(glyph, (gx, gy))
            elif ch != ' ':
                # Non-digit characters (e.g. the '/' in a "cur/max" HP
                # reading, or '—' for an unknown stat) — no sprite for
                # these in dmg_font, so drop back to the plain font,
                # still centred in its slot to keep the column steady.
                surf = self._data_stat_value_font.render(ch, True, (255, 255, 255))
                surface.blit(surf, (cx + (slot_w - surf.get_width()) // 2,
                                     center_y - surf.get_height() // 2))

    def _draw_data_stats(self, surface, obj):
        """Stat readout — four rows stacked top to bottom, HP/STR/POW/END,
        each a colored word sprite on the left and its number right-
        aligned on the right (see _get_data_stats(),
        _get_data_stat_label_sprite(), _draw_data_stat_number()).

        Origin is the integer-scaled panel's top-left (see
        _get_data_bg_layout), with _DATA_STAT_RAW_LEFT/_TOP expressed in
        the background art's own pixel space so the column tracks the
        panel rather than a fixed full-screen coordinate."""
        layout = self._get_data_bg_layout()
        if layout is None:
            return
        scale, ox, oy, _sw, _sh = layout
        stat_left = ox + self._DATA_STAT_RAW_LEFT * scale
        stat_top = oy + self._DATA_STAT_RAW_TOP * scale

        stats = self._get_data_stats(obj)

        # Label column width is driven by the widest label sprite (or
        # its text fallback) actually in use, so the number column
        # starts at a consistent x regardless of which label happens to
        # be widest — rather than a guessed fixed offset.
        label_sprites = []
        label_w = 0
        row_h = 0
        for label, _value in stats:
            sprite = self._get_data_stat_label_sprite(label)
            if sprite is None:
                sprite = self._data_stat_label_font.render(
                    label, True, self._DATA_STAT_COLORS.get(label, (255, 255, 255)))
            label_sprites.append(sprite)
            label_w = max(label_w, sprite.get_width())
            row_h = max(row_h, sprite.get_height())
        row_h = max(row_h, self._scaled_digit_height())

        number_right_x = stat_left + label_w + self._DATA_STAT_LABEL_NUMBER_GAP + (
            self._scaled_digit_max_width() + self._scaled_digit_gap()
        ) * 3  # reserves space for a 3-digit (hundreds/tens/1s) number by
                # default; a 4th+ digit just extends further left/right of
                # this — see _draw_data_stat_number's slot math.

        y = stat_top
        for (label, value), label_sprite in zip(stats, label_sprites):
            row_center_y = y + row_h // 2
            surface.blit(label_sprite, (stat_left,
                                         row_center_y - label_sprite.get_height() // 2))
            self._draw_data_stat_number(surface, value, number_right_x, row_center_y)
            y += row_h + self._DATA_STAT_ROW_GAP

    def _get_entity_display_name(self, obj):
        name = getattr(obj, 'display_name', None) or getattr(obj, 'name', None)
        if not name:
            # Last-resort fallback so an entity with neither field still
            # shows *something* rather than a blank name plate.
            name = type(obj).__name__.replace('_', ' ').title()
        return str(name)

    def _render_data_name_word(self, word, gap):
        """Builds one word's glyph row left-to-right using the
        scouter_stats letter sprites for letters and the
        assets/ui/fonts/numbers sprites for digits, falling back
        PER-CHARACTER (not per-word) to _data_name_font for anything
        neither glyph set covers — apostrophes, periods, parentheses,
        etc. Plain enemy/NPC names ("Saibaman", "Raditz") are
        all-letters and never hit either fallback, but boss display
        names are often dressed up ("Android 19", "Cell (Perfect
        Form)", "King Kai's Guardian") and used to fail this whole word
        — see _get_data_name_sprite below for the old all-or-nothing
        behavior this replaces. Rendered in plain white regardless of
        source, same convention as the other sprite fonts here, so the
        caller can tint sprite glyphs and fallback glyphs together in a
        single BLEND_RGBA_MULT pass rather than double-tinting the
        fallback ones."""
        glyphs = []
        for ch in word:
            if ch.isdigit():
                g = self._data_name_digit_font.glyphs.get(ch)
            else:
                g = self._data_stat_word_font.glyphs.get(ch.lower())
            if g is None:
                g = self._data_name_font.render(ch, True, (255, 255, 255))
            glyphs.append(g)
        if not glyphs:
            return None
        gap_total = gap * (len(glyphs) - 1)
        total_w = sum(g.get_width() for g in glyphs) + gap_total
        h = max(g.get_height() for g in glyphs)
        surf = pygame.Surface((max(1, total_w), h), pygame.SRCALPHA)
        x = 0
        for g in glyphs:
            surf.blit(g, (x, h - g.get_height()))
            x += g.get_width() + gap
        return surf

    def _get_data_name_sprite(self, name):
        """Assemble `name` with the same scouter_stats letter sprites used
        for HP/STR/POW/END labels (see _get_data_stat_label_sprite), tinted
        _DATA_NAME_COLOR and scaled by _DATA_STAT_LABEL_SCALE. Words are
        rendered individually (see _render_data_name_word) so spaces
        between them don't fail the letter-font lookup, and so any single
        unsupported character within a word falls back to a plain glyph
        for just that character rather than dropping the entire name to
        the plain pygame font. Returns None only if `name` has no
        characters at all, so the caller still has a last-resort fallback
        for that edge case."""
        words = str(name).split()
        if not words:
            return None
        gap = self._DATA_STAT_GLYPH_GAP
        parts = [self._render_data_name_word(word, gap) for word in words]
        parts = [p for p in parts if p is not None]
        if not parts:
            return None
        # Gap between words ≈ four letter gaps so it reads as a space
        # rather than letter-to-letter spacing.
        word_gap = gap * 4
        total_w = sum(p.get_width() for p in parts) + word_gap * (len(parts) - 1)
        h = max(p.get_height() for p in parts)
        surf = pygame.Surface((max(1, total_w), h), pygame.SRCALPHA)
        x = 0
        for i, part in enumerate(parts):
            surf.blit(part, (x, h - part.get_height()))
            x += part.get_width()
            if i < len(parts) - 1:
                x += word_gap
        tinted = surf.copy()
        tinted.fill(self._DATA_NAME_COLOR, special_flags=pygame.BLEND_RGBA_MULT)
        scale = self._DATA_STAT_LABEL_SCALE
        if scale != 1.0:
            w = max(1, round(tinted.get_width() * scale))
            hh = max(1, round(tinted.get_height() * scale))
            tinted = pygame.transform.scale(tinted, (w, hh))
        return tinted

    def _draw_data_name(self, surface, obj):
        """Entity name — centred inside _DATA_NAME_RAW_RECT, drawn with
        the scouter_stats letter font (same as the stat labels). No
        plate/border behind it."""
        x0, y0, x1, y1 = self._DATA_NAME_RAW_RECT
        box = self._bg_raw_rect_to_screen(x0, y0, x1 + 1, y1 + 1)
        if box.width <= 0 or box.height <= 0:
            return

        name = self._get_entity_display_name(obj)
        name_surf = self._get_data_name_sprite(name)
        if name_surf is None:
            # Letter-font missing a glyph (digits/punctuation/etc.) —
            # plain pygame fallback so *something* still shows.
            name_surf = self._data_name_font.render(name, True, self._DATA_NAME_COLOR)
        x = box.centerx - name_surf.get_width() // 2
        y = box.centery - name_surf.get_height() // 2
        surface.blit(name_surf, (x, y))

    def _resolve_portrait_key(self, obj):
        """(id, costume, form) triple used both to resolve the portrait
        file and to cache it — keyed by this rather than by entity object
        identity, since a player's costume/transformation can change
        between Scouter snapshots and a stale cached portrait would then
        be wrong."""
        # Player objects identify themselves via .character (see
        # dialogue.py's _resolve_portrait_key, which reads the exact same
        # field) rather than .id/.char_id/.entity_id — those are only
        # populated on NPCs/enemies. Without this, ident fell through to
        # type(obj).__name__.lower() == "player" for the player character,
        # which never matches a real portrait file.
        ident = (getattr(obj, 'id', None) or getattr(obj, 'char_id', None)
                 or getattr(obj, 'entity_id', None) or getattr(obj, 'character', None)
                 or type(obj).__name__.lower())
        costume = getattr(obj, 'costume', '') or ''

        # NOTE: don't stringify obj.transformation directly — it's a
        # TransformationState object (same one dialogue.py reads
        # ts.is_transformed off of), not a form-name string. str()'ing it
        # used to produce garbage like "<...TransformationState object at
        # 0x...>", which never matched a real file and silently fell back
        # to the base portrait every time. Prefer an explicit form-name
        # field if the entity has one; otherwise fall back to the same
        # is_transformed → "ssj" convention dialogue.py uses.
        form = getattr(obj, 'current_form', '') or getattr(obj, 'form', '') or ''
        if not form:
            ts = getattr(obj, 'transformation', None)
            if ts is not None and getattr(ts, 'is_transformed', False):
                form = (getattr(ts, 'form_id', '') or getattr(ts, 'form_name', '')
                        or getattr(ts, 'name', '') or 'ssj')

        return (str(ident), str(costume), str(form))

    def _resolve_portrait_path(self, ident, costume, form):
        """Same fallback convention as character_creator.py's
        resolve_portrait_path(): assets/portraits/{id}_{costume}_{form}.png,
        falling back through less-specific combinations down to a bare
        {id}.png, so portrait art can be filled in incrementally instead
        of needing every combo up front. Kept as its own copy here rather
        than importing character_creator (a dev-tool module) into runtime
        game code."""
        candidates = []
        if costume and form:
            candidates.append(f'{ident}_{costume}_{form}')
        if costume:
            candidates.append(f'{ident}_{costume}')
        if form:
            candidates.append(f'{ident}_{form}')
        candidates.append(ident)

        seen = set()
        for name in candidates:
            if name in seen:
                continue
            seen.add(name)
            path = os.path.join('assets', 'portraits', f'{name}.png')
            if os.path.exists(path):
                return path
        return None

    def _get_entity_portrait(self, obj):
        key = self._resolve_portrait_key(obj)
        if key in self._portrait_cache:
            return self._portrait_cache[key]

        path = self._resolve_portrait_path(*key)
        portrait = None
        if path is not None:
            try:
                portrait = pygame.image.load(path).convert_alpha()
            except Exception as e:
                print(f'[scouter_menu] could not load {path}: {e}')
                portrait = None
        self._portrait_cache[key] = portrait
        return portrait

    def _get_data_portrait_box(self):
        """The portrait's REAL on-screen rect — the single source of
        truth for where the gold-framed portrait slot actually lands,
        derived straight from scouter_background.png's own scaling via
        _bg_raw_rect_to_screen(). Anything else that needs to size or
        position itself relative to the portrait (like the description
        box below it) should call this rather than recomputing its own
        guess from _DATA_PORTRAIT_W/_DATA_PORTRAIT_H/_DATA_SIDE_MARGIN —
        those constants describe an assumed 220x220-at-40px-margin
        layout that has nothing to do with where the frame actually
        renders once the background's 9-slice scaling is applied, and
        the two drifting apart is what caused the description box to
        overlap into the bottom-right of the portrait art. (x0,y0)/
        (x1,y1) are the top-left/bottom-right CORNER PIXELS themselves
        (inclusive), not an exclusive edge, so we pass x1+1/y1+1 as the
        far edge — without the +1 the frame's last pixel row/column gets
        clipped."""
        x0, y0, x1, y1 = self._DATA_PORTRAIT_RAW_RECT
        return self._bg_raw_rect_to_screen(x0, y0, x1 + 1, y1 + 1)

    # Final manual nudge, screen pixels, applied only to where the
    # portrait art itself is blitted — NOT to the box (box still drives
    # size and the description panel's position below it). Everything
    # upstream of this is now provably exact (integer y-scale, matched
    # corner rounding, etc.); this is a deliberate cosmetic hair-adjust
    # on top of that, not a correction for a miscalculation.
    _DATA_PORTRAIT_NUDGE_X = -7
    _DATA_PORTRAIT_NUDGE_Y = 0
    # Flat pixel-pad added to width only, height untouched. Growth goes
    # to the RIGHT edge only (left edge stays exactly where
    # _DATA_PORTRAIT_NUDGE_X already placed it) so the two adjustments
    # don't fight each other.
    #
    # This is a flat pixel count, NOT a percentage/ratio multiplier —
    # that's the important part. box.width is normally an exact multiple
    # of the portrait's 64px source width (e.g. 512 = 64*8), so nearest-
    # neighbour scaling maps every source column to an identical 8
    # screen-pixel-wide block. A percentage stretch (dw = box.width*1.02)
    # breaks that clean multiple across the ENTIRE width, forcing many
    # columns to round to slightly different widths — that's the
    # "pixel inconsistency" that showed up. Adding a flat pixel count
    # instead (dw = box.width + N) still isn't a clean multiple of 64,
    # but nearest-neighbour scaling only needs to make ONE column N
    # pixels wider to absorb the whole difference, everywhere else stays
    # uniform — the smallest possible amount of unevenness for a
    # requested size that isn't an exact multiple.
    _DATA_PORTRAIT_WIDTH_PAD_PX = 7

    # Vertical gap (screen px, post-scale) between the "No" / "Portrait" /
    # "Data" lines drawn in the portrait box when an entity has no
    # portrait art — see _draw_data_portrait(). Same figure
    # _DATA_STAT_ROW_GAP uses for the stat rows, so the fallback message
    # reads with the same line rhythm as the rest of the panel.
    _DATA_NO_PORTRAIT_LINE_GAP = 12

    def _draw_data_no_portrait_message(self, surface, box):
        """"No" / "Portrait" / "Data" stacked on three centered lines,
        drawn with the same scouter_stats/numbers sprite glyphs, white
        color, and scale as the entity name (see _get_data_name_sprite)
        — rather than the small plain-pygame-font single line this used
        to be — so a missing portrait still reads as part of the same
        pixel-art panel instead of dropping to a different font."""
        lines = [self._get_data_name_sprite(word) for word in ('No', 'Portrait', 'Data')]
        lines = [l for l in lines if l is not None]
        if not lines:
            return
        gap = self._DATA_NO_PORTRAIT_LINE_GAP
        total_h = sum(l.get_height() for l in lines) + gap * (len(lines) - 1)
        y = box.centery - total_h // 2
        for l in lines:
            x = box.centerx - l.get_width() // 2
            surface.blit(l, (x, y))
            y += l.get_height() + gap

    def _draw_data_portrait(self, surface, obj):
        """Top-right portrait box — see _get_data_portrait_box() for how
        this rect is derived."""
        box = self._get_data_portrait_box()

        portrait = self._get_entity_portrait(obj)
        if portrait is not None:
            # Scale directly to the box's own on-screen pixel size, rather
            # than recomputing a size from pw * x_scale. box.width/height
            # come from rounding each mapped CORNER independently (see
            # _bg_raw_rect_to_screen) — round(map_x(x1)) - round(map_x(x0))
            # — which is not always equal to round(map_x(x1) - map_x(x0)),
            # the single rounding a from-scratch pw * x_scale computation
            # does. That's a genuine 1px double-rounding mismatch, not
            # something a "better" scale factor can fix — matching
            # box.width/height exactly is what guarantees zero gap.
            # pygame.transform.scale (not smoothscale) stays nearest-
            # neighbour so pixel art doesn't blur. dw adds the flat
            # width pad on top; height is untouched.
            dw = max(1, box.width + self._DATA_PORTRAIT_WIDTH_PAD_PX)
            scaled = pygame.transform.scale(portrait, (dw, box.height))
            dest = (box.x + self._DATA_PORTRAIT_NUDGE_X,
                    box.y + self._DATA_PORTRAIT_NUDGE_Y)
            surface.blit(scaled, dest)
        else:
            self._draw_data_no_portrait_message(surface, box)

    def _get_entity_description(self, obj):
        """character_creator.py and entity_creator.py now write a
        "description" field into each character/enemy/NPC's JSON config
        (see their DEFAULT_CONFIG / DEFAULT_ENEMY_CONFIG /
        DEFAULT_NPC_CONFIG). This still just reads obj.description rather
        than reaching into the JSON directly, so it's on whatever code
        constructs the live Player/Enemy/NPC objects to copy that field
        from the loaded config onto the instance (the same way it already
        does for display_name, stats, etc.) — if that attribute isn't
        set, this quietly falls back to the placeholder below rather than
        erroring."""
        desc = getattr(obj, 'description', None)
        return desc or 'No description available.'

    def _get_data_description_box(self):
        """The description box's REAL on-screen rect, derived straight
        from _DATA_DESCRIPTION_RAW_RECT via _bg_raw_rect_to_screen() —
        same convention as _get_data_portrait_box()."""
        x0, y0, x1, y1 = self._DATA_DESCRIPTION_RAW_RECT
        return self._bg_raw_rect_to_screen(x0, y0, x1 + 1, y1 + 1)

    def _get_data_description_geometry(self):
        """Shared geometry for the description box, used by both
        _draw_data_description() and the scroll-range/clamp code so
        they can never disagree with each other:

        - 'clip_rect': the box's FULL height, padded/left-shifted —
          text is allowed to slide its pixels through this whole area
          (including the rows the bars sit on) and only gets hidden
          where a bar is actually painted on top of it.
        - 'safe_top'/'safe_bottom': the band that's clear of BOTH bars.
          A line positioned here renders completely un-clipped. Scroll
          position 0 anchors the first line's top to safe_top, and the
          maximum scroll anchors the last line's bottom to
          safe_bottom — so both ends can always be scrolled to a spot
          where they're fully visible, not permanently wedged under a
          bar with nowhere left to scroll (see the bug this fixes).
        Returns None if the box has no on-screen area."""
        box = self._get_data_description_box()
        if box.width <= 0 or box.height <= 0:
            return None
        top_bar = self._bg_raw_rect_from(self._DATA_DESCRIPTION_TOP_BAR_RAW_RECT)
        bottom_bar = self._bg_raw_rect_from(self._DATA_DESCRIPTION_BOTTOM_BAR_RAW_RECT)

        clip_rect = box.inflate(-16, -8)
        clip_rect.x     += self._DATA_DESCRIPTION_TEXT_LEFT_PAD_PX
        clip_rect.width -= self._DATA_DESCRIPTION_TEXT_LEFT_PAD_PX
        if clip_rect.width <= 0 or clip_rect.height <= 0:
            return None

        safe_top = max(clip_rect.top, top_bar.bottom)
        safe_bottom = min(clip_rect.bottom, bottom_bar.top)

        return {
            'box': box,
            'top_bar': top_bar,
            'bottom_bar': bottom_bar,
            'clip_rect': clip_rect,
            'safe_top': safe_top,
            'safe_bottom': safe_bottom,
        }

    def _draw_data_description(self, surface, obj):
        """Bottom-right description box, at its exact authored raw-art
        rect (see _DATA_DESCRIPTION_RAW_RECT). Two solid bars (see
        _DATA_DESCRIPTION_TOP_BAR_RAW_RECT/_BOTTOM_BAR_RAW_RECT) overlap
        the top and bottom of this box in the original game, so they're
        drawn last — on top of the panel fill and the wrapped text.

        The text itself is laid out across the box's FULL height (see
        _get_data_description_geometry()'s 'clip_rect') and scrolled by
        self._data_desc_scroll_px (see _update_data_description_scroll),
        clipped only to that full-box rect — NOT to the band between
        the bars. That's deliberate: since the bars are drawn on top
        afterwards, a line scrolling past one only has the pixel rows
        that actually sit under the bar hidden; whatever's left above
        or below stays visible, giving a smooth cut rather than an
        all-or-nothing line-level clip. Scroll 0 and max-scroll are
        anchored to 'safe_top'/'safe_bottom' (see
        _get_data_description_geometry()), so the very first and very
        last lines always have somewhere fully un-clipped to scroll to
        rather than being permanently pinned under a bar. No panel
        fill or border is drawn behind the text — that translucent
        black box + green outline was a dev placeholder, not part of
        the panel art, and just muddies the real scouter_background.png
        underneath."""
        geo = self._get_data_description_geometry()
        if geo is None:
            return

        text = self._get_entity_description(obj)
        self._blit_data_description_text(
            surface, text, geo['clip_rect'], (220, 220, 220),
            geo['safe_top'], self._data_desc_scroll_px)

        top_bar, bottom_bar = geo['top_bar'], geo['bottom_bar']
        if top_bar.width > 0 and top_bar.height > 0:
            surface.draw_rect(self._DATA_DESCRIPTION_BAR_COLOR, top_bar)
        if bottom_bar.width > 0 and bottom_bar.height > 0:
            surface.draw_rect(self._DATA_DESCRIPTION_BAR_COLOR, bottom_bar)

        self._draw_data_description_scroll_arrows(surface)

    def _bg_raw_rect_from(self, raw_rect):
        """Convenience wrapper around _bg_raw_rect_to_screen() for a
        (x0, y0, x1, y1) inclusive-corner tuple constant."""
        x0, y0, x1, y1 = raw_rect
        return self._bg_raw_rect_to_screen(x0, y0, x1 + 1, y1 + 1)

    # Description text uses the same native-pixel-art scale-up as the
    # entity name / stat labels (see _DATA_STAT_LABEL_SCALE) — the
    # uppercase/lowercase glyph sheets are tiny source art, same as
    # scouter_stats, and read as illegibly small if blitted 1:1.
    _DATA_DESC_TEXT_SCALE = _DATA_STAT_LABEL_SCALE

    def _measure_data_description_layout(self, text, width):
        """Word-wraps `text` to `width` using the same glyph metrics
        _blit_data_description_text() draws with, and returns the
        shared layout info both that method and the scroll-clamping
        code in _update_data_description_scroll() need:
        {'lines', 'line_h', 'descend_pad', 'row_advance',
        'content_height'}. Pulled out on its own so the two call sites
        can never drift out of sync with each other's idea of how tall
        the wrapped text actually is."""
        sfont = self._data_desc_sprite_font
        scale = self._DATA_DESC_TEXT_SCALE
        gap = max(1, round(1 * scale))
        space_w = round((sfont.space_width() or self._data_desc_font.size(' ')[0]) * scale)

        # `width` is an on-screen pixel measurement of the description
        # box, which scales with the panel's own dynamic per-resolution
        # integer scale (_get_data_bg_layout) — NOT with the fixed
        # _DATA_DESC_TEXT_SCALE the glyphs are blown up by. Comparing
        # text_width() (measured in font-scale units) straight against
        # a box measured in panel-scale units only lines up when those
        # two scales happen to match; otherwise the box silently has
        # more (or less) room than the font's actual size accounts
        # for, so an extra word creeps onto every line instead of
        # breaking where the original art intended. Convert `width`
        # into the same font-scale unit basis before comparing.
        bg_layout = self._get_data_bg_layout()
        panel_scale = bg_layout[0] if bg_layout else scale
        width = width * (scale / panel_scale)

        # Reserve a strip on the right that words are never allowed to
        # wrap into — see _DATA_DESCRIPTION_TEXT_RIGHT_PAD_PX. Applied
        # here (not at the call sites) so every caller of this method
        # — the live draw AND the scroll-range/clamp math — always
        # agrees on the same effective width.
        width -= self._DATA_DESCRIPTION_TEXT_RIGHT_PAD_PX

        def char_width(ch):
            if ch == ' ':
                return space_w
            g = sfont.glyph(ch)
            if g is not None:
                return max(1, round(g.get_width() * scale))
            return max(1, round(self._data_desc_font.size(ch)[0] * scale))

        def text_width(s):
            if not s:
                return 0
            return sum(char_width(ch) for ch in s) + gap * (len(s) - 1)

        # Text may contain explicit '\n' hard breaks (e.g. to match a
        # specific line break from source material exactly, where the
        # greedy pixel-width wrap below won't reliably reproduce it on
        # its own). Each '\n'-delimited paragraph is word-wrapped
        # independently so a forced break never gets swallowed by the
        # auto-wrap.
        lines = []
        for paragraph in text.split('\n'):
            words = paragraph.split(' ')
            cur = ''
            for w in words:
                trial = f'{cur} {w}'.strip()
                if text_width(trial) <= width:
                    cur = trial
                else:
                    if cur:
                        lines.append(cur)
                    cur = w
            lines.append(cur)

        line_h = max(round(sfont.height() * scale),
                     round(self._data_desc_font.get_height() * scale))
        # Reserve extra room at the bottom of every line for descenders
        # (see _MixedCaseSpriteFont.descender_offset) — otherwise a
        # 'g'/'j'/'p'/'q'/'y' gets its tail clipped or crowds directly
        # into the next line.
        descend_pad = round(sfont._DESCENDER_OFFSET * scale)
        row_advance = line_h + descend_pad + round(1 * scale) - 4
        content_height = (row_advance * (len(lines) - 1) + line_h + descend_pad
                           if lines else 0)
        return {
            'lines': lines,
            'line_h': line_h,
            'descend_pad': descend_pad,
            'row_advance': row_advance,
            'content_height': content_height,
        }

    def _get_data_description_scroll_range(self, obj):
        """Returns (content_height, safe_height, max_scroll_px) for the
        currently-inspected entity's description, used by both
        _update_data_description_scroll() (to clamp the live scroll
        offset) and anything that wants to know whether the text is
        even scrollable. max_scroll_px is how far self._data_desc_scroll_px
        can go before the LAST line's bottom would pull back past
        safe_bottom (see _get_data_description_geometry()) — 0 if the
        text already fits inside the safe band without scrolling."""
        geo = self._get_data_description_geometry()
        if geo is None:
            return 0, 0, 0
        clip_rect = geo['clip_rect']
        safe_height = max(0, geo['safe_bottom'] - geo['safe_top'])
        text = self._get_entity_description(obj)
        layout = self._measure_data_description_layout(text, clip_rect.width)
        max_scroll = max(0, layout['content_height'] - safe_height)
        return layout['content_height'], safe_height, max_scroll

    # Smooth scroll speed, screen px/sec, for the held up/down (or w/s)
    # keys while SECTION_DATA is open — see _update_data_description_scroll().
    _DATA_DESC_SCROLL_SPEED = 400.0

    def _update_data_description_scroll(self, dt):
        """Held UP/DOWN (or W/S) smoothly scrolls the description text
        while Scouter Data is open — same held-key-resolved-every-frame
        approach as _update_crosshair_position(), and the same keys,
        which is safe because the two sections are mutually exclusive
        (this only runs while section == SECTION_DATA; crosshair
        movement only runs while section == SECTION_SCOUTER). Clamped
        every frame to the current entity's own content height via
        _get_data_description_scroll_range() so switching to a shorter
        description never leaves the view stuck scrolled past its own
        end. Also ticks the scroll-arrow blink timer (see
        _DATA_DESC_ARROW_BLINK_INTERVAL / _draw_data_description_scroll_arrows)
        — it lives here rather than its own update method since both
        are "SECTION_DATA-only, every-frame" concerns."""
        if self._data_entity is None:
            return
        keys = pygame.key.get_pressed()
        dy = (keys[pygame.K_DOWN] or keys[pygame.K_s]) - (keys[pygame.K_UP] or keys[pygame.K_w])
        _, _, max_scroll = self._get_data_description_scroll_range(self._data_entity)
        if dy != 0:
            self._data_desc_scroll_px += dy * self._DATA_DESC_SCROLL_SPEED * dt
        self._data_desc_scroll_px = min(max(self._data_desc_scroll_px, 0.0), max_scroll)

        self._data_desc_arrow_blink_timer += dt
        if self._data_desc_arrow_blink_timer >= self._DATA_DESC_ARROW_BLINK_INTERVAL:
            self._data_desc_arrow_blink_timer -= self._DATA_DESC_ARROW_BLINK_INTERVAL
            self._data_desc_arrow_blink_index = 1 - self._data_desc_arrow_blink_index

    def _load_arrow(self):
        """Load assets/ui/scouter/arrow.png once — same lazy/cached
        convention as _load_crosshair(). A horizontal strip of 2
        frames; individual frames are sliced out and cached on demand
        by _get_arrow_frame()."""
        self._arrow_load_attempted = True
        path = os.path.join('assets', 'ui', 'scouter', 'arrow.png')
        try:
            self._arrow_raw = pygame.image.load(path).convert_alpha()
        except Exception as e:
            print(f'[scouter_menu] could not load {path}: {e}')
            self._arrow_raw = None

    def _get_arrow_frame(self, frame_number):
        """frame_number is 1-based (1 = static/no-more-to-scroll pose,
        2 = the alternate blink pose). Slices the frame_number-th cell
        out of the horizontal strip, each cell the same raw size as
        _DATA_DESC_SCROLL_ARROW_TOP_RAW_RECT (the sprite is authored
        1:1 with its on-screen raw-rect destination, same convention
        as the rest of the Data section's small art). Cached but NOT
        scaled — draw code should go through
        _draw_data_description_scroll_arrows(), which scales to the
        mapped destination rect via _bg_raw_rect_to_screen()."""
        if not self._arrow_load_attempted:
            self._load_arrow()
        if self._arrow_raw is None:
            return None
        if frame_number in self._arrow_frames:
            return self._arrow_frames[frame_number]

        x0, y0, x1, y1 = self._DATA_DESC_SCROLL_ARROW_TOP_RAW_RECT
        fw, fh = x1 - x0 + 1, y1 - y0 + 1
        rect = pygame.Rect((frame_number - 1) * fw, 0, fw, fh)
        raw_w, raw_h = self._arrow_raw.get_size()
        if rect.right > raw_w or rect.bottom > raw_h:
            # Sheet doesn't actually have this frame — fail soft instead
            # of raising, same tolerance as the other asset loaders here.
            print(f'[scouter_menu] arrow.png has no frame {frame_number} '
                  f'(sheet is {raw_w}x{raw_h}, needs at least '
                  f'{rect.right}x{rect.bottom})')
            self._arrow_frames[frame_number] = None
            return None

        frame = self._arrow_raw.subsurface(rect).copy()
        self._arrow_frames[frame_number] = frame
        return frame

    def _draw_data_description_scroll_arrows(self, surface):
        """Draws the two scroll-availability arrows baked into the
        right edge of the description box's top/bottom bars (see
        _DATA_DESC_SCROLL_ARROW_TOP_RAW_RECT/_BOTTOM_RAW_RECT). Each
        shows frame 1 (static) when that direction has nothing left to
        scroll to, otherwise blinks between frame 1 and frame 2 every
        _DATA_DESC_ARROW_BLINK_INTERVAL seconds (see
        _update_data_description_scroll()). The bottom arrow is the
        SAME sprite as the top one, just vertically flipped — drawn
        last so it sits on top of the bars, same as the text is
        obscured by them from underneath."""
        if self._data_entity is None:
            return
        _, _, max_scroll = self._get_data_description_scroll_range(self._data_entity)
        scroll_px = self._data_desc_scroll_px

        can_scroll_up = scroll_px > 0.01
        can_scroll_down = scroll_px < max_scroll - 0.01

        top_frame_num = 2 if (can_scroll_up and self._data_desc_arrow_blink_index == 1) else 1
        bottom_frame_num = 2 if (can_scroll_down and self._data_desc_arrow_blink_index == 1) else 1

        top_frame = self._get_arrow_frame(top_frame_num)
        bottom_frame = self._get_arrow_frame(bottom_frame_num)

        if top_frame is not None:
            dest = self._bg_raw_rect_from(self._DATA_DESC_SCROLL_ARROW_TOP_RAW_RECT)
            if dest.width > 0 and dest.height > 0:
                scaled = pygame.transform.scale(top_frame, (dest.width, dest.height))
                surface.blit(scaled, dest)

        if bottom_frame is not None:
            dest = self._bg_raw_rect_from(self._DATA_DESC_SCROLL_ARROW_BOTTOM_RAW_RECT)
            if dest.width > 0 and dest.height > 0:
                # Vertical flip, not horizontal — the sprite points
                # up/down (toward more content in that direction), so
                # mirroring left-right left it looking unchanged.
                flipped = pygame.transform.flip(bottom_frame, False, True)
                scaled = pygame.transform.scale(flipped, (dest.width, dest.height))
                surface.blit(scaled, dest)

    def _blit_data_description_text(self, surface, text, rect, color,
                                     origin_y, scroll_px=0.0):
        """Word-wraps `text` into `rect`'s width using the uppercase/
        lowercase sprite letter fonts (see _MixedCaseSpriteFont /
        self._data_desc_sprite_font), scaled up by
        _DATA_DESC_TEXT_SCALE so it reads at the same size/weight as
        the rest of the Scouter Data panel's pixel-art text. Falls back
        per-character (not per-word or for the whole block) to
        self._data_desc_font for anything neither glyph set covers —
        digits, punctuation, etc. — so one stray period doesn't drop an
        entire sentence into the plain font, just that one character
        (that fallback glyph is scaled up to match too, via
        transform.scale on its rendered surface).

        `origin_y` is where the first line's top sits when scroll_px is
        0 — the top of the "safe" band clear of both bars (see
        _get_data_description_geometry()), NOT rect.y. `scroll_px`
        then shifts the whole wrapped block upward from there. This
        clips to `rect` (the full box, via surface.set_clip, restored
        afterwards) and draws every line regardless of whether it
        falls outside that rect: pygame simply won't draw the part
        that's clipped away, which is what lets a scrolled line get
        PARTIALLY hidden — e.g. by the top/bottom bars painted over
        this in _draw_data_description() — rather than disappearing
        entirely the instant any part of it crosses a boundary."""
        sfont = self._data_desc_sprite_font
        scale = self._DATA_DESC_TEXT_SCALE
        gap = max(1, round(1 * scale))
        space_w = round((sfont.space_width() or self._data_desc_font.size(' ')[0]) * scale)

        def scaled_glyph(g):
            w = max(1, round(g.get_width() * scale))
            h = max(1, round(g.get_height() * scale))
            return pygame.transform.scale(g, (w, h))

        layout = self._measure_data_description_layout(text, rect.width)
        lines = layout['lines']
        line_h = layout['line_h']
        descend_pad = layout['descend_pad']
        row_advance = layout['row_advance']

        prev_clip = surface.get_clip()
        surface.set_clip(rect)
        try:
            y = origin_y - round(scroll_px)
            for line in lines:
                # Cheap cull: skip rows that can't possibly touch rect
                # at all (set_clip would no-op them anyway, this just
                # avoids the per-glyph work for far-offscreen lines).
                if y + line_h + descend_pad < rect.top or y > rect.bottom:
                    y += row_advance
                    continue
                x = rect.x
                for ch in line:
                    if ch == ' ':
                        x += space_w + gap
                        continue
                    offset = round(sfont.descender_offset(ch) * scale)
                    g = sfont.glyph(ch)
                    if g is not None:
                        tinted = scaled_glyph(g)
                        tinted.fill(color, special_flags=pygame.BLEND_RGBA_MULT)
                        surface.blit(tinted, (x, y + line_h - tinted.get_height() + offset))
                        x += tinted.get_width() + gap
                    else:
                        fb = self._data_desc_font.render(ch, True, color)
                        fb = scaled_glyph(fb)
                        surface.blit(fb, (x, y + line_h - fb.get_height() + offset))
                        x += fb.get_width() + gap
                y += row_advance
        finally:
            surface.set_clip(prev_clip)