from pathlib import Path

import pytest
from ebooklib import epub as ebooklib_epub

from narrator.epub import EpubExtractionError, extract_book_text


def build_epub(
    tmp_path: Path,
    chapters: list[tuple[str, str]],
    non_linear: set[str] = frozenset(),
) -> str:
    book = ebooklib_epub.EpubBook()
    book.set_identifier("test-book")
    book.set_title("Test Book")
    book.set_language("en")
    items = []
    for i, (name, body_html) in enumerate(chapters):
        item = ebooklib_epub.EpubHtml(title=name, file_name=f"{name}.xhtml", lang="en")
        item.id = f"chapter_{i}"
        item.content = f"<html><head><title>{name}</title></head><body>{body_html}</body></html>"
        book.add_item(item)
        items.append(item)
    book.spine = [(item.id, "no" if item.title in non_linear else "yes") for item in items]
    book.add_item(ebooklib_epub.EpubNav())
    path = tmp_path / "test.epub"
    ebooklib_epub.write_epub(str(path), book)
    return str(path)


def test_extracts_spine_order_with_global_offsets(tmp_path: Path):
    path = build_epub(
        tmp_path,
        [
            ("one", "<p>First chapter text.</p>"),
            ("two", "<p>Second chapter text.</p>"),
        ],
    )
    book = extract_book_text(path)

    assert [d.href for d in book.docs] == ["one.xhtml", "two.xhtml"]
    assert book.full_text == "First chapter text.\n\nSecond chapter text."
    second = book.docs[1]
    assert book.full_text[second.char_start :] == second.text
    assert book.total_chars == len(book.full_text)


def test_inline_markup_does_not_split_sentences(tmp_path: Path):
    path = build_epub(
        tmp_path,
        [("one", "<p>He said <i>hello</i> and <b>waved</b> goodbye.</p><p>Next para.</p>")],
    )
    book = extract_book_text(path)
    assert book.full_text == "He said hello and waved goodbye.\n\nNext para."


def test_whitespace_and_br_normalization(tmp_path: Path):
    path = build_epub(
        tmp_path,
        [("one", "<p>Line one<br/>still   the\n same sentence.</p>")],
    )
    book = extract_book_text(path)
    assert book.full_text == "Line one still the same sentence."


def test_skips_non_linear_spine_items(tmp_path: Path):
    path = build_epub(
        tmp_path,
        [("cover", "<p>Cover art description.</p>"), ("one", "<p>Real text.</p>")],
        non_linear={"cover"},
    )
    book = extract_book_text(path)
    assert book.full_text == "Real text."


def test_headings_become_their_own_paragraphs(tmp_path: Path):
    path = build_epub(
        tmp_path,
        [("one", "<h1>Chapter One</h1><p>It begins.</p>")],
    )
    book = extract_book_text(path)
    assert book.full_text == "Chapter One\n\nIt begins."


def test_deterministic_across_runs(tmp_path: Path):
    path = build_epub(tmp_path, [("one", "<p>Some stable text.</p>" * 50)])
    assert extract_book_text(path).full_text == extract_book_text(path).full_text


def test_image_only_epub_raises(tmp_path: Path):
    path = build_epub(tmp_path, [("one", '<img src="page1.png"/>')])
    with pytest.raises(EpubExtractionError):
        extract_book_text(path)


def test_garbage_file_raises(tmp_path: Path):
    path = tmp_path / "not-an-epub.epub"
    path.write_bytes(b"definitely not a zip")
    with pytest.raises(EpubExtractionError):
        extract_book_text(str(path))
