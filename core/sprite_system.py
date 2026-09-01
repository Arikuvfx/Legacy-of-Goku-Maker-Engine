import pygame
import os
import random
from config.settings import RENDER_SCALE


# --- Shared direction tables -------------------------------------------------
# Every animation-loading method needs "which row is this direction on" info.
# Previously each method rebuilt its own copy of these dicts/lists inline
# (same values, six+ separate places). Pulling them out to module level means
# there's exactly one place to edit if a direction ever needs to change, and
# avoids re-allocating identical dicts every time a sprite sheet is loaded.
DIRECTIONS_4 = ('down', 'left', 'right', 'up')
DIRECTION_MAP_4 = {name: index for index, name in enumerate(DIRECTIONS_4)}

DIRECTIONS_8 = ('down', 'down_left', 'left', 'up_left', 'up', 'up_right', 'right', 'down_right')
DIRECTION_MAP_8 = {name: index for index, name in enumerate(DIRECTIONS_8)}


def _directions(use_8_directions):
    """Return (direction_names, direction_to_row_map, num_directions) for a sheet layout."""
    if use_8_directions:
        return DIRECTIONS_8, DIRECTION_MAP_8, 8
    return DIRECTIONS_4, DIRECTION_MAP_4, 4


class SpriteSheet:
    """Wraps a sprite sheet PNG and gives back individual frames.

    Instances are cached by filepath (see __new__) and cut frame-rows are
    cached per-instance (see get_sprite_row) — a room that places many
    entities of the same (type, variant) — e.g. 31 'shooter' enemies —
    used to re-run pygame.image.load()+convert_alpha() on the same PNG,
    and re-cut+convert_alpha() every frame, once per entity. That's disk
    I/O and surface conversion multiplied by however many duplicates are
    placed, all up front when the room loads. Caching means the file is
    read and every frame is cut exactly once no matter how many entities
    share that sprite; each entity still gets its own Animation object
    wrapping the shared (read-only) frame Surfaces, so playback state
    (current frame, timers) stays independent per instance.
    """

    _sheet_cache: dict = {}

    def __new__(cls, filepath):
        cached = cls._sheet_cache.get(filepath)
        if cached is not None:
            return cached
        instance = super().__new__(cls)
        instance.sheet = None
        instance._row_cache = {}
        if os.path.exists(filepath):
            instance.sheet = pygame.image.load(filepath).convert_alpha()
        cls._sheet_cache[filepath] = instance
        return instance

    def __init__(self, filepath):
        # __new__ does all the real work so a repeat SpriteSheet(filepath)
        # for an already-loaded file is a cache hit; Python still calls
        # __init__ on the cached instance every time, so keep it a no-op
        # rather than re-running setup (or clobbering _row_cache) here.
        pass

    def get_sprite(self, x, y, width, height):
        """Cut a single frame out of the sheet. Returns magenta placeholder if sheet is missing."""
        if not self.sheet:
            surface = pygame.Surface((width, height))
            surface.fill((255, 0, 255))
            return surface

        sprite = pygame.Surface((width, height), pygame.SRCALPHA)
        sprite.blit(self.sheet, (0, 0), (x, y, width, height))
        # convert_alpha() puts the frame in the display's native pixel format.
        # It costs a little time once here at load, but every future blit/scale
        # of this frame (i.e. every frame it's on screen) is noticeably faster
        # than blitting/scaling a plain SRCALPHA surface.
        return sprite.convert_alpha()

    def get_sprite_row(self, row, num_frames, width, height, start_x=0):
        """Pull every frame from a single row — handy for grabbing a full animation.

        Cached per (row, num_frames, width, height, start_x): once one
        'shooter' has had its idle/down row cut and convert_alpha()'d, the
        next 30 shooters loading the same row get the same Surface list
        back instead of re-cutting identical pixels.
        """
        key = (row, num_frames, width, height, start_x)
        cached = self._row_cache.get(key)
        if cached is not None:
            return cached

        y = row * height
        frames = [
            self.get_sprite(start_x + (i * width), y, width, height)
            for i in range(num_frames)
        ]
        self._row_cache[key] = frames
        return frames

    def get_all_frames(self, width, height, direction_row=0):
        """Return all frames from one row, auto-detecting the frame count from sheet width."""
        if not self.sheet:
            return []

        num_frames = self.sheet.get_width() // width
        return self.get_sprite_row(direction_row, num_frames, width, height)


def _frame_has_pixels(surface):
    """True if `surface` has at least one non-fully-transparent pixel.

    Used by idle-blink animations to decide whether the second frame is
    real art or just an empty/placeholder frame — an idle sheet whose
    second frame is blank should never blink to it, it should just sit
    on frame 0 forever.
    """
    if surface is None:
        return False
    try:
        return pygame.mask.from_surface(surface).count() > 0
    except pygame.error:
        return False


