"""
core/asset_watcher.py — generic background file watcher for hot-reloading assets.

Why polling instead of an OS-level watcher (e.g. the `watchdog` package)?
This keeps the engine dependency-free — no extra pip install required for
anyone who clones the project — at the cost of up to `poll_interval` seconds
of latency before a change is picked up. For asset iteration (drop a new
.wav, tweak a sprite, edit a room JSON) that latency is imperceptible; if
you later want instant OS-level events, AssetWatcher's public interface
(start/stop/poll_events) is exactly the shape you'd need to swap in a
watchdog-backed implementation without touching any calling code.

IMPORTANT: this class only *detects* changes, on a background thread. It
never touches pygame/SDL itself. Actually reloading an asset (creating a new
pygame.mixer.Sound, re-parsing a room JSON, etc.) should happen on the main
thread, once per frame, by draining poll_events() and acting on the results.
That keeps every SDL/pygame call on the thread that initialized pygame,
which is the only thread pygame reliably supports.
"""

import os
import queue
import threading
import time


class AssetWatcher:
    """Watches one or more directories (recursively) for files being added,
    modified, or removed, and reports them through a thread-safe queue.

    Usage:
        watcher = AssetWatcher(['assets/audio'], extensions=('.wav', '.ogg'))
        watcher.start()
        ...
        # once per frame, on the main thread:
        for kind, path in watcher.poll_events():
            handle(kind, path)
        ...
        watcher.stop()   # e.g. in cleanup()
    """

    def __init__(self, watch_paths, extensions=None, poll_interval=1.0):
        """
        watch_paths: iterable of directory paths to watch recursively.
                     Non-existent paths are skipped silently (so you can
                     watch a folder that doesn't exist yet — nothing breaks
                     if a user hasn't created assets/audio/sfx, say).
        extensions:  optional iterable of lowercase extensions (e.g.
                     ('.wav', '.ogg')) to restrict what's tracked. None
                     means "watch every file".
        poll_interval: seconds between scans. 1.0 is a good default for
                     asset iteration — fast enough to feel instant, cheap
                     enough to never show up in a profiler.
        """
        self.watch_paths = list(watch_paths)
        self.extensions = tuple(e.lower() for e in extensions) if extensions else None
        self.poll_interval = poll_interval

        self._events = queue.Queue()
        self._snapshot = {}
        self._stop_flag = threading.Event()
        self._thread = None

    def start(self):
        """Take an initial snapshot (so pre-existing files don't all fire as
        'added' the moment the watcher starts) and spin up the background
        thread. Safe to call once; calling twice restarts the thread."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_flag.clear()
        self._snapshot = self._scan()
        self._thread = threading.Thread(
            target=self._run, name="AssetWatcher", daemon=True
        )
        self._thread.start()

    def stop(self):
        """Signal the background thread to stop and wait briefly for it to
        exit. Safe to call even if start() was never called."""
        self._stop_flag.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def poll_events(self):
        """Drain and return every event queued since the last call, as a
        list of (kind, path) tuples where kind is 'added', 'modified', or
        'removed'. Call this once per frame from the main thread — cheap
        and non-blocking even when nothing changed."""
        events = []
        while True:
            try:
                events.append(self._events.get_nowait())
            except queue.Empty:
                break
        return events

    # ── internal ─────────────────────────────────────────────────────────

    def _scan(self):
        """Walk every watched directory and return {filepath: mtime}."""
        snapshot = {}
        for base in self.watch_paths:
            if not os.path.isdir(base):
                continue
            for root, _dirs, filenames in os.walk(base):
                for filename in filenames:
                    if self.extensions and not filename.lower().endswith(self.extensions):
                        continue
                    path = os.path.join(root, filename)
                    try:
                        snapshot[path] = os.path.getmtime(path)
                    except OSError:
                        # File got deleted between listdir and getmtime —
                        # treat it as absent this scan, next scan will
                        # correctly see it as 'removed' if it's really gone.
                        pass
        return snapshot

    def _run(self):
        while not self._stop_flag.is_set():
            # Sleep in small slices so stop() doesn't have to wait a full
            # poll_interval to take effect.
            slept = 0.0
            while slept < self.poll_interval and not self._stop_flag.is_set():
                time.sleep(min(0.1, self.poll_interval - slept))
                slept += 0.1
            if self._stop_flag.is_set():
                break

            try:
                current = self._scan()
            except Exception as e:
                print(f"AssetWatcher: scan failed: {e}")
                continue

            old_paths = set(self._snapshot)
            new_paths = set(current)

            for path in new_paths - old_paths:
                self._events.put(('added', path))
            for path in old_paths - new_paths:
                self._events.put(('removed', path))
            for path in new_paths & old_paths:
                if current[path] != self._snapshot[path]:
                    self._events.put(('modified', path))

            self._snapshot = current