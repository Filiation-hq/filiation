"""Fails PRs that pass CI without real TDD: new tests must fail first and tests can't be gamed (#73)."""

from __future__ import annotations

import argparse
import ast
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import tokenize
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from check_pr_body import EDGE_HEADING, HTML_COMMENT, TEST_REF, split_sections

GUARD_FILES = ("AGENTS.md", "Makefile")
GUARDED_TOOL_SECTIONS = ("[tool.ruff", "[tool.pytest")
SUPPRESSION = re.compile(r"#\s*noqa\b|pragma:\s*no\s*cover", re.IGNORECASE)
EXCEPTION_LINE = re.compile(r"^\s*-?\s*Guardrail exception:\s*(.+?)\s+(?:—|–|--|-)\s+(.+?)\s*$", re.MULTILINE)
MIN_EXCEPTION_REASON_WORDS = 4
BROAD_EXCEPTIONS = {"Exception", "BaseException", "AssertionError"}
FAILURE_PREFIXES = ("setup-failed", "call-failed", "teardown-failed", "collection-error")
SOURCE_ROOTS = "packages/core/src:packages/pipeline/src:packages/models/src"


@dataclass(frozen=True)
class Violation:
    """One guard finding about a test or file."""

    subject: str
    message: str


@dataclass
class ChangedTests:
    """Test functions a PR added, edited, or removed, as "path::qualname" ids."""

    new: set[str] = field(default_factory=set)
    changed: set[str] = field(default_factory=set)
    deleted: set[str] = field(default_factory=set)


def find_test_functions(source: str) -> dict[str, ast.FunctionDef]:
    """Maps each test function in a module to its AST node, including methods of classes.

    Args:
        source: Python source of a test module.

    Returns:
        dict[str, ast.FunctionDef]: "test_x" or "Class::test_x" to the function node.
    """
    found: dict[str, ast.FunctionDef] = {}
    for node in ast.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
            found[node.name] = node
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name.startswith("test_"):
                    found[f"{node.name}::{item.name}"] = item
    return found


def classify_tests(base: dict[str, str], head: dict[str, str]) -> ChangedTests:
    """Compares test functions between base and head, ignoring formatting and position.

    Args:
        base: Test file path to its base source (absent if the file is new).
        head: Test file path to its head source (absent if the file was deleted).

    Returns:
        ChangedTests: New, changed, and deleted test ids.
    """
    changes = ChangedTests()
    for path in sorted(set(base) | set(head)):
        before = find_test_functions(base.get(path, ""))
        after = find_test_functions(head.get(path, ""))
        changes.new |= {f"{path}::{q}" for q in after.keys() - before.keys()}
        changes.deleted |= {f"{path}::{q}" for q in before.keys() - after.keys()}
        changes.changed |= {
            f"{path}::{q}" for q in before.keys() & after.keys() if ast.dump(before[q]) != ast.dump(after[q])
        }
    return changes


def _call_name(call: ast.Call) -> str:
    """Returns the called function's bare name.

    Args:
        call: A call node.

    Returns:
        str: "raises" for pytest.raises(...), "assertEqual" for self.assertEqual(...), etc.
    """
    func = call.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def _is_trivial_assert(node: ast.Assert) -> bool:
    """Reports whether an assert can never fail: a truthy constant or a value compared to itself.

    Args:
        node: An assert statement.

    Returns:
        bool: True for assert True, assert 1, assert x == x, assert x is x.
    """
    test = node.test
    if isinstance(test, ast.Constant):
        return bool(test.value)
    return (
        isinstance(test, ast.Compare)
        and len(test.ops) == 1
        and isinstance(test.ops[0], (ast.Eq, ast.Is))
        and ast.dump(test.left) == ast.dump(test.comparators[0])
    )


def _asserting_helpers(tree: ast.Module) -> set[str]:
    """Names the module-level non-test functions that contain a real assertion.

    Args:
        tree: Parsed test module.

    Returns:
        set[str]: Helper function names whose calls count as assertions.
    """
    return {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and not node.name.startswith("test_")
        and any(isinstance(n, ast.Assert) and not _is_trivial_assert(n) for n in ast.walk(node))
    }


