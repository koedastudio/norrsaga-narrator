import itertools

from narrator.epub import BookText
from narrator.segments import (
    Segment,
    fingerprint,
    percent_to_segment,
    position_to_percent,
    segment_book,
)


def book_of(text: str) -> BookText:
    return BookText(docs=[], full_text=text)


def segment_texts(book: BookText, max_chars: int = 300) -> list[str]:
    return [book.full_text[s.char_start : s.char_end] for s in segment_book(book, max_chars)]


def test_packs_consecutive_sentences_up_to_limit():
    book = book_of("One sentence here. Another short one. A third one follows.")
    assert segment_texts(book, max_chars=45) == [
        "One sentence here. Another short one.",
        "A third one follows.",
    ]


def test_single_paragraph_below_limit_is_one_segment():
    book = book_of("Just one short paragraph.")
    assert segment_texts(book) == ["Just one short paragraph."]


def test_paragraph_break_always_splits():
    book = book_of("Short one.\n\nShort two.")
    assert segment_texts(book) == ["Short one.", "Short two."]


def test_overlong_sentence_splits_at_word_boundaries():
    words = " ".join(["word"] * 30)  # 149 chars, no sentence boundary
    book = book_of(words)
    texts = segment_texts(book, max_chars=50)
    assert len(texts) > 1
    assert all(len(t) <= 50 for t in texts)
    assert " ".join(texts) == words


def test_unbroken_run_hard_splits():
    blob = "x" * 120
    texts = segment_texts(book_of(blob), max_chars=50)
    assert "".join(texts) == blob
    assert all(len(t) <= 50 for t in texts)


def test_indices_are_sequential_and_spans_ordered():
    book = book_of("A. B. C.\n\n" * 20)
    segs = segment_book(book, max_chars=10)
    assert [s.index for s in segs] == list(range(len(segs)))
    for prev, cur in itertools.pairwise(segs):
        assert cur.char_start >= prev.char_end


def test_deterministic():
    book = book_of("Sentence one. Sentence two! Sentence three?\n\nMore text here. " * 10)
    assert segment_book(book) == segment_book(book)


def test_fingerprint_changes_with_text_and_params():
    a, b = book_of("Some text."), book_of("Other text.")
    assert fingerprint(a) != fingerprint(b)
    assert fingerprint(a, max_chars=100) != fingerprint(a, max_chars=300)
    assert fingerprint(a) == fingerprint(book_of("Some text."))


def test_percent_to_segment_boundaries():
    segs = [Segment(0, 0, 100), Segment(1, 100, 200), Segment(2, 200, 300)]
    assert percent_to_segment(segs, 0.0, 300) == 0
    assert percent_to_segment(segs, 0.1, 300) == 0
    assert percent_to_segment(segs, 0.5, 300) == 1
    assert percent_to_segment(segs, 1.0, 300) == 2  # clamps to last
    assert percent_to_segment(segs, -0.5, 300) == 0
    assert percent_to_segment([], 0.5, 300) == 0


def test_position_to_percent_interpolates():
    segs = [Segment(0, 0, 100), Segment(1, 100, 200), Segment(2, 200, 300)]
    # Start at segment 1; two synthesized segments of 10 s each.
    at = lambda pos: position_to_percent(segs, 1, [10.0, 10.0], pos, 300)  # noqa: E731
    assert at(0.0) == 100 / 300
    assert at(5.0) == 150 / 300
    assert at(10.0) == 200 / 300
    assert at(15.0) == 250 / 300
    assert at(999.0) == 300 / 300  # clamps to end of known audio


def test_position_to_percent_round_trips_with_percent_to_segment():
    segs = [Segment(i, i * 50, (i + 1) * 50) for i in range(10)]
    total = 500
    start = percent_to_segment(segs, 0.42, total)  # char 210 → segment 4
    pct = position_to_percent(segs, start, [20.0] * 3, 30.0, total)
    # 30 s into segment 4 (+1.5 segments) → chars 200..300 range
    assert 200 / 500 < pct < 300 / 500
    assert percent_to_segment(segs, pct, total) in (start + 1, start + 2)
