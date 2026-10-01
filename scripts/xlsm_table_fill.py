#!/usr/bin/env python3
from __future__ import annotations

import shutil
import zipfile
from dataclasses import dataclass
import posixpath
from pathlib import Path
from typing import Any
import re
from xml.etree import ElementTree as ET


_NS = {
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "ss": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}

# Preserve common OOXML prefixes when re-serializing XML.
ET.register_namespace("", _NS["ss"])
ET.register_namespace("r", _NS["r"])
ET.register_namespace("mc", "http://schemas.openxmlformats.org/markup-compatibility/2006")
ET.register_namespace("x14ac", "http://schemas.microsoft.com/office/spreadsheetml/2009/9/ac")
ET.register_namespace("xr", "http://schemas.microsoft.com/office/spreadsheetml/2014/revision")
ET.register_namespace("xr2", "http://schemas.microsoft.com/office/spreadsheetml/2015/revision2")
ET.register_namespace("xr3", "http://schemas.microsoft.com/office/spreadsheetml/2016/revision3")


def _q(tag: str) -> str:
    if ":" not in tag:
        return tag
    prefix, local = tag.split(":", 1)
    return f"{{{_NS[prefix]}}}{local}"


def _col_to_idx(col: str) -> int:
    idx = 0
    for ch in col:
        if not ("A" <= ch <= "Z"):
            raise ValueError(f"Bad column: {col}")
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return idx


def _idx_to_col(idx: int) -> str:
    if idx <= 0:
        raise ValueError("idx must be > 0")
    out = []
    while idx:
        idx, rem = divmod(idx - 1, 26)
        out.append(chr(rem + ord("A")))
    return "".join(reversed(out))


def _split_cell_ref(ref: str) -> tuple[str, int]:
    ref = ref.strip().upper()
    col = ""
    row = ""
    for ch in ref:
        if "A" <= ch <= "Z":
            col += ch
        elif "0" <= ch <= "9":
            row += ch
        else:
            raise ValueError(f"Bad cell ref: {ref}")
    return col, int(row)


def _parse_range(ref: str) -> tuple[int, int, int, int]:
    # returns (min_col_idx, min_row, max_col_idx, max_row)
    ref = ref.strip().upper()
    if ":" in ref:
        a, b = ref.split(":", 1)
    else:
        a = b = ref
    c1, r1 = _split_cell_ref(a)
    c2, r2 = _split_cell_ref(b)
    min_col = min(_col_to_idx(c1), _col_to_idx(c2))
    max_col = max(_col_to_idx(c1), _col_to_idx(c2))
    min_row = min(r1, r2)
    max_row = max(r1, r2)
    return min_col, min_row, max_col, max_row


def _iter_range_cells(ref: str) -> list[str]:
    min_col, min_row, max_col, max_row = _parse_range(ref)
    cells: list[str] = []
    for r in range(min_row, max_row + 1):
        for c in range(min_col, max_col + 1):
            cells.append(f"{_idx_to_col(c)}{r}")
    return cells


def _read_xml(z: zipfile.ZipFile, name: str) -> ET.Element:
    data = z.read(name)
    return ET.fromstring(data)


def _write_xml_bytes(root: ET.Element) -> bytes:
    # If the sheet uses MC (markup-compatibility) Ignorable prefixes, Excel expects
    # those prefixes to be declared in-scope. ElementTree may drop unused namespace
    # declarations when serializing, so we force-declare the common ones.
    mc_ignorable = root.get("{http://schemas.openxmlformats.org/markup-compatibility/2006}Ignorable")
    if mc_ignorable:
        prefixes = set(mc_ignorable.split())
        if "xr2" in prefixes and "xmlns:xr2" not in root.attrib:
            root.set("xmlns:xr2", "http://schemas.microsoft.com/office/spreadsheetml/2015/revision2")
        if "xr3" in prefixes and "xmlns:xr3" not in root.attrib:
            root.set("xmlns:xr3", "http://schemas.microsoft.com/office/spreadsheetml/2016/revision3")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _load_shared_strings(z: zipfile.ZipFile) -> list[str]:
    try:
        root = _read_xml(z, "xl/sharedStrings.xml")
    except KeyError:
        return []
    strings: list[str] = []
    for si in root.findall(_q("ss:si")):
        # gather all text nodes under <si>
        texts = []
        for t in si.findall(".//" + _q("ss:t")):
            texts.append(t.text or "")
        strings.append("".join(texts))
    return strings


