"""The ears. Audio in, text out.

Seam: give it audio, get back text. Whisper runs on this machine, so nothing
spoken here is ever uploaded anywhere. Swapping in a different transcriber means
rewriting `transcribe` and nothing else.
"""

import array
import atexit
import difflib
import math
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import wave
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
    """Ask ffmpeg to quit cleanly, then insist. Never raises.

    'q' lets it write a valid WAV header, which is what we want. But a recorder
    can wedge — the audio device is busy, or another one is mid-handover — and
    this used to escalate to terminate() with an *unguarded* wait, so one stuck
    ffmpeg killed the whole conversation. Escalate all the way to kill, and let
    the caller deal with a short or missing file instead of a traceback.
    """
    for step in ("quit", "terminate", "kill"):
        try:
            if step == "quit":
                process.stdin.write(b"q")
                process.stdin.flush()
            elif step == "terminate":
                process.terminate()
            else:
                process.kill()
            process.wait(timeout=2)
            return
        except (BrokenPipeError, subprocess.TimeoutExpired, OSError, ValueError):
            continue


# --- the model, kept warm -----------------------------------------------------
# whisper-cli reloads a 141MB model on every run. Measured: 1.26s for 0.65s of
# audio, but only 1.39s for 3.26s — so ~1.2s of that is startup, every single
# chunk. whisper-server loads the model once and answers in ~0.15s. Same model,
# same output, 8x less waiting.

_server = None


def _model_path():
    model = ROOT / CONFIG["voice"]["whisper_model"]
    if not model.exists():
        raise FileNotFoundError(f"Whisper model missing: {model}")
    return model


def server_url():
    return f"http://127.0.0.1:{CONFIG['voice']['server_port']}/inference"


def server_ready(timeout=1):
    try:
        urllib.request.urlopen(
            f"http://127.0.0.1:{CONFIG['voice']['server_port']}/", timeout=timeout
        )
        return True
    except urllib.error.HTTPError:
        return True  # answered at all, which is all we need to know
    except (urllib.error.URLError, OSError):
        return False


def start_server(wait_seconds=90):
    """Load the model once, up front. The slow part happens here, deliberately.

    Returns True if a server is answering — one we started, or one already
    running from a previous session.
    """
    global _server
    if server_ready():
        return True

    _server = subprocess.Popen(
        [
            "whisper-server", "-m", str(_model_path()),
            "--host", "127.0.0.1",
            "--port", str(CONFIG["voice"]["server_port"]),
            "-nt", "-l", "en",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    atexit.register(stop_server)

    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        if server_ready():
            return True
        if _server.poll() is not None:
            return False  # it died on the way up
        time.sleep(0.5)
    return False


def stop_server():
    global _server
    if _server and _server.poll() is None:
        _server.terminate()
        try:
            _server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _server.kill()
    _server = None


def _multipart(fields, filename, payload):
    """Build a multipart body. ponytail: stdlib only, no requests dependency."""
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"'
            f"\r\n\r\n{value}\r\n".encode()
        )
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
        f'filename="{filename}"\r\nContent-Type: audio/wav\r\n\r\n'.encode()
    )
    parts.append(payload)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _transcribe_via_server(wav, prompt):
    body, content_type = _multipart(
        {"response_format": "text", **({"prompt": prompt} if prompt else {})},
        Path(wav).name,
        Path(wav).read_bytes(),
    )
    request = urllib.request.Request(
        server_url(), data=body, headers={"Content-Type": content_type}
    )
    with urllib.request.urlopen(
        request, timeout=CONFIG["voice"]["transcribe_timeout"]
    ) as response:
        return response.read().decode("utf-8", "replace")


def _transcribe_via_cli(wav, prompt):
    command = ["whisper-cli", "-m", str(_model_path()), "-f", str(wav), "-nt", "-np"]
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
    return result.stdout


def transcribe(wav, prompt=None):
    """Return what was said, or "" if it was silence or noise.

    `prompt` primes the decoder. Whisper has never seen the word "Zetsu", so
    left alone it reaches for real words that sound similar. Telling it the name
    up front makes it far likelier to spell it the same way twice.
    """
    raw = None
    if CONFIG["voice"]["use_server"] and server_ready():
        try:
            raw = _transcribe_via_server(wav, prompt)
        except (urllib.error.URLError, OSError, TimeoutError):
            raw = None  # fall through to the CLI rather than lose the turn
    if raw is None:
        raw = _transcribe_via_cli(wav, prompt)

    text = " ".join(ANNOTATION.sub(" ", raw).split())
    return "" if normalise(text) in NOISE else text


def level_dbfs(wav, last_ms=None):
    """Loudness of a recording, or of just its last `last_ms`, in dBFS.

    Measured on this machine: an empty room sits near -48, speech near -33. That
    15dB gap is what makes silence detectable without a neural VAD.
    """
    try:
        with wave.open(str(wav)) as handle:
            rate, frames = handle.getframerate(), handle.getnframes()
            if last_ms:
                skip = max(0, frames - int(rate * last_ms / 1000))
                handle.setpos(skip)
                frames -= skip
            data = handle.readframes(frames)
    except (wave.Error, OSError, EOFError):
        return -99.0
    if not data:
        return -99.0
    samples = array.array("h")
    samples.frombytes(data[: len(data) // 2 * 2])
    if not samples:
        return -99.0
    rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples))
    return 20 * math.log10(rms / 32768) if rms > 0 else -99.0


