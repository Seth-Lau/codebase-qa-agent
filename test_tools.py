"""
test_tools.py - offline tests for the tools. Run with:  python test_tools.py

No API calls, so these are free, instant and give the same result every run.
They run against a temp copy of sample_repo/ with secrets and blocked folders
planted in it (see fixture.py), so they work on any machine, including CI.

Concept: test the deterministic parts (your Python tools) with ordinary tests.
Save LLM evals (evals.py) for the parts that aren't deterministic
(the model's decisions and answers). If these tests fail, no prompt tweak will fix it.
"""

import os

import tools
from fixture import CANARIES, OUTSIDE_FILE_NAME, build_fixture, cleanup_fixture
from tools import ToolError, is_blocked_name, list_files, read_file, run_tool, safe_path, search_code

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    if condition:
        print(f"  PASS  {label}")
    else:
        failures += 1
        print(f"  FAIL  {label}")


def is_refused(fn, *args) -> bool:
    """True if calling fn(*args) raises ToolError."""
    try:
        fn(*args)
    except ToolError:
        return True
    return False


def leaks_canary(text: str) -> list[str]:
    """Names of any canaries that appear in `text`."""
    return [name for name, value in CANARIES.items() if value in text]


root = build_fixture()
tools.set_repo_root(root)
print(f"REPO_ROOT = {tools.REPO_ROOT}\n")

