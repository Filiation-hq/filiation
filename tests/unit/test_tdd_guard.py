"""Unit tests for the anti-gaming TDD guard in .github/scripts/check_tdd.py (#73).

Task map:
    Input: base and head contents of the PR's changed files, the PR body, per-test outcomes
        of the PR's new tests run against the base code, and the PR's commits.
    Output: violations, each with a subject (a test or file) that a
        "Guardrail exception: <subject> — <reason>" line under Before merge can excuse.
    Edge cases:
        Fail-first: a new test that passes on base; a parametrized test where only some
            cases fail; a new test file that fails to import on base (counts as failing,
            with the reason shown); a new test that never ran on base.
        Static scan: assertions written as self.assertX, pytest.raises, stub
            assert_* calls, or a same-module helper that asserts (all real); assert True,
            assert <constant>, assert x == x; broad except that swallows failures; skip
            and xfail markers or calls; tests inside classes.
        Weakening: deleted tests (including a whole deleted file); an existing test losing
            assertions; a test only moved or reformatted (not a change); added noqa or
            pragma: no cover; [tool.ruff] / [tool.pytest] edits; guard-file edits.
        Citations: an edge case citing an old, untouched test; parametrized ids.
        Exceptions: subject not named; reason too short; the line outside Before merge.
        Commit order: source committed before any test (reported only).
    Invariants: nothing is excused without a named subject and a reason; outcomes, not
        test wording, decide fail-first.
"""

import importlib.util
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / ".github" / "scripts"


def load_guard():
    """Imports .github/scripts/check_tdd.py, which imports its sibling check_pr_body.

    Returns:
        module: The loaded check_tdd module.
    """
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location("check_tdd", SCRIPTS / "check_tdd.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_tdd"] = module  # dataclasses resolve their module through sys.modules
    spec.loader.exec_module(module)
    return module


def src(text: str) -> str:
    """Dedents an inline Python source sample.

    Args:
        text: Indented source.

    Returns:
        str: Dedented source.
    """
    return textwrap.dedent(text).lstrip()


def subjects(violations) -> list[str]:
    """Extracts subjects from violations.

    Args:
        violations: Violation objects.

    Returns:
        list[str]: Their subjects, in order.
    """
    return [v.subject for v in violations]


BASE_TEST = src(
    """
    def test_kept():
        assert 1 + 1 == 2
        assert "a".upper() == "A"

    def test_removed():
        assert True is not False
    """
)


# --- classify_tests --------------------------------------------------------------------


def test_classify_tests_finds_new_changed_and_deleted_functions():
    # Sample: base has test_kept and test_removed; head drops one assert from test_kept,
    # deletes test_removed, and adds test_added.
    head = src(
        """
        def test_kept():
            assert 1 + 1 == 2

        def test_added():
            assert 2 * 2 == 4
        """
    )
    changes = load_guard().classify_tests({"tests/unit/test_a.py": BASE_TEST}, {"tests/unit/test_a.py": head})
    assert changes.new == {"tests/unit/test_a.py::test_added"}
    assert changes.changed == {"tests/unit/test_a.py::test_kept"}
    assert changes.deleted == {"tests/unit/test_a.py::test_removed"}


def test_classify_tests_treats_moved_or_reformatted_test_as_unchanged():
    # Sample: head reorders the two tests and adds blank lines. The code is identical, so
    # neither is new, changed, or deleted.
    head = src(
        """
        def test_removed():

            assert True is not False


        def test_kept():
            assert 1 + 1 == 2
            assert "a".upper() == "A"
        """
    )
    changes = load_guard().classify_tests({"t.py": BASE_TEST}, {"t.py": head})
    assert (changes.new, changes.changed, changes.deleted) == (set(), set(), set())


def test_classify_tests_reports_every_test_of_a_deleted_file_as_deleted():
    # Sample: tests/unit/test_a.py exists on base and is absent on head.
    changes = load_guard().classify_tests({"tests/unit/test_a.py": BASE_TEST}, {})
    assert changes.deleted == {"tests/unit/test_a.py::test_kept", "tests/unit/test_a.py::test_removed"}


