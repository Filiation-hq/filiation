"""Validates that a pull request body follows .github/pull_request_template.md."""

import os
import re
import sys

REQUIRED_HEADINGS = [
    "## Summary",
    "## Commits",
    "## Validation",
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
            violations.append(f"section '{heading}' is empty (write 'None.' if N/A)")

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
    violations = find_violations(os.environ.get("PR_BODY", ""))
    for violation in violations:
        print(f"::error title=PR template::{violation}")
    if violations:
        print("PR body must follow .github/pull_request_template.md (see AGENTS.md Section 8).")
        return 1
    print("PR body follows the template.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
