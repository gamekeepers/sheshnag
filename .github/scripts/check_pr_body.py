#!/usr/bin/env python3
"""Fail unless a pull request's description answers the template's sections.

Reads the body from $BODY and the section headings from the template, so the
two cannot drift: a heading renamed in the template is the heading checked
here. One error line per missing answer, naming the heading and what it wants.
"""
import os
import re
import sys
from pathlib import Path

TEMPLATE = Path(".github/pull_request_template.md")

# What each section is for, in the words the author sees when it is empty.
ASKS = {
    "what changes for someone using it": "one line naming a screen, a command's output or an API response",
    "how i ran it": "the command you ran and what it printed",
    "what a reviewer must do to run this": "migrations, env vars and new dependencies — or the word \"nothing\"",
}


def sections(markdown: str) -> dict:
    """{lowercased heading: (heading as written, body)} for each `##` heading.

    Matched case-insensitively so a heading is not missed over its capitals;
    the author's own spelling is what the error message quotes back.
    """
    out, heading, shown, buf = {}, None, None, []
    for line in markdown.splitlines():
        m = re.match(r"^##\s+(.*?)\s*$", line)
        if m:
            if heading is not None:
                out[heading] = (shown, "\n".join(buf))
            shown = m.group(1).strip()
            heading, buf = shown.lower(), []
        elif heading is not None:
            buf.append(line)
    if heading is not None:
        out[heading] = (shown, "\n".join(buf))
    return out


def answered(text: str) -> bool:
    """Is there anything here the author wrote?"""
    stripped = re.sub(r"<!--.*?-->", "", text, flags=re.S)       # template prompts
    stripped = re.sub(r"^\s*```.*?^\s*```", lambda m: m.group(0).strip("` \n"),
                      stripped, flags=re.S | re.M)               # keep fence contents
    return bool(stripped.strip())


def main() -> int:
    body = os.environ.get("BODY") or ""
    template = TEMPLATE.read_text(encoding="utf-8") if TEMPLATE.exists() else ""

    if not body.strip():
        print("::error::The description is empty. The template's three "
              "sections are what a reviewer needs to run this.")
        return 1

    if template and body.strip() == template.strip():
        print("::error::The description is still the template. Answer its "
              "three sections.")
        return 1

    from_template = sections(template)
    wanted = [h for h in from_template if h in ASKS] or list(ASKS)
    given = sections(body)
    missing = []
    for heading in wanted:
        label = from_template.get(heading, (heading, ""))[0]
        if heading not in given:
            missing.append(f'"{label}" is missing. Add it: {ASKS[heading]}.')
        elif not answered(given[heading][1]):
            missing.append(f'"{label}" is empty. It wants {ASKS[heading]}.')

    for problem in missing:
        print(f"::error::{problem}")
    if missing:
        print("::notice::Template: .github/pull_request_template.md")
        return 1

    print(f"Description answers all {len(wanted)} sections.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
