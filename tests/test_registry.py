from dispatcher.dispatcher import Dispatcher
from core.registry import get_registered_processors


def test_all_known_file_types_are_registered():
    registered = get_registered_processors()
    for file_type in ("VIDEO", "AUDIO", "PDF", "ARCHIVE"):
        assert file_type in registered


def test_dispatcher_resolves_a_processor_instance_per_type():
    dispatcher = Dispatcher()
    video_processor = dispatcher._get_processor("VIDEO")
    assert video_processor is not None
    assert video_processor.__class__.__name__ == "VideoProcessor"


def test_dispatcher_returns_none_for_unknown_type():
    dispatcher = Dispatcher()
    assert dispatcher._get_processor("SOMETHING_MADE_UP") is None