class Animation:
    """Drives a flipbook of frames at a fixed rate.

    Normally this just advances through `frames` every `frame_duration`
    seconds (optionally looping). Idle animations use a different,
    "blink" mode instead (see `idle_blink` below).
    """

    # How long an idle animation rests on frame 0 before briefly blinking
    # to frame 1. Fixed at the class level so every idle animation across
    # every sprite type (player/enemy/npc/critter/boss) blinks on the same
    # cadence, per spec: "the idle switches every 4 seconds shortly to the
    # second frame. Otherwise it stays at the first frame."
    IDLE_BLINK_INTERVAL = 4.0

    def __init__(self, frames, frame_duration=0.1, loop=True, loop_tail_frames=None, idle_blink=False,
                 hold_frames=None):
        self.frames = frames
        self.frame_duration = frame_duration
        self.loop = loop
        # When set on a non-looping animation, the animation plays through
        # every frame once as usual, but instead of freezing on the final
        # frame it keeps looping the last `loop_tail_frames` frames forever
        # (until reset()/a new animation takes over). e.g. loop_tail_frames=2
        # on a 6-frame punch plays 0,1,2,3,4,5 then loops 4,5,4,5,4,5...
        self.loop_tail_frames = loop_tail_frames

        # hold_frames=(start_idx, end_idx): once playback first reaches
        # start_idx, instead of continuing on to the rest of the sheet it
        # loops start_idx<->end_idx forever — like a mid-animation "charging"
        # pause — until an external caller invokes release_hold(). At that
        # point it resumes normal playback from wherever it was in the hold
        # loop, through the remaining frames, and finishes as usual.
        # e.g. hold_frames=(2, 3) on a 5-frame transform sheet plays 0,1,2,
        # then loops 2,3,2,3,2,3... until released, then plays 4 and finishes.
        self.hold_frames = hold_frames
        self.holding = False
        self._hold_released = False

        self.current_frame = 0
        self.time_elapsed = 0
        self.finished = False

        # Idle-blink mode: instead of cycling through every frame at
        # frame_duration like a normal flipbook, hold frame 0 and only
        # briefly cut to frame 1 every IDLE_BLINK_INTERVAL seconds (like
        # an eye-blink), then return to frame 0. Only turns on if there's
        # actually a usable second frame — a single-frame idle sheet, or
        # one whose second frame is fully transparent/blank, just stays
        # on frame 0 forever and never blinks.
        self.idle_blink = bool(idle_blink) and len(frames) > 1 and _frame_has_pixels(frames[1])
        self._blinking = False

    def update(self, dt):
        if self.idle_blink:
            self.time_elapsed += dt
            if self._blinking:
                # Currently showing the blink frame — hold it for one
                # frame_duration, then drop back to the resting frame.
                if self.time_elapsed >= self.frame_duration:
                    self.time_elapsed = 0
                    self._blinking = False
                    self.current_frame = 0
            else:
                # Resting on frame 0 — wait out the blink interval.
                if self.time_elapsed >= self.IDLE_BLINK_INTERVAL:
                    self.time_elapsed = 0
                    self._blinking = True
                    self.current_frame = 1
            return

        # Once finished, a plain non-looping animation just holds its last
        # frame forever — nothing left to advance. A tail-looping animation
        # is a different story: it's marked finished after its first full
        # playthrough (see below) but still needs to keep ticking so the
        # tail frames continue to cycle.
        if self.finished and not self.loop and not self.loop_tail_frames:
            return

        # Hold-and-loop segment: once playback reaches hold_frames[0], stop
        # advancing past hold_frames[1] and just loop between the two until
        # release_hold() is called externally (e.g. TransformationSystem
        # releasing the 'transform' animation once its charge timer completes).
        if self.hold_frames and not self._hold_released and self.current_frame >= self.hold_frames[0]:
            self.holding = True
            self.time_elapsed += dt
            if self.time_elapsed >= self.frame_duration:
                self.time_elapsed = 0
                self.current_frame += 1
                if self.current_frame > self.hold_frames[1]:
                    self.current_frame = self.hold_frames[0]
            return

        self.holding = False
        self.time_elapsed += dt

        if self.time_elapsed >= self.frame_duration:
            self.time_elapsed = 0
            self.current_frame += 1

            tail_start = (
                max(0, len(self.frames) - self.loop_tail_frames)
                if self.loop_tail_frames else None
            )

            if self.current_frame >= len(self.frames):
                if self.loop:
                    self.current_frame = 0
                elif self.loop_tail_frames:
                    # First time reaching the end: mark finished (same signal
                    # every other non-looping animation gives) and drop back
                    # into the tail range instead of freezing on frame -1.
                    self.finished = True
                    self.current_frame = tail_start
                else:
                    self.current_frame = len(self.frames) - 1
                    self.finished = True
            elif self.finished and self.loop_tail_frames and self.current_frame < tail_start:
                # Already past the first playthrough and looping the tail —
                # keep current_frame from drifting below the tail range.
                self.current_frame = tail_start

    def get_current_frame(self):
        if not self.frames:
            return None
        return self.frames[self.current_frame]

    def reset(self):
        self.current_frame = 0
        self.time_elapsed = 0
        self.finished = False
        self._blinking = False
        self.holding = False
        self._hold_released = False

    def release_hold(self):
        """Let a held animation (see hold_frames) continue past its hold-loop
        toward its final frame(s). No-op if this animation has no hold segment
        or has already been released."""
        self._hold_released = True
        self.holding = False
        self.time_elapsed = 0