def _problems(node: ast.FunctionDef, helpers: set[str]) -> list[str]:
    """Lists the ways one test function cannot fail.

    Args:
        node: The test function.
        helpers: Module helpers that assert, so calling them counts as asserting.

    Returns:
        list[str]: Problems found; empty if the test can fail.
    """
    problems = []
    decorators = " ".join(ast.unparse(d) for d in node.decorator_list)
    calls = [n for n in ast.walk(node) if isinstance(n, ast.Call)]
    if "skip" in decorators or "xfail" in decorators or any(_call_name(c) in ("skip", "xfail") for c in calls):
        problems.append("is skipped or xfailed")
    for handler in (h for n in ast.walk(node) if isinstance(n, ast.Try) for h in n.handlers):
        caught = {getattr(t, "id", "") for t in ast.walk(handler.type)} if handler.type else {"Exception"}
        if caught & BROAD_EXCEPTIONS and not any(isinstance(n, ast.Raise) for n in ast.walk(handler)):
            problems.append("swallows failures in a broad except")
            break
    asserts = [n for n in ast.walk(node) if isinstance(n, ast.Assert)]
    if any(_is_trivial_assert(a) for a in asserts):
        problems.append("asserts something that is always true")
    real = (
        any(not _is_trivial_assert(a) for a in asserts)
        or any(_call_name(c).startswith("assert") or _call_name(c) in ("raises", "warns") for c in calls)
        or any(_call_name(c) in helpers for c in calls)
    )
    if not real and "asserts something that is always true" not in problems:
        problems.append("has no assertion")
    return problems


def static_violations(head: dict[str, str], targets: set[str]) -> list[Violation]:
    """Flags targeted tests that cannot fail: no assertion, trivial asserts, swallowed errors, skips.

    Args:
        head: Test file path to its head source.
        targets: "path::qualname" ids to scan (the PR's new and changed tests).

    Returns:
        list[Violation]: One violation per test that cannot fail, in id order.
    """
    violations = []
    for path, source in sorted(head.items()):
        tree = ast.parse(source)
        helpers = _asserting_helpers(tree)
        for qual, node in sorted(find_test_functions(source).items()):
            test_id = f"{path}::{qual}"
            if test_id in targets and (problems := _problems(node, helpers)):
                violations.append(Violation(test_id, "test cannot fail: " + "; ".join(problems)))
    return violations


def weakened_tests(base: dict[str, str], head: dict[str, str], changes: ChangedTests) -> list[Violation]:
    """Flags existing tests that now make fewer assertions than before.

    Args:
        base: Test file path to its base source.
        head: Test file path to its head source.
        changes: Output of classify_tests for the same files.

    Returns:
        list[Violation]: One per weakened test, in id order.
    """

    def count(node: ast.FunctionDef) -> int:
        """Counts assert statements and assert-style calls in a test.

        Args:
            node: Test function.

        Returns:
            int: Number of assertions.
        """
        return sum(
            isinstance(n, ast.Assert)
            or (isinstance(n, ast.Call) and (_call_name(n).startswith("assert") or _call_name(n) == "raises"))
            for n in ast.walk(node)
        )

    violations = []
    for test_id in sorted(changes.changed):
        path, qual = test_id.split("::", 1)
        before = count(find_test_functions(base[path])[qual])
        after = count(find_test_functions(head[path])[qual])
        if after < before:
            violations.append(Violation(test_id, f"existing test has fewer assertions ({before} → {after})"))
    return violations


def _tool_sections(text: str) -> dict[str, str]:
    """Extracts the [tool.ruff*] and [tool.pytest*] tables of a pyproject.toml.

    Args:
        text: pyproject.toml contents.

    Returns:
        dict[str, str]: Table header to its body text.
    """
    sections: dict[str, str] = {}
    current = None
    for line in text.splitlines():
        if line.startswith("["):
            current = line.strip() if line.startswith(GUARDED_TOOL_SECTIONS) else None
            if current:
                sections[current] = ""
        elif current:
            sections[current] += line + "\n"
    return sections


