from __future__ import annotations

from pathlib import Path

from mutagen.easyid3 import EasyID3
from mutagen.id3 import (
    ID3,
    APIC,
    TIT2,
    TPE1,
    ID3NoHeaderError,
)

from mutagen.mp4 import MP4

from core.logger import get_logger

logger = get_logger(__name__)


class TagService:

    def __init__(self):
        pass

    # =====================================================
    # MP3
    # =====================================================

    def update_mp3(
        self,
        file: Path,
        *,
        title: str = "",
        artist: str = "",
        cover: Path | None = None,
    ) -> bool:

        try:

            try:
                tags = EasyID3(file)
            except Exception:
                EasyID3.RegisterTextKey("artist", "TPE1")
                tags = EasyID3()

            if title:
                tags["title"] = title

            if artist:
                tags["artist"] = artist

            tags.save(file)

            if cover and cover.exists():

                try:
                    audio = ID3(file)
                except ID3NoHeaderError:
                    audio = ID3()

                with open(cover, "rb") as img:

                    audio.add(
                        APIC(
                            encoding=3,
                            mime="image/jpeg",
                            type=3,
                            desc="Cover",
                            data=img.read(),
                        )
                    )

                audio.save(file)

            return True

        except Exception as e:

            logger.exception(e)

            return False

    # =====================================================
    # M4A
    # =====================================================

    def update_m4a(
        self,
        file: Path,
        *,
        title: str = "",
        artist: str = "",
    ) -> bool:

        try:

            audio = MP4(file)

            if title:
                audio["\xa9nam"] = [title]

            if artist:
                audio["\xa9ART"] = [artist]

            audio.save()

            return True

        except Exception as e:

            logger.exception(e)

            return False

    # =====================================================
    # Generic
    # =====================================================

    def update_audio(
        self,
        file: Path,
        *,
        title: str = "",
        artist: str = "",
        cover: Path | None = None,
    ) -> bool:

        suffix = file.suffix.lower()

        if suffix == ".mp3":

            return self.update_mp3(
                file,
                title=title,
                artist=artist,
                cover=cover,
            )

        if suffix == ".m4a":

            return self.update_m4a(
                file,
                title=title,
                artist=artist,
            )

        return False

    # =====================================================
    # Remove Words
    # =====================================================

    def clean_title(
        self,
        title: str,
        exclude: str = "",
    ) -> str:

        if not exclude:
            return title.strip()

        return title.replace(
            exclude,
            "",
        ).strip()

    # =====================================================
    # Build Title
    # =====================================================

    def build_title(
        self,
        filename: str,
    ) -> str:

        return Path(filename).stem.replace(
            "_",
            " ",
        )

    # =====================================================
    # Exists
    # =====================================================

    def has_cover(
        self,
        file: Path,
    ) -> bool:

        try:

            audio = ID3(file)

            return "APIC:" in audio

        except Exception:

            return False


tag_service = TagService()