class AnimatedSprite:
    """
    Entity sprite that plays directional animations loaded from separate files.
    Each animation lives at {base_path}/{animation_name}.png.
    """

    def __init__(self, character_name, costume_name, sprite_width, sprite_height):
        self.character_name = character_name
        self.costume_name = costume_name
        self.sprite_width = sprite_width
        self.sprite_height = sprite_height

        self.base_path = f"assets/sprites/player/{character_name}/{costume_name}"

        self.animations = {}
        self.current_animation = None
        self.current_direction = 'down'
        self.current_variant_index = 0

        self.offset_x = sprite_width // 2
        self.offset_y = sprite_height // 2

        # Draw-time render cache — see _ensure_render_cache(). Built lazily so
        # that sprites created via AnimatedSprite.__new__() (the enemy/NPC/boss
        # loaders below) don't need to remember to set this up too.
        self._scaled_frame_cache = {}
        self._scaled_width = int(sprite_width * RENDER_SCALE)
        self._scaled_height = int(sprite_height * RENDER_SCALE)

    @classmethod
    def _create_bare(cls, character_name, costume_name, sprite_width, sprite_height, base_path):
        """Build an AnimatedSprite that points at a custom base_path, without going
        through __init__ (used by EnemySpriteLoader / NPCSpriteLoader / create_boss_sprite,
        whose folder layout doesn't match assets/sprites/player/{name}/{costume}).

        Centralizing this in one place matters: the previous version had this same
        block copy-pasted three times, and two of the three copies forgot to set
        current_variant_index, which __init__ always sets. That's a latent
        AttributeError waiting to happen the first time draw()/is_animation_finished()
        hit a variant-list animation before set_animation() had run. Building bare
        sprites through one method means that can't happen again.
        """
        sprite = cls.__new__(cls)
        sprite.character_name = character_name
        sprite.costume_name = costume_name
        sprite.sprite_width = sprite_width
        sprite.sprite_height = sprite_height
        sprite.base_path = base_path
        sprite.animations = {}
        sprite.current_animation = None
        sprite.current_direction = 'down'
        sprite.current_variant_index = 0
        sprite.offset_x = sprite_width // 2
        sprite.offset_y = sprite_height // 2
        sprite._scaled_frame_cache = {}
        sprite._scaled_width = int(sprite_width * RENDER_SCALE)
        sprite._scaled_height = int(sprite_height * RENDER_SCALE)
        return sprite

    def load_animation(self, animation_name, direction, frame_duration=0.1, loop=True, num_variants=1,
                       use_8_directions=False, loop_tail_frames=None, hold_frames=None):
        """Load one directional animation from {base_path}/{animation_name}.png.

        Sheet rows map to directions; stacked variant blocks sit below them
        (each block is num_directions rows tall).

        loop_tail_frames: for a non-looping animation, loop just the last N
        frames forever once the full sheet has played through once (instead
        of freezing on the final frame). See Animation.loop_tail_frames.

        hold_frames: (start_idx, end_idx) — loop those two frames mid-animation
        until release_hold() is called. See Animation.hold_frames.

        Returns True on success, False if the file is missing or has no frames.
        """
        filepath = f"{self.base_path}/{animation_name}.png"

        if not os.path.exists(filepath):
            return False

        sprite_sheet = SpriteSheet(filepath)

        # Support both 4-directional and 8-directional sprite sheets
        # 8-directional: down, down_left, left, up_left, up, up_right, right, down_right
        # 4-directional: down, left, right, up (legacy support)
        _, direction_map, num_directions = _directions(use_8_directions)
        direction_offset = direction_map.get(direction, 0)  # unknown direction falls back to row 0

        key = f"{animation_name}_{direction}"
        variants = []

        # Every 'idle' animation (player, enemy, npc, critter, boss — they
        # all funnel through this one method) uses blink mode rather than
        # cycling through its frames like a normal flipbook. See
        # Animation.idle_blink.
        idle_blink = (animation_name == 'idle')

        for variant_index in range(num_variants):
            row = (variant_index * num_directions) + direction_offset
            frames = sprite_sheet.get_all_frames(self.sprite_width, self.sprite_height, row)

            if not frames:
                continue

            # get_all_frames() derives frame count from the sheet's overall
            # width, which is shared across every direction row in the file.
            # That's correct for down/left/right (each genuinely has a second
            # blink frame), but the up-facing idle pose only has one real
            # frame — its second column isn't blank art (so _frame_has_pixels
            # can't filter it out for us), it's just not meant to be shown.
            # Simplest fix: only ever look at column 0 for 'up' idle, so
            # there's nothing to blink OR cycle to.
            if animation_name == 'idle' and direction == 'up':
                frames = frames[:1]

            animation = Animation(frames, frame_duration, loop, loop_tail_frames, idle_blink=idle_blink,
                                   hold_frames=hold_frames)
            variants.append(animation)

        if not variants:
            return False

        self.animations[key] = variants[0] if len(variants) == 1 else variants
        return True

    def load_animation_all_directions(self, animation_name, frame_duration=0.1, loop=True, num_variants=1,
                                      use_8_directions=False, loop_tail_frames=None, hold_frames=None):
        """Load an animation for every direction in one shot.

        use_8_directions=True  → down, down_left, left, up_left, up, up_right, right, down_right
        use_8_directions=False → down, left, right, up  (legacy 4-dir)

        loop_tail_frames: see load_animation() — looped through to every direction.
        hold_frames: see load_animation() — looped through to every direction.
        """
        directions, _, _ = _directions(use_8_directions)
        for direction in directions:
            self.load_animation(animation_name, direction, frame_duration, loop, num_variants, use_8_directions,
                                loop_tail_frames, hold_frames)

    def append_animation_variants(self, animation_name, source_filename, frame_duration=0.1, loop=True, num_variants=1,
                                  use_8_directions=False):
        """Tack extra variants onto an existing animation from a different sheet file.

        Useful when overflow variants live in a separate file, e.g. melee_extra.png.
        """
        filepath = f"{self.base_path}/{source_filename}"

        if not os.path.exists(filepath):
            return False

        sprite_sheet = SpriteSheet(filepath)
        directions, direction_map, num_directions = _directions(use_8_directions)

        for direction in directions:
            direction_offset = direction_map.get(direction, 0)
            key = f"{animation_name}_{direction}"

            existing_anim = self.animations.get(key)
            if not existing_anim:
                continue

            # Convert single animation to list if needed
            if not isinstance(existing_anim, list):
                self.animations[key] = [existing_anim]

            # Load and append new variants
            for variant_index in range(num_variants):
                row = (variant_index * num_directions) + direction_offset
                frames = sprite_sheet.get_all_frames(self.sprite_width, self.sprite_height, row)

                if frames:
                    animation = Animation(frames, frame_duration, loop)
                    self.animations[key].append(animation)

        return True

    def load_animation_branching(self, animation_name, direction, frame_duration=0.1,
                                  num_endings=2, use_8_directions=False):
        """Load an animation from a sheet laid out as [start_frame, ending_1, ending_2, ...]
        on a single row (e.g. kiblast.png: start, right-hand throw, left-hand throw).

        Unlike load_animation(), this does NOT play every frame in sequence. Instead it
        builds `num_endings` separate 2-frame animations — [start, ending_1], [start, ending_2],
        etc. — and set_animation() will randomly choose ONE of them to play, so a single
        trigger shows the start frame plus exactly one ending, never all endings back to back.

        Returns True on success, False if the file is missing or doesn't have enough frames.
        """
        filepath = f"{self.base_path}/{animation_name}.png"

        if not os.path.exists(filepath):
            return False

        sprite_sheet = SpriteSheet(filepath)
        _, direction_map, _ = _directions(use_8_directions)

        row = direction_map.get(direction, 0)
        all_frames = sprite_sheet.get_all_frames(self.sprite_width, self.sprite_height, row)

        # Need at least a start frame plus every ending frame.
        if len(all_frames) < num_endings + 1:
            return False

        start_frame = all_frames[0]
        variants = [
            Animation([start_frame, all_frames[i]], frame_duration, loop=False)
            for i in range(1, num_endings + 1)
        ]

        key = f"{animation_name}_{direction}"
        self.animations[key] = variants if len(variants) > 1 else variants[0]
        return True

    def load_animation_branching_all_directions(self, animation_name, frame_duration=0.1,
                                                 num_endings=2, use_8_directions=False):
        """Branching version of load_animation_all_directions() — see load_animation_branching()."""
        directions, _, _ = _directions(use_8_directions)
        for direction in directions:
            self.load_animation_branching(animation_name, direction, frame_duration,
                                          num_endings, use_8_directions)

    def load_animation_fixed_frames(self, animation_name, direction, frame_duration=0.1,
                                     frame_indices=(0, 2), use_8_directions=False, source_name=None):
        """Load an animation that only plays specific frame indices from the row, in the
        given order — no sequential playback, no randomness. e.g. frame_indices=(0, 2)
        plays frame 0 then frame 2, skipping frame 1 entirely.

        source_name lets multiple differently-keyed animations share one sheet file —
        e.g. source_name='kiblast' reads kiblast.png but registers under 'kiblast_hold2'
        instead of looking for a nonexistent 'kiblast_hold2.png'. Defaults to animation_name.

        Returns True on success, False if the file is missing or doesn't have enough frames.
        """
        filepath = f"{self.base_path}/{source_name or animation_name}.png"

        if not os.path.exists(filepath):
            return False

        sprite_sheet = SpriteSheet(filepath)
        _, direction_map, _ = _directions(use_8_directions)

        row = direction_map.get(direction, 0)
        all_frames = sprite_sheet.get_all_frames(self.sprite_width, self.sprite_height, row)

        if not all_frames or max(frame_indices) >= len(all_frames):
            return False

        frames = [all_frames[i] for i in frame_indices]

        key = f"{animation_name}_{direction}"
        self.animations[key] = Animation(frames, frame_duration, loop=False)
        return True

    def load_animation_fixed_frames_all_directions(self, animation_name, frame_duration=0.1,
                                                    frame_indices=(0, 2), use_8_directions=False,
                                                    source_name=None):
        """Fixed-frame version of load_animation_all_directions() — see load_animation_fixed_frames()."""
        directions, _, _ = _directions(use_8_directions)
        for direction in directions:
            self.load_animation_fixed_frames(animation_name, direction, frame_duration,
                                             frame_indices, use_8_directions, source_name)

    def has_animation(self, animation_name, direction):
        """True if this animation/direction combo was successfully loaded."""
        return f"{animation_name}_{direction}" in self.animations

    def set_animation(self, animation_name, direction):
        """Switch to a different animation, resetting it only if the key actually changed."""
        key = f"{animation_name}_{direction}"

        if key not in self.animations:
            return

        # Only reset if this is actually a different animation
        if self.current_animation != key:
            self.current_animation = key
            self.current_direction = direction

            # Reset the animation(s)
            anim = self.animations[key]
            if isinstance(anim, list):
                # Randomly pick which variant plays this time (covers variants
                # from both the base sheet and any appended extra sheet, e.g.
                # melee.png + melee_extra.png) instead of always using index 0.
                self.current_variant_index = random.randrange(len(anim))
                anim[self.current_variant_index].reset()
            else:
                self.current_variant_index = 0
                anim.reset()

    def restart_animation(self, animation_name, direction):
        """Force this animation to reset to frame 0 and play from the start,
        even if it's already the current animation — set_animation() above
        deliberately no-ops in that case (so callers can safely re-request
        the same animation every frame without constantly resetting it), but
        some callers genuinely want a fresh restart every time they call this
        even mid-playback. Example: an enemy repeatedly hurt by a beam should
        visibly flinch on every single landed hit, not just the first one —
        calling set_animation('hurt', ...) on each hit was a no-op once
        already playing 'hurt', so it only ever looked like it flinched once
        and had to fully finish before flinching again.
        """
        key = f"{animation_name}_{direction}"
        if key not in self.animations:
            return

        self.current_animation = key
        self.current_direction = direction

        anim = self.animations[key]
        if isinstance(anim, list):
            self.current_variant_index = random.randrange(len(anim))
            anim[self.current_variant_index].reset()
        else:
            self.current_variant_index = 0
            anim.reset()

    def update(self, dt):
        """Tick the current animation forward by dt seconds."""
        if not self.current_animation or self.current_animation not in self.animations:
            return

        anim = self.animations[self.current_animation]

        # Handle both single animation and variant lists
        if isinstance(anim, list):
            if 0 <= self.current_variant_index < len(anim):
                anim[self.current_variant_index].update(dt)
        else:
            anim.update(dt)

    def _get_scaled_frame(self, frame):
        """Return `frame` scaled to on-screen size, reusing a cached copy when possible.

        This is the main perf fix in this file: draw() used to call
        pygame.transform.scale() on every single call, for every sprite, every
        frame — but an animation frame is a fixed Surface that only actually
        changes when the animation advances to its next frame (a handful of
        times a second, not 60 times a second). Scaling is one of the more
        expensive things pygame does per-sprite, so caching the scaled result
        keyed by the frame it came from turns "rescale N sprites every tick"
        into "rescale a frame only the first time it's shown."
        """
        cached = self._scaled_frame_cache.get(id(frame))
        if cached is not None:
            return cached

        scaled = pygame.transform.scale(frame, (self._scaled_width, self._scaled_height))
        self._scaled_frame_cache[id(frame)] = scaled
        return scaled

    def draw(self, screen, x, y, camera=None, scale=1.0, hurt_tint=0.0, flash_white=0.0):
        """Draw the current frame at world position (x, y).

        hurt_tint is a 0.0-1.0 value that adds red via BLEND_RGB_ADD —
        this keeps transparent pixels clean instead of drawing a coloured box.

        flash_white is the same idea but a flat white add — a 0.0-1.0
        opacity used for Player.charged_melee_flash_amount's ramp/pulse
        during the charged-melee wind-up (0 = no glow, 1 = full glow).
        A plain bool still works (True behaves like 1.0). Stacks with
        hurt_tint if both are ever active at once (unlikely, but neither
        excludes the other).

        Note: `scale` is accepted for backwards compatibility but isn't used —
        on-screen size is driven entirely by RENDER_SCALE plus this sprite's
        own sprite_width/sprite_height.
        """
        # Sprites created before this cache existed (or via a stale pickle/save)
        # won't have these attributes; build them on first use rather than
        # requiring every construction path to remember to do it.
        if not hasattr(self, '_scaled_frame_cache'):
            self._scaled_frame_cache = {}
            self._scaled_width = int(self.sprite_width * RENDER_SCALE)
            self._scaled_height = int(self.sprite_height * RENDER_SCALE)

        if not self.current_animation or self.current_animation not in self.animations:
            # Draw placeholder if no animation
            if camera:
                screen_x = (x * RENDER_SCALE) - camera.x
                screen_y = (y * RENDER_SCALE) - camera.y
            else:
                screen_x = x * RENDER_SCALE
                screen_y = y * RENDER_SCALE

            pygame.draw.rect(screen, (255, 0, 255),
                             (screen_x - self.offset_x * RENDER_SCALE,
                              screen_y - self.offset_y * RENDER_SCALE,
                              self.sprite_width * RENDER_SCALE,
                              self.sprite_height * RENDER_SCALE))
            return

        anim = self.animations[self.current_animation]

        if isinstance(anim, list):
            if 0 <= self.current_variant_index < len(anim):
                frame = anim[self.current_variant_index].get_current_frame()
            elif anim:
                frame = anim[0].get_current_frame()
            else:
                frame = None
        else:
            frame = anim.get_current_frame()

        if frame:
            # Convert WORLD coordinates to SCREEN coordinates
            # Formula: (world_pos * scale) - camera_screen_pos
            # This matches how spawn objects are drawn in game.py
            if camera:
                screen_x = (x * RENDER_SCALE) - camera.x
                screen_y = (y * RENDER_SCALE) - camera.y
            else:
                screen_x = x * RENDER_SCALE
                screen_y = y * RENDER_SCALE

            frame = self._get_scaled_frame(frame)

            # Hurt tint / charged-melee glow: add colour to each pixel's RGB,
            # alpha untouched → no square artifact. Only copy when actually
            # active, so the common case (neither active) just blits the
            # cached scaled surface directly with no copy at all.
            if hurt_tint > 0 or flash_white > 0:
                frame = frame.copy()
                if hurt_tint > 0:
                    red_amount = int(hurt_tint * 180)
                    frame.fill((red_amount, 0, 0), special_flags=pygame.BLEND_RGB_ADD)
                if flash_white > 0:
                    # A flat (255, 255, 255) add at full strength drives every
                    # pixel straight to pure white — effectively a 100%-opaque
                    # flash with none of the sprite's detail showing through.
                    # Scale it down the same way hurt_tint does above (landing
                    # in the ~0-65% range), then further scale by flash_white
                    # itself so the glow actually ramps/pulses in and out
                    # instead of snapping on at a fixed strength.
                    white_amount = int(min(1.0, flash_white) * 0.65 * 255)
                    frame.fill((white_amount, white_amount, white_amount), special_flags=pygame.BLEND_RGB_ADD)

            offset_x = self._scaled_width // 2
            offset_y = self._scaled_height // 2

            # Draw sprite centered on position
            screen.blit(frame, (screen_x - offset_x, screen_y - offset_y))

    def release_hold(self, animation_name, direction):
        """Let a held animation (see Animation.hold_frames) continue past its
        hold-loop toward its final frame(s). No-op if that animation/direction
        isn't loaded or has no hold segment defined."""
        key = f"{animation_name}_{direction}"
        if key not in self.animations:
            return
        anim = self.animations[key]
        if isinstance(anim, list):
            for variant in anim:
                variant.release_hold()
        else:
            anim.release_hold()

    def is_animation_finished(self):
        """True when the current non-looping animation has played through all its frames."""
        if self.current_animation and self.current_animation in self.animations:
            anim = self.animations[self.current_animation]

            if isinstance(anim, list):
                if 0 <= self.current_variant_index < len(anim):
                    return anim[self.current_variant_index].finished
                return all(variant.finished for variant in anim)
            else:
                return anim.finished
        return False

    def get_current_frame_index(self):
        """Return the 0-based frame index of the active animation variant, or -1 if none."""
        if not self.current_animation or self.current_animation not in self.animations:
            return -1
        anim = self.animations[self.current_animation]
        if isinstance(anim, list):
            idx = getattr(self, 'current_variant_index', 0)
            if 0 <= idx < len(anim):
                return anim[idx].current_frame
            return -1
        return anim.current_frame

    def get_animation_duration(self, animation_name, direction):
        """Total playtime of one full playthrough (frame_count * frame_duration),
        or 0.0 if the animation/direction isn't loaded.

        Lets callers size a timer to match how long an animation actually
        takes instead of guessing with a hardcoded constant — e.g. Player's
        charged-melee lunge/spin action, which must stay active exactly as
        long as 'charged_melee_action' takes to play through, the same way
        beam_charge_required/final_flash_charge_required/etc. are sized from
        their ChargeEffect's get_total_duration() rather than a fixed guess.
        For a variant list, uses the first variant — variants loaded together
        via append_animation_variants()/num_variants share the same frame
        count and frame_duration, so any variant gives the same answer.
        """
        key = f"{animation_name}_{direction}"
        if key not in self.animations:
            return 0.0
        anim = self.animations[key]
        if isinstance(anim, list):
            if not anim:
                return 0.0
            anim = anim[0]
        return len(anim.frames) * anim.frame_duration