def _suppressions(source: str) -> Counter:
    """Counts suppression comments (noqa, pragma: no cover) in real comments, not strings.

    Args:
        source: Python source.

    Returns:
        Counter: Suppression comment text to occurrences.
    """
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, SyntaxError, IndentationError):
        return Counter(line.strip() for line in source.splitlines() if SUPPRESSION.search(line))
    return Counter(t.string for t in tokens if t.type == tokenize.COMMENT and SUPPRESSION.search(t.string))


def config_violations(base: dict[str, str], head: dict[str, str]) -> list[Violation]:
    """Flags edits that could relax the checks: guard files, lint/test settings, suppressions.

    Args:
        base: Changed file path to its base contents (absent if new).
        head: Changed file path to its head contents (absent if deleted).

    Returns:
        list[Violation]: Findings in path order.
    """
    violations = []
    for path in sorted(set(base) | set(head)):
        before, after = base.get(path, ""), head.get(path, "")
        if before == after:
            continue
        if path.startswith(".github/") or path in GUARD_FILES:
            violations.append(Violation(path, "edits a guard file (rules, CI, or build)"))
        elif path.endswith("pyproject.toml"):
            old, new = _tool_sections(before), _tool_sections(after)
            for header in sorted(set(old) | set(new)):
                if old.get(header) != new.get(header):
                    violations.append(Violation(f"{path} {header}", "changes lint or test settings"))
        elif path.endswith(".py") and _suppressions(after) - _suppressions(before):
            violations.append(Violation(f"{path}: suppression", "adds a noqa or pragma: no cover comment"))
    return violations


def citation_violations(body: str, touched: set[str]) -> list[Violation]:
    """Flags edge-case citations of tests this PR neither added nor changed.

    Args:
        body: PR body.
        touched: "path::qualname" ids of the PR's new and changed tests.

    Returns:
        list[Violation]: One per cited test name outside touched, in citation order.
    """
    section = HTML_COMMENT.sub("", split_sections(body.replace("\r\n", "\n")).get(EDGE_HEADING, ""))
    touched_names = {test_id.rsplit("::", 1)[-1] for test_id in touched}
    cited = dict.fromkeys(TEST_REF.findall(section))
    return [
        Violation(name, "edge case cites a test this PR didn't add or change")
        for name in cited
        if name not in touched_names
    ]


def excuse(violations: list[Violation], body: str) -> list[Violation]:
    """Drops violations excused by a "Guardrail exception: <subject> — <reason>" line in Before merge.

    Args:
        violations: Guard findings.
        body: PR body.

    Returns:
        list[Violation]: Findings with no exception line that names the subject and gives a reason.
    """
    before_merge = split_sections(body.replace("\r\n", "\n")).get("## Before merge", "")
    excused = [
        subject
        for subject, reason in EXCEPTION_LINE.findall(before_merge)
        if len(reason.split()) >= MIN_EXCEPTION_REASON_WORDS
    ]
    return [v for v in violations if not any(v.subject in subject for subject in excused)]


def fail_first_violations(
    new_tests: set[str], outcomes: dict[str, list[str]]
) -> tuple[list[Violation], list[tuple[str, str, str]]]:
    """Checks that every new test failed when run against the base code.

    Args:
        new_tests: "path::qualname" ids of tests the PR added.
        outcomes: Probe results keyed by test id (parametrize suffix removed) or by file
            path for collection errors.

    Returns:
        tuple: (violations for tests that passed, skipped, or never ran on base;
            summary rows of (test id, "failed" | "passed" | "not run", failure reason)).
    """
    violations, rows = [], []
    for test_id in sorted(new_tests):
        path = test_id.split("::", 1)[0]
        results = [
            r for key, values in outcomes.items() if key in (test_id, path) or key.startswith(test_id + "[")
            for r in values
        ]
        failures = [r for r in results if r.startswith(FAILURE_PREFIXES)]
        if failures:
            rows.append((test_id, "failed", failures[0].split(": ", 1)[-1]))
        elif results:
            rows.append((test_id, "passed", ""))
            violations.append(Violation(test_id, "new test passes against the base code, so it was not written first"))
        else:
            rows.append((test_id, "not run", ""))
            violations.append(Violation(test_id, "new test never ran against the base code"))
    return violations, rows