class Recording:
    """A recording in progress. Start it, let the user talk, then finish it."""

    def __init__(self):
        self.workspace = tempfile.TemporaryDirectory()
        self.wav = Path(self.workspace.name) / "turn.wav"
        self.process = start_recording(self.wav)
        self.level = -99.0
        self.tail_level = -99.0

    def finish(self, prompt=None, quiet_below=None):
        """Stop recording and return what was said ("" if nothing was)."""
        try:
            stop_recording(self.process)
            if not self.wav.exists() or self.wav.stat().st_size < 1024:
                raise RuntimeError(
                    "No audio captured. Grant microphone access to this terminal "
                    "in System Settings > Privacy & Security > Microphone."
                )
            self.level = level_dbfs(self.wav)
            self.tail_level = level_dbfs(self.wav, CONFIG["voice"]["vad_tail_ms"])
            if quiet_below is not None and self.level < quiet_below:
                # Nothing was said. Whisper on silence costs time and invents
                # words; skipping it is faster *and* more accurate.
                return ""
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

    def __init__(self, seconds, prompt=None, overlap=None):
        self.seconds = seconds
        self.prompt = prompt
        self.overlap = CONFIG["wake"]["chunk_overlap_seconds"] if overlap is None else overlap
        self.current = Recording()
        # Learned from the room rather than assumed. A fixed threshold is right
        # until a fan comes on, and then it is wrong for the rest of the day.
        self.noise_floor = CONFIG["voice"]["vad_silence_dbfs"] - CONFIG["voice"]["vad_margin_db"]
        self.level = -99.0
        self.tail_level = -99.0

    def silence_threshold(self):
        return self.noise_floor + CONFIG["voice"]["vad_margin_db"]

    def tail_is_quiet(self):
        """True if this slice ended in silence — i.e. the speaker has stopped."""
        return self.tail_level < self.silence_threshold()

    def _learn_floor(self, level):
        if level <= -99.0:
            return
        if level < self.noise_floor:
            self.noise_floor = self.noise_floor * 0.7 + level * 0.3   # drops fast
        elif level < self.silence_threshold():
            self.noise_floor = self.noise_floor * 0.98 + level * 0.02  # creeps up

    def next_chunk(self, seconds=None):
        """One slice. `seconds` overrides the default for this slice only.

        Asleep, a slice must be long enough to hold the whole wake word.
        Awake, it must be short enough that an interruption registers quickly.
        Same rolling recorder either way — only the length changes.
        """
        window = self.seconds if seconds is None else seconds
        overlap = min(self.overlap, window * 0.5)

        # Start the next recorder BEFORE this window closes, so consecutive
        # slices share `overlap` seconds of audio. Without it a wake word can
        # land across the join and be chopped in half — "Friday" arrives as
        # "Day" and nothing wakes. With it, a split word is still whole in one
        # of the two slices.
        time.sleep(max(0.05, window - overlap))
        following = Recording()
        time.sleep(overlap)
        try:
            text = self.current.finish(
                self.prompt,
                quiet_below=self.silence_threshold()
                if CONFIG["voice"]["vad_skip_silent"]
                else None,
            )
            self.level = self.current.level
            self.tail_level = self.current.tail_level
            self._learn_floor(self.level)
            return text
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


def stitch(parts):
    """Join overlapping slices without repeating the words they share.

    Consecutive slices deliberately share audio, so the same words are
    transcribed twice. Left alone the question arrives as "what is on my todo
    list model list". Trim the longest run of words that ends one slice and
    starts the next.
    """
    merged = []
    for part in parts:
        words = part.split()
        if not words:
            continue
        if not merged:
            merged = words
            continue
        longest = min(len(merged), len(words), 8)
        for size in range(longest, 0, -1):
            tail = [w.lower().strip(".,?!") for w in merged[-size:]]
            head = [w.lower().strip(".,?!") for w in words[:size]]
            if tail == head:
                words = words[size:]
                break
        merged += words
    return " ".join(merged)


def best_near_miss(text, cfg=CONFIG):
    """The closest thing to the wake word in `text`, and how close it got.

    Returns (phrase, ratio) or None. Used to learn what your wake word is
    actually being heard as: anything that lands just under the threshold is a
    variant worth adding, and you should not have to guess which.
    """
    words = normalise(text).split()
    variants = cfg["wake"]["variants"]
    best = (None, 0.0)
    for size in (1, 2, 3):
        for start in range(len(words) - size + 1):
            window = " ".join(words[start : start + size])
            if len(window) < 4:
                continue
            for variant in variants:
                ratio = difflib.SequenceMatcher(None, window, variant).ratio()
                if ratio > best[1]:
                    best = (window, ratio)
    return best if best[0] else None
