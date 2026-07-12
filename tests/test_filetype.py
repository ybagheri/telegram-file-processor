from utils.filetype import FileTypeDetector


def test_detects_video_by_extension():
    assert FileTypeDetector.detect("", "clip.mp4") == "VIDEO"
    assert FileTypeDetector.detect("", "clip.mkv") == "VIDEO"


def test_detects_video_by_mime():
    assert FileTypeDetector.detect("video/quicktime", "unknown_ext.bin") == "VIDEO"


def test_detects_audio():
    assert FileTypeDetector.detect("", "song.mp3") == "AUDIO"
    assert FileTypeDetector.detect("audio/ogg", "voice.oga") == "AUDIO"


def test_detects_pdf():
    assert FileTypeDetector.detect("", "book.pdf") == "PDF"
    assert FileTypeDetector.detect("application/pdf", "no_extension") == "PDF"


def test_detects_archive():
    for ext in ("zip", "rar", "7z"):
        assert FileTypeDetector.detect("", f"course.{ext}") == "ARCHIVE"


def test_unknown_falls_back_safely():
    assert FileTypeDetector.detect("", "readme.txt") == "UNKNOWN"
    assert FileTypeDetector.detect("", "") == "UNKNOWN"
