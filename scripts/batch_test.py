#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import os
import re
import shlex
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable

from batch_identity import atomic_json, load_success, save_success, successful, task_identity


_CFG_INPUTFILE_RE = re.compile(r"^\s*InputFile\s*:\s*(.*?)\s*(?:#.*)?$")
_CFG_FRAMES_RE = re.compile(r"^\s*FramesToBeEncoded\s*:\s*(\d+)\b")
_CFG_FRAMERATE_RE = re.compile(r"^\s*FrameRate\s*:\s*([0-9.]+)\b")
_EXPERIMENT_LINE_RE = re.compile(r"^\s*EXPERIMENT:\s*(.*?)\s*$", re.MULTILINE)
_SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")
_INI_KV_RE = re.compile(r"^\s*([^=]+?)\s*=\s*(.*?)\s*$")
_INI_CLASS_RE = re.compile(r"^\s*#\s*Class\s*([A-Za-z0-9]+)\b", re.IGNORECASE)

_PRESET_CFGS = {
    "AI": "cfg/encoder_intra_nx2.cfg",
    "AI_HIGH": "cfg/encoder_intra_nx2High.cfg",
    "RA": "cfg/encoder_randomaccess_nx2.cfg",
    "RA_HIGH": "cfg/encoder_randomaccess_nx2High.cfg",
    "RA_GOP16": "cfg/encoder_randomaccess_nx2_gop16.cfg",
    "LB": "cfg/encoder_lowdelay_nx2.cfg",
    "LB_HIGH": "cfg/encoder_lowdelay_nx2High.cfg",
    "LDP": "cfg/encoder_lowdelay_P_nx2.cfg",
    "LDP_HIGH": "cfg/encoder_lowdelay_P_nx2High.cfg",
}

_PRESET_XLSM_TAGS = {
    "AI": "ai",
    "AI_HIGH": "ai",
    "RA": "ra",
    "RA_HIGH": "ra",
    "RA_GOP16": "ra",
    "LB": "lb",
    "LB_HIGH": "lb",
    "LDP": "lb",
    "LDP_HIGH": "lb",
}

_HHI_PRESET_INIS = {
    "AIeu": "ConfigAI.ini",
    "RAeu": "ConfigRA.ini",
    "LBeu": "ConfigLB.ini",
}

_HHI_PRESET_XLSM_TAGS = {
    "AIeu": "ai",
    "RAeu": "ra",
    "LBeu": "lb",
}

_PER_SEQUENCE_DIRS = (
    "cfg/per-sequence",
    "cfg/per-sequence-CFE",
    "cfg/per-sequence-Cfe2025",
    "cfg/per-sequence-HBD",
    "cfg/per-sequence-HDR",
    "cfg/per-sequence-non-420",
)


@dataclass(frozen=True)
class TestJob:
    order: int
    name: str
    repo_root: Path
    cwd: Path
    encoder: Path
    decoder: Path
    cfgs: list[Path]
    sequence_cfg: Path
    input_path: Path
    qp: int
    frames: int | None
    extra_args: list[str]
    decoder_args: list[str]
    decode_md5: bool
    xlsm_tag: str | None
    out_dir: Path
    bitstream: Path
    recon: Path
    encode_log: Path
    decode_log: Path
    overwrite: bool
    group: str = "baseline"
    write_recon: bool = False
    identity_files: tuple[Path, ...] = ()


@dataclass(frozen=True)
class HhiRunSpec:
    preset: str
    classes: str | None = None
    sequences: str | None = None


def _now_tag() -> str:
    return time.strftime("%Y%m%d_%H%M%S", time.localtime())


def _safe_mkdir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _split_csv_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [x.strip() for x in value.split(",") if x.strip()]


def _parse_qps(value: str) -> list[int]:
    qps = [int(x) for x in re.findall(r"\d+", value)]
    if not qps:
        raise ValueError(f"cannot parse QP list: {value}")
    return qps


def _parse_hhi_run_spec(spec: str) -> HhiRunSpec:
    raw = (spec or "").strip()
    if not raw:
        raise ValueError("empty --run spec")
    parts = raw.split(":", 2)
    preset = parts[0].strip()
    if preset not in _HHI_PRESET_INIS:
        raise ValueError(f"bad preset in --run: {preset}")
    classes = parts[1].strip() if len(parts) >= 2 and parts[1].strip() else None
    sequences = parts[2].strip() if len(parts) >= 3 and parts[2].strip() else None
    return HhiRunSpec(preset=preset, classes=classes, sequences=sequences)


def _parse_optional_int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    m = re.search(r"-?\d+", value)
    if not m:
        return None
    return int(m.group(0))


