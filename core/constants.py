from enum import Enum

class MessageType(str, Enum):
    JOB = "job"
    RESULT = "result"
    PASSWORD_REQUEST = "password_request"
    PASSWORD_RESPONSE = "password_response"
    ERROR = "error"
    INFO = "info"

PROJECT_IDENTIFIER = "IELTS"
