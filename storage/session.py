from __future__ import annotations

from dataclasses import dataclass, field

from core.job_options import JobOptions


@dataclass(slots=True)
class UserSession:
    # =====================================================
    # User
    # =====================================================

    user_id: int

    # =====================================================
    # Telegram Message
    # =====================================================

    chat_id: int = 0

    message_id: int = 0

    file_id: str | None = None

    # =====================================================
    # File Information
    # =====================================================

    file_name: str = ""

    mime_type: str = ""

    file_size: int = 0

    file_type: str = ""

    # =====================================================
    # Archive Password
    # =====================================================

    waiting_password: bool = False

    password_job_id: str | None = None

    # =====================================================
    # User Options
    # =====================================================

    options: JobOptions = field(
        default_factory=JobOptions,
    )

    # =====================================================
    # Helpers
    # =====================================================

    def reset(self):

        self.chat_id = 0

        self.message_id = 0

        self.file_id = None

        self.file_name = ""

        self.mime_type = ""

        self.file_size = 0

        self.file_type = ""

        self.waiting_password = False

        self.password_job_id = None

        self.options = JobOptions()


class SessionStorage:

    def __init__(self):

        self._sessions: dict[int, UserSession] = {}

    # =====================================================
    # Get
    # =====================================================

    def get(
        self,
        user_id: int,
    ) -> UserSession:

        if user_id not in self._sessions:

            self._sessions[user_id] = UserSession(
                user_id=user_id,
            )

        return self._sessions[user_id]

    # =====================================================
    # Exists
    # =====================================================

    def exists(
        self,
        user_id: int,
    ) -> bool:

        return user_id in self._sessions

    # =====================================================
    # Remove
    # =====================================================

    def remove(
        self,
        user_id: int,
    ):

        self._sessions.pop(
            user_id,
            None,
        )

    # =====================================================
    # Reset
    # =====================================================

    def reset(
        self,
        user_id: int,
    ):

        session = self.get(
            user_id,
        )

        session.reset()

    # =====================================================
    # Clear
    # =====================================================

    def clear(self):

        self._sessions.clear()


session_storage = SessionStorage()