

import os
import sys
import json
import time
import uuid
import struct
import socket
import threading


DISCORD_CLIENT_ID = "1555909606524452894"
LARGE_IMAGE = "large_icon"
LARGE_TEXT = "Legacy of Goku Engine"

_MIN_UPDATE_INTERVAL = 15.0
_RECONNECT_INTERVAL = 30.0


_last_log = [None]


def _log(msg):

    if msg == _last_log[0]:
        return
    _last_log[0] = msg
    line = time.strftime('%H:%M:%S ') + '[discord] ' + msg
    print(line)


class _Ipc:


    def __init__(self, client_id):
        self.client_id = client_id
        self._f = None      # Windows named pipe
        self._s = None      # Unix socket

    def connect(self):
        for i in range(10):
            try:
                if sys.platform == 'win32':
                    self._f = open(r'\\?\pipe\discord-ipc-%d' % i, 'r+b', buffering=0)
                else:
                    base = (os.environ.get('XDG_RUNTIME_DIR') or os.environ.get('TMPDIR')
                            or os.environ.get('TMP') or os.environ.get('TEMP') or '/tmp')
                    s = socket.socket(socket.AF_UNIX)
                    s.settimeout(5)
                    s.connect(os.path.join(base, 'discord-ipc-%d' % i))
                    self._s = s
                break
            except OSError:
                self._f = self._s = None
        else:
            raise ConnectionError('Discord not running')
        self._send(0, {'v': 1, 'client_id': self.client_id})
        self._recv()     # raises if Discord rejects the client id

    def _send(self, op, payload):
        data = json.dumps(payload).encode('utf-8')
        buf = struct.pack('<II', op, len(data)) + data
        if self._f:
            self._f.write(buf)
        else:
            self._s.sendall(buf)

    def _read(self, n):
        out = b''
        while len(out) < n:
            chunk = self._f.read(n - len(out)) if self._f else self._s.recv(n - len(out))
            if not chunk:
                raise ConnectionError('Discord closed the connection')
            out += chunk
        return out

    def _recv(self):
        op, length = struct.unpack('<II', self._read(8))
        data = json.loads(self._read(length).decode('utf-8'))
        if op == 2 or data.get('evt') == 'ERROR':
            raise ConnectionError('Discord rejected the request: %s' % (data,))
        return op, data

    def set_activity(self, activity):
        self._send(1, {'cmd': 'SET_ACTIVITY',
                       'args': {'pid': os.getpid(), 'activity': activity},
                       'nonce': str(uuid.uuid4())})
        self._recv()

    def close(self):
        try:
            if self._f:
                self._f.close()
            if self._s:
                self._s.close()
        except Exception:
            pass
        self._f = self._s = None


class DiscordPresence:
    def __init__(self):
        self.client_id = (os.environ.get('DISCORD_CLIENT_ID') or DISCORD_CLIENT_ID).strip()
        self.enabled = bool(self.client_id)
        self._lock = threading.Lock()
        self._wanted = None
        self._sent = None
        self._stop = threading.Event()
        self._start_time = int(time.time())
        self._thread = None
        if not self.enabled:
            _log('disabled: DISCORD_CLIENT_ID is empty')
        else:
            _log('starting (client id %s)' % self.client_id)
            self._thread = threading.Thread(target=self._worker, name='discord-rpc', daemon=True)
            self._thread.start()



    def set_state(self, details, state=None):
        """Record the presence we want shown. Cheap; safe to call every frame."""
        if not self.enabled:
            return
        activity = {'details': (details or '')[:128] or None,
                    'state': (state or '')[:128] or None,
                    'timestamps': {'start': self._start_time}}
        if LARGE_IMAGE:
            activity['assets'] = {'large_image': LARGE_IMAGE, 'large_text': LARGE_TEXT}
        activity = {k: v for k, v in activity.items() if v is not None}
        with self._lock:
            self._wanted = activity

    def shutdown(self):
        if self.enabled:
            self._stop.set()
            if self._thread:
                self._thread.join(timeout=2.0)



    def _worker(self):
        ipc = None
        last_send = 0.0
        next_connect = 0.0
        while not self._stop.wait(1.0):
            now = time.time()
            if ipc is None:
                if now < next_connect:
                    continue
                try:
                    ipc = _Ipc(self.client_id)
                    ipc.connect()
                    self._sent = None
                    _log('connected to Discord')
                except Exception as e:
                    _log('connect failed: %s: %s' % (type(e).__name__, e))
                    if ipc:
                        ipc.close()
                    ipc = None
                    next_connect = now + _RECONNECT_INTERVAL
                    continue
            with self._lock:
                wanted = self._wanted
            if wanted is None or wanted == self._sent or now - last_send < _MIN_UPDATE_INTERVAL:
                continue
            try:
                ipc.set_activity(wanted)
                self._sent = dict(wanted)
                last_send = now
                _log('presence sent: %s / %s' % (wanted.get('details'), wanted.get('state')))
            except Exception as e:
                _log('update failed: %s: %s' % (type(e).__name__, e))
                ipc.close()
                ipc = None
                next_connect = now + _RECONNECT_INTERVAL
        if ipc is not None:
            try:
                ipc.set_activity(None)
            except Exception:
                pass
            ipc.close()