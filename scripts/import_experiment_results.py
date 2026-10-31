#!/usr/bin/env python3
"""Import reported RD points into a copy of a headerless HHI Test worksheet.

Only OOXML is read or edited; no Excel/VBA or codec process is executed.
read_csv() and read_raw_sheet() return records, including empty task slots.
Layout labels (for example ecm/jvet10) are retained but excluded from matching;
the configuration tag is matched exactly, without aliases or case folding.
"""
from __future__ import annotations

import argparse
import copy
import csv
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any
import zipfile
from xml.etree import ElementTree as ET

import xlsm_table_fill as xml


CSV_COLUMNS = (
    "Sequence", "kbps", "Ypsnr", "Upsnr", "Vpsnr", "EncT[s]", "DecT[s]",
    "EncVmPeak[KB]", "DecVmPeak[KB]", "ErrorInfo", "Time",
)
DATA_FIELDS = (
    "kbps", "ypsnr", "upsnr", "vpsnr", "seconds", "decode_seconds",
    "enc_vmpeak_kb", "dec_vmpeak_kb", "error_info", "time",
)
_KEY = re.compile(r"(.+)\.Q([0-9]+)\.([^.]+)\.([^.]+)")
_PREFIX = rb"(?:[A-Za-z_][\w.-]*:)?"


def _element(local: str) -> re.Pattern[bytes]:
    name = local.encode("ascii")
    return re.compile(
        rb"<(?P<prefix>" + _PREFIX + rb")" + name
        + rb"\b(?P<attrs>[^>]*?)(?:\s*/>|>(?P<body>.*?)</(?P=prefix)"
        + name + rb"\s*>)", re.DOTALL,
    )


_CELL = _element("c")
_ROW = _element("row")


def parse_key(value: str) -> tuple[str, int, str]:
    """Return (sequence, QP, exact configuration tag), ignoring only layout label."""
    match = _KEY.fullmatch(value.strip())
    if match is None:
        raise ValueError(f"invalid task key: {value!r}")
    sequence, qp, _layout, tag = match.groups()
    return sequence, int(qp), tag


def _record(key: str, values: list[Any], row: int) -> dict[str, Any]:
    sequence, qp, tag = parse_key(key)
    result = dict(zip(DATA_FIELDS, values))
    result.update(seq=sequence, sequence=sequence, qp=qp, tag=tag, key=key,
                  layout_marker=key.rsplit(".", 2)[1], row=row)
    result["measured"] = any(result.get(field) is not None for field in DATA_FIELDS[:8])
    result["reported"] = any(result.get(field) is not None for field in DATA_FIELDS)
    return result


def read_csv(path: str | Path) -> list[dict[str, Any]]:
    """Read the standard 11-column CSV with its header at any position.

    Empty slots may contain just a key or a key plus ten empty columns. All
    normalized keys must be unique. Numeric fields are finite; bitrate is
    positive and times/memory nonnegative. A pass row requires all four RD values.
    Missing measurements remain None, including failures without measurements.
    """
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        rows = [(number, [value.strip() for value in row])
                for number, row in enumerate(csv.reader(stream), 1) if any(value.strip() for value in row)]
    headers = [number for number, row in rows if tuple(row) == CSV_COLUMNS]
    if len(headers) != 1:
        raise ValueError(f"expected exactly one standard 11-column header, found {len(headers)}")
    result: list[dict[str, Any]] = []
    seen: dict[tuple[str, int, str], int] = {}
    for number, row in rows:
        if number == headers[0]:
            continue
        if len(row) not in (1, len(CSV_COLUMNS)):
            raise ValueError(f"CSV row {number}: expected a task key or 11 columns, found {len(row)}")
        key = parse_key(row[0])
        if key in seen:
            raise ValueError(f"duplicate CSV task {key!r} on rows {seen[key]} and {number}")
        seen[key] = number
        raw = row[1:] if len(row) > 1 else [""] * len(DATA_FIELDS)
        values: list[Any] = []
        for index, value in enumerate(raw):
            if not value:
                values.append(None)
                continue
            if index >= 8:
                values.append(value)
                continue
            try:
                numeric = float(value)
            except ValueError as error:
                raise ValueError(f"CSV row {number}: invalid {CSV_COLUMNS[index + 1]}={value!r}") from error
            if not math.isfinite(numeric) or (index == 0 and numeric <= 0) or (index >= 4 and numeric < 0):
                raise ValueError(f"CSV row {number}: invalid {CSV_COLUMNS[index + 1]}={value!r}")
            if index in (6, 7):
                if not numeric.is_integer():
                    raise ValueError(f"CSV row {number}: memory value must be an integer: {value!r}")
                numeric = int(numeric)
            values.append(numeric)
        record = _record(row[0], values, number)
        if record["error_info"] == "pass" and any(record[field] is None for field in DATA_FIELDS[:4]):
            raise ValueError(f"CSV row {number}: pass requires bitrate and all three PSNR values")
        result.append(record)
    return result


