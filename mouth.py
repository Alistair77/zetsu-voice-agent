"""The mouth. Text out loud.

Seam: give it text, it speaks. Sentences are spoken as soon as they are complete,
so Zetsu starts talking while the rest of the reply is still being written.
Swapping in a different voice engine means rewriting `_say` and nothing else.

Two engines. Piper is a local neural voice — it sounds like a person, and its
model is loaded once and kept in memory (0.07-0.24s a sentence; reloading it
each time costs 0.95s, the same trap whisper had). macOS `say` is the fallback:
instant, always present, unmistakably a robot. If Piper is missing or fails to
load, speech degrades to `say` rather than going silent.
"""

import difflib
import queue
import re
import subprocess
import tempfile
import threading
import time
import wave
from pathlib import Path

from config import CONFIG, ROOT

# Speak on sentence boundaries — waiting for the whole reply is what makes an
# assistant feel laggy. A long first sentence is still a bad voice UI, though,
# so `feed` also releases a word boundary once the buffer reaches the configured
# cap. Native streaming TTS would be smoother; this keeps the built-in `say`
# fallback from waiting for a paragraph.
BOUNDARY = re.compile(r"(?<=[.!?])\s+|\n+")

# For the FIRST thing said in a reply only. A full stop can be a long way off,
# and every millisecond before the first sound is one the listener experiences
# as lag — so the opening fragment is released at a clause boundary instead.
OPENING = re.compile(r"(?<=[,;:])\s+|(?<=[.!?])\s+|\n+")


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
        self.opening_chars = cfg["voice"].get("speak_opening_chars", 45)
        self.current = None
        # What it has said lately, so the ears can recognise their own echo.
        self.echo = []
        self.echo_seconds = cfg["voice"].get("echo_memory_seconds", 20)
        self.echo_threshold = cfg["voice"].get("echo_threshold", 0.45)
        self.first_audio = None   # when sound actually reached the speakers
        self.engine = cfg["voice"].get("engine", "say")
        self.piper = self._load_piper() if self.engine == "piper" else None
        self.worker = threading.Thread(target=self._drain, daemon=True)
        self.worker.start()

    def _load_piper(self):
        """Load the neural voice once. Falling back is better than falling over."""
        try:
            from piper import PiperVoice

            model = ROOT / CONFIG["voice"]["piper_model"]
            if not model.exists():
                print(f"  [voice] {model.name} missing — using the system voice")
                return None
            return PiperVoice.load(str(model))
        except Exception as exc:
            print(f"  [voice] neural voice unavailable ({exc}) — using the system voice")
            return None

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

    def reset_timing(self):
        self.first_audio = None

    def _say(self, text):
        """The engine seam. Everything above this is engine-agnostic."""
        self.remember_saying(text)
        try:
            if self.piper is not None:
                self._speak_neural(text)
            else:
                self._speak_system(text)
        finally:
            self.current = None

    def _speak_system(self, text):
        if self.first_audio is None:
            self.first_audio = time.time()
        self.current = subprocess.Popen(
            ["say", "-v", self.voice, "-r", self.rate, text],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.current.wait()

    def _speak_neural(self, text):
        """Synthesise to a file, then play it. Deleted straight after.

        Kept as a separate process for playback specifically so `stop()` can
        terminate it — barge-in has to be able to cut a sentence off mid-word.
        """
        with tempfile.TemporaryDirectory() as workspace:
            path = Path(workspace) / "say.wav"
            try:
                with wave.open(str(path), "wb") as handle:
                    self.piper.synthesize_wav(text, handle)
            except Exception:
                self._speak_system(text)  # synthesis failed; still say it
                return
            if self.first_audio is None:
                self.first_audio = time.time()
            self.current = subprocess.Popen(
                ["afplay", str(path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.current.wait()

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
            # Nothing spoken yet? Get *something* out fast: split on clauses and
            # accept a much shorter fragment. After that, whole sentences —
            # they sound better and there is no longer any latency to save.
            opening = self.first_audio is None and not self.queue.qsize()
            pattern = OPENING if opening else BOUNDARY
            minimum = self.opening_chars if opening else self.chunk_chars
            parts = pattern.split(self.buffer, maxsplit=1)
            # Synthesis time scales with length: ~113ms for 15 characters,
            # ~553ms for 36. So the opening fragment is capped short even when a
            # clause boundary sits further along — the rest catches up while the
            # first words are already playing.
            if (
                len(parts) >= 2
                and len(parts[0].strip()) >= (4 if opening else 1)
                and not (opening and len(parts[0].strip()) > minimum)
            ):
                sentence, self.buffer = parts[0].strip(), parts[1]
            elif len(self.buffer) >= minimum:
                # Keep words intact. This is a latency escape hatch, not a
                # replacement for punctuation-aware speech.
                cut = self.buffer.rfind(" ", 0, minimum + 1)
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

    def close(self):
        """Shut the speech thread down before the interpreter exits.

        The neural voice holds an ONNX session. Letting Python tear down while
        the worker still owns it aborts the process on the way out
        ("recursive_mutex lock failed"), which looks like a crash to anyone
        reading the terminal. Stop, drain, join, release — in that order.
        """
        self.stop()
        self.queue.put(None)
        if self.worker.is_alive():
            self.worker.join(timeout=3)
        self.piper = None

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
