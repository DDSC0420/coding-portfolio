"""Reading files: the right reader by content, and clear refusals for everything else."""

import io
import zipfile

import pytest

from intake import readers
from intake.readers import ReadError

from .conftest import make_docx, make_pdf

CV_LINES = ["Jordan Example", "Support Analyst", "jordan@example.com", "EXPERIENCE",
            "Support Analyst | Example Ltd | Jan 2020 - Present"]


def test_pdf_text_is_read():
    doc = readers.read(make_pdf(CV_LINES), "cv.pdf")
    assert doc.kind == "pdf" and doc.pages == 1
    assert "Support Analyst | Example Ltd | Jan 2020 - Present" in doc.text


def test_type_comes_from_the_bytes_not_the_name():
    assert readers.read(make_pdf(CV_LINES), "cv.txt").kind == "pdf"
    assert readers.read("\n".join(CV_LINES).encode(), "cv.pdf").kind == "text"


def test_docx_reads_page_header_bullets_and_tables_in_order():
    data = make_docx(header=["Jordan Example", "Support Analyst"], body=["Email: jordan@example.com", "Skills"],
                     bullets=["Answered 60 tickets a day"], table=[["Excel", "Zendesk"], ["SQL", ""]])
    text = readers.read(data, "cv.docx").text
    assert text.startswith("Jordan Example\nSupport Analyst\nEmail: jordan@example.com")
    assert "- Answered 60 tickets a day" in text  # Word list bullets get a visible marker
    assert "Excel | Zendesk\nSQL" in text


def test_plain_text_in_utf8_and_windows_1252():
    assert "Zoë Example" in readers.read("Zoë Example\n".encode() + b"x" * 60, "cv.txt").text
    assert "Zoë Example" in readers.read("Zoë Example\n".encode("cp1252") + b"x" * 60, "cv.txt").text


def test_long_pdfs_are_cut_with_a_warning():
    doc = readers.read(make_pdf(CV_LINES, pages=12), "cv.pdf")
    assert doc.pages == 12
    assert doc.warnings == ["Only the first 10 of 12 pages were read."]


def test_very_long_text_is_cut_with_a_warning():
    doc = readers.read(("word " * 20_000).encode(), "cv.txt")
    assert len(doc.text) == readers.MAX_CHARS and "very long" in doc.warnings[0]


@pytest.mark.parametrize("data, message", [
    (b"", "empty"),
    (b"x" * (readers.MAX_BYTES + 1), "larger than 5 MB"),
    (b"\xd0\xcf\x11\xe0" + b"\0" * 100, "Old Word .doc"),
    (b"\x89PNG\r\n\x1a\n" + b"\0" * 100, "image"),
    (b"\x00\x01\x02binary" * 20, "Unsupported file type"),
    (b"Too short", "Couldn't find any text"),
])
def test_unreadable_inputs_get_a_clear_message(data, message):
    with pytest.raises(ReadError, match=message):
        readers.read(data, "upload")


def test_a_pdf_with_no_text_layer_is_refused():
    with pytest.raises(ReadError, match="OCR"):
        readers.read(make_pdf([]), "scan.pdf")


def test_password_protected_pdf_is_refused():
    with pytest.raises(ReadError, match="password"):
        readers.read(make_pdf(CV_LINES, encrypt="secret"), "cv.pdf")


def test_damaged_pdf_is_refused():
    with pytest.raises(ReadError, match="damaged"):
        readers.read(b"%PDF-1.7\n" + b"garbage " * 50, "cv.pdf")


def _zip(files: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return out.getvalue()


def test_a_zip_that_isnt_a_word_document_is_refused():
    with pytest.raises(ReadError, match="not a Word document"):
        readers.read(_zip({"notes.txt": b"hello"}), "cv.docx")


def test_a_word_file_that_expands_hugely_is_not_opened():
    bomb = _zip({"word/document.xml": b"\0" * (readers.MAX_UNZIPPED + 1)})
    assert len(bomb) < readers.MAX_BYTES  # small on disk, huge once unpacked
    with pytest.raises(ReadError, match="expands to more than 50 MB"):
        readers.read(bomb, "cv.docx")