def _sheet_records(root: ET.Element, strings: list[str]) -> list[dict[str, Any]]:
    cells = xml._build_cell_map(root)
    result = []
    for address, cell in cells.items():
        if not re.fullmatch(r"A[0-9]+", address):
            continue
        key = xml._get_cell_value(cell, strings)
        if not isinstance(key, str) or not _KEY.fullmatch(key.strip()):
            continue
        row = int(address[1:])
        values = []
        for column in range(2, 12):
            source = cells.get(f"{xml._idx_to_col(column)}{row}")
            value = xml._get_cell_value(source, strings) if source is not None else None
            values.append(None if value == "" else value)
        result.append(_record(key.strip(), values, row))
    return sorted(result, key=lambda record: record["row"])


def read_raw_sheet(path: str | Path, sheet_name: str = "Reference") -> list[dict[str, Any]]:
    """Read headerless A:K raw rows, preserving duplicate/empty template slots.

    Fields match read_csv(); row is the Excel row number. Reference records may
    legitimately have RD values and no status/time; this function does not infer
    pass, provenance, or experiment identity from the worksheet's layout label.
    """
    with zipfile.ZipFile(path) as archive:
        part = xml._get_sheet_xml_by_name(archive, sheet_name)
        if part is None:
            raise ValueError(f"worksheet not found: {sheet_name}")
        return _sheet_records(xml._read_xml(archive, part), xml._load_shared_strings(archive))


def _attribute(attributes: bytes, name: str) -> str | None:
    match = re.search(rb"\b" + name.encode("ascii") + rb"\s*=\s*([\"'])(.*?)\1", attributes)
    return match.group(2).decode("utf-8") if match else None


def _serialize_cell(cell: ET.Element) -> bytes:
    # A self-contained cell preserves style/metadata attributes and is safe even
    # when the surrounding worksheet uses a namespace prefix rather than default.
    cloned = copy.deepcopy(cell)
    cloned.tail = None
    return ET.tostring(cloned, encoding="utf-8")


