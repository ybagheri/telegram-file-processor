"""
Lightweight plugin registry for processors.

Instead of Dispatcher hardcoding an if/elif per file type, each processor
registers itself against the file_type string it handles:

    from core.registry import register_processor

    @register_processor("IMAGE")
    class ImageProcessor:
        async def process(self, job):
            ...

Dispatcher then just looks the class up by job.file_type. To add a new
processor: create the class, decorate it, and add one import line in
dispatcher/dispatcher.py so the module actually gets loaded (imports are
what trigger registration — nothing else needs to change).
"""

from __future__ import annotations

_PROCESSORS: dict[str, type] = {}


def register_processor(file_type: str):
    def decorator(cls):
        _PROCESSORS[file_type.upper()] = cls
        return cls
    return decorator


def get_registered_processors() -> dict[str, type]:
    return dict(_PROCESSORS)
