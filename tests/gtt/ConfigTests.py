#!/usr/bin/env python3
"""Exercise the real EncAppCfg through GttConfigProbe; never launch a codec app.

Run this against each build to verify its own macro defaults. CLI mode coverage
within one executable is not evidence for other compile-time default values.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile


def fields(output: str, prefix: str) -> dict[str, str] | None:
    lines = [line for line in output.splitlines() if line.startswith(prefix)]
    if not lines:
        return None
    if len(lines) != 1:
        raise ValueError(f"Expected one {prefix} line, found {len(lines)}")
    return dict(re.findall(r"([A-Za-z_]+)=([^\s]+)", lines[0]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", type=Path, required=True, help="Path to the parser-only GttConfigProbe executable")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path, help="Keep case logs and summary.json here; existing summary is never overwritten")
    args = parser.parse_args()
    probe = args.probe.resolve()
    repo = args.repo_root.resolve()
    # Prevent accidentally substituting EncoderApp/DecoderApp in a CTest invocation.
    if probe.name not in {"GttConfigProbe", "GttConfigProbe.exe"} or not probe.is_file():
        parser.error("--probe must point to the parser-only executable named GttConfigProbe")
    baseline_cfg = repo / "cfg/encoder_intra_nx2High.cfg"
    if not baseline_cfg.is_file():
        parser.error("--repo-root does not contain cfg/encoder_intra_nx2High.cfg")
    if args.output:
        output_dir = args.output.resolve()
        if (output_dir / "summary.json").exists():
            parser.error("Output summary already exists; choose a new --output directory")
        output_dir.mkdir(parents=True, exist_ok=True)
    else:
        output_dir = None

    with tempfile.TemporaryDirectory(prefix="gtt-config-tests-") as temporary:
        work = Path(temporary)
        unused_input = work / "never-created-input.yuv"
        unused_bitstream = work / "never-created-output.bin"
        base = [
            str(probe), "-c", str(baseline_cfg), f"--InputFile={unused_input}",
            "--SourceWidth=64", "--SourceHeight=64", "--FrameRate=30", "--FramesToBeEncoded=1",
            "--InputBitDepth=10", "--InternalBitDepth=10", f"--BitstreamFile={unused_bitstream}", "--ReconFile=",
        ]

        def invoke(arguments: list[str]) -> tuple[int, str]:
            process = subprocess.run(base + arguments, cwd=repo, text=True, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, timeout=30, check=False)
            if unused_input.exists() or unused_bitstream.exists():
                raise RuntimeError("Parser-only probe unexpectedly created a video/bitstream file")
            return process.returncode, process.stdout

        code, identity_log = invoke(["--GTT4x4=0", "--GTT=0"])
        identity = fields(identity_log, "PARSE_ONLY_RESULT ")
        if code != 0 or identity is None or identity.get("accepted") != "1":
            raise RuntimeError("Parser-only baseline fixture failed:\n" + identity_log)
        for key in ("compile", "default_sub", "stats_default", "old_compile"):
            if key not in identity:
                raise RuntimeError(f"Probe lacks required identity field: {key}")
        compiled = int(identity["compile"])
        default = int(identity["default_sub"])
        stats_default = int(identity["stats_default"])
        old_compiled = int(identity["old_compile"])
        if default not in range(7) or compiled != int(default != 0) or stats_default not in (0, 1) or old_compiled not in (0, 1):
            raise RuntimeError(f"Inconsistent compile-time identity: {identity}")
        if output_dir:
            (output_dir / "identity.log").write_text(identity_log)

        configs: dict[str, Path] = {}
        contents = {"off": "GTT: 0\n", "one": "GTT: 1\n", "two": "GTT: 2\n", "five": "GTT: 5\n",
                    "invalid": "GTT: 7\n", "stats0": "GTTStats: 0\n", "stats1": "GTTStats: 1\n",
                    "both": "GTT4x4: 1\nGTT: 1\n", "old": "GTT4x4: 1\nGTT: 0\n"}
        for name, content in contents.items():
            configs[name] = work / f"{name}.cfg"
            configs[name].write_text(content)

        cases: list[dict] = []

        def add(name: str, arguments: list[str], sub: int | None = None, stats: int | None = None,
                legacy: int = 0, accepted: bool | None = None, diagnostic: str | None = None) -> None:
            selected = default if sub is None else sub
            if accepted is None:
                accepted = (selected == 0 or bool(compiled)) and (legacy == 0 or bool(old_compiled)) and not (selected and legacy)
            if not accepted and diagnostic is None:
                diagnostic = "cannot both be enabled" if selected and legacy else "HHI_GTT4X4=0" if legacy else "HHI_GTT=0"
            cases.append({"name": name, "arguments": arguments, "accepted": bool(accepted), "sub": selected,
                          "stats": stats_default if stats is None else stats, "legacy": legacy, "diagnostic": diagnostic})

        # The raw default includes the old macro; the new-default case isolates the new entry.
        add("raw_macro_defaults", [], legacy=old_compiled)
        add("new_macro_default", ["--GTT4x4=0"])
        for mode in range(7):
            add(f"cli_mode_{mode}", ["--GTT4x4=0", f"--GTT={mode}"], sub=mode)
        for mode in range(1, 7):
            config = repo / f"cfg/experiments/gtt_exp2_{mode}.cfg"
            add(f"registered_config_{mode}", ["-c", str(config)], sub=mode, stats=1)
        for value in (0, 1):
            add(f"stats_cli_{value}", ["--GTT4x4=0", f"--GTTStats={value}"], stats=value)
            add(f"stats_cfg_{value}", ["--GTT4x4=0", "-c", str(configs[f"stats{value}"])], stats=value)
            add(f"off_stats_cli_{value}", ["--GTT4x4=0", "--GTT=0", f"--GTTStats={value}"], sub=0, stats=value)
            add(f"stats_cfg_{value}_cli_{1-value}",
                ["--GTT4x4=0", "-c", str(configs[f"stats{value}"]), f"--GTTStats={1-value}"], stats=1-value)
            add(f"stats_cli_{value}_cfg_{1-value}",
                ["--GTT4x4=0", f"--GTTStats={value}", "-c", str(configs[f"stats{1-value}"])], stats=1-value)
        add("cfg_two_cli_three", ["--GTT4x4=0", "-c", str(configs["two"]), "--GTT=3"], sub=3)
        add("cli_three_cfg_two", ["--GTT4x4=0", "--GTT=3", "-c", str(configs["two"])], sub=2)
        add("cfg_five_cli_off", ["--GTT4x4=0", "-c", str(configs["five"]), "--GTT=0"], sub=0)
        add("cli_off_cfg_five", ["--GTT4x4=0", "--GTT=0", "-c", str(configs["five"])], sub=5)
        add("cfg_one_cli_off", ["--GTT4x4=0", "-c", str(configs["one"]), "--GTT=0"], sub=0)
        add("cli_one_cfg_off", ["--GTT4x4=0", "--GTT=1", "-c", str(configs["off"])], sub=0)
        add("cfg_invalid_cli_off", ["--GTT4x4=0", "-c", str(configs["invalid"]), "--GTT=0"], sub=0)
        add("cli_seven_then_off", ["--GTT4x4=0", "--GTT=7", "--GTT=0"], sub=0)
        add("cli_negative_then_off", ["--GTT4x4=0", "--GTT=-1", "--GTT=0"], sub=0)
        for name, value in (("negative", "-1"), ("seven", "7")):
            add(f"invalid_mode_{name}", ["--GTT4x4=0", f"--GTT={value}"], accepted=False,
                diagnostic="GTT must be a registered mode")
        for name, value in (("suffix", "2x"), ("fraction", "2.5"), ("empty", ""), ("word", "true"),
                            ("overflow", "999999999999999999999999"), ("space", "2 3")):
            add(f"invalid_mode_{name}", ["--GTT4x4=0", f"--GTT={value}"], accepted=False, diagnostic="Option Parse Failure")
        add("lexical_error_not_overridden", ["--GTT4x4=0", "--GTT=2x", "--GTT=0"],
            accepted=False, diagnostic="Option Parse Failure")
        for name, value in (("two", "2"), ("negative", "-1"), ("suffix", "1x"), ("fraction", "0.5"), ("empty", "")):
            add(f"invalid_stats_{name}", ["--GTT4x4=0", "--GTT=0", f"--GTTStats={value}"],
                accepted=False, diagnostic="Option Parse Failure")
        add("legacy_only", ["--GTT=0", "--GTT4x4=1"], sub=0, legacy=1)
        add("both_cli_rejected", ["--GTT=1", "--GTT4x4=1"], sub=1, legacy=1)
        add("both_cfg_old_disabled", ["-c", str(configs["both"]), "--GTT4x4=0"], sub=1)
        add("both_cfg_new_disabled", ["-c", str(configs["both"]), "--GTT=0"], sub=0, legacy=1)
        add("legacy_request_overridden", ["--GTT4x4=1", "--GTT4x4=0", "--GTT=0"], sub=0)
        # Baseline itself requires Log2MaxTbSize>=5. Do not pretend a max-TU=4
        # rejection originates from GTT, or weaken that pre-existing requirement.
        add("off_lowest_legal_max_tu", ["--GTT4x4=0", "--GTT=0", "--Log2MaxTbSize=5"], sub=0)
        add("off_dq0_bitdepth8", ["--GTT4x4=0", "--GTT=0", "--DepQuant=0", "--InputBitDepth=8",
                                 "--InternalBitDepth=8", "--Log2MaxTbSize=5"], sub=0)

        results = []
        expected_modes = {0: (0, 0, 0), 1: (8, 2, 0), 2: (16, 4, 0), 3: (32, 8, 0),
                          4: (16, 4, 1), 5: (32, 8, 1), 6: (4, 1, 0)}
        for case in cases:
            code, log = invoke(case["arguments"])
            parsed = fields(log, "PARSE_ONLY_RESULT ")
            identity_line = fields(log, "EXPERIMENT: GTT ")
            errors = []
            accepted = code == 0 and parsed is not None and parsed.get("accepted") == "1"
            if accepted != case["accepted"]:
                errors.append(f"acceptance {accepted}, expected {case['accepted']} (exit {code})")
            if case["accepted"] and accepted:
                for key in ("sub", "stats", "legacy"):
                    if int(parsed[key]) != case[key]:
                        errors.append(f"{key}={parsed[key]}, expected {case[key]}")
                if identity_line is None:
                    errors.append("accepted config missing GTT startup identity")
                else:
                    size, mask, group = expected_modes[case["sub"]]
                    expected = {"sub": case["sub"], "compile": compiled, "default_sub": default,
                                "enabled": int(case["sub"] != 0), "version": 3 if case["sub"] else 0,
                                "sizes": size, "mask": mask, "groupMode": group,
                                "stats_default": stats_default, "stats": case["stats"]}
                    for key, value in expected.items():
                        if key not in identity_line or int(identity_line[key], 0) != value:
                            errors.append(f"startup {key}={identity_line.get(key)}, expected {value}")
            if not case["accepted"] and not accepted:
                if code not in (1, 2, 3):
                    errors.append(f"rejection must be a controlled error, got exit {code}")
                if case["diagnostic"] and case["diagnostic"] not in log:
                    errors.append(f"missing expected rejection diagnostic: {case['diagnostic']}")
            if parsed:
                for key in ("compile", "default_sub", "stats_default", "old_compile"):
                    if parsed[key] != identity[key]:
                        errors.append(f"compile identity changed in case: {key}")
            record = {**case, "returncode": code, "observed": parsed, "startup": identity_line,
                      "passed": not errors, "errors": errors}
            if output_dir:
                path = output_dir / f"{case['name']}.log"
                path.write_text(log)
                record["log"] = str(path)
            results.append(record)
            if errors:
                print(f"FAIL {case['name']}: {'; '.join(errors)}")
        summary = {
            "kind": "parameter-parser-only", "codec_run": False, "probe": str(probe),
            "probe_sha256": hashlib.sha256(probe.read_bytes()).hexdigest(),
            "baseline_config": str(baseline_cfg),
            "baseline_config_sha256": hashlib.sha256(baseline_cfg.read_bytes()).hexdigest(),
            "identity": identity, "cases": len(results), "passed": sum(item["passed"] for item in results),
            "results": results,
        }
        if output_dir:
            (output_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(f"GTT config tests: {summary['passed']}/{summary['cases']} passed; "
              f"default_sub={default}, stats_default={stats_default}, old_compile={old_compiled}")
        return 0 if summary["passed"] == summary["cases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
