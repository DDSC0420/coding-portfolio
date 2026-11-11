"""Turn an uploaded file into cleaned text. PDF, Word (.docx) and plain text.

The file type is decided by its first bytes, not its name. Everything happens in memory;
nothing is written to disk.
"""

import io
import logging
import zipfile
from dataclasses import dataclass, field

from .textutil import clean

MAX_BYTES = 5 * 1024 * 1024
MAX_PAGES = 10
MAX_CHARS = 60_000
MAX_UNZIPPED = 50 * 1024 * 1024
MIN_CHARS = 40

logging.getLogger("pypdf").setLevel(logging.ERROR)


class ReadError(ValueError):
    """The file can't be read. The message is safe to show to the user."""


@dataclass
class Document:
    text: str
    kind: str  # "pdf" | "docx" | "text"
    pages: int | None = None
    warnings: list[str] = field(default_factory=list)


def read(data: bytes, filename: str = "") -> Document:
    if not data:
        raise ReadError("The file is empty.")
    if len(data) > MAX_BYTES:
        raise ReadError(f"The file is larger than {MAX_BYTES // (1024 * 1024)} MB.")

    if data.startswith(b"%PDF"):
        doc = _read_pdf(data)
    elif data.startswith(b"PK\x03\x04"):
        doc = _read_docx(data)
    elif data.startswith(b"\xd0\xcf\x11\xe0"):
        raise ReadError("Old Word .doc files aren't supported. Save it as .docx or PDF and try again.")
    elif data.startswith((b"\x89PNG", b"\xff\xd8\xff", b"GIF8")):
        raise ReadError("That's an image. Intake reads text, so scanned CVs would need OCR, which it doesn't do.")
    else:
        doc = Document(_decode_text(data), "text")

    doc.text = clean(doc.text)
    if len(doc.text) < MIN_CHARS:
        raise ReadError("Couldn't find any text in that file. Scanned CVs (images) need OCR, which Intake doesn't do.")
    if len(doc.text) > MAX_CHARS:
        doc.text = doc.text[:MAX_CHARS]
        doc.warnings.append(f"The CV is very long; only the first {MAX_CHARS:,} characters were read.")
    return doc


def _decode_text(data: bytes) -> str:
    if b"\x00" in data[:4096]:
        raise ReadError("Unsupported file type. Use a PDF, a Word .docx file or plain text.")
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ReadError("Couldn't read the text encoding of that file. Save it as UTF-8 and try again.")


def _read_pdf(data: bytes) -> Document:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ReadError("That PDF is password protected.")
        pages = reader.pages
        count = len(pages)
        texts = [(pages[i].extract_text() or "") for i in range(min(count, MAX_PAGES))]
    except ReadError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError, OSError) as exc:
        raise ReadError("That PDF looks damaged and couldn't be read.") from exc
    warnings = [f"Only the first {MAX_PAGES} of {count} pages were read."] if count > MAX_PAGES else []
    return Document("\n\n".join(texts), "pdf", pages=count, warnings=warnings)


def _read_docx(data: bytes) -> Document:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())
            unzipped = sum(info.file_size for info in archive.infolist())
    except zipfile.BadZipFile as exc:
        raise ReadError("That file looks like a damaged Word document.") from exc
    if "word/document.xml" not in names:
        raise ReadError("That's a zip file, not a Word document.")
    if unzipped > MAX_UNZIPPED:
        raise ReadError("That Word file expands to more than 50 MB, so it wasn't opened.")

    from docx import Document as open_docx
    from docx.table import Table

    try:
        document = open_docx(io.BytesIO(data))
    except Exception as exc:  # python-docx raises a variety of parser errors
        raise ReadError("That Word document couldn't be read.") from exc

    lines: list[str] = []
    # CV templates often put the name and contact details in the page header.
    for paragraph in document.sections[0].header.paragraphs if document.sections else []:
        lines.append(_paragraph_text(paragraph))
    for block in document.iter_inner_content():
        if isinstance(block, Table):
            lines.extend(_table_lines(block))
        else:
            lines.append(_paragraph_text(block))
    return Document("\n".join(lines), "docx")


def _paragraph_text(paragraph) -> str:
    text = paragraph.text
    # Word list bullets live in numbering properties, not in the text. Put a marker back.
    ppr = paragraph._p.pPr
    is_list = (ppr is not None and ppr.numPr is not None) or (paragraph.style is not None
                                                              and paragraph.style.name.startswith("List"))
    return f"- {text}" if is_list and text.strip() else text


def _table_lines(table) -> list[str]:
    out = []
    for row in table.rows:
        cells, seen = [], set()
        for cell in row.cells:  # merged cells repeat; keep each once
            if id(cell._tc) in seen:
                continue
            seen.add(id(cell._tc))
            if cell.text.strip():
                cells.append(" ".join(cell.text.split()))
        if cells:
            out.append(" | ".join(cells))
    return out
