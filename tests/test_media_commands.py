"""
Tests for services/media.py's ffmpeg *argument construction* — the
built command lists are captured at the `_run` boundary (monkeypatched),
so no ffmpeg/ffprobe binary is ever executed. Also covers the
ffprobe-based info parsing with a mocked `subprocess.check_output`.
"""

import asyncio
import json

from pathlib import Path

import pytest

from services.media import media_service


@pytest.fixture
def captured_ffmpeg(monkeypatch):

    commands = []

    async def fake_run(command, **kwargs):
        commands.append(command)
        return True

    monkeypatch.setattr(media_service, "_run", fake_run)

    return commands


# ======================================================================
# Video conversion
# ======================================================================


async def test_convert_video_builds_expected_command(captured_ffmpeg):

    ok = await media_service.convert_video(
        input_file=Path("in.mp4"),
        output_file=Path("out.mp4"),
        width=640,
        height=360,
        crf=29,
        preset="veryfast",
    )

    assert ok is True
    assert len(captured_ffmpeg) == 1

    command = captured_ffmpeg[0]

    assert command[0] == media_service.ffmpeg
    assert "-y" in command

    i = command.index("-i")
    assert command[i + 1] == "in.mp4"

    vf = command[command.index("-vf") + 1]
    assert "scale=640:360:force_original_aspect_ratio=decrease" in vf
    assert "pad=640:360:(ow-iw)/2:(oh-ih)/2" in vf

    assert command[command.index("-c:v") + 1] == "libx264"
    assert command[command.index("-preset") + 1] == "veryfast"
    assert command[command.index("-crf") + 1] == "29"
    assert command[command.index("-c:a") + 1] == "aac"
    assert command[command.index("-b:a") + 1] == "128k"
    assert command[command.index("-movflags") + 1] == "+faststart"

    assert command[-1] == "out.mp4"


async def test_convert_video_with_logo_builds_overlay_filter(
    captured_ffmpeg, tmp_path
):

    logo = tmp_path / "logo.png"

    # A real (tiny) PNG so _prepare_logo_sync can read it with PIL.
    from PIL import Image

    Image.new("RGBA", (20, 20), (255, 0, 0, 255)).save(logo)

    ok = await media_service.convert_video(
        input_file=Path("in.mp4"),
        output_file=Path("out.mp4"),
        width=640,
        height=360,
        crf=29,
        preset="veryfast",
        logo=logo,
        logo_position="top_left",
    )

    assert ok is True

    vf = captured_ffmpeg[0][captured_ffmpeg[0].index("-vf") + 1]

    assert f"movie={logo}" in vf
    assert "scale=w=128:h=72" in vf          # max logo size = 1/5 of 640x360
    assert "[0:v]scale=640:360" in vf
    assert "overlay=" in vf


async def test_convert_video_skips_missing_logo(captured_ffmpeg):

    ok = await media_service.convert_video(
        input_file=Path("in.mp4"),
        output_file=Path("out.mp4"),
        width=640,
        height=360,
        crf=29,
        preset="veryfast",
        logo=Path("/nonexistent/logo.png"),
    )

    assert ok is True

    vf = captured_ffmpeg[0][captured_ffmpeg[0].index("-vf") + 1]

    assert "movie=" not in vf  # fell back to the plain scale/pad filter


# ======================================================================
# Audio extraction
# ======================================================================


async def test_extract_mp3_command(captured_ffmpeg):

    await media_service.extract_mp3(Path("in.mp4"), Path("out.mp3"), bitrate="96k")

    command = captured_ffmpeg[0]

    assert command[0] == media_service.ffmpeg
    assert "-vn" in command
    assert command[command.index("-map") + 1] == "0:a:0"
    assert command[command.index("-codec:a") + 1] == "libmp3lame"
    assert command[command.index("-b:a") + 1] == "96k"
    assert command[-1] == "out.mp3"


async def test_extract_m4a_command(captured_ffmpeg):

    await media_service.extract_m4a(Path("in.mp4"), Path("out.m4a"))

    command = captured_ffmpeg[0]

    assert command[command.index("-c:a") + 1] == "aac"
    assert command[command.index("-b:a") + 1] == "192k"  # default bitrate
    assert command[-1] == "out.m4a"


async def test_extract_voice_command(captured_ffmpeg):

    await media_service.extract_voice(Path("in.mp4"), Path("out.ogg"))

    command = captured_ffmpeg[0]

    assert command[command.index("-ac") + 1] == "1"          # mono
    assert command[command.index("-ar") + 1] == "48000"
    assert command[command.index("-c:a") + 1] == "libopus"
    assert command[command.index("-b:a") + 1] == "48k"
    assert command[command.index("-application") + 1] == "voip"
    assert command[-1] == "out.ogg"


