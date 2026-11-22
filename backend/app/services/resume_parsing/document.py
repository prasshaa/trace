"""Document ingestion and validation, then layout-aware line extraction (resume parser v2).

Everything happens in memory on the bytes the API received: nothing is written to disk, nothing is fetched, and no
content is logged. Supported formats are decided by the file's own signature, never by its name or the declared type
alone:

  PDF   b"%PDF-" ... text-based PDFs (pdfplumber, MIT). Encrypted PDFs, PDFs over MAX_PAGES and PDFs with no text
        layer (scans: OCR is not supported) are refused with a reason code.
  DOCX  a ZIP with [Content_Types].xml and word/document.xml (python-docx, MIT). Macro-enabled documents, path
        traversal names, too many entries, a large uncompressed size or a suspicious compression ratio are refused
        before python-docx opens the archive.

Output: a list of Line objects in reading order. For PDFs the reading order is reconstructed from word coordinates:
a page with two columns (a sidebar or a skills grid) is read column by column instead of across, and every line keeps
its position, font size and boldness so later stages can join wrapped lines and spot headings.
"""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from typing import List, Optional

MAX_PAGES = 8
MAX_TEXT_CHARS = 60_000
MAX_ZIP_ENTRIES = 400
MAX_ZIP_UNCOMPRESSED = 25 * 1024 * 1024
MAX_ZIP_RATIO = 120
# Link and icon glyphs that decorate names ("Acme ↗"), icon-font private-use characters and variation selectors. They
# carry no text and are removed everywhere (one once ended up inside an employer name).
DECOR = re.compile("[↖-↙⇗⬈➚➶⧉\U0001f517︎️-]")
X_TOLERANCE = 1.5
# A hyperlink LABEL with its link glyph names the link, not the role or the employer.
_GLYPH = "\u2196-\u2199\u21d7\u2b08\u279a\u27b6\u29c9\U0001f517"
# Only the bracketed form is certain ("(Case Study ↗)"); a bare "Company ↗" may end a tagline that is itself a link, so the
# header parser decides that case (parser._parts drops a trailing "Company" only after a job title).
LINK_LABEL = re.compile(rf"\s*\(\s*(?:company|website|web\s?site|site|link|profile|portfolio|homepage|home\s?page|case\s?study|demo|"
                        rf"app|article|blog|video|github|linkedin)\s*[{_GLYPH}][\ufe0e\ufe0f]?\s*\)", re.I)


def clean_text(text: str) -> str:
    text = LINK_LABEL.sub(" ", text or "")
    text = re.sub(r"\(\s*\)", " ", DECOR.sub(" ", text))
    return re.sub(r"\s+", " ", text).strip()

BULLET_GLYPHS = "•▪●◦‣·–-*■□►▶➢✓✔"


