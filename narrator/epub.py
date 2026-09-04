"""Epub → plain narration text.

Extraction must be deterministic: character offsets into the result are the
coordinate system for progress mapping and the segment cache. Any change in
output must bump EXTRACTOR_VERSION so cached books are re-extracted.
"""

import re
import warnings
from dataclasses import dataclass

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from ebooklib import epub

# Lenient HTML parsing on purpose: real epubs are full of malformed XHTML.
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

EXTRACTOR_VERSION = 1
PARAGRAPH_BREAK = "\n\n"  # between docs and paragraphs; segments never cross it

_DROP_TAGS = ["script", "style", "head", "title"]
_BLOCK_TAGS = [
    "p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "figcaption",
    "caption", "tr", "dt", "dd", "section", "article", "header", "footer", "pre",
    "table", "ol", "ul",
]  # fmt: skip
# Private-use marker placed around block elements before text extraction, so inline
# markup never splits a sentence while block structure still breaks paragraphs.
_BLOCK_MARK = "\ue000"
_WS_RUN = re.compile(r"\s+")


@dataclass(frozen=True)
class DocText:
    href: str
    char_start: int  # offset of this doc within BookText.full_text
    text: str


@dataclass(frozen=True)
class BookText:
    docs: list[DocText]
    full_text: str

    @property
    def total_chars(self) -> int:
        return len(self.full_text)


class EpubExtractionError(Exception):
    """Not a narratable epub: DRM, image-only, or malformed."""


def extract_book_text(path: str) -> BookText:
    try:
        book = epub.read_epub(path, options={"ignore_ncx": True})
    except Exception as e:  # ebooklib raises bare Exception subclasses
        raise EpubExtractionError(f"unreadable epub: {e}") from e

    docs: list[DocText] = []
    parts: list[str] = []
    offset = 0
    for idref, linear in book.spine:
        if isinstance(linear, str) and linear.lower() == "no":
            continue  # out-of-flow content: covers, answer keys
        item = book.get_item_with_id(idref)
        if item is None:
            continue
        text = _document_text(item.get_content())
        if not text:
            continue
        if parts:
            offset += len(PARAGRAPH_BREAK)
        docs.append(DocText(href=item.get_name(), char_start=offset, text=text))
        parts.append(text)
        offset += len(text)

    full_text = PARAGRAPH_BREAK.join(parts)
    if not full_text:
        raise EpubExtractionError("epub contains no extractable text (image-only or DRM?)")
    return BookText(docs=docs, full_text=full_text)


def _document_text(content: bytes) -> str:
    soup = BeautifulSoup(content, "lxml")
    for tag in soup.find_all(_DROP_TAGS):
        tag.decompose()
    for br in soup.find_all("br"):
        br.replace_with(" ")
    for block in soup.find_all(_BLOCK_TAGS):
        block.insert_before(_BLOCK_MARK)
        block.append(_BLOCK_MARK)
    paragraphs = (_WS_RUN.sub(" ", piece).strip() for piece in soup.get_text().split(_BLOCK_MARK))
    return PARAGRAPH_BREAK.join(p for p in paragraphs if p)