# ======================================================================
# Thumbnails / frames / stream copy
# ======================================================================


async def test_extract_frame_at_seeks_before_input(captured_ffmpeg):

    await media_service.extract_frame_at(Path("in.mp4"), 7, Path("frame.jpg"))

    command = captured_ffmpeg[0]

    ss = command.index("-ss")
    assert command[ss + 1] == "7"
    assert command.index("-i") > ss  # seek happens before the input
    assert command[command.index("-frames:v") + 1] == "1"
    assert command[-1] == "frame.jpg"


async def test_generate_thumbnail_scales_to_telegram_limits(captured_ffmpeg):

    await media_service.generate_thumbnail(Path("in.mp4"), Path("thumb.jpg"), second=3)

    command = captured_ffmpeg[0]

    assert command[command.index("-ss") + 1] == "3"
    vf = command[command.index("-vf") + 1]
    assert "min(320,iw)" in vf
    assert command[-1] == "thumb.jpg"


async def test_normalize_thumbnail(captured_ffmpeg):

    await media_service.normalize_thumbnail(Path("photo.png"), Path("norm.jpg"))

    command = captured_ffmpeg[0]

    vf = command[command.index("-vf") + 1]
    assert "min(320,iw)" in vf
    assert command[command.index("-q:v") + 1] == "2"
    assert command[-1] == "norm.jpg"


async def test_copy_streams_uses_stream_copy(captured_ffmpeg):

    await media_service.copy_streams(Path("in.mkv"), Path("out.mp4"))

    command = captured_ffmpeg[0]

    assert command[command.index("-c") + 1] == "copy"
    assert command[-1] == "out.mp4"


# ======================================================================
# ffprobe argument construction + info parsing (subprocess mocked)
# ======================================================================


def test_probe_builds_expected_command(monkeypatch, tmp_path):

    seen = []

    def fake_check_output(command):
        seen.append(command)
        return json.dumps({"streams": [], "format": {}}).encode()

    monkeypatch.setattr("services.media.subprocess.check_output", fake_check_output)

    media_service.probe(tmp_path / "in.mp4")

    command = seen[0]

    assert command[0] == media_service.ffprobe
    assert command[command.index("-print_format") + 1] == "json"
    assert "-show_streams" in command
    assert "-show_format" in command
    assert command[-1] == str(tmp_path / "in.mp4")


def test_get_video_info_parses_probe_output(monkeypatch, tmp_path):

    probe_data = {
        "format": {"duration": "12.75", "bit_rate": "500000"},
        "streams": [
            {"codec_type": "audio", "sample_rate": "48000"},
            {"codec_type": "video", "width": 1280, "height": 720},
        ],
    }

    monkeypatch.setattr(
        "services.media.subprocess.check_output",
        lambda command: json.dumps(probe_data).encode(),
    )

    info = media_service.get_video_info(tmp_path / "in.mp4")

    assert info == {
        "duration": 12,
        "width": 1280,
        "height": 720,
        "bitrate": 500000,
    }


def test_get_video_info_defaults_when_fields_missing(monkeypatch, tmp_path):

    monkeypatch.setattr(
        "services.media.subprocess.check_output",
        lambda command: json.dumps({"streams": [], "format": {}}).encode(),
    )

    info = media_service.get_video_info(tmp_path / "in.mp4")

    assert info == {"duration": 0, "width": 0, "height": 0, "bitrate": 0}


def test_get_audio_info_parses_probe_output(monkeypatch, tmp_path):

    probe_data = {
        "format": {"duration": "61.9"},
        "streams": [
            {
                "codec_type": "audio",
                "bit_rate": "128000",
                "sample_rate": "44100",
                "channels": 2,
            },
            {"codec_type": "video", "width": 1920, "height": 1080},
        ],
    }

    monkeypatch.setattr(
        "services.media.subprocess.check_output",
        lambda command: json.dumps(probe_data).encode(),
    )

    info = media_service.get_audio_info(tmp_path / "in.mp3")

    assert info["duration"] == 61
    assert info["bitrate"] == 128000
    assert info["sample_rate"] == 44100
    assert info["channels"] == 2


# ======================================================================
# ffmpeg live progress parsing (services/media.py's `-progress pipe:1`
# support)
# ======================================================================


class _FakeProgressStream:
    """A fake asyncio stdout stream: `_pump_ffmpeg_progress` only ever
    calls `await stream.readline()` on it."""

    def __init__(self, lines: list[bytes]):
        self._lines = list(lines) + [b""]  # b"" == EOF, like a real stream

    async def readline(self):
        return self._lines.pop(0)


