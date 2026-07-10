from __future__ import annotations

import asyncio
import json
import subprocess

from pathlib import Path

from config import FFmpeg
from core.logger import get_logger

logger = get_logger(__name__)


class MediaService:

    def __init__(self):

        self.ffmpeg = FFmpeg.EXECUTABLE

        self.ffprobe = FFmpeg.PROBE

    # =====================================================
    # Run Command
    # =====================================================

    async def _run(self, command: list[str]) -> bool:

        logger.info(" ".join(command))

        process = await asyncio.create_subprocess_exec(

            *command,

            stdout=asyncio.subprocess.PIPE,

            stderr=asyncio.subprocess.PIPE,

        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:

            logger.error(stderr.decode())

            return False

        return True

    # =====================================================
    # Probe
    # =====================================================

    def probe(self, file: Path) -> dict:

        command = [

            self.ffprobe,

            "-v",
            "quiet",

            "-print_format",
            "json",

            "-show_streams",

            "-show_format",

            str(file),

        ]

        result = subprocess.check_output(command)

        return json.loads(result)

    # =====================================================
    # Video Information
    # =====================================================

    def get_video_info(self, file: Path):

        info = self.probe(file)

        duration = 0

        width = 0

        height = 0

        bitrate = 0

        try:

            duration = int(
                float(
                    info["format"]["duration"]
                )
            )

        except Exception:

            pass

        try:

            bitrate = int(
                info["format"]["bit_rate"]
            )

        except Exception:

            pass

        for stream in info["streams"]:

            if stream["codec_type"] == "video":

                width = stream.get(
                    "width",
                    0,
                )

                height = stream.get(
                    "height",
                    0,
                )

                break

        return {

            "duration": duration,

            "width": width,

            "height": height,

            "bitrate": bitrate,

        }

    # =====================================================
    # Audio Information
    # =====================================================

    def get_audio_info(self, file: Path):

        info = self.probe(file)

        duration = 0

        bitrate = 0

        sample_rate = 0

        channels = 0

        try:

            duration = int(
                float(
                    info["format"]["duration"]
                )
            )

        except Exception:

            pass

        for stream in info["streams"]:

            if stream["codec_type"] == "audio":

                bitrate = int(
                    stream.get(
                        "bit_rate",
                        0,
                    )
                )

                sample_rate = int(
                    stream.get(
                        "sample_rate",
                        0,
                    )
                )

                channels = int(
                    stream.get(
                        "channels",
                        0,
                    )
                )

                break

        return {

            "duration": duration,

            "bitrate": bitrate,

            "sample_rate": sample_rate,

            "channels": channels,

        }    # =====================================================
    # Convert Video
    # =====================================================

    LOGO_POSITIONS = {
        "top_left": "15:15",
        "top_center": "(W-w)/2:15",
        "top_right": "W-w-15:15",
        "middle_left": "15:(H-h)/2",
        "center": "(W-w)/2:(H-h)/2",
        "middle_right": "W-w-15:(H-h)/2",
        "bottom_left": "15:H-h-15",
        "bottom_center": "(W-w)/2:H-h-15",
        "bottom_right": "W-w-15:H-h-15",
    }

    async def convert_video(
        self,
        *,
        input_file: Path,
        output_file: Path,
        width: int,
        height: int,
        crf: int,
        preset: str,
        logo: Path | None = None,
        logo_position: str = "bottom_right",
    ) -> bool:

        vf = f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"

        if logo and logo.exists():

            max_w = max(width // 5, 2)
            max_h = max(height // 5, 2)

            overlay_xy = self.LOGO_POSITIONS.get(
                logo_position,
                self.LOGO_POSITIONS["bottom_right"],
            )

            vf = (
                f"movie={logo},scale=w={max_w}:h={max_h}:force_original_aspect_ratio=decrease[wm];"
                f"[0:v]{vf}[video];"
                f"[video][wm]overlay={overlay_xy}"
            )

        command = [

            self.ffmpeg,

            "-y",

            "-i",
            str(input_file),

            "-vf",
            vf,

            "-c:v",
            "libx264",

            "-preset",
            preset,

            "-crf",
            str(crf),

            "-c:a",
            "aac",

            "-b:a",
            "128k",

            "-movflags",
            "+faststart",

            str(output_file),

        ]

        return await self._run(command)

    # =====================================================
    # Extract MP3
    # =====================================================

    async def extract_mp3(
        self,
        input_file: Path,
        output_file: Path,
        bitrate: str = "192k",
    ) -> bool:

        command = [

            self.ffmpeg,

            "-y",

            "-i",
            str(input_file),

            "-vn",

            "-map",
            "0:a:0",

            "-codec:a",
            "libmp3lame",

            "-b:a",
            bitrate,

            str(output_file),

        ]

        return await self._run(command)

    # =====================================================
    # Extract M4A
    # =====================================================

    async def extract_m4a(
        self,
        input_file: Path,
        output_file: Path,
        bitrate: str = "192k",
    ) -> bool:

        command = [

            self.ffmpeg,

            "-y",

            "-i",
            str(input_file),

            "-vn",

            "-map",
            "0:a:0",

            "-c:a",
            "aac",

            "-b:a",
            bitrate,

            str(output_file),

        ]

        return await self._run(command)

    # =====================================================
    # Extract Voice
    # =====================================================

    async def extract_voice(
        self,
        input_file: Path,
        output_file: Path,
    ) -> bool:

        command = [

            self.ffmpeg,

            "-y",

            "-i",
            str(input_file),

            "-vn",

            "-map",
            "0:a:0",

            "-ac",
            "1",

            "-ar",
            "48000",

            "-c:a",
            "libopus",

            "-b:a",
            "48k",

            "-vbr",
            "on",

            "-application",
            "voip",

            "-compression_level",
            "10",

            str(output_file),

        ]

        return await self._run(command)
	
	    # =====================================================
    # Generate Thumbnail
    # =====================================================

    async def generate_thumbnail(
        self,
        input_file: Path,
        output_file: Path,
        second: int = 3,
    ) -> bool:

        command = [

            self.ffmpeg,

            "-y",

            "-ss",
            str(second),

            "-i",
            str(input_file),

            "-frames:v",
            "1",

            "-vf",
            "scale='min(320,iw)':'min(320,ih)':force_original_aspect_ratio=decrease",

            "-q:v",
            "2",

            str(output_file),

        ]

        return await self._run(command)

    async def normalize_thumbnail(
        self,
        input_file: Path,
        output_file: Path,
    ) -> bool:
        """Re-encode an arbitrary image (e.g. a user-supplied photo) into a
        JPEG that respects Telegram's thumbnail limits (<=320x320)."""

        command = [

            self.ffmpeg,

            "-y",

            "-i",
            str(input_file),

            "-frames:v",
            "1",

            "-vf",
            "scale='min(320,iw)':'min(320,ih)':force_original_aspect_ratio=decrease",

            "-q:v",
            "2",

            str(output_file),

        ]

        return await self._run(command)

    # =====================================================
    # Copy Streams (No Re-Encode)
    # =====================================================

    async def copy_streams(
        self,
        input_file: Path,
        output_file: Path,
    ) -> bool:

        command = [

            self.ffmpeg,

            "-y",

            "-i",
            str(input_file),

            "-c",
            "copy",

            str(output_file),

        ]

        return await self._run(command)

    # =====================================================
    # Utilities
    # =====================================================

    def exists(
        self,
        file: Path,
    ) -> bool:

        return (
            file.exists()
            and file.is_file()
            and file.stat().st_size > 0
        )

    def size(
        self,
        file: Path,
    ) -> int:

        if not self.exists(file):

            return 0

        return file.stat().st_size

    def delete(
        self,
        file: Path,
    ):

        try:

            if file.exists():

                file.unlink()

        except Exception as e:

            logger.warning(
                "Delete failed: %s",
                e,
            )

    def delete_many(
        self,
        files: list[Path],
    ):

        for file in files:

            self.delete(file)

    def ensure_parent(
        self,
        file: Path,
    ):

        file.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

    # =====================================================
    # Move
    # =====================================================

    def move(
        self,
        source: Path,
        destination: Path,
    ):

        self.ensure_parent(destination)

        source.replace(destination)

    # =====================================================
    # Copy
    # =====================================================

    def copy(
        self,
        source: Path,
        destination: Path,
    ):

        import shutil

        self.ensure_parent(destination)

        shutil.copy2(
            source,
            destination,
        )


# =====================================================
# Singleton
# =====================================================

media_service = MediaService()