def _load_sprite_size(folder, default_w=32, default_h=32):
    """Read frame size from {folder}/sprite_size.txt if it exists.

    Expected format — just one line like:  48x48
    Falls back to default_w / default_h so every existing spritesheet
    that has no config file keeps working at 32×32 without any changes.
    """
    path = os.path.join(folder, 'sprite_size.txt')
    if os.path.isfile(path):
        try:
            with open(path) as f:
                text = f.read().strip().lower()
            w, h = text.split('x')
            return int(w), int(h)
        except Exception:
            pass
    return default_w, default_h


def _sheet_frame_count(filepath, frame_width):
    """Auto-detect how many frames wide a single-row sheet is, without
    building any Animation objects — used to split charged_melee.png into
    a held first frame (charged_melee_hold) and a played-through remainder
    (charged_melee_action) without hardcoding the sheet's length.
    Returns 0 if the file is missing or frame_width is invalid.
    """
    if not os.path.exists(filepath) or frame_width <= 0:
        return 0
    try:
        sheet = pygame.image.load(filepath)
        return sheet.get_width() // frame_width
    except pygame.error:
        return 0


def _has_png(folder):
    """True if `folder` exists and has at least one .png file directly inside it."""
    if not os.path.isdir(folder):
        return False
    return any(
        f.endswith('.png') for f in os.listdir(folder)
        if os.path.isfile(os.path.join(folder, f))
    )


