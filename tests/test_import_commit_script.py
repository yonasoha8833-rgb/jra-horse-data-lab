import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "commit_imported_data.sh"


class ImportCommitScriptTests(unittest.TestCase):
    def run_script(self, *, diff_exit: int, pull_exit: int = 0):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            bin_dir = temp_path / "bin"
            bin_dir.mkdir()
            log_path = temp_path / "git.log"
            fake_git = bin_dir / "git"
            fake_git.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                "printf '%s\\n' \"$*\" >> \"$GIT_LOG\"\n"
                'if [[ "$1" == "diff" ]]; then exit "$MOCK_GIT_DIFF_EXIT"; fi\n'
                'if [[ "$1" == "pull" ]]; then exit "$MOCK_GIT_PULL_EXIT"; fi\n'
                "exit 0\n",
                encoding="utf-8",
            )
            fake_git.chmod(0o755)
            env = os.environ.copy()
            env.update({
                "PATH": str(bin_dir) + os.pathsep + env.get("PATH", ""),
                "GIT_LOG": str(log_path),
                "MOCK_GIT_DIFF_EXIT": str(diff_exit),
                "MOCK_GIT_PULL_EXIT": str(pull_exit),
            })
            result = subprocess.run(
                ["bash", str(SCRIPT)],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            calls = log_path.read_text(encoding="utf-8").splitlines()
            return result, calls

    def test_skips_commit_and_push_when_no_import_data_changed(self):
        result, calls = self.run_script(diff_exit=0)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, [
            "add data/race_results.json",
            "diff --cached --quiet",
        ])
        self.assertIn("skipping commit and push", result.stdout)

    def test_rebases_import_commit_before_push_when_data_changed(self):
        result, calls = self.run_script(diff_exit=1)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(calls.index("pull --rebase origin main"), calls.index("push"))
        self.assertIn("commit -m Import JRA race results", calls)

    def test_does_not_push_when_rebase_fails(self):
        result, calls = self.run_script(diff_exit=1, pull_exit=1)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("pull --rebase origin main", calls)
        self.assertNotIn("push", calls)

    def test_reports_git_diff_errors_without_committing(self):
        result, calls = self.run_script(diff_exit=2)

        self.assertEqual(result.returncode, 2)
        self.assertIn("Unable to inspect staged import changes.", result.stderr)
        self.assertNotIn("commit -m Import JRA race results", calls)
        self.assertNotIn("push", calls)


if __name__ == "__main__":
    unittest.main()
