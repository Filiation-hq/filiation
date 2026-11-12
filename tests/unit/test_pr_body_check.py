"""Unit tests for the PR body template checker in .github/scripts/check_pr_body.py.

Task map:
    Input: a pull request body (markdown string).
    Output: a list of human-readable violations; empty means the body follows
        .github/pull_request_template.md.
    Edge cases: missing/reordered headings, empty sections, unfilled hint comment,
        missing `Closes #N`, blank Base branch / Milestone / validation lines,
        AI attribution lines, Windows line endings.
    Edge cases section (AGENTS.md §9): a bare "None." or "N/A" with no reason; a "None"
        reason too short to explain anything; a bullet with no "→" handling; a bullet that
        cites neither a test nor "untested: <reason>"; free text that is not a bullet;
        ASCII "->" arrows; parametrized test ids like test_x[case]; cited tests that do not
        exist in tests/.
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

## Edge cases
- Letter O in the digit positions ("KO12345") → repaired to "K012345" (`test_ocr_repair_replaces_letter_O_with_zero_in_k_number`)
- Candidate shorter than 7 characters → returns None (`test_ocr_repair_rejects_short_candidate`)

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
    assert sum("missing heading" in v for v in violations) == 7


def test_commits_section_with_only_empty_bullet_is_reported_as_empty():
    # Sample: the template's placeholder "-" bullet left under "## Commits" with no subject.
    body = VALID_BODY.replace("- extraction/ocr: add O-to-0 repair for K-numbers", "-")
    violations = load_checker().find_violations(body)
    assert any("Commits" in v and "empty" in v for v in violations)


def edge_cases(section: str) -> str:
    """Returns VALID_BODY with its Edge cases section replaced.

    Args:
        section: New text under "## Edge cases".

    Returns:
        str: The modified PR body.
    """
    start = VALID_BODY.index("## Edge cases\n") + len("## Edge cases\n")
    end = VALID_BODY.index("## Contract")
    return VALID_BODY[:start] + section + "\n" + VALID_BODY[end:]


def edge_violations(section: str) -> list[str]:
    """Returns only the Edge cases violations for a replacement section.

    Args:
        section: New text under "## Edge cases".

    Returns:
        list[str]: Violations that mention edge cases.
    """
    return [v for v in load_checker().find_violations(edge_cases(section)) if "dge case" in v]


def test_missing_edge_cases_heading_is_reported():
    # Sample: VALID_BODY with the whole "## Edge cases" section deleted.
    body = VALID_BODY.split("## Edge cases")[0] + "## Contract" + VALID_BODY.split("## Contract")[1]
    violations = load_checker().find_violations(body)
    assert any("## Edge cases" in v for v in violations)


def test_edge_cases_with_only_template_placeholder_is_reported_as_empty():
    # Sample: the template's hint comment and empty "-" bullet left untouched.
    violations = load_checker().find_violations(edge_cases("<!-- hint -->\n-\n"))
    assert any("Edge cases" in v and "empty" in v for v in violations)


def test_bare_none_without_reason_is_rejected():
    # Sample: "None." alone. Saying there are no edge cases requires explaining why.
    assert edge_violations("None.")


def test_not_applicable_shorthand_is_rejected():
    # Sample: "N/A". Same rule as a bare None.
    assert edge_violations("N/A")


def test_none_with_too_short_reason_is_rejected():
    # Sample: "None — trivial." Two words don't explain why no input can misbehave.
    assert edge_violations("None — trivial.")


def test_none_with_real_reason_is_accepted():
    # Sample: a docs-only PR. The reason names what changed and why no input reaches code.
    reason = "None — this PR only edits Markdown in docs/, so no code path or input changes."
    assert edge_violations(reason) == []


def test_bullet_without_handling_arrow_is_rejected():
    # Sample: names the case but never says what the code does about it.
    assert edge_violations("- Empty export zip (`test_count_export_records_rejects_empty_zip`)")


def test_bullet_without_test_or_untested_reason_is_rejected():
    # Sample: case and handling given, but no proof: no test cited, no "untested:" reason.
    assert edge_violations("- Empty export zip → raises ExportIntegrityError")


def test_untested_bullet_with_reason_is_accepted():
    # Sample: an honest gap. Untested is allowed only with the reason it can't be tested.
    bullet = "- R2 network timeout → boto3 retries (untested: botocore Stubber cannot simulate timeouts)"
    assert edge_violations(bullet) == []


def test_untested_without_reason_is_rejected():
    # Sample: "untested:" followed by nothing.
    assert edge_violations("- R2 network timeout → boto3 retries (untested:)")


def test_ascii_arrow_and_parametrized_test_id_are_accepted():
    # Sample: "->" typed instead of "→", and a pytest parametrize id in the test name.
    bullet = "- Key with '..' -> InvalidKeyError (`test_local_store_rejects_keys[parent-dir]`)"
    assert edge_violations(bullet) == []


def test_free_text_line_in_edge_cases_is_rejected():
    # Sample: prose instead of bullets. Each case must be its own checkable bullet.
    assert edge_violations("We handled the usual edge cases and they are tested.")


def test_cited_tests_that_do_not_exist_are_reported(tmp_path):
    # Sample: a tests/ tree defining one test; the body cites it and a made-up one.
    (tmp_path / "test_a.py").write_text("def test_real_one():\n    pass\n", encoding="utf-8")
    body = edge_cases("- A → B (`test_real_one`)\n- C → D (`test_made_up[case]`)")
    missing = load_checker().find_unknown_test_references(body, tmp_path)
    assert missing == ["test_made_up"]


def test_cited_test_methods_inside_classes_are_found(tmp_path):
    # Sample: a unittest-style method indented inside a class.
    (tmp_path / "test_b.py").write_text(
        "class T:\n    def test_in_class(self):\n        pass\n", encoding="utf-8"
    )
    body = edge_cases("- A → B (`test_in_class`)")
    assert load_checker().find_unknown_test_references(body, tmp_path) == []


def test_template_headings_match_the_checker_in_order():
    # Sample: .github/pull_request_template.md. If the template and REQUIRED_HEADINGS drift,
    # every PR filled from the template fails CI.
    template = (SCRIPT_PATH.parents[1] / "pull_request_template.md").read_text(encoding="utf-8")
    headings = [line.rstrip() for line in template.splitlines() if line.startswith("## ")]
    assert headings == load_checker().REQUIRED_HEADINGS


def test_pr_template_workflow_checks_out_tests_to_verify_cited_tests():
    # Sample: .github/workflows/pr-template.yml. Its sparse checkout must include tests/,
    # or every cited test would be reported as unverifiable.
    workflow = (SCRIPT_PATH.parents[1] / "workflows" / "pr-template.yml").read_text(encoding="utf-8")
    assert "tests" in workflow.split("sparse-checkout:")[1].split("- uses:")[0]


def test_test_names_inside_the_template_hint_comment_are_not_treated_as_citations(tmp_path):
    # Sample: the template's hint comment, which shows `test_that_proves_it` as an example,
    # left in place above a real bullet. Only the real bullet's citation counts.
    (tmp_path / "test_c.py").write_text("def test_real():\n    pass\n", encoding="utf-8")
    body = edge_cases("<!-- e.g. (`test_that_proves_it`) -->\n- A → B (`test_real`)")
    assert load_checker().find_unknown_test_references(body, tmp_path) == []
