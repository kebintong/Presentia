"""Minimal .xlsx writer — enough for attendance exports, no extra packages.

An .xlsx file is a zip of a few XML parts. Writing them directly keeps the
sidecar's dependencies (and the frozen installer) exactly as they are, and
the output opens in Excel, LibreOffice and Google Sheets.

Supports: several sheets, text and number cells, a bold header row with a
filter, frozen header row/columns, column widths, percent cells and the
Present / Late / Absent colour fills.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from typing import Any
from xml.sax.saxutils import escape

# Cell styles (indexes into cellXfs in _STYLES below).
TEXT = 0
HEADER = 1
NUMBER = 2
PERCENT = 3
CENTER = 4
PRESENT = 5
LATE = 6
ABSENT = 7

_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="2">
<font><sz val="11"/><name val="Calibri"/><family val="2"/></font>
<font><b/><sz val="11"/><name val="Calibri"/><family val="2"/></font>
</fonts>
<fills count="6">
<fill><patternFill patternType="none"/></fill>
<fill><patternFill patternType="gray125"/></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FFE3EAF3"/><bgColor indexed="64"/></patternFill></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FFD1FAE5"/><bgColor indexed="64"/></patternFill></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FFFEF3C7"/><bgColor indexed="64"/></patternFill></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FFFEE2E2"/><bgColor indexed="64"/></patternFill></fill>
</fills>
<borders count="2">
<border><left/><right/><top/><bottom/><diagonal/></border>
<border><left/><right/><top/><bottom style="thin"><color rgb="FF94A3B8"/></bottom><diagonal/></border>
</borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="8">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf>
<xf numFmtId="1" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
<xf numFmtId="9" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment horizontal="center"/></xf>
<xf numFmtId="0" fontId="0" fillId="3" borderId="0" xfId="0" applyFill="1" applyAlignment="1"><alignment horizontal="center"/></xf>
<xf numFmtId="0" fontId="0" fillId="4" borderId="0" xfId="0" applyFill="1" applyAlignment="1"><alignment horizontal="center"/></xf>
<xf numFmtId="0" fontId="0" fillId="5" borderId="0" xfId="0" applyFill="1" applyAlignment="1"><alignment horizontal="center"/></xf>
</cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>"""

# Characters XML 1.0 does not allow; a stray one would make Excel refuse the file.
_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


@dataclass
class Column:
    header: str
    width: float = 12
    style: int = TEXT


@dataclass
class Sheet:
    name: str
    columns: list[Column]
    # A cell is a value, or (value, style) to override the column's style.
    rows: list[list[Any]] = field(default_factory=list)
    freeze_cols: int = 0
    header_height: float | None = None
    autofilter: bool = True


def _col(index: int) -> str:
    """0 → A, 25 → Z, 26 → AA."""
    letters = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def safe_sheet_name(name: str) -> str:
    cleaned = re.sub(r"[\[\]:*?/\\]", " ", name).strip().strip("'") or "Sheet"
    return cleaned[:31]


def _cell(ref: str, value: Any, style: int) -> str:
    if value is None or value == "":
        return f'<c r="{ref}" s="{style}"/>' if style else ""
    if isinstance(value, bool):
        value = int(value)
    if isinstance(value, (int, float)):
        return f'<c r="{ref}" s="{style}"><v>{value}</v></c>'
    text = escape(_ILLEGAL.sub("", str(value)))
    return (
        f'<c r="{ref}" t="inlineStr" s="{style}"><is>'
        f'<t xml:space="preserve">{text}</t></is></c>'
    )


def _sheet_xml(sheet: Sheet) -> str:
    ncols = max(len(sheet.columns), 1)
    nrows = len(sheet.rows) + 1
    last = f"{_col(ncols - 1)}{nrows}"

    if sheet.freeze_cols:
        top_left = f"{_col(sheet.freeze_cols)}2"
        pane = (
            f'<pane xSplit="{sheet.freeze_cols}" ySplit="1" topLeftCell="{top_left}" '
            'activePane="bottomRight" state="frozen"/>'
            '<selection pane="bottomRight"/>'
        )
    else:
        pane = (
            '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
            '<selection pane="bottomLeft"/>'
        )

    cols = "".join(
        f'<col min="{i + 1}" max="{i + 1}" width="{c.width}" customWidth="1"/>'
        for i, c in enumerate(sheet.columns)
    )

    header_attrs = (
        f' ht="{sheet.header_height}" customHeight="1"' if sheet.header_height else ""
    )
    out = [
        f'<row r="1"{header_attrs}>'
        + "".join(_cell(f"{_col(i)}1", c.header, HEADER) for i, c in enumerate(sheet.columns))
        + "</row>"
    ]
    for r, row in enumerate(sheet.rows, start=2):
        cells = []
        for i, value in enumerate(row):
            style = sheet.columns[i].style if i < len(sheet.columns) else TEXT
            if isinstance(value, tuple):
                value, style = value
            cells.append(_cell(f"{_col(i)}{r}", value, style))
        out.append(f'<row r="{r}">' + "".join(cells) + "</row>")

    autofilter = f'<autoFilter ref="A1:{last}"/>' if sheet.autofilter else ""
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<dimension ref="A1:{last}"/>'
        f'<sheetViews><sheetView workbookViewId="0">{pane}</sheetView></sheetViews>'
        '<sheetFormatPr defaultRowHeight="15"/>'
        f"<cols>{cols}</cols>"
        f'<sheetData>{"".join(out)}</sheetData>'
        f"{autofilter}"
        '<pageMargins left="0.5" right="0.5" top="0.75" bottom="0.75" header="0.3" footer="0.3"/>'
        "</worksheet>"
    )


def build_workbook(sheets: list[Sheet]) -> bytes:
    names: list[str] = []
    for s in sheets:
        base = safe_sheet_name(s.name)
        name, n = base, 2
        while name.lower() in (x.lower() for x in names):
            suffix = f" ({n})"
            name = base[: 31 - len(suffix)] + suffix
            n += 1
        names.append(name)

    def q(name: str) -> str:
        return "'" + name.replace("'", "''") + "'"

    defined = "".join(
        f'<definedName name="_xlnm._FilterDatabase" localSheetId="{i}" hidden="1">'
        f"{escape(q(names[i]))}!$A$1:${_col(max(len(s.columns), 1) - 1)}${len(s.rows) + 1}"
        "</definedName>"
        for i, s in enumerate(sheets)
        if s.autofilter
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        "<sheets>"
        + "".join(
            f'<sheet name="{escape(n, {chr(34): "&quot;"})}" sheetId="{i + 1}" r:id="rId{i + 1}"/>'
            for i, n in enumerate(names)
        )
        + "</sheets>"
        + (f"<definedNames>{defined}</definedNames>" if defined else "")
        + "</workbook>"
    )
    wb_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(
            f'<Relationship Id="rId{i + 1}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{i + 1}.xml"/>'
            for i in range(len(sheets))
        )
        + f'<Relationship Id="rId{len(sheets) + 1}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>'
        "</Relationships>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        + "".join(
            f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            for i in range(len(sheets))
        )
        + "</Types>"
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/>'
        "</Relationships>"
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", root_rels)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        z.writestr("xl/styles.xml", _STYLES)
        for i, s in enumerate(sheets):
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml", _sheet_xml(s))
    return buf.getvalue()
