import os, sys, time, threading, queue, select
from joki.state import _joki_cancel, _IS_TTY, _console
from joki.display import stream_print

class ThinkingDisplay:
    def __init__(self):
        self._stop = threading.Event()
        self._queue = queue.Queue()
        self._thread = None
        self._buf = ""

    def push(self, text):
        self._queue.put(text)

    def start(self):
        self._stop.clear()
        self._buf = ""
        if _IS_TTY:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def _run(self):
        esc_count = 0
        last_esc = 0.0
        while not self._stop.is_set():
            while True:
                try:
                    token = self._queue.get_nowait()
                    self._buf += token
                except queue.Empty:
                    break

            while '\n' in self._buf:
                line, self._buf = self._buf.split('\n', 1)
                stripped = line.strip()
                if stripped:
                    sys.stdout.write(f"🤔 {stripped}\n")
                    sys.stdout.flush()

            if self._stop.is_set():
                return

            now = time.time()
            if now - last_esc > 1.0:
                esc_count = 0

            if select.select([sys.stdin], [], [], 0.05)[0]:
                key = sys.stdin.read(1)
                if key == '\x1b':
                    esc_count += 1
                    last_esc = now
                    if esc_count >= 2:
                        _joki_cancel.set()
                        self._stop.set()
                        sys.stdout.write("\r\033[KDibatalkan pengguna (Esc Esc)\n")
                        sys.stdout.flush()
                        return
                elif key == '\x03':
                    return

            time.sleep(0.01)

    def finish(self):
        self._stop.set()
        if self._thread:
            self._thread.join()

        remaining = self._buf.strip()
        if remaining:
            sys.stdout.write(f"🤔 {remaining}\n")
            sys.stdout.flush()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.finish()