"""Positional ambient sound emitter used by the object editor."""

import time


class AmbientSoundObject:
    """A placed positional ambient-loop emitter.

    The object owns only world/config state plus its transient mixer Channel.
    Runtime persistence should serialize x/y, sound_name, max_distance and
    fade_duration; the channel and fade envelope are deliberately never
    serialized.
    """

    width = 16
    height = 16

    # Default time (seconds) the fade envelope takes to go fully in or out.
    # This is independent of max_distance/falloff: the distance-based volume
    # already ramps smoothly as the player walks toward/away from the
    # emitter, but it snaps straight to whatever that falloff value is the
    # instant the emitter becomes audible/inaudible (e.g. the player
    # sprinting straight into the zone, or the zone already covering the
    # player's spawn point when a room loads). The time-based envelope below
    # smooths out that instant, on top of the existing distance falloff.
    DEFAULT_FADE_DURATION = 0.6

    def __init__(self, x, y, sound_name='', max_distance=256, fade_duration=None):
        self.x = int(x)
        self.y = int(y)
        self.sound_name = str(sound_name or '')
        self.max_distance = max(1, int(max_distance))
        self.fade_duration = (
            self.DEFAULT_FADE_DURATION if fade_duration is None
            else max(0.0, float(fade_duration))
        )
        self.channel = None

        # Temporal fade envelope, separate from the distance-based falloff.
        # 0.0 = not yet faded in at all, 1.0 = fully faded in. Ramps toward
        # 1.0 whenever the player is within max_distance, and back toward
        # 0.0 otherwise (or once the emitter is silenced).
        self._fade_level = 0.0
        self._last_update_time = None

    def update_audio(self, player_x, player_y, sound_manager):
        """Drive this emitter's private positional BGS Channel.

        Distance-based volume is 1.0 at the emitter and falls linearly to
        0.0 at max_distance, same as before. That's multiplied by a
        time-based fade envelope that ramps 0 -> 1 over fade_duration
        seconds once the player enters range, and 1 -> 0 once they leave,
        so the loop eases in/out instead of jumping straight to whatever
        the distance falloff happens to be on the first audible frame.
        SoundEngine applies the global BGS volume on top of whatever this
        method sends it.
        """
        now = time.monotonic()
        dt = 0.0 if self._last_update_time is None else max(0.0, now - self._last_update_time)
        self._last_update_time = now

        if sound_manager is None or not self.sound_name:
            self.stop_audio(sound_manager)
            return 0.0

        dx = float(player_x) - self.x
        dy = float(player_y) - self.y
        distance = (dx * dx + dy * dy) ** 0.5
        distance_volume = max(0.0, min(1.0, 1.0 - distance / float(self.max_distance)))
        in_range = distance_volume > 0.0

        if self.fade_duration > 0.0:
            step = dt / self.fade_duration
        else:
            step = 1.0  # No fade configured — snap instantly, old behavior.

        if in_range:
            self._fade_level = min(1.0, self._fade_level + step)
        else:
            self._fade_level = max(0.0, self._fade_level - step)

        volume = distance_volume * self._fade_level

        # Do not allocate a mixer channel until there's actually something
        # to fade in. Once an emitter has been heard we keep its channel
        # alive (even at volume 0) instead of stop/restarting it every time
        # the player crosses the boundary — see stop_audio() for the one
        # place a channel is actually released.
        if self.channel is None and (in_range or self._fade_level > 0.0):
            self.channel = sound_manager.play_positional_bgs(self.sound_name)

        if self.channel is not None:
            # A toggle/cleanup may have stopped the old Channel behind us. In
            # that case drop the stale handle so the next in-range frame can
            # obtain a fresh one.
            try:
                if not self.channel.get_busy():
                    self.channel = None
                    self._fade_level = 0.0
                else:
                    sound_manager.set_positional_bgs_volume(self.channel, volume)
            except Exception:
                self.channel = None
                self._fade_level = 0.0

        return volume

    def stop_audio(self, sound_manager):
        if self.channel is not None and sound_manager is not None:
            try:
                sound_manager.stop_positional_bgs(self.channel)
            except Exception:
                pass
        self.channel = None
        self._fade_level = 0.0

    def to_dict(self):
        return {
            'x': self.x,
            'y': self.y,
            'sound_name': self.sound_name,
            'max_distance': self.max_distance,
            'fade_duration': self.fade_duration,
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            data.get('x', 0), data.get('y', 0),
            data.get('sound_name', data.get('sound', '')),
            data.get('max_distance', data.get('radius', 256)),
            data.get('fade_duration'),
        )