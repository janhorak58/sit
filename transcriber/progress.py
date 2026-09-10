"""Progress of the single in-flight transcription job, polled by the UI."""

import threading

IDLE = {
    "stage": "idle",
    "percent": 0,
    "message": "",
    "text": None,
    "error": None,
    "warning": None,
    "saved_path": None,
    "backend": None,
    "source_path": None,
    "operation": None,
    "diarization_backend": None,
}


class Progress:
    def __init__(self):
        self._lock = threading.Lock()
        self._state = dict(IDLE)

    def begin(self, **fields):
        """Start the only supported job, returning False while one is active."""
        with self._lock:
            if self._state["stage"] in {"transcribing", "diarizing", "merging", "analyzing"}:
                return False
            self._state = {**IDLE, "stage": "transcribing", **fields}
            return True

    def update(self, **fields):
        with self._lock:
            self._state.update(fields)


    def finish(self, **fields):
        with self._lock:
            self._state.update(fields)

    def snapshot(self):
        with self._lock:
            return dict(self._state)


progress = Progress()