def commit_order_note(commits: list[tuple[str, list[str]]]) -> str | None:
    """Reports when source code was committed before any test (informational only).

    Args:
        commits: (short sha, changed paths) for each PR commit, oldest first.

    Returns:
        str | None: A note naming the first source commit, or None if tests came first.
    """

    def first(predicate) -> int | None:
        """Returns the index of the first commit with a path matching predicate.

        Args:
            predicate: Path test.

        Returns:
            int | None: Commit index, or None.
        """
        return next((i for i, (_, paths) in enumerate(commits) if any(predicate(p) for p in paths)), None)

    first_test = first(lambda p: p.startswith("tests/"))
    first_src = first(lambda p: (p.startswith("packages/") and "/src/" in p) or p.startswith(".github/scripts/"))
    if first_src is not None and (first_test is None or first_src < first_test):
        return f"Source changed in {commits[first_src][0]} before any test commit; TDD puts tests first."
    return None


def _git(args: list[str], cwd: Path | None = None) -> str:
    """Runs a git command and returns its stdout.

    Args:
        args: Arguments after "git".
        cwd: Repository directory; defaults to the current directory.

    Returns:
        str: Command output.

    Raises:
        subprocess.CalledProcessError: If git fails.
    """
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


def changed_files(base: str, head: str, cwd: Path | None = None) -> list[str]:
    """Lists paths that differ between two commits, with renames shown as delete plus add.

    Args:
        base: Base commit.
        head: Head commit.
        cwd: Repository directory.

    Returns:
        list[str]: Sorted paths.
    """
    return sorted(filter(None, _git(["diff", "--name-only", "--no-renames", base, head], cwd).splitlines()))


def file_at(rev: str, path: str, cwd: Path | None = None) -> str | None:
    """Reads a file's contents at a commit.

    Args:
        rev: Commit.
        path: Repository-relative path.
        cwd: Repository directory.

    Returns:
        str | None: Contents, or None if the file doesn't exist at rev.
    """
    try:
        return _git(["show", f"{rev}:{path}"], cwd)
    except subprocess.CalledProcessError:
        return None


def commits(base: str, head: str, cwd: Path | None = None) -> list[tuple[str, list[str]]]:
    """Lists the commits in base..head, oldest first, with their changed paths.

    Args:
        base: Base commit.
        head: Head commit.
        cwd: Repository directory.

    Returns:
        list[tuple[str, list[str]]]: (short sha, sorted paths) per commit.
    """
    out = _git(["log", "--reverse", "--no-renames", "--format=%x00%h", "--name-only", f"{base}..{head}"], cwd)
    result = []
    for chunk in filter(None, out.split("\x00")):
        lines = [line for line in chunk.splitlines() if line.strip()]
        result.append((lines[0], sorted(lines[1:])))
    return result


def _is_test_file(path: str) -> bool:
    """Reports whether a path is a pytest test module.

    Args:
        path: Repository-relative path.

    Returns:
        bool: True for tests/**/test_*.py.
    """
    return path.startswith("tests/") and Path(path).name.startswith("test_") and path.endswith(".py")


def _report(title: str, violations: list[Violation], rows: list[str]) -> None:
    """Prints violations as GitHub error annotations and appends a job summary.

    Args:
        title: Summary heading.
        violations: Remaining (unexcused) findings.
        rows: Extra Markdown lines for the summary.
    """
    for v in violations:
        print(f"::error title=TDD guard::{v.subject}: {v.message}")
    lines = [f"## {'❌' if violations else '✅'} {title}", "", *rows, ""]
    lines += [f"- `{v.subject}`: {v.message}" for v in violations]
    if violations:
        lines += ["", "Fix these, or excuse one under **Before merge** with "
                  "`Guardrail exception: <subject> — <reason>` (AGENTS.md §9)."]
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as out:
            out.write("\n".join(lines) + "\n")
    print("\n".join(lines))