def test_classify_tests_names_class_methods_with_their_class():
    # Sample: a unittest-style class gains one method.
    head = src(
        """
        class TestThing:
            def test_new_method(self):
                self.assertEqual(1, 1)
        """
    )
    changes = load_guard().classify_tests({}, {"t.py": head})
    assert changes.new == {"t.py::TestThing::test_new_method"}


# --- weakened_tests ----------------------------------------------------------------------


def test_weakened_tests_flags_existing_test_that_lost_an_assertion():
    # Sample: test_kept goes from two asserts to one.
    head = BASE_TEST.replace('    assert "a".upper() == "A"\n', "")
    guard = load_guard()
    changes = guard.classify_tests({"t.py": BASE_TEST}, {"t.py": head})
    assert subjects(guard.weakened_tests({"t.py": BASE_TEST}, {"t.py": head}, changes)) == ["t.py::test_kept"]


def test_weakened_tests_ignores_existing_test_that_gained_an_assertion():
    # Sample: test_kept goes from two asserts to three.
    head = BASE_TEST.replace('    assert "a".upper() == "A"\n', '    assert "a".upper() == "A"\n    assert 3 > 2\n')
    guard = load_guard()
    changes = guard.classify_tests({"t.py": BASE_TEST}, {"t.py": head})
    assert guard.weakened_tests({"t.py": BASE_TEST}, {"t.py": head}, changes) == []


# --- static_violations -------------------------------------------------------------------

STATIC_SAMPLE = src(
    """
    import pytest

    def check_shape(x):
        assert x.shape == (2, 2)

    def test_plain_assert():
        assert parse("K123456") == "K123456"

    def test_raises():
        with pytest.raises(ValueError):
            parse("")

    def test_stub_assert(stubber):
        stubber.assert_no_pending_responses()

    def test_helper_asserts():
        check_shape(make())

    def test_no_assertion():
        parse("K123456")

    def test_assert_true():
        parse("K123456")
        assert True

    def test_assert_constant():
        assert 1

    def test_self_compare():
        value = parse("K1")
        assert value == value

    def test_swallowed():
        try:
            assert parse("K1") == "K2"
        except AssertionError:
            pass

    def test_broad_except():
        try:
            assert parse("K1") == "K2"
        except Exception:
            print("ignored")

    @pytest.mark.skip(reason="later")
    def test_skipped():
        assert parse("K1") == "K1"

    @pytest.mark.xfail
    def test_xfailed():
        assert parse("K1") == "K2"

    def test_skip_call():
        pytest.skip("not today")
        assert parse("K1") == "K1"

    class TestUnit:
        def test_unittest_style(self):
            self.assertEqual(parse("K1"), "K1")
    """
)


def static_subjects_for(*names: str) -> list[str]:
    """Runs the static scan on STATIC_SAMPLE for the named tests.

    Args:
        *names: Test qualnames in STATIC_SAMPLE.

    Returns:
        list[str]: Subjects of the resulting violations.
    """
    targets = {f"t.py::{n}" for n in names}
    return subjects(load_guard().static_violations({"t.py": STATIC_SAMPLE}, targets))


@pytest.mark.parametrize(
    "name",
    ["test_plain_assert", "test_raises", "test_stub_assert", "test_helper_asserts", "TestUnit::test_unittest_style"],
)
def test_static_scan_accepts_real_assertions_in_every_form(name):
    # Sample: STATIC_SAMPLE tests that check a result via assert, pytest.raises, a stub's
    # assert_* call, a same-module helper that asserts, or unittest's self.assertEqual.
    assert static_subjects_for(name) == []


@pytest.mark.parametrize(
    "name",
    [
        "test_no_assertion",
        "test_assert_true",
        "test_assert_constant",
        "test_self_compare",
        "test_swallowed",
        "test_broad_except",
        "test_skipped",
        "test_xfailed",
        "test_skip_call",
    ],
)
def test_static_scan_flags_tests_that_cannot_fail(name):
    # Sample: STATIC_SAMPLE tests that pass whatever parse() returns: no check, a constant
    # or self-comparison, a swallowed AssertionError or broad except, or a skip/xfail.
    assert static_subjects_for(name) == [f"t.py::{name}"]


