"""Play soft local reminder clips and prepare user-imported audio."""

from contextlib import closing
from pathlib import Path
import json
from random import shuffle
import shutil
import subprocess
import wave
import winsound

import miniaudio
import numpy as np

from runtime_paths import ROOT, SETTINGS_ROOT

SAMPLE_RATE = 24000
MAX_SECONDS = 4.6
SOUND_NAMES = {
    "piglets": "小猪哨声 · 随机",
    "silent": "静音",
    "chime": "轻柔叮咚",
    "voice": "本地中文语音",
    "custom": "自定义音频",
}


def write_wav(path: Path, samples: np.ndarray) -> None:
    with wave.open(str(path), "wb") as out:
        out.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
        out.writeframes(samples.astype("<i2").tobytes())


def _decode_clip(source: Path) -> bytes:
    try:
        with closing(miniaudio.stream_file(str(source), nchannels=1, sample_rate=SAMPLE_RATE,
                                           frames_to_read=round(MAX_SECONDS * SAMPLE_RATE))) as stream:
            samples = next(stream, None)
            return samples.tobytes() if samples is not None else b""
    except miniaudio.DecodeError:
        pass
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise ValueError("请选择可以播放的 WAV、MP3、FLAC 或 Ogg Vorbis 音频。")
    try:
        return subprocess.run(
            [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
                "-protocol_whitelist", "file,pipe", "-i", str(source.resolve()),
                "-map", "0:a:0", "-vn", "-t", str(MAX_SECONDS),
                "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "pipe:1",
            ],
            capture_output=True, check=True, timeout=12, creationflags=subprocess.CREATE_NO_WINDOW,
        ).stdout
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError("读取音频失败。请选择可以正常播放的本地音频文件。") from error


def prepare_clip(source: Path, destination: Path) -> None:
    samples = np.frombuffer(_decode_clip(source), dtype="<i2").astype(np.float32)
    if len(samples) < SAMPLE_RATE // 15 or np.abs(samples).max() < 60:
        raise ValueError("这段音频太短，或开头没有可用的声音。")
    peak = np.abs(samples).max()
    rms = np.sqrt(np.mean(samples**2))
    samples *= min(0.21 * 32767 / peak, 0.058 * 32767 / rms)
    fade_in = min(int(0.045 * SAMPLE_RATE), len(samples) // 3)
    fade_out = min(int(0.10 * SAMPLE_RATE), len(samples) // 3)
    samples[:fade_in] *= np.linspace(0, 1, fade_in, dtype=np.float32)
    samples[-fade_out:] *= np.linspace(1, 0, fade_out, dtype=np.float32)

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    try:
        write_wav(temporary, samples)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


class ReminderAudio:
    def __init__(self, assets: Path = ROOT / "assets", data: Path = SETTINGS_ROOT / "local_audio"):
        self.prefs = data / "niuma-sound.txt"
        self.achievement = assets / "achievement-soft.wav"
        self.files = {
            "chime": assets / "soft-chime.wav",
            "voice": assets / "windows-zh.wav",
            "custom": data / "custom.wav",
        }
        library = assets / "piglets"
        self.piglets: list[tuple[str, Path]] = []
        try:
            manifest = json.loads((library / "credits.json").read_text(encoding="utf-8"))
            for entry in manifest["clips"]:
                file = library / Path(entry["file"]).name
                if file.is_file():
                    self.piglets.append((entry["name"], file))
        except (OSError, ValueError, KeyError, TypeError):
            pass
        self._bag: list[Path] = []
        self._last_pig: Path | None = None
        self.last_reminder: Path | None = None
        self.playing = False
        default = "piglets" if self.piglets else "chime"
        try:
            choice = self.prefs.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            choice = default
        self.choice = choice if choice in self.options() else default

    def options(self) -> tuple[str, ...]:
        library = ("piglets",) if self.piglets else ()
        return library + tuple(key for key, file in self.files.items() if file.is_file()) + ("silent",)

    def select(self, choice: str) -> None:
        if choice not in self.options():
            raise ValueError("这段声音的文件暂时不可用。")
        self.prefs.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.prefs.with_suffix(".tmp")
        try:
            temporary.write_text(choice, encoding="utf-8")
            temporary.replace(self.prefs)
        finally:
            temporary.unlink(missing_ok=True)
        self.choice = choice
        if choice == "silent":
            self.stop()

    def import_clip(self, source: Path) -> None:
        self.stop()
        prepare_clip(source, self.files["custom"])
        self.select("custom")

    def _draw(self) -> Path | None:
        if not self.piglets:
            return None
        if not self._bag:
            self._bag = [path for _, path in self.piglets]
            shuffle(self._bag)
            if len(self._bag) > 1 and self._bag[-1] == self._last_pig:
                self._bag[0], self._bag[-1] = self._bag[-1], self._bag[0]
        self._last_pig = self._bag.pop()
        return self._last_pig

    def _play(self, path: Path | None) -> bool:
        if path is None:
            return False
        try:
            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            self.playing = True
            return True
        except RuntimeError:
            return False

    def play(self) -> bool:
        self.last_reminder = None
        if self.choice == "silent":
            return False
        selected = self._draw() if self.choice == "piglets" else self.files.get(self.choice)
        for path in dict.fromkeys((selected, self.files["chime"])):
            if self._play(path):
                self.last_reminder = path
                return True
        return False

    def preview(self, index: int | None = None) -> bool:
        if index is not None and not 0 <= index < len(self.piglets):
            return False
        return self._play(self._draw() if index is None else self.piglets[index][1])

    def cheer(self, *, achievement=False) -> bool:
        self.last_reminder = None
        if self.choice == "silent":
            return False
        path = self.achievement if achievement else self.files["chime"]
        if self._play(path):
            self.last_reminder = path
            return True
        return False

    def replay(self) -> bool:
        return self.choice != "silent" and self._play(self.last_reminder)

    def stop(self) -> None:
        if self.playing:
            self.playing = False
            try:
                winsound.PlaySound(None, 0)
            except RuntimeError:
                pass