def run_static(base: str, head: str, new_tests_path: Path, body: str) -> int:
    """Runs the static, weakening, config, and citation checks, and saves the new test ids.

    Args:
        base: PR base commit.
        head: PR head commit.
        new_tests_path: File to write new test ids to, one per line, for the fail-first step.
        body: PR body.

    Returns:
        int: 1 if any unexcused violation remains, else 0.
    """
    paths = changed_files(base, head)
    base_files = {p: s for p in paths if (s := file_at(base, p)) is not None}
    head_files = {p: s for p in paths if (s := file_at(head, p)) is not None}
    base_tests = {p: s for p, s in base_files.items() if _is_test_file(p)}
    head_tests = {p: s for p, s in head_files.items() if _is_test_file(p)}
    changes = classify_tests(base_tests, head_tests)

    violations = [Violation(t, "test deleted") for t in sorted(changes.deleted)]
    violations += weakened_tests(base_tests, head_tests, changes)
    violations += static_violations(head_tests, changes.new | changes.changed)
    violations += config_violations(base_files, head_files)
    violations += citation_violations(body, changes.new | changes.changed)
    remaining = excuse(violations, body)

    new_tests_path.write_text("\n".join(sorted(changes.new)) + "\n", encoding="utf-8")
    note = commit_order_note(commits(base, head))
    rows = [f"New tests: {len(changes.new)} · changed: {len(changes.changed)} · deleted: {len(changes.deleted)}"]
    if note:
        print(f"::notice title=TDD order::{note}")
        rows.append(f"> ⚠️ {note} (reported, not enforced)")
    _report("TDD guard: test and config scan", remaining, rows)
    return 1 if remaining else 0


def probe_command(node_ids: list[str]) -> list[str]:
    """Builds the pytest command that runs node_ids with the outcome probe.

    One module failing to import must not stop the rest, so collection errors don't abort.

    Args:
        node_ids: Pytest node ids to run.

    Returns:
        list[str]: The command line.
    """
    return [
        sys.executable, "-m", "pytest", "-q", "-p", "tdd_probe", "-p", "no:cacheprovider",
        "--continue-on-collection-errors", *node_ids,
    ]


def run_fail_first(new_tests_path: Path, body: str) -> int:
    """Runs the PR's new tests against the base code checked out in the current directory.

    Args:
        new_tests_path: File of new test ids written by run_static.
        body: PR body.

    Returns:
        int: 1 if any new test passed, skipped, or never ran on base (unexcused), else 0.
    """
    new_tests = {line for line in new_tests_path.read_text(encoding="utf-8").splitlines() if line}
    if not new_tests:
        _report("TDD guard: new tests fail first", [], ["No new tests in this PR."])
        return 0
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "outcomes.json"
        env = {
            **os.environ,
            "PYTHONPATH": f"{SOURCE_ROOTS}:{Path(__file__).resolve().parent}",
            "TDD_PROBE_OUT": str(out),
        }
        subprocess.run(probe_command(sorted(new_tests)), env=env, check=False)
        outcomes = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    violations, results = fail_first_violations(new_tests, outcomes)
    remaining = excuse(violations, body)
    rows = ["| New test | On base code | Why it failed |", "|---|---|---|"]
    rows += [f"| `{test}` | {status} | {reason.replace('|', '/')[:120]} |" for test, status, reason in results]
    _report("TDD guard: new tests fail first", remaining, rows)
    return 1 if remaining else 0


def main(argv: list[str]) -> int:
    """Dispatches the "static" or "fail-first" check.

    Args:
        argv: Command-line arguments.

    Returns:
        int: Process exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    static = sub.add_parser("static")
    static.add_argument("--base", required=True)
    static.add_argument("--head", required=True)
    static.add_argument("--new-tests", type=Path, required=True)
    first = sub.add_parser("fail-first")
    first.add_argument("--new-tests", type=Path, required=True)
    args = parser.parse_args(argv)
    body = os.environ.get("PR_BODY", "")
    if args.command == "static":
        return run_static(args.base, args.head, args.new_tests, body)
    return run_fail_first(args.new_tests, body)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
