import argparse
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import team


class TeamQueueTests(unittest.TestCase):
    def setUp(self):
        platform_patch = patch("worker.platform.platform", return_value="test-platform")
        platform_patch.start()
        self.addCleanup(platform_patch.stop)
        info_patch = patch("worker.system_info", return_value={"kernel": "test"})
        info_patch.start()
        self.addCleanup(info_patch.stop)
        version_patch = patch("worker.code_version", return_value={"pipeline_commit": "test-commit", "pipeline_dirty": False})
        version_patch.start()
        self.addCleanup(version_patch.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / "model.gguf").write_bytes(b"fake model")
        (root / "bench").touch()
        self.settings = dict(bench_path=str(root / "bench"), models_dir=str(root),
                             work_dir=str(root / "work"), default_model="model.gguf")
        self.base = team.prepare(self.settings)

    def submit(self):
        args = argparse.Namespace(request="baseline", model=None, threads=[1],
                                  repeat=1, prompt_tokens=[8], gen_tokens=[4])
        with contextlib.redirect_stdout(io.StringIO()):
            return team.submit(args, self.settings, self.base)

    def success(self, cmd, **kwargs):
        prompt = cmd[cmd.index("-p") + 1]
        gen = cmd[cmd.index("-n") + 1]
        csv = "n_prompt,n_gen,test_time,cpu_info,build_commit,model_filename,n_threads,avg_ts,stddev_ts\n"
        csv += f"{prompt},{gen},now,cpu,commit,model.gguf,1,10,1\n"
        return subprocess.CompletedProcess(cmd, 0, csv, "")

    def test_unique_atomic_submission(self):
        a, b = self.submit(), self.submit()
        self.assertNotEqual(a, b)
        self.assertEqual(len(list((self.base / "queue").glob("*.yaml"))), 2)
        self.assertFalse(list((self.base / "queue").glob("*.tmp")))

    def test_success_moves_to_done_and_saves_csv(self):
        ident = self.submit()
        with patch("worker.subprocess.run", side_effect=self.success):
            team.process_one(self.base / "queue" / (ident + ".yaml"), self.settings, self.base)
        self.assertTrue((self.base / "done" / (ident + ".yaml")).is_file())
        meta = json.loads((self.base / "results" / ident / "meta.json").read_text())
        self.assertEqual((meta["ok"], meta["failed"]), (2, 0))
        self.assertEqual(meta["pipeline_commit"], "test-commit")
        self.assertIn("pp8", (self.base / "results" / ident / "results.csv").read_text())

    def test_partial_failure_is_visible(self):
        ident = self.submit()
        calls = iter([False, True])
        def bench(cmd, **kwargs):
            return self.success(cmd, **kwargs) if next(calls) else subprocess.CompletedProcess(cmd, 1, "", "bad")
        with patch("worker.subprocess.run", side_effect=bench):
            team.process_one(self.base / "queue" / (ident + ".yaml"), self.settings, self.base)
        self.assertEqual(team.outcome(self.base, ident, "done"), "partial")

    def test_timeouts_move_request_to_failed(self):
        ident = self.submit()
        with patch("worker.subprocess.run", side_effect=subprocess.TimeoutExpired("bench", 1)):
            team.process_one(self.base / "queue" / (ident + ".yaml"), self.settings, self.base)
        self.assertTrue((self.base / "failed" / (ident + ".yaml")).is_file())

    def test_reject_path_traversal_and_bad_numbers(self):
        req = dict(model="../model.gguf", repeat=1, threads=[1], prompt_tokens=[8], gen_tokens=[4])
        with self.assertRaises(ValueError):
            team.validate(req, self.settings)
        req.update(model="model.gguf", threads=[True])
        with self.assertRaises(ValueError):
            team.validate(req, self.settings)
        with self.assertRaises(ValueError):
            team.check_id("../private")

    @unittest.skipUnless(sys.platform == "linux", "Linux flock")
    def test_second_worker_cannot_recover_running_request(self):
        import fcntl
        ident = self.submit()
        (self.base / "queue" / (ident + ".yaml")).rename(self.base / "running" / (ident + ".yaml"))
        with (self.base / "worker.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(ValueError):
                team.worker(self.settings, self.base, once=True)
        self.assertTrue((self.base / "running" / (ident + ".yaml")).exists())

    @unittest.skipUnless(sys.platform == "linux", "Linux worker")
    def test_restart_preserves_results_and_marks_interrupted_request(self):
        ident = self.submit()
        (self.base / "queue" / (ident + ".yaml")).rename(self.base / "running" / (ident + ".yaml"))
        folder = self.base / "results" / ident
        folder.mkdir()
        (folder / "results.csv").write_text("partial data", encoding="utf-8")
        team.worker(self.settings, self.base, once=True)
        self.assertTrue((self.base / "failed" / (ident + ".yaml")).exists())
        self.assertEqual((folder / "results.csv").read_text(), "partial data")


if __name__ == "__main__":
    unittest.main()
