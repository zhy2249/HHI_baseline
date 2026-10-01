"""Read-only task identity and atomic checkpoints; never executes a codec."""
from __future__ import annotations

import hashlib
import json
import os
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1024)
def _digest_cached(path: str, size: int, mtime_ns: int, ctime_ns: int) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_identity(path: Path, *, content: bool = True) -> dict:
    path = path.resolve()
    stat = path.stat()
    result = {"path": str(path), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    if content:
        result["sha256"] = _digest_cached(str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    return result


def task_identity(job, command: list[str]) -> dict:
    files = [job.encoder, *job.cfgs, job.sequence_cfg, *job.identity_files,
             Path(__file__), Path(__file__).with_name("batch_test.py")]
    if job.decode_md5:
        files.append(job.decoder)
    payload = {
        "schema": 1, "command": command, "cwd": str(job.cwd), "group": job.group,
        "decode_md5": job.decode_md5, "decoder_args": job.decoder_args,
        "files": [file_identity(p) for p in dict.fromkeys(files)],
        # Avoid scanning multi-GB YUV files on every launch. This is NOT a content hash.
        "input_stat": file_identity(job.input_path, content=False),
    }
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return {"fingerprint": fingerprint, "identity": payload}


def atomic_json(path: Path, value: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def successful(row: dict) -> bool:
    return row.get("error_info") == "pass" and row.get("encode_status") in {"ok", "resumed"}


def load_success(job, fingerprint: str) -> dict | None:
    try:
        saved = json.loads(job.bitstream.with_suffix(".success.json").read_text(encoding="utf-8"))
        if saved["fingerprint"] != fingerprint or not successful(saved["result"]):
            return None
        paths = [job.bitstream, job.encode_log]
        if job.decode_md5:
            paths.append(job.decode_log)
        if job.write_recon:
            paths.append(job.recon)
        # Logs and bitstreams must still match; recon uses stat only to avoid GB scans.
        current = [file_identity(p, content=p != job.recon) for p in paths]
        if current != saved["outputs"]:
            return None
        return dict(saved["result"], encode_status="resumed", status="resumed")
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save_success(job, identity: dict, result: dict) -> None:
    if not successful(result):
        return
    paths = [job.bitstream, job.encode_log]
    if job.decode_md5:
        paths.append(job.decode_log)
    if job.write_recon:
        paths.append(job.recon)
    atomic_json(job.bitstream.with_suffix(".success.json"), {
        **identity, "result": result,
        "outputs": [file_identity(p, content=p != job.recon) for p in paths],
    })