def _parse_bool(value: str | bool | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _strip_optional_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _is_comment_or_empty(line: str) -> bool:
    stripped = line.strip()
    return not stripped or stripped.startswith("#")


def _resolve_repo_path(repo_root: Path, value: str | Path) -> Path:
    path = Path(_strip_optional_quotes(str(value)))
    if path.is_absolute():
        return path
    return repo_root / path


def _parse_hhi_ini(path: Path) -> dict:
    sections: dict[str, dict[str, str]] = {}
    section_order: list[str] = []
    section_classes: dict[str, str] = {}
    current_section: str | None = None
    current_class: str | None = None
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        class_match = _INI_CLASS_RE.match(raw)
        if class_match:
            current_class = class_match.group(1).upper()
            continue
        if _is_comment_or_empty(raw):
            continue
        section_match = _SECTION_RE.match(raw)
        if section_match:
            current_section = section_match.group(1).strip()
            sections.setdefault(current_section, {})
            section_order.append(current_section)
            if current_class is not None:
                section_classes.setdefault(current_section, current_class)
            continue
        kv_match = _INI_KV_RE.match(raw)
        if kv_match and current_section:
            key = kv_match.group(1).strip()
            value = kv_match.group(2).strip()
            sections[current_section][key] = value
    return {"sections": sections, "section_order": section_order, "section_classes": section_classes}


def _iter_hhi_sequence_sections(ini: dict) -> Iterable[tuple[str, dict[str, str]]]:
    sections: dict[str, dict[str, str]] = ini["sections"]
    for name in ini.get("section_order", []):
        if name in {"Update", "Main", "Global", "Cmds"}:
            continue
        yield name, sections[name]


def _default_hhi_root(repo_root: Path) -> Path:
    candidates = [
        repo_root / "scripts" / "HHI测试cfg",
        repo_root / "script" / "HHI测试cfg",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return candidates[0]


def _default_hhi_ini(hhi_root: Path, preset: str) -> Path:
    return hhi_root / preset / _HHI_PRESET_INIS[preset]


def _cfg_args_to_paths(cwd: Path, cfg_args_raw: str) -> list[Path]:
    tokens = shlex.split(_strip_optional_quotes(cfg_args_raw))
    paths: list[Path] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "-c" and i + 1 < len(tokens):
            path = Path(tokens[i + 1])
            paths.append(path if path.is_absolute() else cwd / path)
            i += 2
            continue
        if token.startswith("-c") and len(token) > 2:
            path = Path(token[2:])
            paths.append(path if path.is_absolute() else cwd / path)
        i += 1
    return paths


def _read_inputfile_from_cfg(cfg_path: Path) -> str | None:
    try:
        lines = cfg_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in lines:
        m = _CFG_INPUTFILE_RE.match(line)
        if m:
            value = _strip_optional_quotes(m.group(1).strip())
            if value:
                return value
    return None


def _read_frames_from_cfg(cfg_path: Path) -> int | None:
    try:
        lines = cfg_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in lines:
        m = _CFG_FRAMES_RE.match(line)
        if m:
            return int(m.group(1))
    return None


def _read_framerate_from_cfg(cfg_path: Path) -> float | None:
    try:
        lines = cfg_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in lines:
        m = _CFG_FRAMERATE_RE.match(line)
        if not m:
            continue
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def _build_basename_index(input_dir: Path, suffixes: tuple[str, ...] = (".yuv", ".y4m")) -> dict[str, Path]:
    index: dict[str, Path] = {}
    if not input_dir.is_dir():
        return index
    for root, _, files in os.walk(input_dir):
        for filename in files:
            lower = filename.lower()
            if not any(lower.endswith(suf) for suf in suffixes):
                continue
            index.setdefault(filename, Path(root) / filename)
    return index


def _find_sequence_cfg(repo_root: Path, seq_name: str) -> Path | None:
    name = seq_name[:-4] if seq_name.lower().endswith(".cfg") else seq_name
    for rel_dir in _PER_SEQUENCE_DIRS:
        candidate = repo_root / rel_dir / f"{name}.cfg"
        if candidate.is_file():
            return candidate
    return None


def _iter_all_sequence_cfgs(repo_root: Path) -> Iterable[Path]:
    seen: set[Path] = set()
    for rel_dir in _PER_SEQUENCE_DIRS:
        base = repo_root / rel_dir
        if not base.is_dir():
            continue
        for cfg in sorted(base.glob("*.cfg"), key=lambda p: p.name.lower()):
            resolved = cfg.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            yield cfg


def _detect_experiment_line(exe: Path, cwd: Path) -> str | None:
    if not exe.is_file() or not os.access(exe, os.X_OK):
        return None
    try:
        proc = subprocess.run(
            [str(exe), "-h"],
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
            timeout=5,
        )
    except Exception:
        return None
    m = _EXPERIMENT_LINE_RE.search(proc.stdout or "")
    if not m:
        return None
    tail = m.group(1).strip()
    return f"EXPERIMENT: {tail}" if tail else "EXPERIMENT:"


def _parse_encoder_summary(text: str) -> dict[str, int | float]:
    result: dict[str, int | float] = {}
    lines = text.splitlines()
    header_idx = None
    for i, line in enumerate(lines):
        if "Total Frames" in line and "Y-PSNR" in line and "U-PSNR" in line and "V-PSNR" in line:
            header_idx = i
    if header_idx is not None:
        for line in lines[header_idx + 1 : header_idx + 30]:
            s = line.strip()
            if not s:
                continue
            m = re.match(r"^(\d+)\s+\S+\s+([0-9.]+)\s+([0-9.]+)\s+([0-9.]+)\s+([0-9.]+)\b", s)
            if not m:
                continue
            result["encoded_frames"] = int(m.group(1))
            result["kbps"] = round(float(m.group(2)), 4)
            result["ypsnr"] = round(float(m.group(3)), 4)
            result["upsnr"] = round(float(m.group(4)), 4)
            result["vpsnr"] = round(float(m.group(5)), 4)
            break

    for line in reversed(lines):
        if "Total Time:" not in line:
            continue
        m_elapsed = re.search(r"Total Time:\s*.*?([0-9.]+)\s*sec\.\s*\[elapsed\]", line)
        m_any = re.search(r"Total Time:\s*([0-9.]+)\s*sec\.", line)
        m = m_elapsed or m_any
        if m:
            result["reported_seconds"] = round(float(m.group(1)), 3)
            break

    m_vm = re.search(r"VmPeak=\s*(\d+)\s*KB", text)
    if m_vm:
        result["vmpeak_kb"] = int(m_vm.group(1))
    return result


def _parse_decode_status(text: str) -> str:
    upper = text.upper()
    if "(ERROR)" in upper or "MISMATCH" in upper or "ERROR" in upper:
        return "failed"
    if "(OK)" in upper:
        return "ok"
    if "MD5:" in upper or "CHECKSUM:" in upper or "CRC:" in upper:
        return "unchecked"
    return "nohash"


def _calc_kbps_from_bitstream(bitstream: Path, frames: int | None, frame_rate: float | None) -> float | None:
    if frames is None or frames <= 0 or frame_rate is None or frame_rate <= 0:
        return None
    try:
        size_bytes = bitstream.stat().st_size
    except OSError:
        return None
    return round((size_bytes * 8.0 * frame_rate) / (frames * 1000.0), 4)


def _default_xlsm_template(repo_root: Path) -> Path:
    candidates = [
        repo_root / "scripts" / "JVET-hhi.xlsm",
        repo_root / "scripts" / "HHI测试cfg" / "JVET-hhi.xlsm",
        repo_root / "script" / "JVET-hhi.xlsm",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def _infer_xlsm_tag_from_cfgs(cfgs: list[Path], fallback: str | None = None) -> str | None:
    joined = " ".join(str(p).lower() for p in cfgs)
    if "randomaccess" in joined:
        return "ra"
    if "lowdelay" in joined:
        return "lb"
    if "intra" in joined:
        return "ai"
    return fallback


def _quote_cmd(cmd: list[str]) -> str:
    return " ".join(shlex.quote(x) for x in cmd)


def _write_log_header(log_file, cmd: list[str], cwd: Path) -> None:
    log_file.write("COMMAND:\n")
    log_file.write(_quote_cmd(cmd) + "\n\n")
    log_file.write(f"CWD: {cwd}\n")
    log_file.write(f"START: {time.asctime(time.localtime())}\n\n")
    log_file.flush()


def _run_decode(job: TestJob) -> dict:
    _safe_mkdir(job.decode_log.parent)
    cmd = [str(job.decoder), "-b", str(job.bitstream), "-o", "/dev/null"]
    cmd.extend(job.decoder_args)
    start = time.time()
    with job.decode_log.open("w", encoding="utf-8") as logf:
        _write_log_header(logf, cmd, job.cwd)
        proc = subprocess.run(
            cmd,
            cwd=str(job.cwd),
            stdout=logf,
            stderr=subprocess.STDOUT,
            check=False,
        )
        logf.write(f"\nEND: {time.asctime(time.localtime())}\n")
        logf.write(f"RETURNCODE: {proc.returncode}\n")
    end = time.time()

    try:
        text = job.decode_log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    status = "failed" if proc.returncode != 0 else _parse_decode_status(text)
    result = {
        "decode_status": status,
        "decode_returncode": proc.returncode,
        "decode_seconds": round(end - start, 3),
        "decode_log": str(job.decode_log),
    }
    parsed = _parse_encoder_summary(text)
    if "vmpeak_kb" in parsed:
        result["dec_vmpeak_kb"] = parsed["vmpeak_kb"]
    return result


def _encode_command(job: TestJob) -> list[str]:
    cmd = [str(job.encoder)]
    for cfg in [*job.cfgs, job.sequence_cfg]:
        cmd.extend(["-c", str(cfg)])
    cmd.extend(["-i", str(job.input_path), "-q", str(job.qp), "-b", str(job.bitstream)])
    # Empty ReconFile disables reconstruction output in EncApp.cpp (unlike /dev/null).
    cmd.extend(["-o", str(job.recon) if job.write_recon else ""])
    if job.frames is not None:
        cmd.extend(["-f", str(job.frames)])
    cmd.extend(job.extra_args)
    return cmd


def _validate_job(job: TestJob) -> None:
    # Do not allow metadata, output ownership or fingerprints to disagree with CLI overrides.
    managed = {"-c", "--config", "-i", "--InputFile", "-q", "--QP", "-f", "--FramesToBeEncoded",
               "-b", "--BitstreamFile", "-o", "--ReconFile"}
    for token in job.extra_args:
        if token.split("=", 1)[0] in managed or any(token.startswith(x) and token != x for x in ("-c", "-i", "-q", "-f", "-b", "-o")):
            raise ValueError(f"use dedicated manifest/CLI fields instead of extra_args: {token}")
    for token in job.decoder_args:
        if token.split("=", 1)[0] in {"-b", "--BitstreamFile", "-o", "--ReconFile"} or any(token.startswith(x) and token != x for x in ("-b", "-o")):
            raise ValueError(f"decoder_args cannot override input/output: {token}")
    if job.frames is not None and job.frames <= 0:
        raise ValueError("frames must be positive")


def _run_one(job: TestJob) -> dict:
    _safe_mkdir(job.out_dir)
    cmd = _encode_command(job)
    identity = task_identity(job, cmd)
    if not job.overwrite:
        resumed = load_success(job, identity["fingerprint"])
        if resumed is not None:
            return dict(resumed, order=job.order)
        if any(p.exists() for p in (job.bitstream, job.encode_log, job.bitstream.with_suffix(".success.json"))):
            raise ValueError("existing output is not a verified matching success; choose a new --out-dir or explicit --overwrite")
    marker = job.bitstream.with_suffix(".success.json")
    if marker.exists():
        marker.unlink()  # Invalidate BEFORE starting an explicitly authorized rerun.
    if job.overwrite and job.bitstream.exists():
        job.bitstream.unlink()  # Never validate a stale stream after a failed rerun.
    atomic_json(job.bitstream.with_suffix(".task.json"), identity)

    base = {
        "order": job.order,
        "group": job.group,
        "fingerprint": identity["fingerprint"],
        "name": job.name,
        "sequence": job.sequence_cfg.stem,
        "sequence_cfg": str(job.sequence_cfg),
        "input": str(job.input_path),
        "qp": job.qp,
        "frames": job.frames if job.frames is not None else "",
        "bitstream": str(job.bitstream),
        "recon": str(job.recon) if job.write_recon else "",
        "encode_log": str(job.encode_log),
        "decode_log": str(job.decode_log) if job.decode_md5 else "",
        "xlsm_tag": job.xlsm_tag or "",
        "time": time.asctime(time.localtime()),
    }

    start = time.time()
    with job.encode_log.open("w", encoding="utf-8") as logf:
        _write_log_header(logf, cmd, job.cwd)
        proc = subprocess.run(
            cmd,
            cwd=str(job.cwd),
            stdout=logf,
            stderr=subprocess.STDOUT,
            check=False,
        )
        logf.write(f"\nEND: {time.asctime(time.localtime())}\n")
        logf.write(f"RETURNCODE: {proc.returncode}\n")
    end = time.time()

    encode_status = "ok" if proc.returncode == 0 else "failed"
    result = {
        **base,
        "encode_status": encode_status,
        "status": encode_status,
        "encode_returncode": proc.returncode,
        "returncode": proc.returncode,
        "encode_seconds": round(end - start, 3),
        "seconds": round(end - start, 3),
        "decode_status": "",
        "decode_returncode": "",
        "decode_seconds": "",
        "error_info": "pass" if encode_status == "ok" else "fail",
    }
    result = _enrich_result(job, result)
    expected_frames = job.frames
    if expected_frames is None:
        for cfg in [*job.cfgs, job.sequence_cfg]:
            configured = _read_frames_from_cfg(cfg)
            if configured is not None:
                expected_frames = configured
    complete = (job.bitstream.is_file() and job.bitstream.stat().st_size > 0
                and result.get("encoded_frames", 0) > 0
                and all(k in result for k in ("ypsnr", "upsnr", "vpsnr"))
                and (expected_frames is None or result["encoded_frames"] == expected_frames))
    if encode_status == "ok" and not complete:
        encode_status = "incomplete"
        result.update(encode_status=encode_status, status=encode_status, error_info="fail(incomplete encode)")

    if job.decode_md5 and encode_status == "ok":
        dec = _run_decode(job)
        if dec["decode_status"] == "ok":
            checked = job.decode_log.read_text(encoding="utf-8", errors="replace").upper().count("(OK)")
            if checked < result.get("encoded_frames", 0):
                dec["decode_status"] = "incomplete_hash_check"
        result.update(dec)
        if dec["decode_status"] != "ok":
            result["error_info"] = "fail(md5/checksum)"
    elif job.decode_md5:
        result.update(
            {
                "decode_status": "skipped_encode_failed",
                "decode_returncode": "",
                "decode_seconds": "",
            }
        )
    save_success(job, identity, result)
    return result


def _enrich_result(job: TestJob, result: dict) -> dict:
    try:
        text = job.encode_log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    if text:
        parsed = _parse_encoder_summary(text)
        result.update(parsed)
        if "reported_seconds" in parsed:
            result["seconds"] = parsed["reported_seconds"]
        if "vmpeak_kb" in parsed:
            result["enc_vmpeak_kb"] = parsed["vmpeak_kb"]
    if "kbps" not in result:
        frames = result.get("encoded_frames")
        if not isinstance(frames, int):
            frames = job.frames or _read_frames_from_cfg(job.sequence_cfg)
        kbps = _calc_kbps_from_bitstream(job.bitstream, frames, _read_framerate_from_cfg(job.sequence_cfg))
        if kbps is not None:
            result["kbps"] = kbps
    return result


def _write_summary(path: Path, rows: list[dict]) -> None:
    _safe_mkdir(path.parent)
    fieldnames = [
        "group",
        "fingerprint",
        "name",
        "sequence",
        "sequence_cfg",
        "input",
        "qp",
        "frames",
        "bitstream",
        "recon",
        "encode_status",
        "status",
        "encode_returncode",
        "returncode",
        "encode_seconds",
        "seconds",
        "decode_status",
        "decode_returncode",
        "decode_seconds",
        "kbps",
        "ypsnr",
        "upsnr",
        "vpsnr",
        "encoded_frames",
        "reported_seconds",
        "vmpeak_kb",
        "enc_vmpeak_kb",
        "dec_vmpeak_kb",
        "error_info",
        "time",
        "xlsm_tag",
        "encode_log",
        "decode_log",
    ]
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})
    os.replace(temporary, path)


def _write_xlsm_reports(*, repo_root: Path, out_dir: Path, rows: list[dict], args: argparse.Namespace) -> None:
    _safe_mkdir(out_dir)
    template = args.xlsm_template.resolve() if args.xlsm_template is not None else _default_xlsm_template(repo_root)
    auto_xlsm = args.xlsm_report is None and template.is_file()
    want_xlsm = bool(args.xlsm_report is True or auto_xlsm)
    if not want_xlsm:
        return
    if not template.is_file():
        print(f"WARN: xlsm report skipped, template not found: {template}", file=sys.stderr)
        return

    try:
        from xlsm_table_fill import fill_jvet_hhi_test_sheet
    except Exception as e:
        print(f"WARN: xlsm report skipped, helper import failed: {e}", file=sys.stderr)
        return

    rows_by_tag: dict[str, list[dict]] = {}
    missing_tag = 0
    for row in rows:
        tag = str(row.get("xlsm_tag") or "").strip().lower()
        if not tag:
            missing_tag += 1
            continue
        rows_by_tag.setdefault(tag, []).append(row)

    if missing_tag:
        print(f"WARN: xlsm report skipped {missing_tag} rows without xlsm_tag", file=sys.stderr)
    if not rows_by_tag:
        print("WARN: xlsm report skipped, no rows with xlsm_tag", file=sys.stderr)
        return

    output = out_dir / "JVET-hhi.xlsm"
    if template.resolve() == output.resolve():
        raise ValueError("output workbook must not overwrite the anchor template")
    current_template = template
    stage_files: list[Path] = []
    tag_items = sorted(rows_by_tag.items())
    for idx, (tag, tag_rows) in enumerate(tag_items):
        is_last = idx == len(tag_items) - 1
        step_output = output if is_last else out_dir / f".JVET-hhi.{idx + 1}.{tag}.stage.xlsm"
        stage_files.append(step_output)
        ok, msg = fill_jvet_hhi_test_sheet(
            template_xlsm=current_template,
            output_xlsm=step_output,
            results=tag_rows,
            condition_tag=tag,
            clear_test=(idx == 0),
        )
        if not ok:
            print(f"WARN: xlsm report skipped for tag={tag}: {msg}", file=sys.stderr)
            break
        current_template = step_output
    else:
        print(f"XLSM       : {output}")

    for stage in stage_files:
        if stage == output:
            continue
        try:
            if stage.exists():
                stage.unlink()
        except OSError:
            pass


def _flush_progress(jobs: list[TestJob], results: dict[int, dict], args, repo_root: Path,
                    reported: dict[str, tuple]) -> None:
    rows = [results[i] for i in sorted(results)]
    _write_summary(args.out_dir / "summary.csv", rows)
    _write_summary(args.out_dir / "failures.csv", [r for r in rows if not successful(r)])
    groups = sorted({job.group for job in jobs})
    for group in groups:
        members = [job for job in jobs if job.group == group]
        done = [results[job.order] for job in members if job.order in results]
        if len(done) != len(members):
            continue
        signature = tuple((r.get("encode_status"), r.get("decode_status"), r.get("error_info")) for r in done)
        if reported.get(group) == signature:
            continue
        directory = args.out_dir / "reports" / group
        _write_summary(directory / "summary.csv", done)
        _write_xlsm_reports(repo_root=repo_root, out_dir=directory, rows=done, args=args)
        atomic_json(directory / "completion.json", {
            "group": group, "tasks": len(done), "passed": sum(successful(r) for r in done),
            "all_passed": all(successful(r) for r in done),
        })
        reported[group] = signature


def _make_job(
    *,
    order: int,
    name: str,
    repo_root: Path,
    cwd: Path,
    encoder: Path,
    decoder: Path,
    cfgs: list[Path],
    sequence_cfg: Path,
    input_path: Path,
    qp: int,
    frames: int | None,
    extra_args: list[str],
    decoder_args: list[str],
    decode_md5: bool,
    xlsm_tag: str | None,
    run_dir: Path,
    overwrite: bool,
    group: str = "baseline",
) -> TestJob:
    safe_name = re.sub(r"[^A-Za-z0-9_.+-]+", "_", name.strip()) or f"job_{order:04d}"
    out_dir = run_dir / safe_name
    bitstream = out_dir / f"{safe_name}_QP{qp}.bin"
    recon = out_dir / f"{safe_name}_QP{qp}.yuv"
    return TestJob(
        order=order,
        name=safe_name,
        repo_root=repo_root,
        cwd=cwd,
        encoder=encoder,
        decoder=decoder,
        cfgs=cfgs,
        sequence_cfg=sequence_cfg,
        input_path=input_path,
        qp=qp,
        frames=frames,
        extra_args=extra_args,
        decoder_args=decoder_args,
        decode_md5=decode_md5,
        xlsm_tag=xlsm_tag,
        out_dir=out_dir,
        bitstream=bitstream,
        recon=recon,
        encode_log=out_dir / f"{safe_name}_QP{qp}.encode.log",
        decode_log=out_dir / f"{safe_name}_QP{qp}.decode.log",
        overwrite=overwrite,
        group=group,
    )


def _plan_from_manifest(
    *,
    repo_root: Path,
    manifest: Path,
    input_index: dict[str, Path],
    encoder: Path,
    decoder: Path,
    run_dir: Path,
    default_extra_args: list[str],
    default_decoder_args: list[str],
    default_decode_md5: bool,
    default_xlsm_tag: str | None,
    overwrite: bool,
    allow_missing_input: bool,
) -> list[TestJob]:
    jobs: list[TestJob] = []
    with manifest.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not _parse_bool(row.get("enabled"), default=True):
                continue
            name = (row.get("name") or "").strip()
            cfgs_raw = row.get("cfgs") or row.get("cfg") or ""
            sequence_cfg_raw = row.get("sequence_cfg") or ""
            input_raw = row.get("input") or ""
            qps_raw = row.get("qps") or row.get("qp") or "32"
            frames = _parse_optional_int(row.get("frames"))
            extra_args = shlex.split(row.get("extra_args") or "") or default_extra_args
            decoder_args = shlex.split(row.get("decoder_args") or "") or default_decoder_args
            decode_md5 = _parse_bool(row.get("decode_md5"), default=default_decode_md5)
            xlsm_tag = (row.get("xlsm_tag") or "").strip().lower() or default_xlsm_tag
            group = (row.get("experiment") or "baseline").strip()
            if not re.fullmatch(r"[A-Za-z0-9_.+-]+", group) or group in {".", ".."}:
                raise ValueError(f"unsafe experiment name: {group}")
            row_encoder = _resolve_repo_path(repo_root, row["encoder"]).resolve() if row.get("encoder") else encoder
            row_decoder = _resolve_repo_path(repo_root, row["decoder"]).resolve() if row.get("decoder") else decoder

            if not sequence_cfg_raw:
                raise ValueError(f"manifest row missing sequence_cfg: {row}")
            sequence_cfg = _resolve_repo_path(repo_root, sequence_cfg_raw).resolve()
            if not sequence_cfg.is_file():
                raise FileNotFoundError(f"sequence_cfg not found: {sequence_cfg}")

            cfgs = []
            for item in [x.strip() for x in cfgs_raw.split(";") if x.strip()]:
                cfg_path = _resolve_repo_path(repo_root, item).resolve()
                if not cfg_path.is_file():
                    raise FileNotFoundError(f"cfg not found: {cfg_path}")
                cfgs.append(cfg_path)
            if xlsm_tag is None:
                xlsm_tag = _infer_xlsm_tag_from_cfgs(cfgs)

            if not input_raw:
                input_raw = _read_inputfile_from_cfg(sequence_cfg) or ""
            input_path = _resolve_input_path(repo_root, input_raw, input_index)
            if not input_path.is_file() and not allow_missing_input:
                raise FileNotFoundError(f"input not found: {input_raw}")

            base_name = name or sequence_cfg.stem
            for qp in _parse_qps(qps_raw):
                jobs.append(
                    _make_job(
                        order=len(jobs),
                        name=base_name,
                        repo_root=repo_root,
                        cwd=repo_root,
                        encoder=row_encoder,
                        decoder=row_decoder,
                        cfgs=cfgs,
                        sequence_cfg=sequence_cfg,
                        input_path=input_path.resolve(),
                        qp=qp,
                        frames=frames,
                        extra_args=extra_args,
                        decoder_args=decoder_args,
                        decode_md5=decode_md5,
                        xlsm_tag=xlsm_tag,
                        run_dir=run_dir / group,
                        overwrite=overwrite,
                        group=group,
                    )
                )
    return jobs


def _resolve_input_path(repo_root: Path, raw: str, input_index: dict[str, Path]) -> Path:
    raw = _strip_optional_quotes(raw)
    if not raw:
        return Path("")
    candidate = Path(raw)
    if candidate.is_absolute() and candidate.is_file():
        return candidate
    repo_candidate = repo_root / candidate
    if repo_candidate.is_file():
        return repo_candidate
    by_name = input_index.get(candidate.name)
    if by_name is not None:
        return by_name
    return candidate


def _plan_auto(args: argparse.Namespace, repo_root: Path, input_index: dict[str, Path]) -> list[TestJob]:
    if args.preset_cfg is not None:
        preset_cfg = args.preset_cfg.resolve()
    else:
        local_preset = args.local_preset or "AI"
        preset_cfg = (repo_root / _PRESET_CFGS[local_preset]).resolve()
    if not preset_cfg.is_file():
        raise FileNotFoundError(f"preset cfg not found: {preset_cfg}")

    cfgs = [preset_cfg]
    for cfg in args.cfg:
        cfg_path = cfg.resolve()
        if not cfg_path.is_file():
            raise FileNotFoundError(f"cfg not found: {cfg_path}")
        cfgs.append(cfg_path)

    sequence_cfgs: list[Path] = []
    for seq_cfg in args.sequence_cfg:
        path = seq_cfg.resolve()
        if not path.is_file():
            raise FileNotFoundError(f"sequence cfg not found: {path}")
        sequence_cfgs.append(path)

    for seq_name in _split_csv_list(args.sequences):
        found = _find_sequence_cfg(repo_root, seq_name)
        if found is None:
            raise FileNotFoundError(f"sequence cfg not found for sequence: {seq_name}")
        sequence_cfgs.append(found.resolve())

    if args.all_sequences:
        sequence_cfgs.extend([p.resolve() for p in _iter_all_sequence_cfgs(repo_root)])

    unique_cfgs: list[Path] = []
    seen: set[Path] = set()
    for cfg in sequence_cfgs:
        if cfg in seen:
            continue
        seen.add(cfg)
        unique_cfgs.append(cfg)

    if not unique_cfgs:
        raise ValueError("no sequence planned: use --manifest, --sequences, --sequence-cfg, or --all-sequences")

    qps = _parse_qps(args.qps or "22,27,32,37")
    local_preset = args.local_preset or "AI"
    xlsm_tag = args.xlsm_tag or _PRESET_XLSM_TAGS.get(local_preset)
    jobs: list[TestJob] = []
    for sequence_cfg in unique_cfgs:
        input_name = _read_inputfile_from_cfg(sequence_cfg)
        if not input_name:
            raise ValueError(f"cannot read InputFile from {sequence_cfg}")
        input_path = _resolve_input_path(repo_root, input_name, input_index)
        if not input_path.is_file() and not args.allow_missing_input:
            raise FileNotFoundError(f"input not found for {sequence_cfg.stem}: {input_name}")
        frames = args.frames if args.frames is not None else None
        for qp in qps:
            jobs.append(
                _make_job(
                    order=len(jobs),
                    name=f"{local_preset}_{sequence_cfg.stem}",
                    repo_root=repo_root,
                    cwd=repo_root,
                    encoder=args.encoder,
                    decoder=args.decoder,
                    cfgs=cfgs,
                    sequence_cfg=sequence_cfg,
                    input_path=input_path.resolve(),
                    qp=qp,
                    frames=frames,
                    extra_args=shlex.split(args.extra_args),
                    decoder_args=shlex.split(args.decoder_args),
                    decode_md5=bool(args.decode_md5),
                    xlsm_tag=xlsm_tag,
                    run_dir=args.out_dir,
                    overwrite=bool(args.overwrite),
                )
            )
    return jobs


def _plan_hhi(args: argparse.Namespace, repo_root: Path, input_index: dict[str, Path]) -> list[TestJob]:
    hhi_root = args.hhi_dir.resolve()
    if not hhi_root.is_dir():
        raise FileNotFoundError(f"HHI cfg dir not found: {hhi_root}")

    run_specs = args.run or args.hhi_run
    hhi_preset = args.hhi_preset or args.preset
    if run_specs:
        specs = [_parse_hhi_run_spec(s) for s in run_specs]
    else:
        specs = [HhiRunSpec(preset=hhi_preset, classes=args.classes, sequences=args.sequences)]

    if args.hhi_ini is not None and len({s.preset for s in specs}) > 1:
        raise ValueError("--hhi-ini cannot be used with multiple --run presets")

    jobs: list[TestJob] = []
    for spec in specs:
        preset_dir = hhi_root / spec.preset
        if not preset_dir.is_dir():
            raise FileNotFoundError(f"HHI preset dir not found: {preset_dir}")
        ini_path = args.hhi_ini.resolve() if args.hhi_ini is not None else _default_hhi_ini(hhi_root, spec.preset)
        if not ini_path.is_file():
            raise FileNotFoundError(f"HHI ini not found: {ini_path}")

        ini = _parse_hhi_ini(ini_path)
        sections: dict[str, dict[str, str]] = ini["sections"]
        global_section = sections.get("Global", {})
        qps = _parse_qps(args.qps) if args.qps else _parse_qps(global_section.get("qps", "22 27 32 37"))

        wanted_sequences = set(_split_csv_list(spec.sequences or args.sequences))
        wanted_classes = {x.upper() for x in _split_csv_list(spec.classes or args.classes)}
        section_classes: dict[str, str] = ini.get("section_classes", {})

        for seq_name, values in _iter_hhi_sequence_sections(ini):
            if wanted_sequences and seq_name not in wanted_sequences:
                continue
            if wanted_classes:
                seq_class = section_classes.get(seq_name)
                if seq_class is None or seq_class not in wanted_classes:
                    continue

            sequence_cfg = _find_sequence_cfg(preset_dir, seq_name)
            if sequence_cfg is None:
                print(f"WARN: missing HHI per-sequence cfg for {spec.preset} {seq_name}, skipped", file=sys.stderr)
                continue
            sequence_cfg = sequence_cfg.resolve()

            cfgs = _cfg_args_to_paths(preset_dir, values.get("cfg", ""))
            if not cfgs:
                print(f"WARN: missing cfg args for {spec.preset} {seq_name}, skipped", file=sys.stderr)
                continue
            missing_cfgs = [p for p in cfgs if not p.is_file()]
            if missing_cfgs:
                print(f"WARN: missing cfg for {spec.preset} {seq_name}: {missing_cfgs[0]}, skipped", file=sys.stderr)
                continue

            input_name = _read_inputfile_from_cfg(sequence_cfg)
            if not input_name:
                print(f"WARN: cannot read InputFile from {sequence_cfg}, skipped {spec.preset} {seq_name}", file=sys.stderr)
                continue
            input_path = _resolve_input_path(repo_root, input_name, input_index)
            if not input_path.is_file() and not args.allow_missing_input:
                raise FileNotFoundError(f"input not found for {spec.preset} {seq_name}: {input_name}")

            default_frames = _parse_optional_int(values.get("framecount"))
            if default_frames is None:
                default_frames = _parse_optional_int(values.get("segLen"))
            frames = args.frames if args.frames is not None else default_frames
            xlsm_tag = args.xlsm_tag or _HHI_PRESET_XLSM_TAGS.get(spec.preset)

            for qp in qps:
                jobs.append(
                    _make_job(
                        order=len(jobs),
                        name=f"{spec.preset}_{seq_name}",
                        repo_root=repo_root,
                        cwd=preset_dir,
                        encoder=args.encoder,
                        decoder=args.decoder,
                        cfgs=[p.resolve() for p in cfgs],
                        sequence_cfg=sequence_cfg,
                        input_path=input_path.resolve(),
                        qp=qp,
                        frames=frames,
                        extra_args=shlex.split(args.extra_args),
                        decoder_args=shlex.split(args.decoder_args),
                        decode_md5=bool(args.decode_md5),
                        xlsm_tag=xlsm_tag,
                        run_dir=args.out_dir,
                        overwrite=bool(args.overwrite),
                        group=spec.preset,
                    )
                )
    return jobs


def _check_tools(args: argparse.Namespace) -> int:
    missing = []
    for label, tool in (("encoder", args.encoder), ("decoder", args.decoder)):
        if label == "decoder" and not args.decode_md5:
            continue
        if not tool.is_file():
            missing.append(f"{label} not found: {tool}")
        elif not os.access(tool, os.X_OK):
            missing.append(f"{label} is not executable: {tool} (run: chmod +x {tool})")
    if not missing:
        return 0
    for item in missing:
        print(f"ERROR: {item}", file=sys.stderr)
    return 2


def main(argv: list[str]) -> int:
    repo_root = Path(__file__).resolve().parents[1]

    parser = argparse.ArgumentParser(
        description="统一批量测试入口：批量运行 EncoderApp，可选 DecoderApp MD5/校验，并生成规范化 summary.csv。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--manifest", type=Path, default=None, help="CSV 任务清单；见 batch_test_jobs.example.csv")
    parser.add_argument("--preset", choices=sorted(_HHI_PRESET_INIS), default="AIeu", help="HHI/CTC 配置 preset；默认行为与原 batch_encode.py 对齐")
    parser.add_argument("--run", action="append", default=None, help="HHI 组合运行：PRESET[:CLASSES][:SEQUENCES]（可重复），例如 RAeu:C,D")
    parser.add_argument("--local-preset", choices=sorted(_PRESET_CFGS), default=None, help="显式使用项目根目录 cfg 的普通模式 preset，例如 AI/RA/LB")
    parser.add_argument("--preset-cfg", type=Path, default=None, help="普通模式自定义公共编码 cfg，覆盖 --local-preset")
    parser.add_argument("--cfg", type=Path, action="append", default=[], help="追加公共 cfg；可重复")
    parser.add_argument("--sequence-cfg", type=Path, action="append", default=[], help="指定 per-sequence cfg；可重复")
    parser.add_argument("--sequences", type=str, default=None, help="按序列名查找 cfg，逗号分隔，如 BasketballPass,BQMall")
    parser.add_argument("--all-sequences", action="store_true", help="自动展开 cfg/per-sequence* 下全部序列")
    parser.add_argument("--qps", type=str, default=None, help="QP 列表；普通模式默认 22,27,32,37，HHI 模式默认读 Config*.ini")
    parser.add_argument("--frames", type=int, default=None, help="覆盖编码帧数，传给 EncoderApp -f")
    parser.add_argument("--input-dir", type=Path, default=Path("/home/zhy/videos"), help="YUV/Y4M 测试序列根目录")
    parser.add_argument("--encoder", type=Path, default=repo_root / "bin" / "EncoderAppStatic", help="EncoderApp 路径")
    parser.add_argument("--decoder", type=Path, default=repo_root / "bin" / "DecoderAppStatic", help="DecoderApp 路径")
    parser.add_argument("--hhi", action="store_true", help="使用 scripts/HHI测试cfg 下的 HHI Config*.ini 规划任务；默认即启用")
    parser.add_argument("--hhi-dir", type=Path, default=_default_hhi_root(repo_root), help="HHI测试cfg 根目录")
    parser.add_argument("--hhi-preset", choices=sorted(_HHI_PRESET_INIS), default=None, help="兼容旧参数；等价于 --preset")
    parser.add_argument("--hhi-run", action="append", default=None, help="兼容旧参数；等价于 --run")
    parser.add_argument("--hhi-ini", type=Path, default=None, help="自定义 HHI Config*.ini；仅单 preset 时使用")
    parser.add_argument("--class", dest="classes", type=str, default=None, help="HHI 模式按 Class 过滤，例如 C,D,E")
    parser.add_argument("--out-dir", type=Path, default=None, help="输出根目录；留空则使用 runs/batch_test/<timestamp>")
    parser.add_argument("--jobs", type=int, default=1, help="并行任务数")
    parser.add_argument("--write-recon", action="store_true", help="显式保存重建视频；默认不输出")
    parser.add_argument("--identity-file", type=Path, action="append", default=[], help="额外模型/文件依赖，纳入续跑指纹；可重复")
    parser.add_argument("--retry-failed", type=int, default=0, help="失败任务结束后按单路重试次数")
    parser.add_argument("--extra-args", type=str, default="--PrintHexPSNR=1 --SEIDecodedPictureHash=1", help="EncoderApp 附加参数")
    parser.add_argument("--decode-md5", action="store_true", help="编码成功后运行 DecoderApp 校验 decoded picture hash")
    parser.add_argument("--decoder-args", type=str, default="-dph 1", help="DecoderApp 附加参数")
    parser.add_argument(
        "--xlsm-report",
        action="store_true",
        help="生成 JVET-hhi.xlsm 报表；默认在模板存在时自动生成",
    )
    parser.add_argument("--no-xlsm-report", action="store_false", dest="xlsm_report", help="禁用 xlsm 报表生成")
    parser.add_argument("--xlsm-template", type=Path, default=None, help="JVET-hhi.xlsm 模板路径；默认尝试 scripts/JVET-hhi.xlsm")
    parser.add_argument("--xlsm-tag", type=str, default=None, help="写入 JVET-hhi.xlsm 的条件 tag，例如 ai/ra/lb；自动模式默认由 --preset 推导")
    parser.add_argument("--overwrite", action="store_true", help="明确允许重跑并覆盖本目录任务；默认仅复用指纹匹配的成功结果")
    parser.add_argument("--allow-missing-input", action="store_true", help="允许输入视频缺失时仍完成任务规划；通常仅用于 --dry-run")
    parser.add_argument("--dry-run", action="store_true", help="只打印任务规划，不实际运行")
    parser.set_defaults(xlsm_report=None)

    args = parser.parse_args(argv)
    if args.jobs < 1 or args.retry_failed < 0:
        parser.error("--jobs must be >=1 and --retry-failed must be >=0")
    args.encoder = args.encoder.resolve()
    args.decoder = args.decoder.resolve()
    args.input_dir = args.input_dir.resolve()
    if args.out_dir is None:
        args.out_dir = (repo_root / "runs" / "batch_test" / _now_tag()).resolve()
    else:
        args.out_dir = args.out_dir.resolve()

    input_index = _build_basename_index(args.input_dir)
    try:
        if args.run and args.hhi_run:
            raise ValueError("--run cannot be combined with --hhi-run")
        normal_mode = bool(
            args.manifest is not None
            or args.local_preset is not None
            or args.preset_cfg is not None
            or args.cfg
            or args.sequence_cfg
            or args.all_sequences
        )
        hhi_mode = bool(args.hhi or args.run or args.hhi_run or args.hhi_ini is not None or args.classes is not None or not normal_mode)
        if hhi_mode and args.manifest is not None:
            raise ValueError("--manifest cannot be combined with HHI mode")
        if args.manifest is not None:
            jobs = _plan_from_manifest(
                repo_root=repo_root,
                manifest=args.manifest.resolve(),
                input_index=input_index,
                encoder=args.encoder,
                decoder=args.decoder,
                run_dir=args.out_dir,
                default_extra_args=shlex.split(args.extra_args),
                default_decoder_args=shlex.split(args.decoder_args),
                default_decode_md5=bool(args.decode_md5),
                default_xlsm_tag=args.xlsm_tag.lower() if args.xlsm_tag else None,
                overwrite=bool(args.overwrite),
                allow_missing_input=bool(args.allow_missing_input),
            )
        elif hhi_mode:
            jobs = _plan_hhi(args, repo_root, input_index)
        else:
            jobs = _plan_auto(args, repo_root, input_index)
        identity_files = tuple(p.resolve() for p in args.identity_file)
        if any(not p.is_file() for p in identity_files):
            raise ValueError("--identity-file must exist")
        jobs = [replace(job, write_recon=args.write_recon, identity_files=identity_files) for job in jobs]
        outputs: set[Path] = set()
        report_keys: set[tuple] = set()
        for job in jobs:
            _validate_job(job)
            if job.bitstream in outputs:
                raise ValueError(f"duplicate output task: {job.bitstream}")
            outputs.add(job.bitstream)
            key = (job.group, job.xlsm_tag, job.sequence_cfg.stem, job.qp)
            if key in report_keys:
                raise ValueError(f"duplicate sequence/QP within report group: {key}")
            report_keys.add(key)
    except (OSError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    if not jobs:
        print("ERROR: no jobs planned", file=sys.stderr)
        return 2

    experiment = None if args.dry_run else _detect_experiment_line(args.encoder, repo_root)
    print(experiment or "EXPERIMENT: none")
    print(f"Repo       : {repo_root}")
    print(f"Input dir  : {args.input_dir}")
    print(f"Out dir    : {args.out_dir}")
    print(f"Jobs       : {len(jobs)} (parallel={max(1, args.jobs)})")

    if args.dry_run:
        for job in jobs[:50]:
            cfg_text = " ".join(str(p.relative_to(repo_root) if p.is_relative_to(repo_root) else p) for p in job.cfgs)
            seq_text = str(job.sequence_cfg.relative_to(repo_root) if job.sequence_cfg.is_relative_to(repo_root) else job.sequence_cfg)
            frame_text = f", frames={job.frames}" if job.frames is not None else ""
            md5_text = ", decode-md5=on" if job.decode_md5 else ""
            xlsm_text = f", xlsm_tag={job.xlsm_tag}" if job.xlsm_tag else ""
            print(f"- [{job.group}] {job.name} QP{job.qp}: {cfg_text} + {seq_text}, input={job.input_path.name}{frame_text}{md5_text}{xlsm_text}, recon={'on' if job.write_recon else 'off'}, encoder={job.encoder}")
        if len(jobs) > 50:
            print(f"... ({len(jobs) - 50} more)")
        return 0

    missing_inputs = [job.input_path for job in jobs if not job.input_path.is_file()]
    if missing_inputs:
        for path in missing_inputs[:20]:
            print(f"ERROR: input not found: {path}", file=sys.stderr)
        if len(missing_inputs) > 20:
            print(f"ERROR: ... and {len(missing_inputs) - 20} more missing inputs", file=sys.stderr)
        return 2

    for job in jobs:
        for tool in [job.encoder, *([job.decoder] if job.decode_md5 else [])]:
            if not tool.is_file() or not os.access(tool, os.X_OK):
                print(f"ERROR: missing/non-executable tool: {tool}", file=sys.stderr)
                return 2

    _safe_mkdir(args.out_dir)
    results_by_order: dict[int, dict] = {}
    reported: dict[str, tuple] = {}
    # Preflight all identities before launching any work, also recording effective CLI.
    atomic_json(args.out_dir / "task_plan.json", [
        {"order": job.order, "name": job.name, **task_identity(job, _encode_command(job))} for job in jobs
    ])
    interrupted = False
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        future_map = {pool.submit(_run_one, job): job for job in jobs}
        try:
            for future in as_completed(future_map):
                job = future_map[future]
                try:
                    result = future.result()
                except Exception as e:
                    result = {
                        "order": job.order,
                        "group": job.group,
                        "name": job.name,
                        "sequence": job.sequence_cfg.stem,
                        "sequence_cfg": str(job.sequence_cfg),
                        "input": str(job.input_path),
                        "qp": job.qp,
                        "frames": job.frames or "",
                        "bitstream": str(job.bitstream),
                        "recon": str(job.recon) if job.write_recon else "",
                        "encode_status": "exception",
                        "status": "exception",
                        "encode_returncode": "",
                        "returncode": "",
                        "encode_seconds": "",
                        "seconds": "",
                        "decode_status": "",
                        "decode_returncode": "",
                        "decode_seconds": "",
                        "error_info": f"exception: {e}",
                        "time": time.asctime(time.localtime()),
                        "xlsm_tag": job.xlsm_tag or "",
                        "encode_log": str(job.encode_log),
                        "decode_log": str(job.decode_log) if job.decode_md5 else "",
                    }
                results_by_order[job.order] = result
                _flush_progress(jobs, results_by_order, args, repo_root, reported)
                status = result.get("encode_status")
                if successful(result):
                    print(f"{status.upper():<14}: {job.name} QP{job.qp}")
                else:
                    print(f"FAIL          : {job.name} QP{job.qp} log={job.encode_log}", file=sys.stderr)
        except KeyboardInterrupt:
            interrupted = True
            for fut in future_map:
                fut.cancel()
            print("Interrupted: canceled remaining jobs.", file=sys.stderr)

    if not interrupted and args.retry_failed > 0:
        retry_jobs = [
            replace(job, overwrite=True)
            for job in jobs
            if not successful(results_by_order.get(job.order, {}))
            and results_by_order.get(job.order, {}).get("fingerprint")
        ]
        for attempt in range(1, args.retry_failed + 1):
            if not retry_jobs:
                break
            print(f"RETRY: {len(retry_jobs)} failed jobs (attempt {attempt}/{args.retry_failed})", file=sys.stderr)
            next_retry: list[TestJob] = []
            with ThreadPoolExecutor(max_workers=1) as pool:
                retry_future_map = {pool.submit(_run_one, job): job for job in retry_jobs}
                for future in as_completed(retry_future_map):
                    job = retry_future_map[future]
                    result = future.result()
                    results_by_order[job.order] = result
                    _flush_progress(jobs, results_by_order, args, repo_root, reported)
                    if not successful(result):
                        next_retry.append(job)
            retry_jobs = next_retry

    rows = [results_by_order[i] for i in sorted(results_by_order)]
    summary_path = args.out_dir / "summary.csv"
    _write_summary(summary_path, rows)
    print(f"Summary    : {summary_path}")
    _flush_progress(jobs, results_by_order, args, repo_root, reported)

    if interrupted:
        return 130
    failed = [r for r in rows if not successful(r)]
    return 0 if not failed else 3


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
