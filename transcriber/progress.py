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
        # The worker thread that owns the active job's updates. Bound lazily
        # to whichever thread reports progress first after begin()/cancel(),
        # so a wedged job's thread that keeps running after cancel() cannot
        # write into the state of whatever job replaces it.
        self._owner = None

    def begin(self, **fields):
        """Start the only supported job, returning False while one is active."""
        with self._lock:
            if self._state["stage"] in {"transcribing", "diarizing", "merging", "analyzing"}:
                return False
            self._state = {**IDLE, "stage": "transcribing", **fields}
            self._owner = None
            return True

    def _accept(self):
        current = threading.current_thread()
        if self._owner is None:
            self._owner = current
        return self._owner is current

    def update(self, **fields):
        with self._lock:
            if self._accept():
                self._state.update(fields)

    def finish(self, **fields):
        with self._lock:
            if self._accept():
                self._state.update(fields)

    def cancel(self):
        """Force a wedged job back to idle so a new one can start. ``_owner``
        is set to a sentinel no thread can ever match, so the old worker's
        late update()/finish() calls are silently dropped instead of
        resurrecting this state; the next begin() reopens it for a claim."""
        with self._lock:
            self._state = dict(IDLE)
            self._owner = object()

    def snapshot(self):
        with self._lock:
            return dict(self._state)


progress = Progress()