def _patch_raw_cells(data: bytes, replacements: dict[str, bytes]) -> bytes:
    remaining = dict(replacements)

    def patch_row(match: re.Match[bytes]) -> bytes:
        row_number = _attribute(match.group("attrs"), "r")
        row_changes = {address: value for address, value in remaining.items()
                       if address.lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ") == row_number}
        if not row_changes:
            return match.group(0)
        body = match.group("body")
        if body is None:
            raise ValueError(f"raw task row {row_number} unexpectedly has no cells")
        edits: list[tuple[int, int, bytes]] = []
        present: list[tuple[int, re.Match[bytes]]] = []
        for cell in _CELL.finditer(body):
            address = _attribute(cell.group("attrs"), "r")
            if address is None:
                raise ValueError("worksheet contains a cell without an address")
            column, _ = xml._split_cell_ref(address)
            present.append((xml._col_to_idx(column), cell))
            if address in row_changes:
                edits.append((cell.start(), cell.end(), row_changes.pop(address)))
                remaining.pop(address)
        insertions: dict[int, list[tuple[int, bytes]]] = {}
        for address, value in row_changes.items():
            column = xml._col_to_idx(xml._split_cell_ref(address)[0])
            position = next((cell.start() for existing, cell in present if existing > column),
                            present[-1][1].end() if present else 0)
            insertions.setdefault(position, []).append((column, value))
            remaining.pop(address)
        for position, values in insertions.items():
            edits.append((position, position, b"".join(value for _, value in sorted(values))))
        for start, end, value in sorted(edits, key=lambda edit: (edit[0], edit[1]), reverse=True):
            body = body[:start] + value + body[end:]
        start = match.start("body") - match.start()
        end = match.end("body") - match.start()
        original = match.group(0)
        return original[:start] + body + original[end:]

    result = _ROW.sub(patch_row, data)
    if remaining:
        raise ValueError(f"could not locate raw cell rows: {sorted(remaining)}")
    ET.fromstring(result)
    return result


def _clear_formula_caches(data: bytes) -> tuple[bytes, int]:
    root = ET.fromstring(data)
    cells = xml._build_cell_map(root)
    targets: set[str] = set()
    ranges = []
    for address, cell in cells.items():
        formula = cell.find(xml._q("ss:f"))
        if formula is not None:
            targets.add(address)
            if formula.get("ref"):
                ranges.append(xml._parse_range(formula.get("ref").replace("$", "")))
    # Array/data-table/shared formula followers can have cached values without f.
    for address in cells:
        column, row = xml._split_cell_ref(address)
        index = xml._col_to_idx(column)
        if any(left <= index <= right and top <= row <= bottom for left, top, right, bottom in ranges):
            targets.add(address)
    changed = 0

    def clear(match: re.Match[bytes]) -> bytes:
        nonlocal changed
        if _attribute(match.group("attrs"), "r") not in targets:
            return match.group(0)
        original = match.group(0)
        result = _element("v").sub(b"", original)
        result = _element("is").sub(b"", result)
        changed += result != original
        return result

    return _CELL.sub(clear, data), changed


def _clear_chart_caches(data: bytes) -> tuple[bytes, int]:
    changed = 0

    def clear_reference(match: re.Match[bytes]) -> bytes:
        nonlocal changed
        original = match.group(0)
        if not _element("f").search(original):
            return original
        result = original
        for cache in ("numCache", "strCache", "multiLvlStrCache"):
            result, count = _element(cache).subn(b"", result)
            changed += count
        return result

    for reference in ("numRef", "strRef", "multiLvlStrRef"):
        data = _element(reference).sub(clear_reference, data)
    return data, changed


def _set_recalculation(data: bytes) -> bytes:
    settings = {"calcMode": "auto", "fullCalcOnLoad": "1", "forceFullCalc": "1"}
    pattern = _element("calcPr")
    match = pattern.search(data)
    if match:
        element = match.group(0)
        for name, value in settings.items():
            attribute = re.compile(rb"\b" + name.encode() + rb"\s*=\s*([\"']).*?\1")
            replacement = f'{name}="{value}"'.encode()
            if attribute.search(element):
                element = attribute.sub(replacement, element, count=1)
            else:
                close = element.find(b"/>")
                if close < 0:
                    close = element.find(b">")
                element = element[:close] + b" " + replacement + element[close:]
        return data[:match.start()] + element + data[match.end():]
    root = ET.fromstring(data)
    closing = re.search(rb"</(?P<prefix>" + _PREFIX + rb")workbook\s*>\s*$", data)
    if closing is None:
        raise ValueError("cannot locate workbook closing element")
    element = (b"<" + closing.group("prefix") + b"calcPr "
               + b" ".join(f'{name}="{value}"'.encode() for name, value in settings.items()) + b"/>")
    position = closing.start()
    # calcPr precedes these optional workbook children in the OOXML sequence.
    following = {"oleSize", "customWorkbookViews", "pivotCaches", "smartTagPr", "smartTagTypes",
                 "webPublishing", "fileRecoveryPr", "webPublishObjects", "extLst"}
    for child in root:
        local = child.tag.rsplit("}", 1)[-1]
        if local in following:
            found = _element(local).search(data)
            if found:
                position = found.start()
                break
    return data[:position] + element + data[position:]


def import_results(csv_path: str | Path, template_path: str | Path,
                   output_path: str | Path) -> dict[str, Any]:
    """Validate all matches, then write a new workbook; never overwrite a file.

    Returns a JSON-compatible report. Unmeasured missing/duplicate template slots
    are reported; any reported CSV point with no unique Test row is rejected.
    Only explicit pass records contribute numeric measurements to the workbook.
    Nonpass records retain status/time only; read_csv() still exposes their raw
    measurements. Report counts distinguish accepted_pass_rows and nonpass_rows.
    Reference bytes, VBA and all formulas/labels remain unchanged. Formula and
    chart-reference caches elsewhere are invalidated and full recalculation is
    requested for the next spreadsheet application opening the output.
    """
    csv_path, template_path, output_path = map(Path, (csv_path, template_path, output_path))
    for source in (csv_path, template_path):
        if output_path.resolve() == source.resolve() or (
            output_path.exists() and source.exists() and os.path.samefile(output_path, source)
        ):
            raise ValueError("output must not overwrite either input source")
    if output_path.exists():
        raise FileExistsError(f"output already exists: {output_path}")
    records = read_csv(csv_path)
    report: dict[str, Any] = dict(csv=str(csv_path), template=str(template_path), output=str(output_path),
                                 slots=len(records), measured=sum(record["measured"] for record in records),
                                 written_rows=0, accepted_pass_rows=0, nonpass_rows=0,
                                 nonpass_rows_with_raw_measurements=0,
                                 missing_unmeasured_keys=[], duplicate_unmeasured_keys=[],
                                 invalidated_formula_cells=0, invalidated_chart_caches=0)
    with zipfile.ZipFile(template_path) as source:
        names = source.namelist()
        if len(names) != len(set(names)):
            raise ValueError("template ZIP contains duplicate part names")
        test_part = xml._get_sheet_xml_by_name(source, "Test")
        reference_part = xml._get_sheet_xml_by_name(source, "Reference")
        if test_part is None or reference_part is None:
            raise ValueError("template must have separate Test and Reference worksheets")
        if test_part == reference_part:
            raise ValueError("Test and Reference must be different worksheet parts")
        strings = xml._load_shared_strings(source)
        test_bytes = source.read(test_part)
        test_root = ET.fromstring(test_bytes)
        worksheet_rows = _sheet_records(test_root, strings)
        lookup: dict[tuple[str, int, str], list[int]] = {}
        for record in worksheet_rows:
            lookup.setdefault(parse_key(record["key"]), []).append(record["row"])
        for record in records:
            key = parse_key(record["key"])
            matches = lookup.get(key, [])
            if record["reported"] and len(matches) != 1:
                raise ValueError(f"reported task {key!r} must match exactly one Test row; found {matches}")
            if not record["reported"] and not matches:
                report["missing_unmeasured_keys"].append(record["key"])
        report["duplicate_unmeasured_keys"] = [
            {"seq": key[0], "qp": key[1], "tag": key[2], "rows": matches}
            for key, matches in lookup.items() if len(matches) > 1
        ]
        cells = xml._build_cell_map(test_root)
        sheet_data = test_root.find(xml._q("ss:sheetData"))
        if sheet_data is None:
            raise ValueError("Test worksheet has no sheetData")
        task_rows = {record["row"] for record in worksheet_rows}
        replacements = {}
        for address, cell in cells.items():
            column, row = xml._split_cell_ref(address)
            if row not in task_rows or not 2 <= xml._col_to_idx(column) <= 11:
                continue
            if cell.find(xml._q("ss:f")) is not None:
                continue  # formulas are preserved, including formulas in empty raw slots
            for child in list(cell):
                if child.tag in (xml._q("ss:v"), xml._q("ss:is")):
                    cell.remove(child)
            cell.attrib.pop("t", None)
            replacements[address] = _serialize_cell(cell)
        for record in records:
            if not record["reported"]:
                continue
            row = lookup[parse_key(record["key"])][0]
            accepted = record["error_info"] == "pass"
            for column, field in enumerate(DATA_FIELDS, 2):
                address = f"{xml._idx_to_col(column)}{row}"
                existing = cells.get(address)
                if existing is not None and existing.find(xml._q("ss:f")) is not None:
                    raise ValueError(f"cannot overwrite a formula in Test!{address}")
                value = record[field] if accepted or field in ("error_info", "time") else None
                xml._set_cell_value(sheet_data=sheet_data, cell_ref=address, value=value,
                                    shared_strings=strings, shared_strings_root=None, shared_string_index={})
            report["written_rows"] += 1
            report["accepted_pass_rows" if accepted else "nonpass_rows"] += 1
            if not accepted and record["measured"]:
                report["nonpass_rows_with_raw_measurements"] += 1
        updated_cells = xml._build_cell_map(test_root)
        for record in records:
            if record["reported"]:
                row = lookup[parse_key(record["key"])][0]
                for column in range(2, 12):
                    address = f"{xml._idx_to_col(column)}{row}"
                    replacements[address] = _serialize_cell(updated_cells[address])
        modified = {test_part: _patch_raw_cells(test_bytes, replacements)}
        for name in names:
            if name.startswith("xl/worksheets/") and name.endswith(".xml") and name != reference_part:
                data, count = _clear_formula_caches(modified.get(name, source.read(name)))
                report["invalidated_formula_cells"] += count
                if count:
                    modified[name] = data
            elif name.startswith("xl/charts/") and name.endswith(".xml"):
                data, count = _clear_chart_caches(source.read(name))
                report["invalidated_chart_caches"] += count
                if count:
                    modified[name] = data
        modified["xl/workbook.xml"] = _set_recalculation(source.read("xl/workbook.xml"))
        for data in modified.values():
            ET.fromstring(data)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        # Build the entire ZIP before publishing it; exclusive creation prevents
        # a concurrent run from replacing an existing result or source.
        with tempfile.TemporaryFile(dir=output_path.parent) as temporary:
            with zipfile.ZipFile(temporary, "w") as destination:
                destination.comment = source.comment
                for item in source.infolist():
                    destination.writestr(item, modified.get(item.filename, source.read(item.filename)))
            temporary.seek(0)
            created = False
            try:
                with output_path.open("xb") as destination:
                    created = True
                    shutil.copyfileobj(temporary, destination)
            except BaseException:
                if created:
                    output_path.unlink()
                raise
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, type=Path)
    parser.add_argument("--template", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = import_results(args.csv, args.template, args.output)
    except (ValueError, OSError, zipfile.BadZipFile, ET.ParseError) as error:
        parser.exit(2, f"import failed: {error}\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