def test_static_scan_only_checks_targeted_tests():
    # Sample: STATIC_SAMPLE scanned for test_plain_assert only. The bad tests in the same
    # file are pre-existing, so they are out of scope for this PR.
    assert static_subjects_for("test_plain_assert") == []


# --- config_violations -------------------------------------------------------------------

PYPROJECT = src(
    """
    [project]
    name = "x"

    [tool.pytest.ini_options]
    testpaths = ["tests"]

    [tool.ruff.lint]
    select = ["E", "F"]
    """
)


def test_config_scan_flags_ruff_and_pytest_setting_changes():
    # Sample: head adds an ignore to [tool.ruff.lint] and a deselect to pytest addopts.
    head = PYPROJECT.replace('select = ["E", "F"]', 'select = ["E", "F"]\nignore = ["F401"]').replace(
        'testpaths = ["tests"]', 'testpaths = ["tests"]\naddopts = "--deselect tests/unit/test_a.py"'
    )
    found = subjects(load_guard().config_violations({"pyproject.toml": PYPROJECT}, {"pyproject.toml": head}))
    assert found == ["pyproject.toml [tool.pytest.ini_options]", "pyproject.toml [tool.ruff.lint]"]


def test_config_scan_ignores_non_tool_pyproject_changes():
    # Sample: head only changes the project name.
    head = PYPROJECT.replace('name = "x"', 'name = "y"')
    assert load_guard().config_violations({"pyproject.toml": PYPROJECT}, {"pyproject.toml": head}) == []


@pytest.mark.parametrize(
    "path", [".github/scripts/check_pr_body.py", ".github/workflows/ci.yml", "AGENTS.md", "Makefile"]
)
def test_config_scan_flags_edits_to_guard_files(path):
    # Sample: one line changed in a file that defines the rules or the checks themselves.
    found = subjects(load_guard().config_violations({path: "a\n"}, {path: "b\n"}))
    assert found == [path]


@pytest.mark.parametrize(
    "line", ["x = compute()  # noqa: E501", "if debug:  # pragma: no cover"], ids=["noqa", "pragma"]
)
def test_config_scan_flags_added_lint_or_coverage_suppressions(line):
    # Sample: a source file gains one suppression comment.
    path = "packages/core/src/stemma/core/x.py"
    found = subjects(load_guard().config_violations({path: "y = 1\n"}, {path: f"y = 1\n{line}\n"}))
    assert found == [f"{path}: suppression"]


def test_config_scan_ignores_suppressions_that_already_existed():
    # Sample: the noqa line exists on base and head; only an unrelated line changes.
    path = "packages/core/src/stemma/core/x.py"
    base = "x = compute()  # noqa: E501\ny = 1\n"
    assert load_guard().config_violations({path: base}, {path: base.replace("y = 1", "y = 2")}) == []


def test_config_scan_ignores_suppression_text_inside_strings():
    # Sample: a test file gains string samples holding a noqa marker and a no-cover pragma.
    # Only real comments suppress anything, so this is not a suppression.
    path = "tests/unit/test_x.py"
    head = 'SAMPLE = "x = 1  # noqa: E501"\nOTHER = "if x:  # pragma: no cover"\n'
    assert load_guard().config_violations({path: ""}, {path: head}) == []


# --- citation_violations -----------------------------------------------------------------


def body_with(edge_cases: str = "", before_merge: str = "None.") -> str:
    """Builds a minimal PR body with the given Edge cases and Before merge sections.

    Args:
        edge_cases: Text under "## Edge cases".
        before_merge: Text under "## Before merge".

    Returns:
        str: PR body.
    """
    return f"## Summary\nx\n\n## Edge cases\n{edge_cases}\n\n## Before merge\n{before_merge}\n"


def test_citation_scan_flags_edge_case_citing_an_untouched_old_test():
    # Sample: the PR adds test_new but cites test_old, which it never touched.
    body = body_with("- A → B (`test_new`)\n- C → D (`test_old`)")
    found = subjects(load_guard().citation_violations(body, {"t.py::test_new"}))
    assert found == ["test_old"]


