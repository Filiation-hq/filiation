"""Validates that a pull request body follows .github/pull_request_template.md."""

import os
import re
import sys
from pathlib import Path

REQUIRED_HEADINGS = [
    "## Summary",
    "## Commits",
    "## Validation",
    "## Edge cases",
    "## Contract / data changes",
    "## Before merge",
    "## Checklist",
]

REQUIRED_FIELDS = {
    "Closes #N": re.compile(r"^Closes #\d+", re.MULTILINE),
    "Milestone": re.compile(r"^Milestone:[ \t]*\S", re.MULTILINE),
    "Base branch": re.compile(r"^Base branch:[ \t]*\S", re.MULTILINE),
    "`pytest` result": re.compile(r"^- `pytest`:[ \t]*\S", re.MULTILINE),
    "`ruff check .` result": re.compile(r"^- `ruff check \.`:[ \t]*\S", re.MULTILINE),
}

SUMMARY_METADATA = re.compile(r"^(Closes #.*|Milestone:.*|Base branch:.*)$", re.MULTILINE)
HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
EMPTY_BULLET = re.compile(r"^[ \t]*-[ \t]*$", re.MULTILINE)
EDGE_HEADING = "## Edge cases"
NONE_LINE = re.compile(r"^(none|n/?a)\b[\s.:;,—–-]*(.*)$", re.IGNORECASE)
MIN_NONE_REASON_WORDS = 6
MIN_UNTESTED_REASON_WORDS = 3
ARROW = re.compile(r"\s*(?:→|->)\s*")
TEST_REF = re.compile(r"`(test_\w+)(?:\[[^\]`]*\])?`")
UNTESTED = re.compile(r"untested:\s*([^)]*)", re.IGNORECASE)
TEST_DEF = re.compile(r"^\s*def (test_\w+)", re.MULTILINE)
AI_NAMES = r"(claude|anthropic|openai|chatgpt|gpt-?\d*|copilot|codex|gemini|cursor|devin)"
AI_ATTRIBUTION = [
    re.compile(rf"^co-authored-by:.*\b{AI_NAMES}\b", re.IGNORECASE | re.MULTILINE),
    re.compile(rf"generated (with|by)\b.*\b{AI_NAMES}\b", re.IGNORECASE),
]


def split_sections(body: str) -> dict[str, str]:
    """Maps each level-2 heading line to the text beneath it.

    Args:
        body: PR body with normalized line endings.

    Returns:
        dict[str, str]: Heading line to section text, in document order.
    """
    sections: dict[str, str] = {}
    current = None
    for line in body.split("\n"):
        if line.startswith("## "):
            current = line.rstrip()
            sections[current] = ""
        elif current is not None:
            sections[current] += line + "\n"
    return sections


def _edge_case_lines(section: str) -> list[str]:
    """Returns the meaningful lines of an Edge cases section, without hints or empty bullets.

    Args:
        section: Raw text under the Edge cases heading.

    Returns:
        list[str]: Non-blank lines, stripped of trailing whitespace.
    """
    text = EMPTY_BULLET.sub("", HTML_COMMENT.sub("", section))
    return [line.rstrip() for line in text.split("\n") if line.strip()]


def edge_case_violations(section: str) -> list[str]:
    """Checks that every edge case names its handling and its proof, or that "None" is explained.

    Args:
        section: Raw text under the Edge cases heading.

    Returns:
        list[str]: Violations; empty if the section is valid or empty (reported elsewhere).
    """
    lines = _edge_case_lines(section)
    if not lines:
        return []
    none = NONE_LINE.match(lines[0].lstrip("- ").strip())
    if none and len(lines) == 1:
        if len(none.group(2).split()) < MIN_NONE_REASON_WORDS:
            return [
                "Edge cases: 'None' needs a reason (at least "
                f"{MIN_NONE_REASON_WORDS} words) explaining why this change has no edge cases"
            ]
        return []

    violations = []
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("- "):
            violations.append(f"Edge cases: not a bullet: {stripped[:60]!r}")
            continue
        parts = ARROW.split(stripped[2:], maxsplit=1)
        if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
            violations.append(f"Edge cases: missing 'case → handling': {stripped[:60]!r}")
            continue
        untested = UNTESTED.search(parts[1])
        if TEST_REF.search(parts[1]):
            continue
        if untested and len(untested.group(1).split()) >= MIN_UNTESTED_REASON_WORDS:
            continue
        violations.append(
            "Edge cases: cite the test (`test_name`) or write 'untested: <reason>': "
            f"{stripped[:60]!r}"
        )
    return violations


def find_unknown_test_references(body: str, tests_root: Path) -> list[str]:
    """Lists test names cited under Edge cases that no test file under tests_root defines.

    Args:
        body: PR body.
        tests_root: Directory searched recursively for `def test_...` definitions.

    Returns:
        list[str]: Cited test names with no definition, in first-cited order.
    """
    section = split_sections(body.replace("\r\n", "\n")).get(EDGE_HEADING, "")
    defined = {
        name
        for path in tests_root.rglob("*.py")
        for name in TEST_DEF.findall(path.read_text(encoding="utf-8"))
    }
    cited = dict.fromkeys(TEST_REF.findall(HTML_COMMENT.sub("", section)))
    return [name for name in cited if name not in defined]


def find_violations(body: str) -> list[str]:
    """Lists every way a PR body deviates from the mandatory template.

    Args:
        body: Raw pull request body as submitted to GitHub.

    Returns:
        list[str]: Human-readable violations; empty if the body is valid.
    """
    body = body.replace("\r\n", "\n")
    sections = split_sections(body)
    violations = [f"missing heading: {h}" for h in REQUIRED_HEADINGS if h not in sections]

    present = [h for h in sections if h in REQUIRED_HEADINGS]
    expected_order = [h for h in REQUIRED_HEADINGS if h in sections]
    if present != expected_order:
        violations.append("headings are out of template order")

    for heading in expected_order:
        text = EMPTY_BULLET.sub("", HTML_COMMENT.sub("", sections[heading]))
        if heading == "## Summary":
            text = SUMMARY_METADATA.sub("", text)
        if not text.strip():
            hint = "list each edge case, or 'None — <why>'" if heading == EDGE_HEADING else "write 'None.' if N/A"
            violations.append(f"section '{heading}' is empty ({hint})")

    if EDGE_HEADING in sections:
        violations.extend(edge_case_violations(sections[EDGE_HEADING]))

    for name, pattern in REQUIRED_FIELDS.items():
        if not pattern.search(body):
            violations.append(f"field not filled in: {name}")

    for pattern in AI_ATTRIBUTION:
        for match in pattern.finditer(body):
            violations.append(f"AI attribution is not allowed: {match.group(0).strip()}")

    return violations


def main() -> int:
    """Checks the PR body in the PR_BODY environment variable and reports violations.

    Returns:
        int: Process exit code, 0 if valid and 1 if any violation was found.
    """
    body = os.environ.get("PR_BODY", "")
    violations = find_violations(body)
    tests_root = Path(__file__).resolve().parents[2] / "tests"
    if tests_root.is_dir():
        violations += [
            f"Edge cases: cited test does not exist under tests/: {name}"
            for name in find_unknown_test_references(body, tests_root)
        ]
    else:
        violations.append("Edge cases: tests/ not checked out, so cited tests can't be verified")
    for violation in violations:
        print(f"::error title=PR template::{violation}")
    if violations:
        print("PR body must follow .github/pull_request_template.md (see AGENTS.md Section 8).")
        return 1
    print("PR body follows the template.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
