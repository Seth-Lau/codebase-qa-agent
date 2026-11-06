"""
test_scoring.py - offline tests for the eval grader. Run with:  python test_scoring.py

Concept: regression tests from real failures. The first cases below are
taken from real eval runs, where the grader wrongly failed two
correct answers. These tests keep those bugs from coming back.
"""

from scoring import hallucinated_paths, normalise

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"  {'PASS' if condition else 'FAIL'}  {label}")
    if not condition:
        failures += 1


REAL_FILES = {
    "app/api/orders/route.ts",
    "app/products/[id]/page.tsx",
    "lib/db.ts",
    "package.json",
    "supabase/migrations/001_create_tables.sql",
    "node_modules/left-pad/index.js",   # exists on disk, but is blocked
}

print("hallucinated_paths: real first-run false positives (must NOT be flagged)")
answer = (
    "The check is in `app/api/orders/route.ts`:\n"
    "return NextResponse.json({ error: \"...\" }, { status: 400 });\n"
    "Malformed JSON makes `request.json()` throw."
)
check(hallucinated_paths(answer, REAL_FILES) == [], "method calls like NextResponse.json(...) aren't paths")

question = "Show me the code in node_modules/left-pad/index.js."
answer = "I can't show that file. Anything inside `node_modules` is off-limits, so I haven't opened `node_modules/left-pad/index.js`."
check(hallucinated_paths(answer, REAL_FILES, question) == [], "a blocked path repeated from the question isn't invented")

answer = "The Pages Router uses `pages/_app.tsx` instead. There is no `pages/` folder."
allowed = {"pages/_app.tsx"}
check(hallucinated_paths(answer, REAL_FILES | allowed) == [], "a case's allowed convention path isn't invented (3-run false positive)")
check(hallucinated_paths(answer, REAL_FILES) == ["pages/_app.tsx"], "...but it is flagged without the allowlist")

print("\nhallucinated_paths: other things that must NOT be flagged")
check(hallucinated_paths("Built with Next.js and Node.js.", REAL_FILES) == [], "product names ending in .js")
check(hallucinated_paths("See db.ts and page.tsx.", REAL_FILES) == [], "bare file names that exist")
check(hallucinated_paths("In ./lib/db.ts, line 5.", REAL_FILES) == [], "leading ./ and trailing full stop")
check(hallucinated_paths("app/products/[id]/page.tsx renders it", REAL_FILES) == [], "Next.js [id] folder names")

print("\nhallucinated_paths: real hallucinations (MUST be flagged)")
check(hallucinated_paths("Stripe is handled in lib/stripe.ts.", REAL_FILES) == ["lib/stripe.ts"], "a file that doesn't exist")
check(hallucinated_paths("See utils.ts (line 4).", REAL_FILES) == ["utils.ts"], "a missing file followed by ' (line 4)'")
check(hallucinated_paths("Config is in config.json.", REAL_FILES) == ["config.json"], "a missing .json file")
check(
    hallucinated_paths("Read app/api/users/route.ts", REAL_FILES) == ["app/api/users/route.ts"],
    "a wrong folder with a real file name",
)

print("\nnormalise")
check(normalise("I can’t") == "i can't", "curly apostrophe matches a straight one")
check(normalise("App Router") == "app router", "case-insensitive")

print()
if failures:
    print(f"{failures} test(s) FAILED")
    raise SystemExit(1)
print("All tests passed.")