def _set_shared_strings(root: ET.Element, strings: list[str]) -> None:
    # rewrite <sst> content with <si><t>...</t></si>
    for child in list(root):
        root.remove(child)
    root.set("count", str(len(strings)))
    root.set("uniqueCount", str(len(strings)))
    for s in strings:
        si = ET.SubElement(root, _q("ss:si"))
        t = ET.SubElement(si, _q("ss:t"))
        # preserve leading/trailing spaces
        if s and (s[0].isspace() or s[-1].isspace()):
            t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        t.text = s


def _get_cell_value(cell: ET.Element, shared_strings: list[str]) -> Any:
    t = cell.get("t")
    if t == "inlineStr":
        is_el = cell.find(_q("ss:is"))
        if is_el is None:
            return None
        texts: list[str] = []
        for tt in is_el.findall(".//" + _q("ss:t")):
            texts.append(tt.text or "")
        return "".join(texts)
    v = cell.find(_q("ss:v"))
    if v is None or v.text is None:
        # Some worksheets may store strings as inlineStr without `t="inlineStr"` (rare but seen in the wild).
        is_el = cell.find(_q("ss:is"))
        if is_el is not None:
            texts: list[str] = []
            for tt in is_el.findall(".//" + _q("ss:t")):
                texts.append(tt.text or "")
            return "".join(texts)
        return None
    raw = v.text
    if t == "s":
        try:
            return shared_strings[int(raw)]
        except Exception:
            return None
    if t == "b":
        return raw == "1"
    if t in (None, "n"):
        try:
            if "." in raw:
                return float(raw)
            return int(raw)
        except Exception:
            return raw
    return raw


def _ensure_row(sheet_data: ET.Element, row_idx: int) -> ET.Element:
    for row in sheet_data.findall(_q("ss:row")):
        if int(row.get("r", "0")) == row_idx:
            return row
    row = ET.Element(_q("ss:row"), {"r": str(row_idx)})
    # Insert keeping row order
    inserted = False
    for i, existing in enumerate(list(sheet_data)):
        if existing.tag != _q("ss:row"):
            continue
        if int(existing.get("r", "0")) > row_idx:
            sheet_data.insert(i, row)
            inserted = True
            break
    if not inserted:
        sheet_data.append(row)
    return row


def _ensure_cell(row: ET.Element, cell_ref: str) -> ET.Element:
    for c in row.findall(_q("ss:c")):
        if (c.get("r") or "").upper() == cell_ref.upper():
            return c
    cell = ET.Element(_q("ss:c"), {"r": cell_ref.upper()})
    # Insert keeping column order
    target_col, _ = _split_cell_ref(cell_ref)
    target_idx = _col_to_idx(target_col)
    inserted = False
    for i, existing in enumerate(list(row)):
        if existing.tag != _q("ss:c"):
            continue
        ref = (existing.get("r") or "").upper()
        if not ref:
            continue
        col, _ = _split_cell_ref(ref)
        if _col_to_idx(col) > target_idx:
            row.insert(i, cell)
            inserted = True
            break
    if not inserted:
        row.append(cell)
    return cell


def _set_cell_value(
    *,
    sheet_data: ET.Element,
    cell_ref: str,
    value: Any,
    shared_strings: list[str],
    shared_strings_root: ET.Element | None,
    shared_string_index: dict[str, int],
) -> None:
    col, row_idx = _split_cell_ref(cell_ref)
    _ = col
    row = _ensure_row(sheet_data, row_idx)
    cell = _ensure_cell(row, cell_ref)

    # Do not overwrite formulas
    if cell.find(_q("ss:f")) is not None:
        return

    # Clear existing <v> and type
    for v in cell.findall(_q("ss:v")):
        cell.remove(v)
    for is_el in cell.findall(_q("ss:is")):
        cell.remove(is_el)
    if "t" in cell.attrib:
        del cell.attrib["t"]

    if value is None or value == "":
        return

    v = ET.SubElement(cell, _q("ss:v"))
    if isinstance(value, bool):
        cell.set("t", "b")
        v.text = "1" if value else "0"
        return
    if isinstance(value, (int, float)):
        v.text = str(value)
        return

    s = str(value)
    # Write strings as inlineStr to avoid relying on sharedStrings.xml,
    # which may be absent depending on how the xlsm was edited/saved.
    cell.set("t", "inlineStr")
    cell.remove(v)
    is_el = ET.SubElement(cell, _q("ss:is"))
    t_el = ET.SubElement(is_el, _q("ss:t"))
    if s and (s[0].isspace() or s[-1].isspace()):
        t_el.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    t_el.text = s


