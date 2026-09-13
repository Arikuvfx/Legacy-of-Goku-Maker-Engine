"""Positional ambient sound emitter used by the object editor."""


class AmbientSoundObject:
    """A placed positional ambient-loop emitter.

    The object owns only world/config state plus its transient mixer Channel.
    Runtime persistence should serialize x/y, sound_name and max_distance; the
    channel itself is deliberately never serialized.
    """

    width = 16
    height = 16

    def __init__(self, x, y, sound_name='', max_distance=256):
        self.x = int(x)
        self.y = int(y)
        self.sound_name = str(sound_name or '')
        self.max_distance = max(1, int(max_distance))
        self.channel = None

    def update_audio(self, player_x, player_y, sound_manager):
        """Drive this emitter's private positional BGS Channel.

        Volume is 1.0 at the emitter and falls linearly to 0.0 at
        max_distance. SoundEngine applies the global BGS volume afterward, so
        this method intentionally returns only a normalized distance factor.
        """
        if sound_manager is None or not self.sound_name:
            self.stop_audio(sound_manager)
            return 0.0

        dx = float(player_x) - self.x
        dy = float(player_y) - self.y
        distance = (dx * dx + dy * dy) ** 0.5
        volume = max(0.0, min(1.0, 1.0 - distance / float(self.max_distance)))

        # Do not allocate a mixer channel while fully out of range. Once an
        # emitter has been heard we keep its channel alive at volume 0 instead
        # of stop/restarting at the boundary every frame.
        if self.channel is None and volume > 0.0:
            self.channel = sound_manager.play_positional_bgs(self.sound_name)

        if self.channel is not None:
            # A toggle/cleanup may have stopped the old Channel behind us. In
            # that case drop the stale handle so the next in-range frame can
            # obtain a fresh one.
            try:
                if not self.channel.get_busy():
                    self.channel = None
                else:
                    sound_manager.set_positional_bgs_volume(self.channel, volume)
            except Exception:
                self.channel = None

        return volume

    def stop_audio(self, sound_manager):
        if self.channel is not None and sound_manager is not None:
            try:
                sound_manager.stop_positional_bgs(self.channel)
            except Exception:
                pass
        self.channel = None

    def to_dict(self):
        return {
            'x': self.x,
            'y': self.y,
            'sound_name': self.sound_name,
            'max_distance': self.max_distance,
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            data.get('x', 0), data.get('y', 0),
            data.get('sound_name', data.get('sound', '')),
            data.get('max_distance', data.get('radius', 256)),
        )