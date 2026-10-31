"""Numerical and registration checks; no workbook mutation or codec process."""
import copy
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from experiment_bd_rate import BDRateError, QPS, bd_rate_percent, build_report, compare_curves, pchip_integral


def curve(sequence="Clip", tag="ra", factor=1.0, status="pass"):
    return [dict(sequence=sequence, qp=qp, tag=tag, kbps=rate * factor,
                 ypsnr=quality, upsnr=quality + 1.5, vpsnr=quality + 3,
                 error_info=status) for qp, rate, quality in
            zip(QPS, (1800, 900, 400, 180), (43.0, 39.0, 35.0, 31.0))]


class PchipTests(unittest.TestCase):
    def test_identity_and_uniform_rate_scaling(self):
        rates, psnr = [200, 450, 950, 2100], [29, 33, 38, 44]
        self.assertAlmostEqual(bd_rate_percent(rates, psnr, rates, psnr), 0.0, places=12)
        for factor in (0.6, 0.98, 1.02, 1.7):
            with self.subTest(factor=factor):
                self.assertAlmostEqual(bd_rate_percent(rates, psnr, [factor * r for r in rates], psnr),
                                       100 * (factor - 1), places=10)

    def test_nonuniform_knots_exact_linear_integral_and_overlap(self):
        x = [27.0, 28.5, 34.0, 43.0]
        y = [0.1 * q - 1 for q in x]
        low, high = 29.0, 39.0
        expected = 0.05 * (high * high - low * low) - (high - low)
        self.assertAlmostEqual(pchip_integral(x, y, low, high), expected, places=12)
        # Both curves are log-linear with different quality endpoints. The
        # overlapping interval still gives the exact multiplicative rate shift.
        second_x = [28.0, 30.0, 36.0, 45.0]
        self.assertAlmostEqual(bd_rate_percent([10 ** v for v in y], x,
                                               [1.1 * 10 ** (0.1 * q - 1) for q in second_x], second_x),
                               10.0, places=11)

    def test_piecewise_cubic_quadrature_with_independent_hermite_basis(self):
        # Slopes are precomputed from the supplied workbook's VBA pchipend and
        # weighted harmonic formula for these nonuniform monotone knots.
        x = [0.0, 1.0, 3.0, 6.0]
        y = [0.0, 1.0, 1.5, 3.0]
        slopes = [1.25, 3 / 7, 15 / 46, 0.65]
        low, high = 0.25, 5.0
        expected = 0.0
        # Two-node Gauss-Legendre integrates each cubic exactly. Evaluating
        # Hermite basis differs from production's monomial antiderivative.
        for index in range(3):
            a, b = max(low, x[index]), min(high, x[index + 1])
            if b <= a:
                continue
            h = x[index + 1] - x[index]
            for sign in (-1, 1):
                point = (a + b) / 2 + sign * (b - a) / (2 * math.sqrt(3))
                t = (point - x[index]) / h
                value = ((2 * t ** 3 - 3 * t ** 2 + 1) * y[index] +
                         (t ** 3 - 2 * t ** 2 + t) * h * slopes[index] +
                         (-2 * t ** 3 + 3 * t ** 2) * y[index + 1] +
                         (t ** 3 - t ** 2) * h * slopes[index + 1])
                expected += (b - a) * value / 2
        self.assertAlmostEqual(pchip_integral(x, y, low, high), expected, places=12)

    def test_clipped_endpoint_slope(self):
        # The unconstrained first slope is negative: PCHIP clips it to zero.
        # Integral of a Hermite segment is h*(y0+y1)/2+h^2*(d0-d1)/12.
        x, y = [0, 1, 2, 3], [0, 0.1, 2.1, 4.1]
        d1 = 6 / (3 / 0.1 + 3 / 2)
        self.assertAlmostEqual(pchip_integral(x, y, 0, 1), 0.05 - d1 / 12, places=12)

    def test_rejects_incomplete_nonfinite_nonmonotone_and_no_overlap(self):
        valid_r, valid_q = [100, 200, 400, 800], [30, 34, 38, 42]
        cases = [([100, 200, 400], valid_q),
                 ([0, 200, 400, 800], valid_q),
                 ([100, 200, float("nan"), 800], valid_q),
                 (valid_r, [30, 34, 34, 42]),
                 ([100, 400, 200, 800], valid_q),
                 (valid_r, [30, 34, float("inf"), 42]),
                 (valid_r, [43, 44, 45, 46])]
        for rates, qualities in cases:
            with self.subTest(rates=rates, qualities=qualities), self.assertRaises(BDRateError):
                bd_rate_percent(valid_r, valid_q, rates, qualities)


