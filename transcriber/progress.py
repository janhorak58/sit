"""Progress of the single in-flight transcription job, polled by the UI."""

import threading

IDLE = {
    "stage": "idle",
    "percent": 0,
    "message": "",
    "text": None,
    "error": None,
    "saved_path": None,
}


class Progress:
    def __init__(self):
        self._lock = threading.Lock()
        self._state = dict(IDLE)

    def update(self, **fields):
        with self._lock:
            self._state.update(fields)

    def reset(self, **fields):
        with self._lock:
            self._state = {**IDLE, **fields}

    def snapshot(self):
        with self._lock:
            return dict(self._state)


progress = Progress()