@dataclass(frozen=True)
class ExcelTable:
    display_name: str
    ref: str
    sheet_xml: str
    sheet_rels_xml: str | None
    table_xml: str


def _read_relationships(root: ET.Element) -> dict[str, str]:
    out: dict[str, str] = {}
    for rel in root.findall(_q("rel:Relationship")):
        rid = rel.get("Id")
        target = rel.get("Target")
        if rid and target:
            out[rid] = target
    return out


def _normalize_part(base: str, target: str) -> str:
    # base is e.g. "xl/worksheets/_rels/sheet1.xml.rels"
    # target is e.g. "../tables/table1.xml"
    base_dir = posixpath.dirname(base)
    joined = posixpath.normpath(posixpath.join(base_dir, target))
    return joined.lstrip("/")


def _list_tables(z: zipfile.ZipFile) -> list[str]:
    return sorted([n for n in z.namelist() if n.startswith("xl/tables/table") and n.endswith(".xml")])


def _get_sheet_xml_by_name(z: zipfile.ZipFile, sheet_name: str) -> str | None:
    wb = _read_xml(z, "xl/workbook.xml")
    wb_rels = _read_xml(z, "xl/_rels/workbook.xml.rels")
    wb_rel_map = _read_relationships(wb_rels)
    for s in wb.findall(".//" + _q("ss:sheet")):
        name = s.get("name") or ""
        if name != sheet_name:
            continue
        rid = s.get(_q("r:id"))
        if not rid:
            return None
        target = wb_rel_map.get(rid)
        if not target:
            return None
        return _normalize_part("xl/workbook.xml", target)
    return None


def _build_cell_map(sheet_root: ET.Element) -> dict[str, ET.Element]:
    sheet_data = sheet_root.find(".//" + _q("ss:sheetData"))
    if sheet_data is None:
        return {}
    cell_map: dict[str, ET.Element] = {}
    for row in sheet_data.findall(_q("ss:row")):
        for c in row.findall(_q("ss:c")):
            r = c.get("r")
            if r:
                cell_map[r.upper()] = c
    return cell_map


def _scan_header_row(
    *,
    sheet_root: ET.Element,
    shared_strings: list[str],
    max_rows: int = 200,
    max_cols: int = 60,
) -> tuple[int | None, list[str]]:
    cell_map = _build_cell_map(sheet_root)

    def cell_value(col_idx: int, row_idx: int) -> str:
        ref = f"{_idx_to_col(col_idx)}{row_idx}"
        cell = cell_map.get(ref)
        v = _get_cell_value(cell, shared_strings) if cell is not None else None
        return "" if v is None else str(v)

    for r in range(1, max_rows + 1):
        headers = [cell_value(c, r) for c in range(1, max_cols + 1)]
        seq_idx = _pick_key(headers, ["sequence", "seq", "name", "序列", "序列名"])
        qp_idx = _pick_key(headers, ["qp"])
        if seq_idx is not None and qp_idx is not None:
            return r, headers
    return None, []


def _read_sheet_rows_by_header(
    *,
    sheet_root: ET.Element,
    shared_strings: list[str],
    header_row: int,
    headers: list[str],
    max_rows_after: int = 5000,
    max_cols: int = 60,
) -> tuple[int, int, list[list[Any]]]:
    """
    Returns (seq_col_idx, qp_col_idx, data_rows) where data_rows is a list of row-value lists (1-based col).
    """
    seq_idx0 = _pick_key(headers, ["sequence", "seq", "name", "序列", "序列名"])
    qp_idx0 = _pick_key(headers, ["qp"])
    if seq_idx0 is None or qp_idx0 is None:
        return -1, -1, []
    seq_col = seq_idx0 + 1
    qp_col = qp_idx0 + 1

    cell_map = _build_cell_map(sheet_root)

    def cell_value(col_idx: int, row_idx: int) -> Any:
        ref = f"{_idx_to_col(col_idx)}{row_idx}"
        cell = cell_map.get(ref)
        return _get_cell_value(cell, shared_strings) if cell is not None else None

    rows: list[list[Any]] = []
    empty_streak = 0
    for r in range(header_row + 1, header_row + 1 + max_rows_after):
        row_vals = [cell_value(c, r) for c in range(1, max_cols + 1)]
        seq = row_vals[seq_col - 1]
        qp = row_vals[qp_col - 1]
        if (seq in (None, "")) and (qp in (None, "")):
            empty_streak += 1
            if empty_streak >= 5:
                break
            continue
        empty_streak = 0
        rows.append(row_vals)
    return seq_col, qp_col, rows


