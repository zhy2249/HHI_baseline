#!/usr/bin/env python3
"""Four-point PCHIP BD-rate and single-QP substitution result registration.

The numerical method matches ``bdrate``/``bdrint`` in the supplied HHI workbook:
integrate log10(rate) over the common PSNR interval, then return percent rate
change. This module does not run Excel/VBA, encoding, or decoding. Callers must
establish that the reference and test measurements are comparable beforehand.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


QPS = (22, 27, 32, 37)
COMPONENTS = {"y": "ypsnr", "u": "upsnr", "v": "vpsnr"}
METRICS = (*COMPONENTS, "weighted_611")
RD_FIELDS = ("kbps", *COMPONENTS.values())


class BDRateError(ValueError):
    """The requested curve or comparison cannot yield a valid BD-rate."""


def _finite(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise BDRateError(f"{label}: missing or nonnumeric value") from error
    if isinstance(value, bool) or not math.isfinite(number):
        raise BDRateError(f"{label}: nonfinite or boolean value")
    return number


def _endpoint(h1: float, h2: float, delta1: float, delta2: float) -> float:
    slope = ((2 * h1 + h2) * delta1 - h1 * delta2) / (h1 + h2)
    if slope * delta1 < 0:
        return 0.0
    if delta1 * delta2 < 0 and abs(slope) > abs(3 * delta1):
        return 3 * delta1
    return slope


def pchip_integral(x: Sequence[float], y: Sequence[float], low: float, high: float) -> float:
    """Integrate a four-knot monotone PCHIP; never extrapolate.

    BD-rate curves have strictly increasing PSNR and log-rate. Rejecting other
    curves also avoids the supplied VBA's division by zero for flat secants.
    """
    if len(x) != 4 or len(y) != 4:
        raise BDRateError("PCHIP requires exactly four points")
    xs = [_finite(value, "PSNR") for value in x]
    ys = [_finite(value, "log-rate") for value in y]
    if any(xs[i + 1] <= xs[i] for i in range(3)):
        raise BDRateError("PSNR must be strictly increasing")
    if any(ys[i + 1] <= ys[i] for i in range(3)):
        raise BDRateError("rate must be strictly increasing with PSNR")
    low, high = _finite(low, "low"), _finite(high, "high")
    if not xs[0] <= low < high <= xs[-1]:
        raise BDRateError("integration interval must be nonempty and within the curve")
    h = [xs[i + 1] - xs[i] for i in range(3)]
    delta = [(ys[i + 1] - ys[i]) / h[i] for i in range(3)]
    derivatives = [_endpoint(h[0], h[1], delta[0], delta[1])]
    for i in (1, 2):
        left_weight = 2 * h[i] + h[i - 1]
        right_weight = h[i] + 2 * h[i - 1]
        derivatives.append((left_weight + right_weight) /
                           (left_weight / delta[i - 1] + right_weight / delta[i]))
    derivatives.append(_endpoint(h[-1], h[-2], delta[-1], delta[-2]))
    pieces = []
    for i in range(3):
        a, b = max(low, xs[i]), min(high, xs[i + 1])
        if b <= a:
            continue
        a, b = a - xs[i], b - xs[i]
        quadratic = (3 * delta[i] - 2 * derivatives[i] - derivatives[i + 1]) / h[i]
        cubic = (derivatives[i] - 2 * delta[i] + derivatives[i + 1]) / h[i] ** 2
        pieces.append((b - a) * ys[i] + (b * b - a * a) * derivatives[i] / 2 +
                      (b ** 3 - a ** 3) * quadratic / 3 + (b ** 4 - a ** 4) * cubic / 4)
    return math.fsum(pieces)


def _ordered_curve(rates: Sequence[float], psnr: Sequence[float]) -> tuple[list[float], list[float]]:
    if len(rates) != 4 or len(psnr) != 4:
        raise BDRateError("BD-rate requires four rate/PSNR points per curve")
    points = sorted((_finite(quality, "PSNR"), _finite(rate, "rate"))
                    for rate, quality in zip(rates, psnr))
    if any(rate <= 0 for _, rate in points):
        raise BDRateError("rate must be positive")
    x = [quality for quality, _ in points]
    y = [math.log10(rate) for _, rate in points]
    if any(x[i + 1] <= x[i] or y[i + 1] <= y[i] for i in range(3)):
        raise BDRateError("PSNR and rate must be strictly increasing together")
    return x, y


def bd_rate_percent(reference_rates: Sequence[float], reference_psnr: Sequence[float],
                    test_rates: Sequence[float], test_psnr: Sequence[float]) -> float:
    """Return percent BD-rate; a negative number denotes a rate reduction."""
    ref_x, ref_y = _ordered_curve(reference_rates, reference_psnr)
    test_x, test_y = _ordered_curve(test_rates, test_psnr)
    low, high = max(ref_x[0], test_x[0]), min(ref_x[-1], test_x[-1])
    if high <= low:
        raise BDRateError("reference and test have no nonempty PSNR overlap")
    average = (pchip_integral(test_x, test_y, low, high) -
               pchip_integral(ref_x, ref_y, low, high)) / (high - low)
    try:
        result = 100 * math.expm1(average * math.log(10))
    except OverflowError as error:
        raise BDRateError("BD-rate overflow") from error
    if not math.isfinite(result):
        raise BDRateError("BD-rate is not finite")
    return result


def _key(row: Mapping[str, Any]) -> tuple[str, str, int]:
    sequence = str(row.get("sequence") or row.get("seq") or "").strip()
    tag = str(row.get("tag") or "").strip()
    try:
        qp = int(row["qp"])
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise BDRateError("record requires an integer QP") from error
    if not sequence or not tag or isinstance(row["qp"], bool) or str(row["qp"]).strip() != str(qp):
        raise BDRateError("record requires sequence, tag, and an exact integer QP")
    return tag, sequence, qp


def _has_result(row: Mapping[str, Any]) -> bool:
    return any(row.get(field) not in (None, "") for field in RD_FIELDS) or bool(row.get("error_info"))


def _point(row: Mapping[str, Any], role: str) -> dict[str, Any]:
    status = str(row.get("error_info") or "").strip().lower()
    if role == "test" and status != "pass":
        raise BDRateError(f"test point is not marked pass: {status or 'missing status'}")
    if role == "reference" and status not in ("", "pass"):
        raise BDRateError(f"reference point has failure status: {status}")
    result = dict(row)
    for field in RD_FIELDS:
        result[field] = _finite(row.get(field), f"{role} {field}")
    if result["kbps"] <= 0:
        raise BDRateError(f"{role} rate must be positive")
    return result


def _check_qp_order(points: Mapping[int, Mapping[str, Any]], qps: Sequence[int]) -> None:
    for field in RD_FIELDS:
        if any(points[a][field] <= points[b][field] for a, b in zip(qps, qps[1:])):
            raise BDRateError(f"{field} must strictly decrease as QP increases")


def compare_curves(reference: Mapping[int, Mapping[str, Any]], test: Mapping[int, Mapping[str, Any]],
                   qps: Sequence[int] = QPS) -> dict[str, Any]:
    """Compare complete QP-indexed curves; report each component and its interval."""
    if set(reference) != set(qps) or set(test) != set(qps):
        raise BDRateError("complete reference and test curves are required")
    _check_qp_order(reference, qps)
    _check_qp_order(test, qps)
    rates_ref = [reference[qp]["kbps"] for qp in qps]
    rates_test = [test[qp]["kbps"] for qp in qps]
    rates = {}
    intervals = {}
    for name, field in COMPONENTS.items():
        qualities_ref = [reference[qp][field] for qp in qps]
        qualities_test = [test[qp][field] for qp in qps]
        rates[name] = bd_rate_percent(rates_ref, qualities_ref, rates_test, qualities_test)
        intervals[name] = [max(min(qualities_ref), min(qualities_test)),
                           min(max(qualities_ref), max(qualities_test))]
    rates["weighted_611"] = (6 * rates["y"] + rates["u"] + rates["v"]) / 8
    return {"bd_rate": rates, "psnr_intervals": intervals}


def _aggregate(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    valid = [row for row in rows if row["status"] == "ok"]

    def mean(items: Sequence[Mapping[str, Any]]) -> dict[str, float] | None:
        return ({name: math.fsum(item["bd_rate"][name] for item in items) / len(items)
                 for name in METRICS} if items else None)

    classes = defaultdict(list)
    unknown = []
    for row in valid:
        if row["class"] is None:
            unknown.append(row["sequence"])
        else:
            classes[row["class"]].append(row)
    class_means = {name: {"sequence_count": len(items), "sequences": sorted(row["sequence"] for row in items),
                          "bd_rate": mean(items)} for name, items in sorted(classes.items())}
    return {
        "sequence_equal_weight": {"sequence_count": len(valid),
                                  "sequences": sorted(row["sequence"] for row in valid),
                                  "bd_rate": mean(valid)},
        "by_class": class_means,
        "class_equal_weight": {"class_count": len(classes), "classes": sorted(classes),
                               "unclassified_sequences": sorted(unknown),
                               "bd_rate": mean(list(class_means.values())) if not unknown else None},
        "excluded_sequences": [{"sequence": row["sequence"], "status": row["status"],
                                "reason": row.get("reason")} for row in rows if row["status"] != "ok"],
    }


def build_report(reference_rows: Iterable[Mapping[str, Any]], test_rows: Iterable[Mapping[str, Any]],
                 sequence_classes: Mapping[str, str] | None = None,
                 qps: Sequence[int] = QPS) -> dict[str, Any]:
    """Register complete-curve and one-QP-at-a-time BD-rate without filling gaps.

    Only sequence/configuration pairs with at least one supplied result receive
    BD entries. Empty CSV task slots remain in coverage counts. Unused malformed
    reference curves (e.g. duplicate LP layout keys) cannot block unrelated RA/LB.
    Reference status may be absent because the caller has confirmed its origin;
    test points must be explicitly marked ``pass``.
    """
    qps = tuple(sorted(qps))
    if len(qps) != 4 or len(set(qps)) != 4 or any(isinstance(qp, bool) or not isinstance(qp, int) for qp in qps):
        raise BDRateError("specify exactly four distinct integer QPs")
    sequence_classes = sequence_classes or {}
    references = defaultdict(list)
    for row in reference_rows:
        references[_key(row)].append(row)
    tests = {}
    coverage = defaultdict(lambda: {"task_slots": 0, "result_points": 0, "pass_points": 0, "empty_slots": 0})
    groups = set()
    for row in test_rows:
        tag, sequence, qp = key = _key(row)
        if key in tests:
            raise BDRateError(f"duplicate test key: {key}")
        tests[key] = row
        coverage[tag]["task_slots"] += 1
        if _has_result(row):
            groups.add((tag, sequence))
            coverage[tag]["result_points"] += 1
            if str(row.get("error_info") or "").strip().lower() == "pass":
                coverage[tag]["pass_points"] += 1
        else:
            coverage[tag]["empty_slots"] += 1
    overall, per_qp, raw_reference = [], [], []
    for tag, sequence in sorted(groups):
        base = {"tag": tag, "sequence": sequence, "class": sequence_classes.get(sequence)}
        ref_curve, test_curve, problems = {}, {}, {}
        reference_error = None
        for qp in qps:
            key = tag, sequence, qp
            matches = references.get(key, [])
            try:
                if len(matches) != 1:
                    raise BDRateError(f"reference QP{qp}: expected one row, found {len(matches)}")
                ref_curve[qp] = _point(matches[0], "reference")
                raw_reference.append(ref_curve[qp])
            except BDRateError as error:
                reference_error = str(error)
            row = tests.get(key)
            if row is None or not _has_result(row):
                problems[qp] = ("missing_test", f"test QP{qp} is missing")
                continue
            try:
                test_curve[qp] = _point(row, "test")
            except BDRateError as error:
                problems[qp] = ("invalid_test", str(error))
        if reference_error is None:
            try:
                _check_qp_order(ref_curve, qps)
            except BDRateError as error:
                reference_error = str(error)

        def result(curve: Mapping[int, Mapping[str, Any]], failure: tuple[str, str] | None = None) -> dict[str, Any]:
            row = {**base, "status": "ok", "bd_rate": None, "psnr_intervals": None}
            if reference_error:
                row.update(status="invalid_reference", reason=reference_error)
            elif failure:
                row.update(status=failure[0], reason=failure[1])
            else:
                try:
                    row.update(compare_curves(ref_curve, curve, qps))
                except BDRateError as error:
                    row.update(status="invalid_curve", reason=str(error))
            return row

        missing = [qp for qp in qps if qp not in test_curve]
        failure = (("incomplete_test", "; ".join(problems[qp][1] for qp in missing)) if missing else None)
        overall.append({**result(test_curve, failure), "measured_qps": sorted(test_curve), "missing_qps": missing})
        for qp in qps:
            # Every candidate starts from the full anchor. Other test points are
            # intentionally excluded, even when available in the input batch.
            substituted = dict(ref_curve)
            if qp in test_curve:
                substituted[qp] = test_curve[qp]
            per_qp.append({**result(substituted, problems.get(qp)), "qp": qp})

    aggregates = {}
    for tag in sorted(coverage):
        aggregates[tag] = {"overall": _aggregate([row for row in overall if row["tag"] == tag]),
                           "per_qp": {str(qp): _aggregate([row for row in per_qp
                                                           if row["tag"] == tag and row["qp"] == qp]) for qp in qps}}
    return {"method": "four_point_pchip_log10_rate_over_common_psnr_interval", "unit": "percent",
            "method_reference": "HHI results workbook Module1 bdrate/bdrint; not bdrateOld",
            "component_weighting": "(6 * BD_Y + BD_U + BD_V) / 8", "qps": list(qps),
            "qp_substitution": "replace only the selected QP's complete RD tuple; retain all other reference tuples",
            "coverage": dict(sorted(coverage.items())), "raw_reference": raw_reference,
            "overall": overall, "per_qp": per_qp, "aggregates": aggregates}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, type=Path, help="Test result CSV, including a trailing header")
    parser.add_argument("--reference", required=True, type=Path, help="Comparable workbook's Reference sheet")
    parser.add_argument("--class-map", type=Path, help="Optional JSON object mapping sequence names to class labels")
    parser.add_argument("--output", required=True, type=Path, help="New local JSON output; existing files are not overwritten")
    args = parser.parse_args()
    from import_experiment_results import read_csv, read_raw_sheet
    classes = json.loads(args.class_map.read_text(encoding="utf-8")) if args.class_map else None
    if classes is not None and (not isinstance(classes, dict) or
                                any(not isinstance(key, str) or not isinstance(value, str) for key, value in classes.items())):
        parser.error("class-map must be a JSON object of string sequence names to string class labels")
    if args.output.exists():
        parser.error(f"output already exists: {args.output}")
    report = build_report(read_raw_sheet(args.reference, "Reference"), read_csv(args.csv), classes)
    report["sources"] = {name: {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                         for name, path in (("csv", args.csv), ("reference_workbook", args.reference),
                                            ("class_map", args.class_map)) if path is not None}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(report, output, indent=2, ensure_ascii=False, allow_nan=False)
        output.write("\n")
    print(f"Registered {sum(row['status'] == 'ok' for row in report['overall'])} complete curves and "
          f"{sum(row['status'] == 'ok' for row in report['per_qp'])} QP substitutions: {args.output}")


if __name__ == "__main__":
    main()