class RegistrationTests(unittest.TestCase):
    def test_component_then_611_weighting(self):
        reference = {row["qp"]: row for row in curve()}
        test = copy.deepcopy(reference)
        for row in test.values():
            row["ypsnr"] += 0.1
            row["upsnr"] -= 0.2
            row["vpsnr"] += 0.4
        comparison = compare_curves(reference, test)
        rates = comparison["bd_rate"]
        self.assertAlmostEqual(rates["weighted_611"], (6 * rates["y"] + rates["u"] + rates["v"]) / 8)
        self.assertLess(rates["y"], 0)
        self.assertGreater(rates["u"], 0)
        self.assertLess(rates["v"], rates["y"])
        # A combined PSNR curve is a different statistic and is not used.
        combined = bd_rate_percent([reference[q]["kbps"] for q in QPS],
                                   [(6 * reference[q]["ypsnr"] + reference[q]["upsnr"] + reference[q]["vpsnr"]) / 8 for q in QPS],
                                   [test[q]["kbps"] for q in QPS],
                                   [(6 * test[q]["ypsnr"] + test[q]["upsnr"] + test[q]["vpsnr"]) / 8 for q in QPS])
        self.assertNotAlmostEqual(rates["weighted_611"], combined, places=6)

    def test_missing_overall_kept_empty_and_single_qp_uses_anchor_rest(self):
        reference = curve(status=None)
        partial = [dict(reference[0], kbps=1700, error_info="pass"),
                   dict(reference[2], kbps=380, error_info="pass")]
        before = copy.deepcopy(reference)
        report = build_report(reference, partial, {"Clip": "C"})
        self.assertEqual(report["overall"][0]["status"], "incomplete_test")
        self.assertIsNone(report["overall"][0]["bd_rate"])
        self.assertEqual(report["overall"][0]["missing_qps"], [27, 37])
        q22, q27, q32, q37 = report["per_qp"]
        self.assertEqual((q22["status"], q27["status"], q32["status"], q37["status"]),
                         ("ok", "missing_test", "ok", "missing_test"))
        expected = {row["qp"]: row for row in reference}
        expected[22] = partial[0]
        self.assertEqual(q22["bd_rate"], compare_curves({row["qp"]: row for row in reference}, expected)["bd_rate"])
        # Adding a different measured QP cannot change the QP22 substitution.
        only22 = build_report(reference, [partial[0]])
        self.assertEqual(q22["bd_rate"], only22["per_qp"][0]["bd_rate"])
        self.assertEqual(reference, before)
        self.assertEqual(len(report["raw_reference"]), 4)

    def test_empty_slots_and_unrelated_duplicate_reference_do_not_create_results(self):
        reference = curve() + [dict(sequence="Other", tag="lp", qp=37, kbps=None)] * 2
        test = curve() + [dict(sequence="Other", tag="lp", qp=37, kbps=None)]
        report = build_report(reference, test)
        self.assertEqual(len(report["overall"]), 1)
        self.assertEqual(report["coverage"]["lp"], {"task_slots": 1, "result_points": 0, "pass_points": 0, "empty_slots": 1})
        self.assertEqual(report["overall"][0]["status"], "ok")
        self.assertIsNone(report["aggregates"]["ra"]["overall"]["class_equal_weight"]["bd_rate"])

    def test_used_duplicate_reference_is_excluded_and_test_duplicate_rejected(self):
        reference = curve()
        report = build_report(reference + [reference[0]], curve())
        self.assertEqual(report["overall"][0]["status"], "invalid_reference")
        self.assertIsNone(report["overall"][0]["bd_rate"])
        with self.assertRaises(BDRateError):
            build_report(reference, curve() + [curve()[0]])

    def test_failed_and_invalid_points_not_silently_treated_as_success(self):
        reference, test = curve(), curve()
        test[1]["error_info"] = "fail"
        test[2]["kbps"] = float("nan")
        report = build_report(reference, test)
        self.assertEqual(report["overall"][0]["missing_qps"], [27, 32])
        self.assertEqual(report["per_qp"][1]["status"], "invalid_test")
        self.assertEqual(report["per_qp"][2]["status"], "invalid_test")
        self.assertIsNone(report["aggregates"]["ra"]["overall"]["sequence_equal_weight"]["bd_rate"])

    def test_single_qp_nonmonotone_replacement_excluded(self):
        reference, test = curve(), [curve()[1]]
        test[0]["ypsnr"] = 44.0
        report = build_report(reference, test)
        self.assertEqual(report["per_qp"][1]["status"], "invalid_curve")
        self.assertIsNone(report["per_qp"][1]["bd_rate"])

    def test_sequence_and_class_means_are_distinct_and_configs_separate(self):
        reference = curve("A") + curve("B") + curve("C") + curve("A", "lb")
        test = curve("A", factor=0.9) + curve("B", factor=0.9) + curve("C", factor=1.2) + curve("A", "lb", factor=0.5)
        report = build_report(reference, test, {"A": "C", "B": "C", "C": "E"})
        ra = report["aggregates"]["ra"]["overall"]
        self.assertAlmostEqual(ra["sequence_equal_weight"]["bd_rate"]["weighted_611"], 0.0, places=10)
        self.assertAlmostEqual(ra["class_equal_weight"]["bd_rate"]["weighted_611"], 5.0, places=10)
        self.assertEqual(ra["sequence_equal_weight"]["sequence_count"], 3)
        self.assertEqual(ra["class_equal_weight"]["class_count"], 2)
        lb = report["aggregates"]["lb"]["overall"]
        self.assertAlmostEqual(lb["sequence_equal_weight"]["bd_rate"]["weighted_611"], -50.0, places=10)


if __name__ == "__main__":
    unittest.main()