def _resolve_variant_folder(direct_path, variant):
    """Pick which folder actually holds an enemy's/NPC's sprite PNGs.

    Priority order (shared by EnemySpriteLoader and NPCSpriteLoader, which
    used to each duplicate this exact logic):
      1. {direct_path}/variants/{variant}/  — variant carries its own full spriteset
      2. {direct_path}/                     — flat layout, no variant subfolders
    Returns None if direct_path doesn't exist or neither location has PNGs.
    """
    if not os.path.exists(direct_path):
        return None

    variant_path = f"{direct_path}/variants/{variant}"
    if _has_png(variant_path):
        return variant_path
    if _has_png(direct_path):
        return direct_path
    return None


class CharacterSpriteLoader:
    """Loads all standard animations for a playable character."""

    @staticmethod
    def load_character(character_name, costume_name, sprite_width, sprite_height):
        folder = f"assets/sprites/player/{character_name}/{costume_name}"
        sprite_width, sprite_height = _load_sprite_size(folder, sprite_width, sprite_height)
        sprite = AnimatedSprite(character_name, costume_name, sprite_width, sprite_height)

        # Standard 4-directional animations
        animations_4dir = [
            ('idle', 0.3, True, 1),
            ('walk', 0.13, True, 1),
            ('run', 0.13, True, 1),
            ('melee', 0.06, False, 2),  # Load first 2 variants from melee.png
            ('melee2', 0.06, False, 2),
            ('melee3', 0.06, False, 1),
            ('hurt', 0.1, False, 1),
            ('death', 0.15, False, 1),
            ('charge', 0.1, True, 1),
            ('charge_genkidama', 0.1, True, 1),
            ('big_bang_attack', 0.1, True, 1),
            ('hold_masenko', 0.1, True, 1),
            ('charge_sword', 0.1, True, 1),
            ('firebeam', 0.1, True, 1),
            # Banshee Blast: one single pose held for both charging AND
            # firing (see Player.start_charging_banshee_blast/
            # fire_banshee_blast_auto, which set this animation once and
            # never swap to a separate firebeam-style key) — loop=True so
            # it keeps playing for however long the whole charge+fire
            # sequence lasts, same as 'charge'/'firebeam' above. Without
            # this entry, set_animation('banshee_blast', ...) would find
            # no matching key in self.animations and silently no-op,
            # leaving the player stuck on whatever animation was already
            # playing (e.g. 'idle') instead of switching at all.
            ('banshee_blast', 0.1, True, 1),
            ('instant_transmission', 0.1, True, 1),
            ('teleport', 0.3, True, 1),
            ('blocking', 0.2, True, 1),
            ('untransform', 0.15, False, 1),
        ]

        for anim_name, duration, loop, num_variants in animations_4dir:
            sprite.load_animation_all_directions(anim_name, duration, loop, num_variants, use_8_directions=False)

        # 'transform' is loaded separately (not through the generic batch
        # above) because it needs hold_frames: the sheet plays frames 1-3
        # normally, then holds/loops frames 3<->4 (indices 2,3) — a
        # "charging" pause — until TransformationSystem.update() calls
        # release_hold() once its charge timer (transform_animation_duration)
        # completes. Only then does it play frame 5 and finish, which is what
        # drives complete_transform() via Player's is_animation_finished()
        # check. If your transform.png doesn't have exactly 5 frames, adjust
        # the (2, 3) indices to match wherever the hold should sit relative
        # to your actual frame count.
        sprite.load_animation_all_directions('transform', 0.15, False, 1, use_8_directions=False,
                                             hold_frames=(2, 3))

        # Idle-wait animations: after standing still for a while (see
        # Player.IDLE_WAIT_DELAY), the character turns to face the camera and
        # plays a wait pose. idle_transition covers however many lead-in frames
        # it takes to reach the loop point (this can vary per character) and
        # plays once; idle_loop is the actual looping wait pose it hands off
        # to. Both are always shown facing down regardless of the player's
        # real facing direction, so only the down row needs to exist —
        # loaded directly rather than via *_all_directions. Entirely optional:
        # if idle_transition.png/idle_loop.png don't exist for this character,
        # has_animation() simply returns False and the player just never
        # leaves regular idle.
        sprite.load_animation('idle_transition', 'down', frame_duration=0.15, loop=False, num_variants=1)
        sprite.load_animation('idle_wait', 'down', frame_duration=0.5, loop=True, num_variants=1)

        # Item-pickup pose (chest opening — see Player.start_pickup_item).
        # pickup_item.png only has a down-facing pose, no other directions,
        # so it's loaded direct like idle_transition/idle_wait above rather
        # than through animations_4dir. start_pickup_item() always forces
        # the sprite to face 'down' for this animation regardless of which
        # way the player was actually facing; the player's own self.direction
        # is left untouched so enter_idle() resumes facing the right way
        # once the pose ends. loop=True so it just holds/keeps playing for
        # however long game.py's ~1s pickup sequence lasts — the real
        # duration is driven by Player.PICKUP_ITEM_DURATION, not this
        # animation's own frame count.
        sprite.load_animation('pickup_item', 'down', frame_duration=0.15, loop=True, num_variants=1)

        # kiblast.png is laid out as [start, right-hand throw, left-hand throw] on one row.
        # A single Q press always shows frame 0 followed by frame 1 — fixed, not random.
        sprite.load_animation_fixed_frames_all_directions('kiblast', frame_duration=0.4,
                                                           frame_indices=(0, 1), use_8_directions=False)

        # Hold-fire follow-up animations: while Q stays held after the first shot,
        # the player alternates frame 2 → frame 1 → frame 2 → ... firing a blast
        # on each switch (see Player._advance_blast_or_idle). Both read from the
        # same kiblast.png sheet — source_name is required since there's no
        # separate kiblast_hold1.png / kiblast_hold2.png file on disk.
        sprite.load_animation_fixed_frames_all_directions('kiblast_hold2', frame_duration=0.4,
                                                           frame_indices=(2,), use_8_directions=False,
                                                           source_name='kiblast')
        sprite.load_animation_fixed_frames_all_directions('kiblast_hold1', frame_duration=0.4,
                                                           frame_indices=(1,), use_8_directions=False,
                                                           source_name='kiblast')

        # Load third melee variant from melee_extra.png (optional)
        sprite.append_animation_variants('melee', 'melee_extra.png', frame_duration=0.06, loop=False, num_variants=1,
                                         use_8_directions=False)

        # Energy Punch: plays through once, then holds the punch pose by
        # looping its last 2 frames for the remainder of Player.punch_duration
        # (see Player.energy_punch()) instead of freezing on a single frame.
        sprite.load_animation_all_directions('energy_punch', frame_duration=0.1, loop=False, num_variants=1,
                                             use_8_directions=False, loop_tail_frames=2)

        # Dragon Fist: same "play through once, then hold" shape as Energy
        # Punch above. 4-frame sheet — plays 0,1,2,3 once, then loops 2,3
        # for as long as Q is held (see Player.update_dragon_fist(), which
        # also waits for current_frame_index to reach 2 before launching
        # the head).
        sprite.load_animation_all_directions('dragon_fist', frame_duration=0.1, loop=False, num_variants=1,
                                             use_8_directions=False, loop_tail_frames=2)

        # Charged Melee: holding the melee button rolls a normal swing into
        # a wind-up (see Player.start_charging_melee) — frame 0 held while
        # the sprite blinks white — then either a lunge or a rooted spin,
        # both of which just play out whatever frames follow frame 0 on the
        # same sheet (see Player.release_charged_melee). Split into two
        # derived animations so switching from the held charge into the
        # action doesn't replay frame 0 a second time:
        #   charged_melee_hold   — frame 0 alone, held for however long the
        #                          charge lasts (not looped — a single frame
        #                          animation has nothing to loop through, it
        #                          just never reports finished).
        #   charged_melee_action — every frame AFTER 0, played once then
        #                          held automatically on its last frame
        #                          (load_animation_fixed_frames always
        #                          builds non-looping Animations — see
        #                          Animation.update()) for however long the
        #                          lunge/spin actually lasts.
        # Frame count is auto-detected from the sheet's width rather than
        # hardcoded, since it varies per character. Entirely optional: if
        # charged_melee.png doesn't exist yet, has_animation() simply
        # returns False and start_charging_melee()/release_charged_melee()
        # just leave whichever animation was already playing in place.
        _charged_melee_count = _sheet_frame_count(f"{folder}/charged_melee.png", sprite_width)
        if _charged_melee_count > 0:
            sprite.load_animation_fixed_frames_all_directions(
                'charged_melee_hold', frame_duration=0.1, frame_indices=(0,),
                use_8_directions=False, source_name='charged_melee')
        if _charged_melee_count > 1:
            sprite.load_animation_fixed_frames_all_directions(
                'charged_melee_action', frame_duration=0.1,
                frame_indices=tuple(range(1, _charged_melee_count)),
                use_8_directions=False, source_name='charged_melee')

        # Ghost Kamikaze cast: unlike Dragon Fist/Energy Punch above, this
        # genuinely needs to loop (not just play-once-then-hold-tail) —
        # Player.update_ghost_kamikaze_cast() counts completed loops by
        # watching get_current_frame_index() wrap back to 0, spawning one
        # ghost per wrap, so loop=True is required here or the loop-count
        # never advances past 0.
        sprite.load_animation_all_directions('ghost_kamikaze_cast', frame_duration=0.1, loop=True,
                                             num_variants=1, use_8_directions=False)

        # Ghost Kamikaze hold pose: only down has a dedicated sprite
        # (ghost_kamikaze_hold.png, down row only) — left/right/up just
        # freeze on frame 0 of the cast sheet instead of getting their own
        # art. Load the frame-0-of-cast fallback for all 4 directions
        # first (same "read a different sheet, register under a different
        # key" trick as kiblast_hold1/2 above), then load the dedicated
        # down sprite second so it overwrites just that one direction's
        # entry — has_animation()/set_animation() can't tell the
        # difference either way, so update_ghost_kamikaze_cast() doesn't
        # need any direction-specific branching.
        sprite.load_animation_fixed_frames_all_directions('ghost_kamikaze_hold', frame_duration=0.1,
                                                           frame_indices=(0,), use_8_directions=False,
                                                           source_name='ghost_kamikaze_cast')
        sprite.load_animation('ghost_kamikaze_hold', 'down', frame_duration=0.1, loop=True, num_variants=1)

        # 8-directional animations (like flying)
        animations_8dir = [
            ('flying', 0.1, True, 1),
            # Clockwise and counter-clockwise spins are hand-drawn as two
            # separate full sheets (not mirror images of each other), so
            # both get loaded straight — see start_sword_spin() in
            # player.py for how the right one is picked at spin time.
            ('sword_spin_cw', 0.1, True, 1),
            ('sword_spin_ccw', 0.1, True, 1),
        ]

        for anim_name, duration, loop, num_variants in animations_8dir:
            sprite.load_animation_all_directions(anim_name, duration, loop, num_variants, use_8_directions=True)

        sprite.set_animation('idle', 'down')

        return sprite

    @staticmethod
    def list_available_characters():
        sprites_path = "assets/sprites"
        if not os.path.exists(sprites_path):
            return []

        characters = []
        for item in os.listdir(sprites_path):
            char_path = os.path.join(sprites_path, item)
            if os.path.isdir(char_path) and item != 'enemies':
                characters.append(item)
        return characters

    @staticmethod
    def list_available_costumes(character_name):
        char_path = f"assets/sprites/player/{character_name}"
        if not os.path.exists(char_path):
            return []

        costumes = []
        for item in os.listdir(char_path):
            costume_path = os.path.join(char_path, item)
            if os.path.isdir(costume_path):
                costumes.append(item)
        return costumes


