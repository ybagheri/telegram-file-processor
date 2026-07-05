import json
from typing import Dict, Any
from .constants import MessageType, PROJECT_IDENTIFIER

class Protocol:
    @staticmethod
    def encode(data: Dict[str, Any]) -> str:
        """Encode message to JSON string"""
        message = {
            "project": PROJECT_IDENTIFIER,
            "version": 1,
            **data
        }
        return json.dumps(message, ensure_ascii=False)

    @staticmethod
    def decode(message: str) -> Dict[str, Any]:
        """Decode JSON string to dict"""
        data = json.loads(message)
        if data.get("project") != PROJECT_IDENTIFIER:
            raise ValueError("Invalid project identifier")
        return data
