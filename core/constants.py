from enum import Enum

class MessageType(str, Enum):
    JOB = "job"
    RESULT = "result"
    PASSWORD_REQUEST = "password_request"
    PASSWORD_RESPONSE = "password_response"
    ERROR = "error"
    INFO = "info"
    FOLDER = "folder"     # archive folder announcement (tracked for the TOC)
    DONE = "done"         # job finished -> bot.py may now send the TOC


class JobStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


PROJECT_IDENTIFIER = "IELTS"
