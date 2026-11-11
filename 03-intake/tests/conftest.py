"""Shared helpers: a fixed 'today' and builders for small PDF and Word files."""

import io
from datetime import date
from pathlib import Path

import pytest

TODAY = date(2026, 11, 11)
SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def make_docx(body: list[str], header: list[str] = (), bullets: list[str] = (), table: list[list[str]] = ()) -> bytes:
    from docx import Document

    doc = Document()
    if header:
        doc.sections[0].header.paragraphs[0].text = header[0]
        for line in header[1:]:
            doc.sections[0].header.add_paragraph(line)
    for line in body:
        doc.add_paragraph(line)
    for line in bullets:
        doc.add_paragraph(line, style="List Bullet")
    if table:
        grid = doc.add_table(rows=len(table), cols=len(table[0]))
        for r, row in enumerate(table):
            for c, value in enumerate(row):
                grid.cell(r, c).text = value
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def make_pdf(lines: list[str], pages: int = 1, encrypt: str | None = None) -> bytes:
    from fpdf import FPDF

    pdf = FPDF()
    for _ in range(pages):
        pdf.add_page()
        pdf.set_font("Helvetica", size=11)
        for line in lines:
            pdf.cell(0, 6, line, new_x="LMARGIN", new_y="NEXT")
    if encrypt:
        pdf.set_encryption(owner_password="owner", user_password=encrypt)
    return bytes(pdf.output())


@pytest.fixture
def sample_bytes():
    def load(name: str) -> bytes:
        return (SAMPLES / name).read_bytes()
    return load
