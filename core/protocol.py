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
        message_id: int,
        file_type: str,
        original_name: str,
        mime_type: str,
        file_size: int,
        options: dict | None = None,
    ) -> dict:

        return {

            "type": MessageType.JOB,

            "user_id": user_id,

            "message_id": message_id,

            "file_type": file_type,

            "original_name": original_name,

            "mime_type": mime_type,

            "file_size": file_size,

            "options": options or {},

        }

    @staticmethod
    def create_result(
        *,
        user_id: int,
        job_id: str,
        file_index: int = 1,
        file_count: int = 1,
        caption: str = "",
        delete_after_send: bool = True,
        silent: bool = False,
    ) -> dict:

        return {
            "type": MessageType.RESULT,
            "user_id": user_id,
            "job_id": job_id,
            "file_index": file_index,
            "file_count": file_count,
            "caption": caption,
            "delete_after_send": delete_after_send,
            "silent": silent,
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
    ) -> dict:

        return {

            "type": MessageType.ERROR,

            "user_id": user_id,

            "job_id": job_id,

            "level": "error",

            "message": message,

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

            "level": "info",

            "message": message,

        }