class EnemySpriteLoader:
    """Loads all standard animations for an enemy, handling both old and new folder layouts."""

    @staticmethod
    def load_enemy(enemy_type, variant='default', sprite_width=32, sprite_height=32):
        """Load an enemy sprite, checking two folder structures in priority order:

        1. Variant subfolder  assets/sprites/enemies/{type}/variants/{variant}/
           (used when a variant has its own full spriteset, e.g. shooter/variants/gunner/)
        2. Flat folder        assets/sprites/enemies/{type}/
           (sprites sitting directly in the enemy folder, no variant subdirectories)
        """
        direct_path = f"assets/sprites/enemies/{enemy_type}"
        base_path = _resolve_variant_folder(direct_path, variant)
        if base_path is None:
            return None

        sprite_width, sprite_height = _load_sprite_size(base_path, sprite_width, sprite_height)

        sprite = AnimatedSprite._create_bare(enemy_type, variant, sprite_width, sprite_height, base_path)

        # Enemy animations (4-directional)
        animations = [
            ('idle', 0.3, True, 1),
            ('walk', 0.15, True, 1),
            ('attack', 0.1, False, 1),  # Ranged/generic attack animation
            ('melee', 0.1, False, 1),   # Melee swing animation (melee.png)
            ('kiblast', 0.3, False, 1), # Ki blast projectile animation (kiblast.png)
            ('hurt', 0.1, False, 1),
            ('death', 0.15, False, 1),
        ]

        for anim_name, duration, loop, num_variants in animations:
            sprite.load_animation_all_directions(anim_name, duration, loop, num_variants, use_8_directions=False)

        sprite.set_animation('idle', 'down')

        return sprite

    @staticmethod
    def list_available_enemies():
        enemies_path = "assets/sprites/enemies"
        if not os.path.exists(enemies_path):
            return []

        enemies = []
        for item in os.listdir(enemies_path):
            enemy_path = os.path.join(enemies_path, item)
            if os.path.isdir(enemy_path):
                enemies.append(item)
        return enemies

    @staticmethod
    def list_available_variants(enemy_type):
        variants_path = f"assets/sprites/enemies/{enemy_type}/variants"
        if not os.path.exists(variants_path):
            return []

        variants = []
        for item in os.listdir(variants_path):
            variant_path = os.path.join(variants_path, item)
            if os.path.isdir(variant_path):
                variants.append(item)
        return variants


