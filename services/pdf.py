from __future__ import annotations

import asyncio
from pathlib import Path

from config import Processing

from core.logger import get_logger

logger = get_logger(__name__)


class PDFService:

    async def optimize(

        self,

        input_file: Path,

        output_file: Path,

    ) -> bool:

        cmd = [

            Processing.GHOSTSCRIPT,

            "-sDEVICE=pdfwrite",

            "-dCompatibilityLevel=1.4",

            "-dPDFSETTINGS=/ebook",

            "-dNOPAUSE",

            "-dQUIET",

            "-dBATCH",

            f"-sOutputFile={output_file}",

            str(input_file),

        ]

        return await self._run(
            cmd,
        )

    async def _run(
        self,
        cmd,
    ) -> bool:

        logger.info(
            "Running: %s",
            " ".join(map(str, cmd)),
        )

        process = await asyncio.create_subprocess_exec(

            *map(str, cmd),

            stdout=asyncio.subprocess.PIPE,

            stderr=asyncio.subprocess.PIPE,

        )

        _, stderr = await process.communicate()

        if process.returncode != 0:

            logger.error(
                stderr.decode(),
            )

            return False

        return True


pdf_service = PDFService()
