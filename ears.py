"""The ears. Audio in, text out.

Seam: give it audio, get back text. Whisper runs on this machine, so nothing
spoken here is ever uploaded anywhere. Swapping in a different transcriber means
rewriting `transcribe` and nothing else.
"""

import difflib
import re
import subprocess
import time
import tempfile
from pathlib import Path

from config import CONFIG, ROOT

# Given near-silence, whisper does not return nothing — it invents. Sound-effect
# annotations in brackets, and stock phrases learnt from captioned video. All of
# it is noise, and all of it derails a conversation if handed to the brain.
ANNOTATION = re.compile(r"[\[(][^\])]*[\])]")
NOISE = {
    "", "you", "thank you", "thanks for watching", "thank you for watching",
    "thanks for watching!", "silence", "music", "applause", "bye", "okay",
    "subtitles by the amara org community", "please subscribe", "the end",
}


def start_recording(path):
    """Record the default mic to 16 kHz mono — the only format whisper wants."""
    return subprocess.Popen(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "avfoundation", "-i", CONFIG["voice"]["mic"],
            "-ar", "16000", "-ac", "1",
            "-t", str(CONFIG["voice"]["max_seconds"]),  # a stuck key can't fill the disk
            str(path),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )


def stop_recording(process):
    """Ask ffmpeg to quit cleanly so it writes a valid WAV header."""
    try:
        process.stdin.write(b"q")
        process.stdin.flush()
        process.wait(timeout=5)
    except (BrokenPipeError, subprocess.TimeoutExpired, OSError):
        process.terminate()
        process.wait(timeout=5)


def transcribe(wav, prompt=None):
    """Return what was said, or "" if it was silence or noise.

    `prompt` primes the decoder. Whisper has never seen the word "Zetsu", so
    left alone it reaches for real words that sound similar. Telling it the name
    up front makes it far likelier to spell it the same way twice.
    """
    model = ROOT / CONFIG["voice"]["whisper_model"]
    if not model.exists():
        raise FileNotFoundError(f"Whisper model missing: {model}")
    command = ["whisper-cli", "-m", str(model), "-f", str(wav), "-nt", "-np"]
    if prompt:
        command += ["--prompt", prompt]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=CONFIG["voice"]["transcribe_timeout"],
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip()[:200] or "whisper failed")
    text = " ".join(ANNOTATION.sub(" ", result.stdout).split())
    return "" if normalise(text) in NOISE else text


class Recording:
    """A recording in progress. Start it, let the user talk, then finish it."""

    def __init__(self):
        self.workspace = tempfile.TemporaryDirectory()
        self.wav = Path(self.workspace.name) / "turn.wav"
        self.process = start_recording(self.wav)

    def finish(self, prompt=None):
        """Stop recording and return what was said ("" if nothing was)."""
        try:
            stop_recording(self.process)
            if not self.wav.exists() or self.wav.stat().st_size < 1024:
                raise RuntimeError(
                    "No audio captured. Grant microphone access to this terminal "
                    "in System Settings > Privacy & Security > Microphone."
                )
            return transcribe(self.wav, prompt)
        finally:
            self.workspace.cleanup()


class OpenMic:
    """Gapless chunked listening.

    The naive loop — record, transcribe, record, transcribe — is deaf for the
    second or so whisper spends thinking, and that is exactly when the start of
    your sentence goes missing. So the next capture is started *before* the last
    one is transcribed: there is always a recorder running.
    """

    def __init__(self, seconds, prompt=None):
        self.seconds = seconds
        self.prompt = prompt
        self.current = Recording()

    def next_chunk(self):
        time.sleep(self.seconds)
        following = Recording()  # rolling before we stop to think
        try:
            return self.current.finish(self.prompt)
        finally:
            self.current = following

    def close(self):
        """Stop listening — used while Zetsu is talking, so it can't hear itself."""
        try:
            self.current.finish()
        except Exception:
            pass
        self.current = None


def normalise(text):
    """Lowercase, strip punctuation, collapse whitespace. Matching and
    calibration must agree exactly, so they share this one function."""
    stripped = "".join(
        ""                     # apostrophes vanish: "I'm" -> "im", not "i m"
        if character in "'\u2019`"
        else character.lower() if character.isalnum() or character.isspace() else " "
        for character in text
    )
    return " ".join(stripped.split())


def find_wake(text, cfg=CONFIG):
    """Look for the wake word. Returns whatever was said after it, or None.

    Whisper will not spell a made-up name consistently — "Zetsu" comes back as
    "Zetso", "Sets you", "Zed Sue". Matching only the exact spelling means a
    wake word that works one time in three, so match a list of near-misses and
    keep it in config where you can add whatever yours actually gets heard as.
    """
    joined = normalise(text)
    variants = cfg["wake"]["variants"]
    for variant in variants:
        position = joined.find(variant)
        if position != -1:
            return joined[position + len(variant):].strip()

    # Nothing matched outright. Slide a small window and accept a close miss —
    # cheaper and far more robust than trying to list every way whisper might
    # spell a made-up name.
    words = joined.split()
    threshold = cfg["wake"]["similarity"]
    for size in (1, 2, 3):
        for start in range(len(words) - size + 1):
            window = " ".join(words[start : start + size])
            if len(window) < 4:
                continue  # short fragments match everything; ignore them
            for variant in variants:
                if difflib.SequenceMatcher(None, window, variant).ratio() >= threshold:
                    return " ".join(words[start + size :]).strip()
    return None


def matches_any(text, phrases):
    """True if any phrase appears in the text, punctuation and case ignored.

    Used for "bye bye" and "let's talk a while" — multi-word cues, so a plain
    substring test is enough; the fuzziness that the wake word needs would only
    invite false positives here.
    """
    cleaned = normalise(text)
    return any(phrase in cleaned for phrase in phrases)
