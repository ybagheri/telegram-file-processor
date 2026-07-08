from __future__ import annotations

import asyncio
from pathlib import Path

from telethon import events

from config import Config

from core.constants import (
    MessageType,
    JobStatus,
)

from core.job import Job
from core.job_options import JobOptions
from core.logger import get_logger
from core.protocol import Protocol

from dispatcher.dispatcher import Dispatcher

from services.telegram import TelegramService

logger = get_logger(__name__)

telegram = TelegramService()

dispatcher = Dispatcher()


# =====================================================
# Part 1
# Initialization
# =====================================================


async def startup():

    logger.info(
        "Worker starting...",
    )

    await telegram.start()

    logger.info(
        "Worker connected.",
    )


async def shutdown():

    logger.info(
        "Worker stopping...",
    )

    await telegram.stop()

    logger.info(
        "Worker disconnected.",
    )
	
# =====================================================
# Part 2
# Build Job
# =====================================================

async def build_job(
    event,
    payload: dict,
) -> Job:

    message = event.message

    options = JobOptions(
        quality=payload.get("options", {}).get(
            "quality",
            "",
        ),
        title=payload.get("options", {}).get(
            "title",
            "",
        ),
        artist=payload.get("options", {}).get(
            "artist",
            "",
        ),
        password=payload.get("options", {}).get(
            "password",
            "",
        ),
    )

    job = Job(
        user_id=payload["user_id"],
        message_id=payload["message_id"],
        original_name=payload["original_name"],
        mime_type=payload["mime_type"],
        file_type=payload["file_type"],
        file_size=payload["file_size"],
        options=options,
    )

    file_name = (
        job.original_name
        or f"{job.job_id}.bin"
    )

    input_file = job.input_dir / file_name

    await telegram.download(
        message,
        input_file,
    )

    job.input_file = input_file

    logger.info(
        "Job %s created",
        job.job_id,
    )

    return job
	
# =====================================================
# Part 3
# Send Results
# =====================================================

async def send_results(
    job: Job,
):

    total = len(
        job.output_files,
    )

    if total == 0:

        logger.warning(
            "Job %s has no output.",
            job.job_id,
        )

        return

    for index, output in enumerate(

        job.output_files,

        start=1,

    ):

        caption = Protocol.create_result(

            user_id=job.user_id,

            job_id=job.job_id,

            file_index=index,

            file_count=total,

            caption=job.result_caption,

            delete_after_send=True,

            silent=False,

        )

        await telegram.upload_file(

            path=output,

            caption=caption,

        )

        logger.info(

            "Output %s/%s sent (%s)",

            index,

            total,

            output.name,

        )
# =====================================================
# Part 4
# Password Request
# =====================================================

async def request_password(
    job: Job,
    filename: str,
) -> bool:

    payload = Protocol.create_password_request(

        user_id=job.user_id,

        job_id=job.job_id,

        filename=filename,

    )

    await telegram.send_password_request(
        payload,
    )

    logger.info(
        "Password requested (%s)",
        job.job_id,
    )

    return True


# =====================================================
# Password Response
# =====================================================

_password_waiters: dict[str, asyncio.Future] = {}


async def wait_for_password(
    job_id: str,
    timeout: int = 300,
) -> str | None:

    loop = asyncio.get_running_loop()

    future = loop.create_future()

    _password_waiters[job_id] = future

    try:

        return await asyncio.wait_for(
            future,
            timeout=timeout,
        )

    except asyncio.TimeoutError:

        logger.warning(
            "Password timeout (%s)",
            job_id,
        )

        return None

    finally:

        _password_waiters.pop(
            job_id,
            None,
        )


async def handle_password_response(
    payload: dict,
):

    job_id = payload["job_id"]

    future = _password_waiters.get(
        job_id,
    )

    if future is None:

        return

    if future.done():

        return

    future.set_result(
        payload["password"],
    )
	
# =====================================================
# Part 5
# Bridge Handler
# =====================================================

@telegram.client.on(
    events.NewMessage(
        chats=Config.GROUP_ID,
    )
)
async def handle_bridge(
    event,
):

    message = event.message

    payload = None

    try:

        if message.caption:

            payload = Protocol.decode(
                message.caption,
            )

        elif message.message:

            payload = Protocol.decode(
                message.message,
            )

        else:

            return

    except Exception:

        return

    msg_type = payload.get(
        "type",
    )

    # -------------------------------------------------
    # Password Response
    # -------------------------------------------------

    if msg_type == MessageType.PASSWORD_RESPONSE:

        await handle_password_response(
            payload,
        )

        return

    # -------------------------------------------------
    # Job
    # -------------------------------------------------

    if msg_type != MessageType.JOB:

        return

    logger.info(
        "Job received (%s)",
        payload["user_id"],
    )

    job = None

    try:

        job = await build_job(
            event,
            payload,
        )

        ok = await dispatcher.dispatch(
            job,
        )

        if not ok:

            logger.error(
                "Job failed (%s)",
                job.job_id,
            )

            return

        await send_results(
            job,
        )

        logger.info(
            "Job completed (%s)",
            job.job_id,
        )

    except Exception:

        logger.exception(
            "Worker error",
        )

    finally:

        if job:

            job.cleanup()
			
# =====================================================
# Part 6
# Main
# =====================================================

async def main():

    await startup()

    logger.info(
        "Worker is running..."
    )

    try:

        await telegram.client.run_until_disconnected()

    finally:

        await shutdown()


if __name__ == "__main__":

    try:

        asyncio.run(
            main(),
        )

    except KeyboardInterrupt:

        logger.info(
            "Worker stopped."
        )