def test_citation_scan_accepts_parametrized_ids_and_class_methods():
    # Sample: citations of a parametrized case and of a method the PR changed.
    body = body_with("- A → B (`test_new[case-1]`)\n- C → D (`test_method`)")
    changed = {"t.py::test_new", "t.py::TestX::test_method"}
    assert load_guard().citation_violations(body, changed) == []


# --- excuse --------------------------------------------------------------------------------


def violation(subject: str):
    """Builds a guard violation for subject.

    Args:
        subject: Test or file the violation is about.

    Returns:
        Violation: With a fixed message.
    """
    return load_guard().Violation(subject, "flagged")


def test_exception_line_naming_the_subject_with_a_reason_excuses_it():
    # Sample: a pinning test that passes on base by design.
    body = body_with(before_merge="Guardrail exception: t.py::test_pin — pins existing parser behavior before refactor")
    assert load_guard().excuse([violation("t.py::test_pin")], body) == []


@pytest.mark.parametrize(
    "before_merge",
    [
        "Guardrail exception: t.py::test_other — pins existing parser behavior before refactor",
        "Guardrail exception: t.py::test_pin — fine",
        "Guardrail exception: t.py::test_pin",
    ],
    ids=["wrong-subject", "short-reason", "no-reason"],
)
def test_exception_line_without_matching_subject_or_real_reason_excuses_nothing(before_merge):
    # Sample: an exception for another test, a one-word reason, and no reason at all.
    remaining = load_guard().excuse([violation("t.py::test_pin")], body_with(before_merge=before_merge))
    assert subjects(remaining) == ["t.py::test_pin"]


def test_exception_line_outside_before_merge_is_ignored():
    # Sample: the exception written under Edge cases instead of Before merge.
    body = body_with(edge_cases="Guardrail exception: t.py::test_pin — pins existing parser behavior before refactor")
    assert subjects(load_guard().excuse([violation("t.py::test_pin")], body)) == ["t.py::test_pin"]


# --- fail_first_violations ---------------------------------------------------------------


def test_fail_first_flags_new_test_that_passes_on_base():
    # Sample: two new tests run against base code: one failed (as TDD requires), one passed.
    outcomes = {
        "t.py::test_good": ["call-failed: AssertionError: assert None == 'K1'"],
        "t.py::test_gamed": ["passed"],
    }
    violations, rows = load_guard().fail_first_violations({"t.py::test_good", "t.py::test_gamed"}, outcomes)
    assert subjects(violations) == ["t.py::test_gamed"]
    assert ("t.py::test_good", "failed", "AssertionError: assert None == 'K1'") in rows


def test_fail_first_counts_parametrized_test_as_failing_if_any_case_fails():
    # Sample: one parametrized test whose cases split: one passes on base, one fails.
    outcomes = {"t.py::test_param": ["passed", "call-failed: AssertionError"]}
    violations, _ = load_guard().fail_first_violations({"t.py::test_param"}, outcomes)
    assert violations == []


def test_fail_first_counts_import_error_on_base_as_failing_and_shows_it():
    # Sample: a new test module that imports a module only the PR adds. On base the file
    # fails to collect; every test in it counts as failing, with the import error shown.
    outcomes = {"t.py": ["collection-error: ModuleNotFoundError: No module named 'stemma.new'"]}
    violations, rows = load_guard().fail_first_violations({"t.py::test_a"}, outcomes)
    assert violations == []
    assert rows == [("t.py::test_a", "failed", "ModuleNotFoundError: No module named 'stemma.new'")]


def test_fail_first_flags_new_test_that_never_ran_on_base():
    # Sample: no outcome recorded for the test or its file (e.g. it was deselected).
    violations, _ = load_guard().fail_first_violations({"t.py::test_ghost"}, {})
    assert subjects(violations) == ["t.py::test_ghost"]