def find_table(z: zipfile.ZipFile, want_name: str) -> ExcelTable | None:
    want = want_name.strip()
    if not want:
        return None

    # Map workbook rId -> sheet xml
    wb = _read_xml(z, "xl/workbook.xml")
    wb_rels = _read_xml(z, "xl/_rels/workbook.xml.rels")
    wb_rel_map = _read_relationships(wb_rels)

    sheets: list[tuple[str, str]] = []
    for s in wb.findall(".//" + _q("ss:sheet")):
        rid = s.get(_q("r:id"))
        if not rid:
            continue
        target = wb_rel_map.get(rid)
        if not target:
            continue
        sheet_xml = _normalize_part("xl/workbook.xml", target)
        sheets.append((s.get("name") or "", sheet_xml))

    # For each sheet, map tablePart rId -> table xml
    for _, sheet_xml in sheets:
        sheet_root = _read_xml(z, sheet_xml)
        table_parts = sheet_root.findall(".//" + _q("ss:tableParts") + "/" + _q("ss:tablePart"))
        if not table_parts:
            continue

        sheet_rels_xml = str(Path(sheet_xml).parent / "_rels" / (Path(sheet_xml).name + ".rels"))
        rel_map: dict[str, str] = {}
        try:
            rel_root = _read_xml(z, sheet_rels_xml)
            rel_map = _read_relationships(rel_root)
        except KeyError:
            sheet_rels_xml = None

        for tp in table_parts:
            rid = tp.get(_q("r:id"))
            if not rid or rid not in rel_map:
                continue
            table_target = rel_map[rid]
            table_xml = _normalize_part(sheet_rels_xml or sheet_xml, table_target)
            try:
                table_root = _read_xml(z, table_xml)
            except KeyError:
                continue
            disp = table_root.get("displayName") or table_root.get("name") or ""
            if disp == want:
                ref = table_root.get("ref") or ""
                return ExcelTable(
                    display_name=disp,
                    ref=ref,
                    sheet_xml=sheet_xml,
                    sheet_rels_xml=sheet_rels_xml,
                    table_xml=table_xml,
                )
    return None


def _header_syn_key(text: str) -> str:
    t = (text or "").strip().lower()
    t = t.replace("_", " ").replace("-", " ")
    t = re.sub(r"\s+", " ", t)
    return t


def _pick_key(headers: list[str], candidates: list[str]) -> int | None:
    norm = [_header_syn_key(h) for h in headers]
    for cand in candidates:
        c = _header_syn_key(cand)
        for i, h in enumerate(norm):
            if h == c or c in h:
                return i
    return None


def _load_table_rows(
    *,
    sheet_root: ET.Element,
    table_ref: str,
    shared_strings: list[str],
) -> tuple[list[str], list[tuple[int, list[Any]]]]:
    # returns (headers, rows) where rows contain absolute row index
    sheet_data = sheet_root.find(".//" + _q("ss:sheetData"))
    if sheet_data is None:
        return [], []

    cell_map: dict[str, ET.Element] = {}
    for row in sheet_data.findall(_q("ss:row")):
        for c in row.findall(_q("ss:c")):
            r = c.get("r")
            if r:
                cell_map[r.upper()] = c

    min_col, min_row, max_col, max_row = _parse_range(table_ref)
    headers: list[str] = []
    for c in range(min_col, max_col + 1):
        ref = f"{_idx_to_col(c)}{min_row}"
        v = _get_cell_value(cell_map.get(ref), shared_strings) if ref in cell_map else None
        headers.append("" if v is None else str(v))

    rows: list[list[Any]] = []
    for r in range(min_row + 1, max_row + 1):
        row_values: list[Any] = []
        for c in range(min_col, max_col + 1):
            ref = f"{_idx_to_col(c)}{r}"
            v = _get_cell_value(cell_map.get(ref), shared_strings) if ref in cell_map else None
            row_values.append(v)
        rows.append((r, row_values))
    return headers, rows


