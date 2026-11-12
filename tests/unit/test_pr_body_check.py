"""Unit tests for the PR body template checker in .github/scripts/check_pr_body.py.

Task map:
    Input: a pull request body (markdown string).
    Output: a list of human-readable violations; empty means the body follows
        .github/pull_request_template.md.
    Edge cases: missing/reordered headings, empty sections, unfilled hint comment,
        missing `Closes #N`, blank Base branch / Milestone / validation lines,
        AI attribution lines, Windows line endings.
    Invariant: a correctly filled template produces zero violations.
"""

import importlib.util
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[2] / ".github" / "scripts" / "check_pr_body.py"

# Sample: a fully filled PR body that follows the template exactly.
# Every other test mutates one part of this to isolate a single rule.
VALID_BODY = """## Summary
Add OCR repair for letter-O/zero confusion in scanned K-numbers.

Closes #12
Milestone: Pilot Extraction Harness
Base branch: main

## Commits
- extraction/ocr: add O-to-0 repair for K-numbers

## Validation
- `pytest`: 42 passed in 1.8s
- `ruff check .`: All checks passed!

## Contract / data changes
None.

## Before merge
None.

## Checklist
- [x] One logical piece of work; one task per commit
"""


def load_checker():
    """Loads the checker script as a module because .github/scripts is not a package.

    Returns:
        module: The imported check_pr_body module.
    """
    spec = importlib.util.spec_from_file_location("check_pr_body", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_filled_template_produces_no_violations():
    # Sample: VALID_BODY unchanged. Every required field is present and filled.
    assert load_checker().find_violations(VALID_BODY) == []


def test_missing_required_heading_is_reported_by_name():
    # Sample: VALID_BODY with the "## Commits" section removed entirely.
    body = VALID_BODY.replace("## Commits\n- extraction/ocr: add O-to-0 repair for K-numbers\n", "")
    violations = load_checker().find_violations(body)
    assert any("## Commits" in v for v in violations)


def test_headings_out_of_template_order_are_reported():
    # Sample: "## Checklist" moved above "## Summary"; all headings still present.
    body = "## Checklist\n- [x] done\n\n" + VALID_BODY.split("## Checklist")[0]
    violations = load_checker().find_violations(body)
    assert any("order" in v for v in violations)


def test_section_with_only_whitespace_is_reported_as_empty():
    # Sample: "## Before merge" left blank instead of "None.".
    body = VALID_BODY.replace("## Before merge\nNone.\n", "## Before merge\n\n")
    violations = load_checker().find_violations(body)
    assert any("Before merge" in v and "empty" in v for v in violations)


def test_summary_containing_only_the_template_hint_is_reported_as_empty():
    # Sample: Summary prose replaced by the untouched template hint comment.
    body = VALID_BODY.replace(
        "Add OCR repair for letter-O/zero confusion in scanned K-numbers.",
        "<!-- What changed and why, 1–3 sentences. -->",
    )
    violations = load_checker().find_violations(body)
    assert any("Summary" in v and "empty" in v for v in violations)


def test_closes_line_without_issue_number_is_reported():
    # Sample: template default "Closes #" with no number filled in.
    body = VALID_BODY.replace("Closes #12", "Closes #")
    violations = load_checker().find_violations(body)
    assert any("Closes #" in v for v in violations)


def test_blank_base_branch_is_reported():
    # Sample: "Base branch:" left as the template default with no value.
    body = VALID_BODY.replace("Base branch: main", "Base branch:")
    violations = load_checker().find_violations(body)
    assert any("Base branch" in v for v in violations)


def test_blank_milestone_is_reported():
    # Sample: "Milestone:" left as the template default with no value.
    body = VALID_BODY.replace("Milestone: Pilot Extraction Harness", "Milestone:")
    violations = load_checker().find_violations(body)
    assert any("Milestone" in v for v in violations)


def test_blank_pytest_validation_line_is_reported():
    # Sample: "- `pytest`:" left with no summary line pasted after it.
    body = VALID_BODY.replace("- `pytest`: 42 passed in 1.8s", "- `pytest`:")
    violations = load_checker().find_violations(body)
    assert any("pytest" in v for v in violations)


def test_blank_ruff_validation_line_is_reported():
    # Sample: "- `ruff check .`:" left with no result pasted after it.
    body = VALID_BODY.replace("- `ruff check .`: All checks passed!", "- `ruff check .`:")
    violations = load_checker().find_violations(body)
    assert any("ruff" in v for v in violations)


def test_ai_co_author_trailer_is_reported():
    # Sample: a Claude Co-Authored-By trailer appended to the body (banned by AGENTS.md §4).
    body = VALID_BODY + "\nCo-Authored-By: Claude <noreply@anthropic.com>\n"
    violations = load_checker().find_violations(body)
    assert any("attribution" in v for v in violations)


def test_generated_with_footer_is_reported():
    # Sample: a "Generated with Claude Code" footer appended to the body.
    body = VALID_BODY + "\n🤖 Generated with [Claude Code](https://claude.com/claude-code)\n"
    violations = load_checker().find_violations(body)
    assert any("attribution" in v for v in violations)


def test_human_co_author_trailer_is_allowed():
    # Sample: a Co-Authored-By trailer for Alden, a human developer. Must not be flagged.
    body = VALID_BODY + "\nCo-Authored-By: Alden Reagins <alden@example.com>\n"
    assert load_checker().find_violations(body) == []


def test_windows_line_endings_are_accepted():
    # Sample: VALID_BODY with \r\n line endings, as GitHub's web editor can submit.
    body = VALID_BODY.replace("\n", "\r\n")
    assert load_checker().find_violations(body) == []


def test_empty_body_reports_every_required_heading():
    # Sample: an empty PR body (author deleted the whole template).
    violations = load_checker().find_violations("")
    assert sum("missing heading" in v for v in violations) == 6


def test_commits_section_with_only_empty_bullet_is_reported_as_empty():
    # Sample: the template's placeholder "-" bullet left under "## Commits" with no subject.
    body = VALID_BODY.replace("- extraction/ocr: add O-to-0 repair for K-numbers", "-")
    violations = load_checker().find_violations(body)
    assert any("Commits" in v and "empty" in v for v in violations)