class NPCSpriteLoader:
    """Loads idle/walk animations for a placed NPC.

    Folder layout mirrors EnemySpriteLoader:
      Direct:   assets/sprites/npcs/{npc_type}/
      Variant:  assets/sprites/npcs/{npc_type}/variants/{variant}/
    """

    @staticmethod
    def load_npc(npc_type='generic', variant='default', sprite_width=32, sprite_height=32):
        base = f"assets/sprites/npc/{npc_type}"
        sprite_path = _resolve_variant_folder(base, variant)
        if sprite_path is None:
            return None

        sprite_width, sprite_height = _load_sprite_size(sprite_path, sprite_width, sprite_height)

        sprite = AnimatedSprite._create_bare(npc_type, variant, sprite_width, sprite_height, sprite_path)

        for anim_name, duration, loop in [
            ('idle', 0.3,  True),
            ('walk', 0.12, True),
        ]:
            sprite.load_animation_all_directions(anim_name, duration, loop,
                                                 num_variants=1,
                                                 use_8_directions=False)

        sprite.set_animation('idle', 'down')
        return sprite

    @staticmethod
    def list_available_npcs():
        path = "assets/sprites/npc"
        if not os.path.exists(path):
            return []
        return [d for d in os.listdir(path) if os.path.isdir(os.path.join(path, d))]

    @staticmethod
    def list_available_variants(npc_type='generic'):
        path = f"assets/sprites/npc/{npc_type}/variants"
        if not os.path.exists(path):
            return []
        return [d for d in os.listdir(path) if os.path.isdir(os.path.join(path, d))]