def fill_reference_to_test(
    *,
    template_xlsm: Path,
    output_xlsm: Path,
    results: list[dict[str, Any]],
    reference_table_name: str = "Reference",
    test_table_name: str = "Test",
) -> tuple[bool, str]:
    """
    Copies template_xlsm to output_xlsm and fills Test table based on Reference table layout.
    Best-effort: if tables/columns are missing, returns (False, reason) or skips fields.
    """
    if not template_xlsm.is_file():
        return False, f"template not found: {template_xlsm}"
    output_xlsm.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(template_xlsm, output_xlsm)

    # Build lookup from encoder run
    lookup: dict[tuple[str, int], dict[str, Any]] = {}
    for r in results:
        seq = r.get("sequence")
        qp = r.get("qp")
        try:
            if seq is None or qp is None:
                continue
            lookup[(str(seq), int(qp))] = r
        except Exception:
            continue

    tmp = output_xlsm.with_suffix(".tmp.xlsm")

    with zipfile.ZipFile(output_xlsm, "r") as zin:
        names = set(zin.namelist())
        shared_strings = _load_shared_strings(zin)
        shared_string_index = {s: i for i, s in enumerate(shared_strings)}

        ref_table = find_table(zin, reference_table_name)
        test_table = find_table(zin, test_table_name)
        use_tables = ref_table is not None and test_table is not None

        if use_tables:
            ref_sheet_root = _read_xml(zin, ref_table.sheet_xml)  # type: ignore[union-attr]
            test_sheet_root = _read_xml(zin, test_table.sheet_xml)  # type: ignore[union-attr]
            ref_headers, ref_rows = _load_table_rows(
                sheet_root=ref_sheet_root, table_ref=ref_table.ref, shared_strings=shared_strings  # type: ignore[union-attr]
            )
            test_headers, test_rows = _load_table_rows(
                sheet_root=test_sheet_root, table_ref=test_table.ref, shared_strings=shared_strings  # type: ignore[union-attr]
            )
            ref_row_items: list[tuple[int, list[Any]]] = ref_rows
        else:
            ref_sheet_xml = _get_sheet_xml_by_name(zin, reference_table_name)
            test_sheet_xml = _get_sheet_xml_by_name(zin, test_table_name)
            if ref_sheet_xml is None or test_sheet_xml is None:
                return False, "neither Reference/Test tables nor sheets were found"
            ref_sheet_root = _read_xml(zin, ref_sheet_xml)
            test_sheet_root = _read_xml(zin, test_sheet_xml)

            ref_header_row, ref_headers = _scan_header_row(sheet_root=ref_sheet_root, shared_strings=shared_strings)
            test_header_row, test_headers = _scan_header_row(sheet_root=test_sheet_root, shared_strings=shared_strings)
            if ref_header_row is None or test_header_row is None:
                return False, "cannot find header rows containing sequence/qp in Reference/Test sheets"

            _, _, ref_rows = _read_sheet_rows_by_header(
                sheet_root=ref_sheet_root, shared_strings=shared_strings, header_row=ref_header_row, headers=ref_headers
            )
            _, _, test_rows = _read_sheet_rows_by_header(
                sheet_root=test_sheet_root, shared_strings=shared_strings, header_row=test_header_row, headers=test_headers
            )
            ref_row_items = [(0, r) for r in ref_rows]

        ref_seq_idx = _pick_key(ref_headers, ["sequence", "seq", "name", "序列", "序列名"])
        ref_qp_idx = _pick_key(ref_headers, ["qp"])
        test_seq_idx = _pick_key(test_headers, ["sequence", "seq", "name", "序列", "序列名"])
        test_qp_idx = _pick_key(test_headers, ["qp"])
        if None in {ref_seq_idx, ref_qp_idx, test_seq_idx, test_qp_idx}:
            return False, "cannot locate sequence/qp columns in Reference/Test headers"

        def map_header_to_key(h: str) -> str | None:
            key = _header_syn_key(h)
            if not key:
                return None
            if "bitstream" in key or key in {"bin", "bitstream file", "码流"}:
                return "bitstream"
            if key in {"log", "encode log", "编码日志"} or "encode log" in key:
                return "log"
            if key in {"status", "result", "pass/fail", "pass fail", "结果"}:
                return "status"
            if key in {"returncode", "rc"}:
                return "returncode"
            if key in {"seconds", "time", "runtime", "耗时"} or "sec" in key:
                return "seconds"
            if "decode" in key and ("status" in key or "result" in key):
                return "decode_status"
            if "md5" in key or "hash" in key:
                return "decode_status"
            if "decode" in key and ("time" in key or "seconds" in key):
                return "decode_seconds"
            if "decode" in key and ("log" in key):
                return "decode_log"
            return None

        test_col_keys: list[str | None] = [map_header_to_key(h) for h in test_headers]

        sheet_data = test_sheet_root.find(".//" + _q("ss:sheetData"))
        if sheet_data is None:
            return False, "Test sheet has no sheetData"

        shared_strings_root: ET.Element | None = None
        if "xl/sharedStrings.xml" in names:
            shared_strings_root = _read_xml(zin, "xl/sharedStrings.xml")

        # Build row index mapping for Test by (sequence, qp) -> absolute row number.
        test_row_map: dict[tuple[str, int], int] = {}
        if use_tables:
            min_col, _, _, _ = _parse_range(test_table.ref)  # type: ignore[union-attr]
            for row_idx, row_vals in test_rows:
                seq = row_vals[test_seq_idx] if test_seq_idx is not None and test_seq_idx < len(row_vals) else None
                qp = row_vals[test_qp_idx] if test_qp_idx is not None and test_qp_idx < len(row_vals) else None
                if seq in (None, "") or qp in (None, ""):
                    continue
                try:
                    test_row_map[(str(seq), int(qp))] = int(row_idx)
                except Exception:
                    continue
        else:
            # In sheet-mode, we already parsed rows but not their row numbers; approximate by scanning again via seq/qp columns.
            # We'll build mapping by locating matching cells in the sheet data.
            cell_map = _build_cell_map(test_sheet_root)
            # Find header row again to determine row index and columns
            header_row, _ = _scan_header_row(sheet_root=test_sheet_root, shared_strings=shared_strings)
            if header_row is None:
                return False, "cannot rebuild Test header row"
            seq_col = (test_seq_idx or 0) + 1
            qp_col = (test_qp_idx or 0) + 1
            for r in range(header_row + 1, header_row + 2000):
                seq_cell = cell_map.get(f"{_idx_to_col(seq_col)}{r}")
                qp_cell = cell_map.get(f"{_idx_to_col(qp_col)}{r}")
                seq_v = _get_cell_value(seq_cell, shared_strings) if seq_cell is not None else None
                qp_v = _get_cell_value(qp_cell, shared_strings) if qp_cell is not None else None
                if seq_v in (None, "") and qp_v in (None, ""):
                    continue
                try:
                    test_row_map[(str(seq_v), int(qp_v))] = r
                except Exception:
                    continue

        updates: list[tuple[str, Any]] = []
        if use_tables:
            min_col, _, _, _ = _parse_range(test_table.ref)  # type: ignore[union-attr]
        else:
            min_col = 1

        for _, ref_row in ref_row_items:
            seq = ref_row[ref_seq_idx]  # type: ignore[index]
            qp = ref_row[ref_qp_idx]  # type: ignore[index]
            if seq in (None, "") or qp in (None, ""):
                continue
            try:
                key = (str(seq), int(qp))
            except Exception:
                continue
            result = lookup.get(key)
            if result is None:
                continue
            target_row_idx = test_row_map.get(key)
            if target_row_idx is None:
                continue

            updates.append((f"{_idx_to_col(min_col + test_seq_idx)}{target_row_idx}", str(seq)))  # type: ignore[operator]
            updates.append((f"{_idx_to_col(min_col + test_qp_idx)}{target_row_idx}", int(qp)))  # type: ignore[operator]
            for j, k in enumerate(test_col_keys):
                if k is None or j in {test_seq_idx, test_qp_idx}:  # type: ignore[operator]
                    continue
                value = result.get(k)
                if value in (None, ""):
                    continue
                updates.append((f"{_idx_to_col(min_col + j)}{target_row_idx}", value))

        for cell_ref, value in updates:
            _set_cell_value(
                sheet_data=sheet_data,
                cell_ref=cell_ref,
                value=value,
                shared_strings=shared_strings,
                shared_strings_root=shared_strings_root,
                shared_string_index=shared_string_index,
            )

        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                name = item.filename
                if use_tables and name == test_table.sheet_xml:
                    zout.writestr(name, _write_xml_bytes(test_sheet_root))
                elif (not use_tables) and name == test_sheet_xml:
                    zout.writestr(name, _write_xml_bytes(test_sheet_root))
                elif name == "xl/sharedStrings.xml" and shared_strings_root is not None:
                    zout.writestr(name, _write_xml_bytes(shared_strings_root))
                else:
                    zout.writestr(item, zin.read(name))

    tmp.replace(output_xlsm)
    return True, f"filled {test_table_name} from {reference_table_name}"