@pytest.mark.asyncio
async def test_pump_ffmpeg_progress_parses_out_time_ms():

    stream = _FakeProgressStream(
        [
            b"frame=10\n",
            b"out_time_ms=25000000\n",  # 25 seconds, in microseconds
            b"progress=continue\n",
        ]
    )

    calls = []

    await media_service._pump_ffmpeg_progress(
        stream,
        lambda elapsed, total, fraction: calls.append((elapsed, total, fraction)),
        total_duration=100.0,
    )

    assert calls == [(25.0, 100.0, 0.25)]


@pytest.mark.asyncio
async def test_pump_ffmpeg_progress_parses_out_time_string():

    stream = _FakeProgressStream(
        [
            b"out_time=00:00:50.000000\n",
            b"progress=continue\n",
        ]
    )

    calls = []

    await media_service._pump_ffmpeg_progress(
        stream,
        lambda elapsed, total, fraction: calls.append((elapsed, total, fraction)),
        total_duration=100.0,
    )

    assert calls == [(50.0, 100.0, 0.5)]


@pytest.mark.asyncio
async def test_pump_ffmpeg_progress_end_reports_full_duration():

    stream = _FakeProgressStream([b"progress=end\n"])

    calls = []

    await media_service._pump_ffmpeg_progress(
        stream,
        lambda elapsed, total, fraction: calls.append((elapsed, total, fraction)),
        total_duration=42.0,
    )

    assert calls == [(42.0, 42.0, 1.0)]


@pytest.mark.asyncio
async def test_pump_ffmpeg_progress_ignores_unparseable_lines_and_keeps_going():

    stream = _FakeProgressStream(
        [
            b"speed=1.02x\n",  # no timing info, must be skipped, not crash
            b"out_time_ms=notanumber\n",  # malformed, must be skipped too
            b"out_time_ms=10000000\n",
            b"progress=continue\n",
        ]
    )

    calls = []

    await media_service._pump_ffmpeg_progress(
        stream,
        lambda elapsed, total, fraction: calls.append((elapsed, total, fraction)),
        total_duration=50.0,
    )

    assert calls == [(10.0, 50.0, 0.2)]


@pytest.mark.asyncio
async def test_pump_ffmpeg_progress_swallows_a_broken_callback():

    stream = _FakeProgressStream([b"out_time_ms=5000000\n"])

    def _boom(elapsed, total, fraction):
        raise RuntimeError("boom")

    # Must return normally rather than propagating the callback's error.
    await media_service._pump_ffmpeg_progress(stream, _boom, total_duration=10.0)


class _FakeProcess:

    def __init__(self, stdout_lines: list[bytes], stderr: bytes = b"", returncode: int = 0):
        self.stdout = _FakeProgressStream(stdout_lines)
        self._stderr = stderr
        self.returncode = returncode

    class _StderrReader:
        def __init__(self, data):
            self._data = data

        async def read(self):
            return self._data

    @property
    def stderr(self):
        return self._StderrReader(self._stderr)

    async def wait(self):
        return self.returncode

    async def communicate(self):
        return b"", self._stderr


@pytest.mark.asyncio
async def test_run_adds_progress_flags_only_when_tracking(monkeypatch):

    captured = {}

    async def fake_create_subprocess_exec(*command, stdout=None, stderr=None):
        captured["command"] = command
        captured["stdout"] = stdout
        return _FakeProcess([b"out_time_ms=1000000\n"])

    monkeypatch.setattr(
        "services.media.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    calls = []

    ok = await media_service._run(
        ["ffmpeg", "-i", "in.mp4", "out.mp4"],
        progress_callback=lambda *a: calls.append(a),
        total_duration=10.0,
    )

    assert ok is True
    assert captured["command"][:3] == ("ffmpeg", "-progress", "pipe:1")
    assert calls == [(1.0, 10.0, 0.1)]


@pytest.mark.asyncio
async def test_run_without_progress_callback_is_unchanged(monkeypatch):
    """No progress_callback/total_duration -> the exact same command list
    and single-communicate() behaviour as before this feature existed."""

    captured = {}

    async def fake_create_subprocess_exec(*command, stdout=None, stderr=None):
        captured["command"] = command
        captured["stdout"] = stdout
        return _FakeProcess([], stderr=b"")

    monkeypatch.setattr(
        "services.media.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    ok = await media_service._run(["ffmpeg", "-i", "in.mp4", "out.mp4"])

    assert ok is True
    assert captured["command"] == ("ffmpeg", "-i", "in.mp4", "out.mp4")
    assert captured["stdout"] == asyncio.subprocess.DEVNULL


@pytest.mark.asyncio
async def test_run_reports_failure_on_nonzero_returncode(monkeypatch):

    async def fake_create_subprocess_exec(*command, stdout=None, stderr=None):
        return _FakeProcess([], stderr=b"ffmpeg blew up", returncode=1)

    monkeypatch.setattr(
        "services.media.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    ok = await media_service._run(["ffmpeg", "-i", "in.mp4", "out.mp4"])

    assert ok is False