import json
from typing import Any

from .constants import (
    PROJECT_IDENTIFIER,
    MessageType,
)

PROTOCOL_VERSION = 1


class Protocol:

    @staticmethod
    def encode(payload: dict[str, Any]) -> str:
        payload = {
            "project": PROJECT_IDENTIFIER,
            "version": PROTOCOL_VERSION,
            **payload,
        }

        return json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @staticmethod
    def decode(text: str) -> dict[str, Any]:
        data = json.loads(text)

        if data.get("project") != PROJECT_IDENTIFIER:
            raise ValueError("Invalid project")

        if data.get("version") != PROTOCOL_VERSION:
            raise ValueError("Unsupported protocol version")

        return data

    @staticmethod
    def create_job(
        *,
        user_id: int,
        options: dict | None = None,
    ) -> dict:

        return {
            "type": MessageType.JOB,
            "user_id": user_id,
            "options": options or {},
        }

    @staticmethod
    def create_result(
        *,
        user_id: int,
        job_id: str,
        files: list | None = None,
        target_chat_id: int = 0,
    ) -> dict:

        return {
            "type": MessageType.RESULT,
            "user_id": user_id,
            "job_id": job_id,
            "files": files or [],
            "target_chat_id": target_chat_id,
        }

    @staticmethod
    def create_password_request(
        *,
        user_id: int,
        job_id: str,
        filename: str,
    ) -> dict:

        return {
            "type": MessageType.PASSWORD_REQUEST,
            "user_id": user_id,
            "job_id": job_id,
            "filename": filename,
        }

    @staticmethod
    def create_password_response(
        *,
        user_id: int,
        job_id: str,
        password: str,
    ) -> dict:

        return {
            "type": MessageType.PASSWORD_RESPONSE,
            "user_id": user_id,
            "job_id": job_id,
            "password": password,
        }

    @staticmethod
    def create_error(
        *,
        user_id: int,
        job_id: str,
        message: str,
        target_chat_id: int = 0,
    ) -> dict:

        return {
            "type": MessageType.ERROR,
            "user_id": user_id,
            "job_id": job_id,
            "message": message,
            "target_chat_id": target_chat_id,
        }

    @staticmethod
    def create_info(
        *,
        user_id: int,
        job_id: str,
        message: str,
    ) -> dict:

        return {
            "type": MessageType.INFO,
            "user_id": user_id,
            "job_id": job_id,
            "message": message,
        }