def fill_jvet_hhi_test_sheet(
    *,
    template_xlsm: Path,
    output_xlsm: Path,
    results: list[dict[str, Any]],
    condition_tag: str,
    reference_sheet: str = "Reference",
    test_sheet: str = "Test",
    clear_test: bool = True,
) -> tuple[bool, str]:
    """
    Tailored for `script/JVET-hhi.xlsm`:
    - Uses `Test` sheet column A keys like `<Sequence>.Q<qp>.ecm.<tag>` to locate rows.
    - Fills columns based on the header row containing: Sequence, kbps, Ypsnr, Upsnr, Vpsnr, EncT[s], DecT[s], EncVmPeak[KB], DecVmPeak[KB], ErrorInfo, Time.
    Clear old Test measurements first; Reference and formulas outside raw-data cells stay intact.
    For multi-condition sequential writes, set clear_test=False after the first call.
    """
    if not template_xlsm.is_file():
        return False, f"template not found: {template_xlsm}"
    if template_xlsm.resolve() == output_xlsm.resolve():
        return False, "template and output must differ"
    output_xlsm.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(template_xlsm, output_xlsm)

    # Lookup by (sequence, qp)
    lookup: dict[tuple[str, int], dict[str, Any]] = {}
    for r in results:
        seq = r.get("sequence")
        qp = r.get("qp")
        try:
            if seq is None or qp is None:
                continue
            lookup[(str(seq), int(qp))] = r
        except Exception:
            continue

    tmp = output_xlsm.with_suffix(".tmp.xlsm")
    with zipfile.ZipFile(output_xlsm, "r") as zin:
        names = set(zin.namelist())
        shared_strings = _load_shared_strings(zin)
        shared_string_index = {s: i for i, s in enumerate(shared_strings)}

        ref_xml = _get_sheet_xml_by_name(zin, reference_sheet)
        test_xml = _get_sheet_xml_by_name(zin, test_sheet)
        if ref_xml is None:
            return False, f"sheet not found: {reference_sheet}"
        if test_xml is None:
            return False, f"sheet not found: {test_sheet}"

        test_root = _read_xml(zin, test_xml)
        test_sheet_data = test_root.find(".//" + _q("ss:sheetData"))
        if test_sheet_data is None:
            return False, "Test sheet has no sheetData"

        # Find header row. The sheet may have been edited (headers moved/renamed),
        # so locate a row that contains both "Sequence" and "kbps" (best-effort).
        cell_map = _build_cell_map(test_root)
        header_row: int | None = None

        def row_texts(row_idx: int, max_cols: int = 30) -> list[str]:
            out: list[str] = []
            for c in range(1, max_cols + 1):
                ref = f"{_idx_to_col(c)}{row_idx}"
                cell = cell_map.get(ref)
                v = _get_cell_value(cell, shared_strings) if cell is not None else None
                out.append("" if v is None else str(v).strip())
            return out

        for r in range(1, 401):
            texts = row_texts(r, 30)
            norm = {_header_syn_key(t) for t in texts if t}
            if ({"sequence", "序列", "序列名"} & norm) and ("kbps" in norm):
                header_row = r
                break

        if header_row is None:
            # Fallback to the old, stricter heuristic.
            for ref, cell in cell_map.items():
                v = _get_cell_value(cell, shared_strings)
                if isinstance(v, str) and v.strip() == "Sequence" and ref.startswith("A"):
                    try:
                        header_row = int("".join([c for c in ref if c.isdigit()]))
                    except Exception:
                        header_row = None
                    break

        if header_row is None:
            return False, "cannot locate header row (need a row containing 'Sequence' and 'kbps')"

        # Read headers in that row across first 30 cols to map normalized-name->col_idx (1-based).
        headers: dict[str, int] = {}
        for c in range(1, 31):
            ref = f"{_idx_to_col(c)}{header_row}"
            cell = cell_map.get(ref)
            v = _get_cell_value(cell, shared_strings) if cell is not None else None
            if isinstance(v, str) and v.strip():
                headers[_header_syn_key(v.strip())] = c

        def col(name: str) -> int | None:
            return headers.get(_header_syn_key(name))

        col_kbps = col("kbps")
        col_ypsnr = col("Ypsnr")
        col_upsnr = col("Upsnr")
        col_vpsnr = col("Vpsnr")
        col_enct = col("EncT[s]")
        col_dect = col("DecT[s]")
        col_encmem = col("EncVmPeak[KB]")
        col_decmem = col("DecVmPeak[KB]")
        col_error = col("ErrorInfo")
        col_time = col("Time")

        # Build row map from column A values.
        row_by_key: dict[str, int] = {}
        for ref, cell in cell_map.items():
            if not re.fullmatch(r"A\d+", ref):
                continue
            try:
                row_idx = int("".join([c for c in ref if c.isdigit()]))
            except Exception:
                continue
            if row_idx == header_row:
                continue
            v = _get_cell_value(cell, shared_strings)
            if isinstance(v, str) and v.strip():
                row_by_key[v.strip()] = row_idx

        if clear_test:
            raw_columns = [col_kbps, col_ypsnr, col_upsnr, col_vpsnr, col_enct, col_dect,
                           col_encmem, col_decmem, col_error, col_time]
            for key, row_idx in row_by_key.items():
                if not re.fullmatch(r".+\.Q\d+\.ecm\.[^.]+", key):
                    continue
                for cidx in raw_columns:
                    if cidx is None:
                        continue
                    cell = cell_map.get(f"{_idx_to_col(cidx)}{row_idx}")
                    if cell is not None:
                        for child in list(cell):
                            cell.remove(child)
                        cell.attrib.pop("t", None)

        shared_strings_root: ET.Element | None = None
        if "xl/sharedStrings.xml" in names:
            shared_strings_root = _read_xml(zin, "xl/sharedStrings.xml")

        written = 0
        skipped = 0
        for (seq, qp), r in lookup.items():
            key = f"{seq}.Q{int(qp)}.ecm.{condition_tag}"
            row_idx = row_by_key.get(key)
            if row_idx is None:
                skipped += 1
                continue
            if r.get("error_info") != "pass":
                r = {"error_info": r.get("error_info", "failed"), "time": r.get("time")}

            def put(cidx: int | None, value: Any) -> None:
                nonlocal written
                if cidx is None:
                    return
                if value in (None, ""):
                    return
                cell_ref = f"{_idx_to_col(cidx)}{row_idx}"
                _set_cell_value(
                    sheet_data=test_sheet_data,
                    cell_ref=cell_ref,
                    value=value,
                    shared_strings=shared_strings,
                    shared_strings_root=shared_strings_root,
                    shared_string_index=shared_string_index,
                )
                written += 1

            # Do not overwrite column A; it's the key.
            put(col_kbps, r.get("kbps"))
            put(col_ypsnr, r.get("ypsnr"))
            put(col_upsnr, r.get("upsnr"))
            put(col_vpsnr, r.get("vpsnr"))
            put(col_enct, r.get("seconds"))
            put(col_dect, r.get("decode_seconds"))
            put(col_encmem, r.get("enc_vmpeak_kb"))
            put(col_decmem, r.get("dec_vmpeak_kb"))
            put(col_error, r.get("error_info"))
            put(col_time, r.get("time"))

        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                name = item.filename
                if name == test_xml:
                    zout.writestr(name, _write_xml_bytes(test_root))
                elif name == "xl/sharedStrings.xml" and shared_strings_root is not None:
                    zout.writestr(name, _write_xml_bytes(shared_strings_root))
                else:
                    zout.writestr(item, zin.read(name))

    tmp.replace(output_xlsm)
    return True, f"filled Test ({written} cells), skipped {skipped} rows not found"


if __name__ == "__main__":
    raise SystemExit("This module is intended to be imported.")
