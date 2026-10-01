"""Synthetic I/O only. No test in this module launches an encoder or decoder."""
import argparse
import contextlib
import io
import json
from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import batch_test as batch
from batch_identity import load_success, save_success, task_identity, successful


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.exe = self.root / "encoder"
        self.exe.write_bytes(b"fake binary, never executed")
        self.cfg = self.root / "sequence.cfg"
        self.cfg.write_text("InputFile : test.yuv\nFramesToBeEncoded : 2\nFrameRate : 50\n")
        self.video = self.root / "test.yuv"
        self.video.write_bytes(b"synthetic")
        self.job = batch._make_job(order=0, name="test", repo_root=self.root, cwd=self.root,
                                  encoder=self.exe, decoder=self.exe, cfgs=[], sequence_cfg=self.cfg,
                                  input_path=self.video, qp=32, frames=2, extra_args=[], decoder_args=[],
                                  decode_md5=False, xlsm_tag="lb", run_dir=self.root / "runs", overwrite=False)

    def identity(self, job=None):
        job = job or self.job
        return task_identity(job, batch._encode_command(job))

    def fake_encode(self, cmd, **kwargs):
        self.job.bitstream.write_bytes(b"new bitstream")
        kwargs["stdout"].write("Total Frames | Bitrate Y-PSNR U-PSNR V-PSNR\n2 a 123.4 40.1 41.2 42.3\n")
        return argparse.Namespace(returncode=0)

    def test_no_recon_default(self):
        cmd = batch._encode_command(self.job)
        self.assertEqual(cmd[cmd.index("-o") + 1], "")
        cmd = batch._encode_command(replace(self.job, write_recon=True))
        self.assertEqual(cmd[cmd.index("-o") + 1], str(self.job.recon))

    def test_hash_error_overrides_ok(self):
        self.assertEqual(batch._parse_decode_status("[MD5: (OK)]\n[MD5: (ERROR)]"), "failed")
        self.assertEqual(batch._parse_decode_status("(OK)"), "ok")
        self.assertEqual(batch._parse_decode_status("nothing"), "nohash")

    def test_verified_success_and_resume(self):
        with patch.object(batch.subprocess, "run", side_effect=self.fake_encode) as run:
            first = batch._run_one(self.job)
            second = batch._run_one(self.job)
        self.assertTrue(successful(first))
        self.assertEqual(second["status"], "resumed")
        self.assertEqual(run.call_count, 1)

    def test_stale_stream_not_success(self):
        self.job.out_dir.mkdir(parents=True)
        self.job.bitstream.write_bytes(b"partial stream")
        with patch.object(batch.subprocess, "run") as run:
            with self.assertRaisesRegex(ValueError, "verified matching success"):
                batch._run_one(self.job)
            run.assert_not_called()

    def test_zero_exit_without_summary_fails(self):
        with patch.object(batch.subprocess, "run", return_value=argparse.Namespace(returncode=0)):
            row = batch._run_one(self.job)
        self.assertFalse(successful(row))
        self.assertFalse(self.job.bitstream.with_suffix(".success.json").exists())

    def test_truncated_frames_fail(self):
        with patch.object(batch.subprocess, "run", side_effect=self.fake_encode):
            row = batch._run_one(replace(self.job, frames=3))
        self.assertEqual(row["status"], "incomplete")

    def test_identity_changes(self):
        before = self.identity()["fingerprint"]
        self.assertNotEqual(before, self.identity(replace(self.job, qp=37))["fingerprint"])
        self.assertNotEqual(before, self.identity(replace(self.job, decode_md5=True))["fingerprint"])
        self.exe.write_bytes(b"different binary")
        self.assertNotEqual(before, self.identity()["fingerprint"])

    def test_tampering_invalidates_resume(self):
        with patch.object(batch.subprocess, "run", side_effect=self.fake_encode):
            batch._run_one(self.job)
        self.job.bitstream.write_bytes(b"tampered")
        self.assertIsNone(load_success(self.job, self.identity()["fingerprint"]))

    def test_config_and_input_change_identity(self):
        before = self.identity()["fingerprint"]
        self.cfg.write_text(self.cfg.read_text() + "QP : 24\n")
        self.assertNotEqual(before, self.identity()["fingerprint"])
        before = self.identity()["fingerprint"]
        self.video.write_bytes(b"new synthetic input")
        self.assertNotEqual(before, self.identity()["fingerprint"])

    def test_managed_overrides_rejected(self):
        for extra in (["--FramesToBeEncoded=9"], ["-o", "recon.yuv"], ["-q22"], ["-c", "secret.cfg"]):
            with self.assertRaises(ValueError):
                batch._validate_job(replace(self.job, extra_args=extra))

    def test_report_each_group_without_waiting_for_others(self):
        jobs = [self.job, replace(self.job, order=1, group="variant")]
        results = {0: {"group": "baseline", "encode_status": "ok", "error_info": "pass"}}
        args = argparse.Namespace(out_dir=self.root / "results")
        reported = {}
        with patch.object(batch, "_write_xlsm_reports") as writer:
            batch._flush_progress(jobs, results, args, self.root, reported)
            self.assertEqual(writer.call_count, 1)
            self.assertEqual(writer.call_args.kwargs["out_dir"].name, "baseline")
            batch._flush_progress(jobs, results, args, self.root, reported)
            self.assertEqual(writer.call_count, 1)
            results[1] = {"group": "variant", "encode_status": "failed", "error_info": "fail"}
            batch._flush_progress(jobs, results, args, self.root, reported)
            self.assertEqual(writer.call_count, 2)
        self.assertFalse(json.loads((args.out_dir / "reports/variant/completion.json").read_text())["all_passed"])

    def test_dry_run_never_launches_codec(self):
        with patch.object(batch.subprocess, "run", side_effect=AssertionError("must not launch")):
            with contextlib.redirect_stdout(io.StringIO()) as out:
                rc = batch.main(["--preset", "LBeu", "--class", "C,E", "--dry-run", "--allow-missing-input",
                                 "--input-dir", str(self.root), "--no-xlsm-report"])
        self.assertEqual(rc, 0)
        self.assertIn("Jobs       : 28", out.getvalue())
        self.assertIn("frames=250", out.getvalue())

    def test_missing_hash_is_failure(self):
        job = replace(self.job, decode_md5=True)
        with patch.object(batch.subprocess, "run", side_effect=self.fake_encode), patch.object(
                batch, "_run_decode", return_value={"decode_status": "nohash", "decode_returncode": 0}):
            row = batch._run_one(job)
        self.assertFalse(successful(row))


if __name__ == "__main__":
    unittest.main()