def test_probe_plugin_records_pass_fail_and_collection_error(tmp_path):
    # Sample: a temp suite with one passing test, one failing test, and one module that
    # imports a missing module, run with the guard's own pytest command. The import error
    # must not stop the other module: pytest aborts on collection errors by default.
    (tmp_path / "test_ok.py").write_text("def test_pass():\n    assert 1 == 1\n\ndef test_fail():\n    assert 1 == 2\n")
    (tmp_path / "test_broken.py").write_text("import not_a_real_module\n\ndef test_x():\n    assert 1\n")
    out = tmp_path / "outcomes.json"
    env = {**os.environ, "PYTHONPATH": str(SCRIPTS), "TDD_PROBE_OUT": str(out)}
    subprocess.run(
        load_guard().probe_command(["test_ok.py", "test_broken.py"]),
        cwd=tmp_path,
        env=env,
        capture_output=True,
        check=False,
    )
    outcomes = json.loads(out.read_text())
    assert outcomes["test_ok.py::test_pass"] == ["passed"]
    assert outcomes["test_ok.py::test_fail"][0].startswith("call-failed: ")
    assert outcomes["test_broken.py"][0].startswith("collection-error: ")
    assert "not_a_real_module" in outcomes["test_broken.py"][0]


# --- commit_order_note -------------------------------------------------------------------


def test_commit_order_note_reports_source_committed_before_tests():
    # Sample: commit 1 touches only packages/, commit 2 adds tests.
    commits = [("aaa1111", ["packages/core/src/stemma/core/x.py"]), ("bbb2222", ["tests/unit/test_x.py"])]
    note = load_guard().commit_order_note(commits)
    assert note is not None and "aaa1111" in note


def test_commit_order_note_is_silent_when_tests_come_first():
    # Sample: the TDD order: tests in commit 1, source in commit 2.
    commits = [("aaa1111", ["tests/unit/test_x.py"]), ("bbb2222", ["packages/core/src/stemma/core/x.py"])]
    assert load_guard().commit_order_note(commits) is None


# --- git plumbing --------------------------------------------------------------------------


def test_changed_files_and_file_at_read_a_real_git_history(tmp_path):
    # Sample: a temp repo with a base commit (a.py) and a head commit that edits a.py,
    # deletes b.py, and adds c.py. Identity is passed per command, never via git config.
    def git(*args):
        return subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false", *args],
            cwd=tmp_path, capture_output=True, text=True, check=True,
        ).stdout.strip()

    git("init", "-q")
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "b.py").write_text("y = 1\n")
    git("add", ".")
    git("commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    (tmp_path / "a.py").write_text("x = 2\n")
    (tmp_path / "b.py").unlink()
    (tmp_path / "c.py").write_text("z = 1\n")
    git("add", "-A")
    git("commit", "-qm", "head")
    head = git("rev-parse", "HEAD")

    guard = load_guard()
    assert guard.changed_files(base, head, cwd=tmp_path) == ["a.py", "b.py", "c.py"]
    assert guard.file_at(base, "a.py", cwd=tmp_path) == "x = 1\n"
    assert guard.file_at(head, "b.py", cwd=tmp_path) is None
    assert guard.commits(base, head, cwd=tmp_path)[0][1] == ["a.py", "b.py", "c.py"]


# --- workflow ------------------------------------------------------------------------------

WORKFLOW = SCRIPTS.parent / "workflows" / "tdd-guard.yml"


def test_guard_workflow_runs_on_every_pr_event_that_can_change_the_answer():
    # Sample: tdd-guard.yml. Editing the body can add an exception line; pushing changes code.
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "pull_request:" in text
    for event in ("opened", "edited", "reopened", "synchronize"):
        assert event in text


def test_guard_workflow_runs_new_tests_against_base_code_with_head_tests():
    # Sample: tdd-guard.yml. Fail-first means: base code, head tests, head's guard scripts
    # copied aside first (the base checkout may not have them).
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "fetch-depth: 0" in text
    assert text.index('cp .github/scripts/') < text.index('git checkout -q "$BASE_SHA"')
    assert 'git checkout -q "$HEAD_SHA" -- tests' in text
    assert "fail-first" in text


def test_guard_workflow_runs_fail_first_even_when_the_static_scan_fails():
    # Sample: tdd-guard.yml. Both reports should appear on one run, so the second step
    # must not be skipped when the first fails.
    text = WORKFLOW.read_text(encoding="utf-8")
    fail_first_step = text[text.index("New tests must fail"):]
    assert "if: always()" in fail_first_step