try:
    # --- 1. Paths that MUST be refused ----------------------------------------
    # Each comment names the attack. These are the classic path-traversal tricks.
    print("safe_path: paths that must be BLOCKED")
    SHOULD_BLOCK = [
        "..",                                   # parent of the repo
        "../..",                                # further up
        "app/../../",                           # climb out through a subfolder
        f"../{OUTSIDE_FILE_NAME}",              # a real file just outside the root
        f"../{root.name}-evil",                 # sibling folder with the same name prefix
        "/etc/passwd",                          # absolute path (Unix)
        "//server/share",                       # network (UNC) path
        "C:secret.txt",                         # drive-relative path (Windows quirk)
        ".env",                                 # secrets
        ".env.local",
        ".ENV.LOCAL",                           # case trick
        ".env.",                                # trailing-dot trick (Windows ignores it)
        ".env::$DATA",                          # alternate data stream trick
        "app/.env",                             # .env in a subfolder
        "node_modules",
        "Node_Modules",                         # case trick
        "node_modules/left-pad/index.js",
        "app/../node_modules",                  # sneaks in via '..'
        ".next/cache",
        ".git/config",
    ]
    # Backslash paths only mean "folder separator" on Windows. On Linux a backslash
    # is an ordinary filename character, so these are harmless there.
    if os.name == "nt":
        SHOULD_BLOCK += [
            r"..\..\Documents",                 # Windows backslashes
            r"C:\Windows\System32",             # absolute path (Windows)
            r"\Users",                          # root-relative path
        ]
    for p in SHOULD_BLOCK:
        check(is_refused(safe_path, p), f"blocked  {p!r}")

    # --- 2. Paths that MUST be allowed ----------------------------------------
    # safe_path doesn't care whether a file exists (that's the tool's job).
    print("\nsafe_path: paths that must be ALLOWED")
    SHOULD_ALLOW = [".", "package.json", "./package.json", "lib", "app/page.tsx", "env.ts", ".envrc-notes"]
    for p in SHOULD_ALLOW:
        check(not is_refused(safe_path, p), f"allowed  {p!r}")

    # --- 3. list_files ----------------------------------------------------------
    print("\nlist_files")
    root_listing = list_files(".")
    check("package.json" in root_listing.splitlines(), "root listing includes package.json")

    deep_listing = list_files(".", 3)
    leaked = [
        line for line in deep_listing.splitlines()[1:]   # skip the header line
        if any(is_blocked_name(seg) for seg in line.rstrip("/").split("/"))
    ]
    check(not leaked, f"depth-3 listing hides blocked names (leaked: {leaked[:3]})")
    check("lib/pricing.ts" in deep_listing.splitlines(), "depth-3 listing shows nested files")
    check(is_refused(list_files, "package.json"), "listing a file (not a folder) is a ToolError")
    check(is_refused(list_files, "this-folder-does-not-exist"), "missing folder is a ToolError")
    check(is_refused(list_files, ".git"), "listing .git is a ToolError")

    # --- 4. read_file -----------------------------------------------------------
    print("\nread_file")
    pricing = read_file("lib/pricing.ts")
    check("VAT_RATE = 0.2" in pricing, "reads a normal file")
    check(" 3 | export const VAT_RATE" in pricing, "output has line numbers")
    part = read_file("lib/pricing.ts", 8, 9)
    check("calculateTotal" in part and "VAT_RATE = 0.2" not in part, "start_line/end_line return just that range")
    check(is_refused(read_file, ".env"), ".env is refused")
    check(is_refused(read_file, ".env.local"), ".env.local is refused")
    check(is_refused(read_file, f"../{OUTSIDE_FILE_NAME}"), "file outside the root is refused")
    check(is_refused(read_file, "node_modules/left-pad/index.js"), "node_modules file is refused")
    check(is_refused(read_file, "public/logo.png"), "binary file is refused")
    check(is_refused(read_file, "lib"), "reading a folder is a ToolError")
    check(is_refused(read_file, "nope.ts"), "missing file is a ToolError")
    check(is_refused(read_file, "lib/pricing.ts", 999), "start_line past the end is a ToolError")

    # A long file must be truncated, and say where to carry on.
    (root / "lib" / "big.ts").write_text("\n".join(f"const x{i} = {i};" for i in range(5000)), encoding="utf-8")
    big = read_file("lib/big.ts")
    check(len(big) < tools.MAX_READ_CHARS + 500, f"long file is capped ({len(big):,} chars)")
    check("call again with start_line=" in big, "truncated output says where to continue")

    # --- 5. search_code ---------------------------------------------------------
    print("\nsearch_code")
    env_hits = search_code("process.env")
    check("lib/db.ts:5:" in env_hits, "finds process.env in lib/db.ts with a line number")
    check("PROCESS.ENV" not in env_hits and "lib/db.ts" in search_code("PROCESS.ENV"), "search is case-insensitive")
    check(search_code("calculateTotal", "lib").count("lib/pricing.ts") == 1, "path argument limits the search")
    check("No matches" in search_code("stripe"), "no matches is a normal result, not an error")
    check(search_code(".*").startswith("No matches"), "query is literal text, not a regex")
    check(is_refused(search_code, ""), "empty query is a ToolError")
    check(is_refused(search_code, "x", ".git"), "searching inside .git is a ToolError")
    # (The "No matches for '...'" message echoes the query, so check for no matches,
    # not for the canary string being absent.)
    for name, value in CANARIES.items():
        check(search_code(value).startswith("No matches"), f"search can't find the {name} canary")
    check(not leaks_canary(search_code("CANARY")), "search for 'CANARY' leaks nothing")

    # --- 6. The dispatcher ------------------------------------------------------
    print("\nrun_tool (dispatcher)")
    check(is_refused(run_tool, "delete_everything", {}), "unknown tool is a ToolError")
    check(is_refused(run_tool, "list_files", {"pathh": "."}), "misspelled argument is a ToolError")
    check(not is_refused(run_tool, "list_files", {"path": ".", "depth": 99}), "depth 99 is clamped, not an error")
    check(not is_refused(run_tool, "list_files", {"path": ".", "depth": "2"}), "depth as a string still works")
    check(not is_refused(run_tool, "read_file", {"path": "lib/db.ts", "start_line": "2"}), "start_line as a string still works")
    check(set(tools.TOOL_FUNCTIONS) == {t["name"] for t in tools.TOOL_DEFINITIONS}, "every defined tool has a function")

finally:
    cleanup_fixture(root)

# --- Summary --------------------------------------------------------------------
print()
if failures:
    print(f"{failures} test(s) FAILED")
    raise SystemExit(1)   # non-zero exit code = failure (CI reads this)
print("All tests passed.")
