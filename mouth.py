"""The mouth. Text out loud through macOS `say`.

Seam: give it text, it speaks. Sentences are spoken as soon as they are complete,
so Zetsu starts talking while the rest of the reply is still being written.
Swapping in a different voice engine means rewriting `_say` and nothing else.
"""

import difflib
import queue
import re
import subprocess
import threading
import time

from config import CONFIG

# Speak on sentence boundaries — waiting for the whole reply is what makes an
# assistant feel laggy. A long first sentence is still a bad voice UI, though,
# so `feed` also releases a word boundary once the buffer reaches the configured
# cap. Native streaming TTS would be smoother; this keeps the built-in `say`
# fallback from waiting for a paragraph.
BOUNDARY = re.compile(r"(?<=[.!?])\s+|\n+")


def _words(text):
    """Lowercase words, punctuation dropped — the air mangles everything else."""
    return [
        "".join(c for c in word.lower() if c.isalnum())
        for word in text.split()
        if any(c.isalnum() for c in word)
    ]


class Speaker:
    """Feed it streamed text; it speaks complete sentences in order."""

    def __init__(self, cfg=CONFIG):
        self.voice = cfg["voice"]["say_voice"]
        self.rate = str(cfg["voice"]["say_rate"])
        self.queue = queue.Queue()
        self.buffer = ""
        self.chunk_chars = cfg["voice"].get("speak_chunk_chars", 120)
        self.current = None
        # What it has said lately, so the ears can recognise their own echo.
        self.echo = []
        self.echo_seconds = cfg["voice"].get("echo_memory_seconds", 20)
        self.echo_threshold = cfg["voice"].get("echo_threshold", 0.45)
        self.worker = threading.Thread(target=self._drain, daemon=True)
        self.worker.start()

    def remember_saying(self, text):
        now = time.time()
        self.echo = [(when, said) for when, said in self.echo
                     if now - when < self.echo_seconds]
        self.echo.append((now, text))

    def sounds_like_me(self, heard, threshold=None):
        """True if `heard` is mostly words this speaker just said out loud.

        A laptop speaker feeds Zetsu's own voice back into the microphone, and
        without this the assistant interrupts itself mid-sentence. Compares word
        overlap rather than exact text, because the round trip through the air
        mangles it: "Your open todos" comes back as "You're open".
        """
        threshold = self.echo_threshold if threshold is None else threshold
        words = _words(heard)
        if len(words) < 2:
            return False  # too short to judge; let min-words handle it

        now = time.time()
        recent = [said for when, said in self.echo if now - when < self.echo_seconds]
        if not recent:
            return False

        spoken = set()
        for said in recent:
            spoken.update(_words(said))
        if sum(1 for word in words if word in spoken) / len(words) >= threshold:
            return True

        # Word overlap alone is too fragile: coming back through the air,
        # "Obito" returns as "A bito" — two words instead of one, which drags
        # the ratio under the line. Compare the raw character sequence too, so a
        # mangled echo is still recognised as an echo.
        heard_flat = " ".join(words)
        return any(
            difflib.SequenceMatcher(None, heard_flat, " ".join(_words(said))).ratio()
            >= threshold
            or heard_flat in " ".join(_words(said))
            for said in recent
        )

    def _say(self, text):
        self.remember_saying(text)
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
            if len(parts) >= 2:
                sentence, self.buffer = parts[0].strip(), parts[1]
            elif len(self.buffer) >= getattr(self, "chunk_chars", 120):
                # Keep words intact. This is a latency escape hatch, not a
                # replacement for punctuation-aware speech.
                chunk_chars = getattr(self, "chunk_chars", 120)
                cut = self.buffer.rfind(" ", 0, chunk_chars + 1)
                if cut <= 0:
                    return
                sentence, self.buffer = self.buffer[:cut].strip(), self.buffer[cut + 1:]
            else:
                return
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