class DocumentRejected(Exception):
    """The file can't be processed. `code` is a stable reason; the message is safe to show (never file content)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class Line:
    text: str
    page: int = 0
    x0: float = 0.0
    x1: float = 0.0
    top: float = 0.0
    bottom: float = 0.0
    size: float = 0.0
    bold: bool = False
    bullet: bool = False          # the line starts with a bullet (glyph removed from text) or is a list paragraph
    column: int = 0               # 0 full width or single column, 1 left, 2 right
    col_left: float = 0.0         # the column's text margins on this page (document-wide), for wrap detection
    col_right: float = 0.0
    index: int = 0                # position in reading order (evidence reference)


@dataclass
class Document:
    format: str                   # "pdf" | "docx"
    pages: int
    layout: str                   # "one_column" | "two_column" | "docx"
    lines: List[Line] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(l.text for l in self.lines)


def sniff(data: bytes) -> Optional[str]:
    if data.startswith(b"%PDF-"):
        return "pdf"
    if data.startswith(b"PK\x03\x04"):
        return "docx"
    return None


def load(data: bytes) -> Document:
    kind = sniff(data)
    if kind == "pdf":
        doc = _pdf(data)
    elif kind == "docx":
        doc = _docx(data)
    else:
        raise DocumentRejected("unsupported_format", "Upload a PDF or Word (.docx) resume.")
    total = sum(len(l.text) for l in doc.lines)
    if total == 0:
        raise DocumentRejected("no_text", "No text could be read from this file. If it's a scan or an image, export "
                                          "your resume as a text-based PDF or a Word document and try again.")
    if total > MAX_TEXT_CHARS:
        raise DocumentRejected("too_long", "This document is much longer than a resume. Upload a resume of up to "
                                           f"{MAX_PAGES} pages.")
    edges: dict = {}
    for l in doc.lines:
        if l.x1 > l.x0:
            e = edges.setdefault((l.page, l.column), [l.x0, l.x1])
            e[0], e[1] = min(e[0], l.x0), max(e[1], l.x1)
    for i, l in enumerate(doc.lines):
        l.index = i
        l.col_left, l.col_right = edges.get((l.page, l.column), (0.0, 0.0))
    return doc


# --------------------------------------------------------------------------------------------------------------- PDF
def _pdf(data: bytes) -> Document:
    from pypdf import PdfReader
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise DocumentRejected("encrypted", "This PDF is password-protected. Remove the password and upload it again.")
        pages = len(reader.pages)
    except DocumentRejected:
        raise
    except Exception:
        raise DocumentRejected("unreadable", "This PDF couldn't be read. Export it again from your editor and retry.") from None
    if pages > MAX_PAGES:
        raise DocumentRejected("too_many_pages", f"Upload a resume of up to {MAX_PAGES} pages.")
    import pdfplumber
    lines: List[Line] = []
    two = False
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for pno, page in enumerate(pdf.pages):
                words = page.extract_words(x_tolerance=X_TOLERANCE, y_tolerance=2.5, keep_blank_chars=False,
                                           extra_attrs=["fontname", "size"])
                page_lines, cols = _page_lines(words, float(page.width), pno)
                two = two or cols
                lines.extend(page_lines)
    except DocumentRejected:
        raise
    except Exception:
        raise DocumentRejected("unreadable", "This PDF couldn't be read. Export it again from your editor and retry.") from None
    return Document("pdf", pages, "two_column" if two else "one_column", lines)


def _gutter(words: list, width: float) -> Optional[float]:
    """The x of a vertical gap that splits the page into two columns, or None. A gap counts when (almost) no word
    crosses it across the body of the page and both sides carry a real share of the words."""
    if len(words) < 40:
        return None
    lo, hi = int(width * 0.22), int(width * 0.78)
    cover = [0] * (hi - lo)
    for w in words:
        a, b = max(int(w["x0"]), lo), min(int(w["x1"]) + 1, hi)
        for x in range(a, b):
            cover[x - lo] += 1
    rows = len(_rows(words))
    limit = max(2, int(rows * 0.04))            # a few rows may overflow the gutter (a long contact or sidebar line)
    best, run_start, best_len = None, None, 0
    for i, c in enumerate(cover + [limit + 1]):
        if c <= limit:
            run_start = i if run_start is None else run_start
        elif run_start is not None:
            if i - run_start > best_len:
                best_len, best = i - run_start, lo + (run_start + i) / 2
            run_start = None
    if best is None or best_len < 8:
        return None
    left = [w for w in words if w["x1"] <= best]
    right = [w for w in words if w["x0"] >= best]
    # Both sides carry real content: a share of the words, or (a narrow sidebar beside a long main column) enough lines.
    small = min((left, right), key=len)
    # Both sides must be columns of their own (the smaller one left-aligned), and each must carry a share of the words,
    # or, for a narrow sidebar beside a long main column, enough lines.
    if not _sidebar(small, 4 if len(small) >= 0.12 * len(words) else 6) or len(small) < 0.04 * len(words):
        return None
    span = lambda ws: (max(w["bottom"] for w in ws) - min(w["top"] for w in ws)) if ws else 0  # noqa: E731
    tall = max(span(words), 1)
    if span(left) < 0.35 * tall or span(right) < 0.35 * tall:
        return None
    return best


def _sidebar(words: list, min_rows: int = 6) -> bool:
    """A narrow column of its own: at least six lines, most starting at one left edge. Right-aligned dates beside a
    one-column layout (ragged left edge) are not a sidebar."""
    rows = _rows(words)
    if len(rows) < min_rows:
        return False
    starts = [r[0]["x0"] for r in rows]
    edge = min(starts)
    # A column of dates (right-aligned "Feb 2024 – Present" beside a one-column layout) is not a sidebar either.
    dated = sum(1 for r in rows if len(r) <= 6 and re.search(r"(19|20)\d{2}|['’]\d{2}\b|present|current|till",
                                                          " ".join(w["text"] for w in r), re.I))
    # One left edge, allowing a bullet's hanging indent (wrapped bullet lines start ~12pt further in).
    return sum(1 for x in starts if x - edge <= 14) >= 0.7 * len(rows) and dated < 0.5 * len(rows)


def _rows(words: list) -> List[list]:
    rows: List[list] = []
    for w in sorted(words, key=lambda w: (round(w["top"]), w["x0"])):
        if rows and abs(rows[-1][0]["top"] - w["top"]) <= 2.5:
            rows[-1].append(w)
        else:
            rows.append([w])
    return [sorted(r, key=lambda w: w["x0"]) for r in rows]


CID = re.compile(r"\(cid:\d+\)")


def _line(row: list, page: int, column: int) -> Optional[Line]:
    # An unmapped glyph ("(cid:127)") at the start of a row is a bullet symbol in a font without a Unicode map;
    # elsewhere it carries no text and is dropped.
    if row and CID.fullmatch(row[0]["text"]) and len(row) > 1:
        row = [{**row[0], "text": "•"}] + row[1:]
    row = [w if not CID.search(w["text"]) or w is row[0] else {**w, "text": CID.sub("", w["text"])} for w in row]
    row = [w for w in row if w["text"].strip()]
    if not row:
        return None
    parts, prev = [], None
    for w in row:
        if prev is not None:
            gap = w["x0"] - prev["x1"]
            # pdfplumber also splits a word where the font changes ("Tallyfox" bold, "," regular). Such pieces touch:
            # a gap within the word tolerance is no space at all (a real space is wider than the tolerance).
            parts.append(" | " if gap > max(18.0, 3.5 * float(w.get("size") or 10)) else ("" if gap <= X_TOLERANCE else " "))
        parts.append(w["text"])
        prev = w
    text = clean_text("".join(parts)).strip("|").strip()
    if not text:
        return None
    bullet = False
    first = row[0]["text"]
    if first.startswith("(cid:") and len(first) > 6 and not CID.fullmatch(first):     # "(cid:127)Designed" glued
        m0 = CID.match(first)
        if m0:
            row = [{**row[0], "text": first[m0.end():]}] + row[1:]
            text = CID.sub("", text, count=1).strip()
            first = "•"
            bullet = True
    if first and all(c in BULLET_GLYPHS for c in first) and len(row) > 1:
        bullet = True
        text = text[len(first):].lstrip(" |")
        x0 = row[1]["x0"]
    else:
        m = re.match(rf"^[{re.escape(BULLET_GLYPHS)}]\s*", text)
        if m and len(text) > m.end() + 1 and (text[0] != "-" or text[1:2] == " "):
            bullet, text = True, text[m.end():]
        x0 = row[0]["x0"]
    sizes = [float(w.get("size") or 0) for w in row]
    bold = sum(1 for w in row if re.search(r"bold|black|heavy|semibold", str(w.get("fontname", "")), re.I)) >= max(1, len(row) * 0.6)
    return Line(text=text, page=page, x0=x0, x1=row[-1]["x1"], top=min(w["top"] for w in row),
                bottom=max(w["bottom"] for w in row), size=max(sizes) if sizes else 0.0, bold=bold, bullet=bullet, column=column)


def _page_lines(words: list, width: float, page: int):
    gutter = _gutter(words, width)
    if gutter is None:
        return [l for l in (_line(r, page, 0) for r in _rows(words)) if l], False
    # Read the page in vertical bands separated by full-width rows (rows with a word crossing the gutter): within a
    # band, the wider column (the main one) first, then the other. Full-width rows keep their place between bands.
    rows = _rows(words)
    # A row is full width when a word really crosses the gutter (not a separator glyph touching it). Other words go to
    # the side holding their centre, so nothing straddling the gutter is lost.
    # A separator glyph sitting on the gutter ("|") belongs to neither column and carries no text.
    words = [w for w in words if not (re.fullmatch(r"[|·•/–—-]+", w["text"]) and w["x0"] < gutter + 4 and w["x1"] > gutter - 4)]
    rows = _rows(words)
    full = [r for r in rows if any(w["x0"] < gutter - 3 and w["x1"] > gutter + 3 for w in r)]
    centre = lambda w: (w["x0"] + w["x1"]) / 2          # noqa: E731
    full_ids = {id(w) for r in full for w in r}
    body = [w for w in words if id(w) not in full_ids]
    left_all = [w for w in body if centre(w) < gutter]
    right_all = [w for w in body if centre(w) >= gutter]
    span = lambda ws: (max((w["x1"] for w in ws), default=0) - min((w["x0"] for w in ws), default=0))  # noqa: E731
    main_left = span(left_all) >= span(right_all)
    out: List[Line] = []
    bounds = [r[0]["top"] for r in full] + [float("inf")]
    prev = float("-inf")
    for i, b in enumerate(bounds):
        band = [w for w in body if prev <= w["top"] < b]
        left = [w for w in band if centre(w) < gutter]
        right = [w for w in band if centre(w) >= gutter]
        order = [(left, 1), (right, 2)] if main_left else [(right, 2), (left, 1)]
        for ws, c in order:
            out.extend(l for l in (_line(r, page, c) for r in _rows(ws)) if l)
        if i < len(full):
            l = _line(full[i], page, 0)
            if l:
                out.append(l)
            prev = full[i][0]["top"] + 0.01
    return out, True


# -------------------------------------------------------------------------------------------------------------- DOCX
def _check_zip(data: bytes) -> None:
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        infos = z.infolist()
    except Exception:
        raise DocumentRejected("unreadable", "This Word document couldn't be read. Save it again as .docx and retry.") from None
    names = {i.filename for i in infos}
    if "[Content_Types].xml" not in names or "word/document.xml" not in names:
        raise DocumentRejected("unsupported_format", "Upload a PDF or Word (.docx) resume.")
    if len(infos) > MAX_ZIP_ENTRIES:
        raise DocumentRejected("unsafe_document", "This document has an unusual structure and can't be processed.")
    total = 0
    for i in infos:
        if i.filename.startswith(("/", "\\")) or ".." in i.filename.replace("\\", "/").split("/") or ":" in i.filename:
            raise DocumentRejected("unsafe_document", "This document has an unusual structure and can't be processed.")
        if i.flag_bits & 0x1:
            raise DocumentRejected("encrypted", "This document is password-protected. Remove the password and upload it again.")
        total += i.file_size
        if i.compress_size and i.file_size / max(i.compress_size, 1) > MAX_ZIP_RATIO and i.file_size > 1_000_000:
            raise DocumentRejected("unsafe_document", "This document has an unusual structure and can't be processed.")
    if total > MAX_ZIP_UNCOMPRESSED:
        raise DocumentRejected("unsafe_document", "This document is too large to process.")
    if any(n.lower().endswith("vbaproject.bin") for n in names):
        raise DocumentRejected("unsafe_document", "Macro-enabled documents can't be processed. Save it as a plain .docx.")


def _docx(data: bytes) -> Document:
    _check_zip(data)
    try:
        import docx
        from docx.oxml.ns import qn
        d = docx.Document(io.BytesIO(data))
    except DocumentRejected:
        raise
    except Exception:
        raise DocumentRejected("unreadable", "This Word document couldn't be read. Save it again as .docx and retry.") from None

    lines: List[Line] = []

    def para(p, column=0):
        text = clean_text(p.text or "")
        if not text:
            return
        style = (p.style.name if p.style is not None else "") or ""
        ppr = p._p.pPr
        numbered = ppr is not None and ppr.find(qn("w:numPr")) is not None
        bullet = numbered or bool(re.search(r"list", style, re.I))
        m = re.match(rf"^[{re.escape(BULLET_GLYPHS)}]\s+", text)
        if m:
            bullet, text = True, text[m.end():]
        runs = [r for r in p.runs if r.text.strip()]
        bold = bool(runs) and all(bool(r.bold) or (r.style is not None and r.style.font.bold) for r in runs)
        heading = style.lower().startswith(("heading", "title"))
        sizes = [r.font.size.pt for r in runs if r.font.size is not None]
        lines.append(Line(text=text, bullet=bullet, bold=bold or heading, size=max(sizes) if sizes else (14.0 if heading else 0.0),
                          column=column))

    # Body order: paragraphs and tables as they appear. Layout tables (two-column resumes) are read column by column.
    body = d.element.body
    for child in body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            para(docx.text.paragraph.Paragraph(child, d))
        elif tag == "tbl":
            table = docx.table.Table(child, d)
            ncols = max((len(r.cells) for r in table.rows), default=0)
            for c in range(ncols):
                seen = set()
                for r in table.rows:
                    if c < len(r.cells) and id(r.cells[c]._tc) not in seen:
                        seen.add(id(r.cells[c]._tc))
                        for p in r.cells[c].paragraphs:
                            para(p, column=c + 1 if ncols > 1 else 0)
    return Document("docx", 1, "docx", lines)
