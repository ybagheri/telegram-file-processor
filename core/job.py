from __future__ import annotations

import shutil

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from config import Paths
from core.constants import JobStatus
from core.job_options import JobOptions


@dataclass(slots=True)
class Job:

    # =====================================================
    # Telegram
    # =====================================================

    user_id: int

    message_id: int

    # =====================================================
    # Job
    # =====================================================

    job_id: str = field(
        default_factory=lambda: uuid4().hex[:12]
    )

    status: str = JobStatus.PENDING

    created_at: datetime = field(
        default_factory=datetime.utcnow
    )

    # =====================================================
    # Original File
    # =====================================================

    original_name: str = ""

    mime_type: str = ""

    file_type: str = ""

    file_size: int = 0

    input_file: Path | None = None

    # =====================================================
    # User Options
    # =====================================================

    options: JobOptions = field(
        default_factory=JobOptions
    )

    # =====================================================
    # Media Information
    # =====================================================

    duration: int = 0

    width: int = 0

    height: int = 0

    bitrate: int = 0

    # =====================================================
    # Results
    # =====================================================

    extracted_files: list[Path] = field(
        default_factory=list
    )

    output_files: list[Path] = field(
        default_factory=list
    )

    thumbnail: Path | None = None

    cover: Path | None = None

    # =====================================================
    # Working Directories
    # =====================================================

    root: Path = field(init=False)

    input_dir: Path = field(init=False)

    extracted_dir: Path = field(init=False)

    output_dir: Path = field(init=False)

    temp_dir: Path = field(init=False)

    thumbs_dir: Path = field(init=False)

    # =====================================================
    # Initialize
    # =====================================================

    def __post_init__(self):

        self.root = Paths.DOWNLOADS / f"job_{self.job_id}"

        self.input_dir = self.root / "input"

        self.extracted_dir = self.root / "extracted"

        self.output_dir = self.root / "output"

        self.temp_dir = self.root / "temp"

        self.thumbs_dir = self.root / "thumbs"

        self.create_directories()

    # =====================================================
    # Directories
    # =====================================================

    def create_directories(self):

        self.root.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.input_dir.mkdir(
            exist_ok=True,
        )

        self.extracted_dir.mkdir(
            exist_ok=True,
        )

        self.output_dir.mkdir(
            exist_ok=True,
        )

        self.temp_dir.mkdir(
            exist_ok=True,
        )

        self.thumbs_dir.mkdir(
            exist_ok=True,
        )

    # =====================================================
    # Status
    # =====================================================

    def set_status(
        self,
        status: str,
    ):

        self.status = status

    # =====================================================
    # Media Information
    # =====================================================

    def update_media_info(

        self,

        *,

        duration: int = 0,

        width: int = 0,

        height: int = 0,

        bitrate: int = 0,

    ):

        self.duration = duration

        self.width = width

        self.height = height

        self.bitrate = bitrate

    # =====================================================
    # Outputs
    # =====================================================

    def add_output(
        self,
        file: Path,
    ):

        if file.exists():

            self.output_files.append(file)

    def add_extracted(
        self,
        file: Path,
    ):

        if file.exists():

            self.extracted_files.append(file)

    def set_thumbnail(
        self,
        file: Path,
    ):

        if file.exists():

            self.thumbnail = file

    def set_cover(
        self,
        file: Path,
    ):

        if file.exists():

            self.cover = file

    # =====================================================
    # Helpers
    # =====================================================

    @property
    def has_output(self) -> bool:

        return len(self.output_files) > 0

    @property
    def first_output(self) -> Path | None:

        if not self.output_files:

            return None

        return self.output_files[0]

    @property
    def stem(self) -> str:

        if self.options and self.options.rename_to:

            return self.options.rename_to

        if self.original_name:

            return Path(self.original_name).stem

        return self.job_id

    # =====================================================
    # Cleanup
    # =====================================================

    def cleanup(self):

        if self.root.exists():

            shutil.rmtree(
                self.root,
                ignore_errors=True,
            )
