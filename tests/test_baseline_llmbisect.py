from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from baseline_llmbisect import static_ranker


def git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return completed.stdout.strip()


def commit_file(repo: Path, filename: str, content: str, message: str) -> str:
    (repo / filename).parent.mkdir(parents=True, exist_ok=True)
    (repo / filename).write_text(content)
    subprocess.run(["git", "-C", str(repo), "add", filename], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", message], check=True)
    return git(repo, "rev-parse", "HEAD")


class StaticRankerTests(unittest.TestCase):
    def test_static_ranker_prioritizes_crash_related_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            subprocess.run(["git", "init", str(repo)], check=True, stdout=subprocess.PIPE)
            git(repo, "config", "user.email", "test@example.invalid")
            git(repo, "config", "user.name", "Test User")

            good = commit_file(repo, "README.md", "base\n", "initial")
            unrelated = commit_file(repo, "llvm/docs/ReleaseNotes.rst", "docs\n", "docs update")
            crash_related = commit_file(
                repo,
                "clang/lib/Sema/SemaExprCXX.cpp",
                "void CheckArityMismatch() {}\n",
                "Fix explicit object parameter arity crash",
            )
            bad = commit_file(repo, "clang/test/SemaCXX/explicit-object.cpp", "test\n", "add regression test")

            profile = static_ranker.IssueProfile(
                issue_id="demo",
                title="Clang crash on explicit object parameter arity mismatch",
                good_commit=good,
                bad_commit=bad,
                bug_report_summary="Assertion in CheckArityMismatch while compiling explicit object member function.",
                keywords=["crash", "explicit object", "arity", "CheckArityMismatch"],
                relevant_paths=["clang/lib/Sema"],
                high_risk_paths=["clang/lib/Sema", "clang/include/clang/Sema"],
            )

            result = static_ranker.rank_issue(repo, profile, top_k=3)

        self.assertEqual(result.issue_id, "demo")
        self.assertEqual(result.interval_size, 3)
        self.assertEqual(result.ranking[0].sha, crash_related)
        self.assertIn("message_keyword", result.ranking[0].generators)
        self.assertIn("tool_component", result.ranking[0].generators)
        self.assertNotEqual(result.ranking[0].sha, unrelated)

    def test_cli_writes_json_ranking(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo = root / "repo"
            subprocess.run(["git", "init", str(repo)], check=True, stdout=subprocess.PIPE)
            git(repo, "config", "user.email", "test@example.invalid")
            git(repo, "config", "user.name", "Test User")
            good = commit_file(repo, "README.md", "base\n", "initial")
            first_bad = commit_file(repo, "clang/lib/CodeGen/CGExpr.cpp", "EmitCallExpr();\n", "CodeGen crash fix")
            bad = commit_file(repo, "clang/test/CodeGen/crash.cpp", "test\n", "test")
            profiles = root / "profiles.json"
            profiles.write_text(
                json.dumps(
                    {
                        "demo": {
                            "issue_id": "demo",
                            "title": "Clang CodeGen crash in EmitCallExpr",
                            "good_commit": good,
                            "bad_commit": bad,
                            "bug_report_summary": "Stack trace points at EmitCallExpr in clang CodeGen.",
                            "keywords": ["crash", "CodeGen", "EmitCallExpr"],
                            "relevant_paths": ["clang/lib/CodeGen"],
                            "high_risk_paths": ["clang/lib/CodeGen"],
                        }
                    }
                )
            )
            output = root / "ranking.json"

            exit_code = static_ranker.main(
                [
                    "rank",
                    "--repo",
                    str(repo),
                    "--profiles",
                    str(profiles),
                    "--issue",
                    "demo",
                    "--top-k",
                    "2",
                    "--output",
                    str(output),
                ]
            )

            payload = json.loads(output.read_text())

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["issue_id"], "demo")
        self.assertEqual(payload["ranking"][0]["sha"], first_bad)
        self.assertIn("prompt_payload", payload)


if __name__ == "__main__":
    unittest.main()