class CritterSpriteLoader:
    """Loads animations for small ambient wildlife (squirrels, birds, butterflies...).

    Folder layout mirrors NPCSpriteLoader/EnemySpriteLoader:
      Direct:   assets/sprites/critters/{critter_type}/
      Variant:  assets/sprites/critters/{critter_type}/variants/{variant}/

    Unlike NPCs/enemies, critters don't share one fixed animation list —
    a butterfly only flies, a squirrel only idles/walks, a bird might do
    both. CRITTER_ANIMATIONS declares, per critter_type, which animations
    to look for and whether they're 4-dir or 8-dir sheets. Anything not
    found on disk is skipped silently (load_animation already no-ops on
    a missing file), so a critter can ship with just one animation file.
    """

    # (animation_name, frame_duration, loop, use_8_directions)
    CRITTER_ANIMATIONS = {
        'squirrel': [
            ('idle', 0.3, True, False),
            ('walk', 0.12, True, False),
        ],
        'bird': [
            ('idle', 0.3, True, False),
            ('walk', 0.15, True, False),
            ('flying', 0.1, True, True),
        ],
        'butterfly': [
            ('flying', 0.1, True, False),
            ('idle', 0.3, True, False),
        ],
    }

    # Fallback for any critter_type not listed above — assume the simplest
    # possible case (idle only, 4-directional) so new critter folders work
    # without a code change; add a real entry above once a critter needs
    # more than that.
    DEFAULT_ANIMATIONS = [
        ('idle', 0.3, True, False),
    ]

    @staticmethod
    def load_critter(critter_type, variant='default', sprite_width=16, sprite_height=16):
        """Load a critter sprite, checking variant/flat folder layouts like enemies/NPCs do."""
        direct_path = f"assets/sprites/critters/{critter_type}"
        base_path = _resolve_variant_folder(direct_path, variant)
        if base_path is None:
            return None

        sprite_width, sprite_height = _load_sprite_size(base_path, sprite_width, sprite_height)

        sprite = AnimatedSprite._create_bare(critter_type, variant, sprite_width, sprite_height, base_path)

        animations = CritterSpriteLoader.CRITTER_ANIMATIONS.get(
            critter_type, CritterSpriteLoader.DEFAULT_ANIMATIONS
        )

        for anim_name, duration, loop, use_8dir in animations:
            sprite.load_animation_all_directions(anim_name, duration, loop, 1, use_8_directions=use_8dir)

        def _loaded(name):
            return sprite.has_animation(name, 'down') or sprite.has_animation(name, 'down_left')

        if not any(_loaded(name) for name, *_ in animations):
            return None

        # Prefer idle as the resting pose; fall back to whichever animation
        # actually loaded (e.g. a butterfly that only has 'flying').
        default_anim = 'idle' if _loaded('idle') else next(
            (name for name, *_ in animations if _loaded(name)), None
        )
        if default_anim:
            sprite.set_animation(default_anim, 'down')

        return sprite

    @staticmethod
    def list_available_critters():
        critters_path = "assets/sprites/critters"
        if not os.path.exists(critters_path):
            return []
        return [d for d in os.listdir(critters_path) if os.path.isdir(os.path.join(critters_path, d))]

    @staticmethod
    def list_available_variants(critter_type):
        variants_path = f"assets/sprites/critters/{critter_type}/variants"
        if not os.path.exists(variants_path):
            return []
        return [d for d in os.listdir(variants_path) if os.path.isdir(os.path.join(variants_path, d))]


def create_character_sprite(character, costume='base', width=32, height=32):
    """Shorthand for CharacterSpriteLoader.load_character."""
    return CharacterSpriteLoader.load_character(character, costume, width, height)


def create_enemy_sprite(enemy_type, variant='default', width=32, height=32):
    """Shorthand for EnemySpriteLoader.load_enemy."""
    return EnemySpriteLoader.load_enemy(enemy_type, variant, width, height)


def create_npc_sprite(npc_type, variant='default', width=32, height=32):
    """Shorthand for NPCSpriteLoader.load_npc."""
    return NPCSpriteLoader.load_npc(npc_type, variant, width, height)


def create_critter_sprite(critter_type, variant='default', width=16, height=16):
    """Shorthand for CritterSpriteLoader.load_critter."""
    return CritterSpriteLoader.load_critter(critter_type, variant, width, height)


def create_boss_sprite(boss_id, variant='default', width=48, height=48):
    """Load a boss sprite from assets/sprites/enemies/boss/{boss_id}/.

    Returns None gracefully if the folder doesn't exist yet.
    """
    boss_path = f"assets/sprites/enemies/boss/{boss_id}"

    if not os.path.exists(boss_path):
        return None

    width, height = _load_sprite_size(boss_path, width, height)

    sprite = AnimatedSprite._create_bare(boss_id, variant, width, height, boss_path)

    animations = [
        ('idle',    0.3,  True,  1),
        ('walk',    0.15, True,  1),
        ('attack',  0.1,  False, 1),
        ('melee',   0.1,  False, 1),
        ('kiblast', 0.3,  False, 1), # Ki blast projectile animation (kiblast.png)
        ('hurt',    0.1,  False, 1),
        ('death',   0.15, False, 1),
    ]

    for anim_name, duration, loop, num_variants in animations:
        sprite.load_animation_all_directions(anim_name, duration, loop, num_variants, use_8_directions=False)

    sprite.set_animation('idle', 'down')
    return sprite