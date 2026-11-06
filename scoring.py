"""
scoring.py - the deterministic checks evals.py uses to grade answers.

Kept separate from evals.py so they can be tested offline (test_scoring.py),
with no API key and no model calls.

Concept: test the grader too. A grader bug looks exactly like an agent failure.
The first eval run failed 2 of 15 cases, and both turned out to be grader false
positives (see test_scoring.py). Without checking the failing answers by hand,
the agent would have been "fixed" for problems it didn't have.
"""

import re

# Things that look like file paths: lib/db.ts, page.tsx, 001_create_tables.sql.
# (?!\() skips method calls: NextResponse.json(...) and request.json() are code,
# not files called "NextResponse.json".
PATH_RE = re.compile(r"[\w\-\[\]./]+\.(?:tsx|ts|jsx|js|json|sql|md|css|py)\b(?!\()")

# Names that end like a file but are product names.
NOT_PATHS = {"next.js", "node.js", "react.js", "vue.js", "nuxt.js", "express.js"}


def normalise(text: str) -> str:
    """Lowercase and straighten curly apostrophes, so "can’t" matches "can't"."""
    return text.casefold().replace("’", "'").replace("‘", "'")


def hallucinated_paths(answer: str, real_files: set[str], question: str = "") -> list[str]:
    """File paths mentioned in the answer that don't exist in the repo.

    real_files should be EVERY file in the repo, including blocked ones such as
    node_modules/..., because naming a blocked file while refusing it isn't
    inventing anything. Paths copied from the question are allowed for the same reason.
    """
    bad = []
    for token in PATH_RE.findall(answer):
        t = token.strip("./").lstrip("/")
        if not t or t.casefold() in NOT_PATHS or token in question:
            continue
        # Accept a full path ("lib/db.ts") or a bare file name that exists ("db.ts").
        if t in real_files or any(f.endswith("/" + t) for f in real_files):
            continue
        bad.append(token)
    return sorted(set(bad))
