"""Shared screen capture loop that feeds every open live stream (MJPEG)."""
import threading
import time

MAX_FPS = 60
DEFAULT_FPS = 30
HEARTBEAT_SECONDS = 2.0
IDLE_AFTER_SECONDS = 1.0
IDLE_INTERVAL = 0.2
FIRST_FRAME_SECONDS = 10
ERROR_RETRY_SECONDS = 1.0
STOPPED = 'Mac에서 화면 공유를 종료했습니다.'


def clamp_fps(value):
    try:
        fps = int(value)
    except (TypeError, ValueError):
        return DEFAULT_FPS
    return max(1, min(MAX_FPS, fps))


class FrameHub:
    """Captures only while someone watches, and only the newest frame is ever sent to a viewer."""

    def __init__(self, capture, clock=time.monotonic):
        self.capture = capture
        self.clock = clock
        self.cond = threading.Condition()
        self.viewers = {}
        self.next_viewer = 0
        self.running = False
        self.stopped = False
        self.seq = 0
        self.frame = None
        self.error = None
        self.captured = 0

    def subscribe(self, fps):
        with self.cond:
            if self.stopped:
                raise RuntimeError(STOPPED)
            self.next_viewer += 1
            viewer = self.next_viewer
            self.viewers[viewer] = clamp_fps(fps)
            if not self.running:
                self.running = True
                threading.Thread(target=self._loop, daemon=True).start()
            return viewer

    def unsubscribe(self, viewer):
        with self.cond:
            self.viewers.pop(viewer, None)
            self.cond.notify_all()

    def stop(self):
        with self.cond:
            self.stopped = True
            self.cond.notify_all()

    def wait_first(self, timeout=FIRST_FRAME_SECONDS):
        """Return (seq, frame) once there is a frame; raise RuntimeError if capturing fails."""
        deadline = self.clock() + timeout
        with self.cond:
            while self.frame is None and not self.stopped:
                if self.error is not None:
                    raise RuntimeError(self.error)
                remaining = deadline - self.clock()
                if remaining <= 0:
                    raise RuntimeError('Mac에서 화면을 가져오는 데 시간이 너무 오래 걸렸습니다.')
                self.cond.wait(remaining)
            if self.stopped:
                raise RuntimeError(STOPPED)
            return self.seq, self.frame

    def wait_next(self, last_seq, timeout=HEARTBEAT_SECONDS):
        """Return (seq, frame, fresh). A repeated frame (fresh False) keeps idle streams alive."""
        with self.cond:
            if self.seq == last_seq and not self.stopped:
                self.cond.wait(timeout)
            if self.stopped:
                return None
            return self.seq, self.frame, self.seq != last_seq

    def _loop(self):
        last_change = self.clock()
        while True:
            with self.cond:
                if self.stopped or not self.viewers:
                    self.running = False
                    return
                interval = 1.0 / max(self.viewers.values())
            started = self.clock()
            try:
                data = self.capture()
            except Exception as exc:  # The capture backend decides what can fail.
                with self.cond:
                    self.error = (str(exc) or 'Mac 화면을 가져오지 못했습니다.')[:800]
                    self.cond.notify_all()
                time.sleep(ERROR_RETRY_SECONDS)
                continue
            now = self.clock()
            with self.cond:
                self.error = None
                self.captured += 1
                if data != self.frame:
                    self.frame = data
                    self.seq += 1
                    last_change = now
                    self.cond.notify_all()
            if now - last_change > IDLE_AFTER_SECONDS:
                interval = max(interval, IDLE_INTERVAL)
            delay = started + interval - self.clock()
            if delay > 0:
                time.sleep(delay)
