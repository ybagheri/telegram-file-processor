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

    # A watermark is always scaled down to a small fraction of the video's
    # size in the end, so there's never a reason to feed ffmpeg a logo
    # bigger than this — doing so can make the filter graph extremely
    # slow/memory-hungry (or hang) on a resource-limited server, even
    # though nothing about the final output actually needs it.
    MAX_LOGO_DIMENSION = 1920

    def _prepare_logo_sync(self, logo_path: Path) -> Path | None:
        """Returns a logo image safe to feed into ffmpeg, downscaling it
        first if it's larger than MAX_LOGO_DIMENSION. Returns None if the
        image can't be read at all, so the caller can skip the watermark
        instead of failing the whole video conversion."""

        try:
            from PIL import Image

            with Image.open(logo_path) as img:

                if img.width <= self.MAX_LOGO_DIMENSION and img.height <= self.MAX_LOGO_DIMENSION:
                    return logo_path

                img = img.convert("RGBA" if img.mode in ("RGBA", "LA") else "RGB")
                img.thumbnail((self.MAX_LOGO_DIMENSION, self.MAX_LOGO_DIMENSION))

                cache_dir = logo_path.parent / "_resized_cache"
                cache_dir.mkdir(parents=True, exist_ok=True)

                cached_path = cache_dir / f"{logo_path.stem}_{img.width}x{img.height}.png"
                img.save(cached_path)

                logger.info(
                    f"Logo {logo_path.name} was {img.width * img.height} px "
                    f"or larger — resized before use ({cached_path.name})"
                )

                return cached_path

        except Exception:
            logger.exception(
                f"Could not read logo image, proceeding without a watermark: {logo_path}"
            )
            return None

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

            loop = asyncio.get_event_loop()
            safe_logo = await loop.run_in_executor(None, self._prepare_logo_sync, logo)

            if safe_logo is not None:

                max_w = max(width // 5, 2)
                max_h = max(height // 5, 2)

                overlay_xy = self.LOGO_POSITIONS.get(
                    logo_position,
                    self.LOGO_POSITIONS["bottom_right"],
                )

                vf = (
                    f"movie={safe_logo},scale=w={max_w}:h={max_h}:force_original_aspect_ratio=decrease[wm];"
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

    async def extract_frame_at(
        self,
        input_file: Path,
        second: float,
        output_file: Path,
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

            "-q:v",
            "2",

            str(output_file),

        ]

        return await self._run(command)

    async def generate_contact_sheet(
        self,
        input_file: Path,
        output_file: Path,
        *,
        count: int = 0,
        columns: int = 0,
        show_timestamps: bool = True,
    ) -> bool:
        """Grabs frames from evenly-spaced points across the video (10%-90%
        of its duration, like a classic video-player "chapters" preview) and
        arranges them into a single grid image."""

        import math
        import tempfile

        from PIL import Image, ImageDraw, ImageFont

        info = self.get_video_info(input_file)

        duration = info["duration"]
        width = info["width"] or 1280

        if duration <= 0:
            return False

        if count <= 0:
            minutes = duration / 60
            if minutes < 2:
                count = 1
            elif minutes < 5:
                count = 2
            elif minutes < 10:
                count = 4
            elif minutes < 20:
                count = 8
            elif minutes < 30:
                count = 10
            else:
                count = 12

        count = max(1, min(count, 60))

        if columns <= 0:
            columns = math.ceil(math.sqrt(count))

        columns = max(1, columns)
        rows = math.ceil(count / columns)

        start = duration * 0.10
        end = duration * 0.90
        step = (end - start) / (count + 1) if count > 0 else 0
        timestamps = [start + i * step for i in range(1, count + 1)]

        thumb_w, thumb_h = (320, 180) if width >= 640 else (240, 135)
        pad = 10

        with tempfile.TemporaryDirectory() as tmp_dir:

            tmp_path = Path(tmp_dir)
            frames = []

            for i, ts in enumerate(timestamps):
                frame_path = tmp_path / f"frame_{i:03d}.jpg"
                ok = await self.extract_frame_at(input_file, ts, frame_path)
                if ok and frame_path.exists():
                    frames.append((frame_path, ts))

            if not frames:
                return False

            total_w = columns * thumb_w + (columns + 1) * pad
            total_h = rows * thumb_h + (rows + 1) * pad

            canvas = Image.new("RGB", (total_w, total_h), (20, 20, 20))
            draw = ImageDraw.Draw(canvas)

            try:
                font = ImageFont.truetype("DejaVuSans-Bold.ttf", 16)
            except Exception:
                font = ImageFont.load_default()

            for idx, (frame_path, ts) in enumerate(frames):

                image = Image.open(frame_path).convert("RGB")
                image = image.resize((thumb_w, thumb_h))

                row = idx // columns
                col = idx % columns
                x = pad + col * (thumb_w + pad)
                y = pad + row * (thumb_h + pad)

                canvas.paste(image, (x, y))

                if show_timestamps:
                    minutes, seconds = divmod(int(ts), 60)
                    label = f"{minutes:02d}:{seconds:02d}"
                    draw.text(
                        (x + 6, y + thumb_h - 22),
                        label,
                        fill=(255, 255, 0),
                        font=font,
                    )

            canvas.save(output_file, quality=90)

        return True

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
