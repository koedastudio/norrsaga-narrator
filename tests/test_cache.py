from pathlib import Path

from narrator.cache import Cache, StoredBook
from narrator.epub import BookText, DocText
from narrator.segments import Segment


def stored_book() -> StoredBook:
    book = BookText(
        docs=[DocText(href="one.xhtml", char_start=0, text="Hello world.")],
        full_text="Hello world.",
    )
    return StoredBook(book, [Segment(0, 0, 12)], "v1-c300-abc", "ino-1")


def write_segment(cache: Cache, index: int, duration: float) -> Path:
    path = cache.segment_path("item1", "voice", index)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"mp3")
    cache.store_segment("item1", "voice", index, duration)
    return path


def test_book_round_trip(tmp_path: Path):
    cache = Cache(tmp_path)
    cache.store_book("item1", stored_book())
    assert cache.load_book("item1") == stored_book()


def test_load_missing_or_corrupt_book(tmp_path: Path):
    cache = Cache(tmp_path)
    assert cache.load_book("nope") is None
    cache.book_path("bad").parent.mkdir(parents=True)
    cache.book_path("bad").write_text("{not json")
    assert cache.load_book("bad") is None


def test_segment_meta_and_contiguous_run(tmp_path: Path):
    cache = Cache(tmp_path)
    for index, duration in [(5, 10.0), (6, 12.0), (8, 9.0)]:  # gap at 7
        write_segment(cache, index, duration)

    assert cache.segment_duration("item1", "voice", 5) == 10.0
    assert cache.segment_duration("item1", "voice", 7) is None
    assert cache.contiguous_durations("item1", "voice", 5, 10) == [10.0, 12.0]
    assert cache.contiguous_durations("item1", "voice", 8, 10) == [9.0]
    assert cache.contiguous_durations("item1", "voice", 0, 10) == []


def test_duration_memo_survives_meta_deletion_but_not_drop_book(tmp_path: Path):
    cache = Cache(tmp_path)
    assert cache.segment_duration("item1", "voice", 0) is None  # miss not memoized
    path = write_segment(cache, 0, 7.5)
    assert cache.segment_duration("item1", "voice", 0) == 7.5
    path.with_suffix(".json").unlink()
    assert cache.segment_duration("item1", "voice", 0) == 7.5
    cache.drop_book("item1")
    assert cache.segment_duration("item1", "voice", 0) is None


def test_drop_book_removes_everything(tmp_path: Path):
    cache = Cache(tmp_path)
    cache.store_book("item1", stored_book())
    path = write_segment(cache, 0, 1.0)
    cache.drop_book("item1")
    assert cache.load_book("item1") is None
    assert not path.exists()
    cache.drop_book("item1")  # no-op when absent


def test_path_traversal_is_neutralized(tmp_path: Path):
    cache = Cache(tmp_path)
    evil = cache.segment_path("../../etc", "voice", 0)
    assert tmp_path in evil.parents
