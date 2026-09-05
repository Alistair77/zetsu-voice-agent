"""The mouth. Text out loud through macOS `say`.

Seam: give it text, it speaks. Sentences are spoken as soon as they are complete,
so Zetsu starts talking while the rest of the reply is still being written.
Swapping in a different voice engine means rewriting `_say` and nothing else.
"""

import queue
import re
import subprocess
import threading

from config import CONFIG

# Speak on sentence boundaries — waiting for the whole reply is what makes an
# assistant feel laggy.
BOUNDARY = re.compile(r"(?<=[.!?])\s+|\n+")


class Speaker:
    """Feed it streamed text; it speaks complete sentences in order."""

    def __init__(self, cfg=CONFIG):
        self.voice = cfg["voice"]["say_voice"]
        self.rate = str(cfg["voice"]["say_rate"])
        self.queue = queue.Queue()
        self.buffer = ""
        self.current = None
        self.worker = threading.Thread(target=self._drain, daemon=True)
        self.worker.start()

    def _say(self, text):
        self.current = subprocess.Popen(
            ["say", "-v", self.voice, "-r", self.rate, text],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.current.wait()
        self.current = None

    def _drain(self):
        while True:
            text = self.queue.get()
            if text is None:
                return
            try:
                self._say(text)
            except Exception:
                pass  # a dead voice must never take the conversation down
            finally:
                self.queue.task_done()

    def feed(self, delta):
        """Take a chunk of streamed text; speak whatever sentences are complete."""
        self.buffer += delta
        while True:
            parts = BOUNDARY.split(self.buffer, maxsplit=1)
            if len(parts) < 2:
                return
            sentence, self.buffer = parts[0].strip(), parts[1]
            if sentence:
                self.queue.put(sentence)

    def flush(self):
        """Speak the tail end that never got a full stop."""
        if tail := self.buffer.strip():
            self.queue.put(tail)
        self.buffer = ""

    def say_now(self, text):
        """Jump the queue — used by the confirmation gate, which can't wait."""
        self.queue.put(text)

    def wait(self):
        self.queue.join()

    def is_busy(self):
        """True while anything is queued or actually being spoken.

        The wake loop asks this before every capture. Without it the open mic
        hears Zetsu say its own name and answers itself, forever.
        """
        return bool(self.buffer) or not self.queue.empty() or self.current is not None

    def stop(self):
        """Shut up immediately. The user starting a new turn always wins."""
        self.buffer = ""
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
                self.queue.task_done()
            except queue.Empty:
                break
        if self.current and self.current.poll() is None:
            self.current.terminate()
