from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional
from uuid import uuid4

@dataclass
class Job:
    user_id: int
    message_id: int
    file_type: str
    job_id: str = field(default_factory=lambda: f"job_{uuid4().hex[:8]}")
    status: str = "pending"
    options: Dict = field(default_factory=dict)
    password: Optional[str] = None
    input_file: Optional[Path] = None
    output_files: List[Path] = field(default_factory=list)
    folders: Dict[str, Path] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)

    def create_folders(self, base_downloads: Path):
        """Create job-specific folders"""
        job_dir = base_downloads / self.job_id
        for folder_name in ["input", "extracted", "output", "temp", "thumbs"]:
            folder = job_dir / folder_name
            folder.mkdir(parents=True, exist_ok=True)
            self.folders[folder_name] = folder
        return job_dir
