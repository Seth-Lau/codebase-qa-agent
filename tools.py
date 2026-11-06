"""
tools.py - read-only tools for the codebase Q&A agent.

Contains:
  1. The SAFETY LAYER (safe_path). Every tool calls this before it touches the disk.
  2. Three tools: list_files, read_file, search_code.
  3. The TOOL REGISTRY: the tool definitions (what Claude sees) and the
     dispatcher (what Python actually runs).

Concepts named in this file:
  - Sandboxing / path confinement: the agent can only see one folder.
  - Canonicalisation: check the *resolved* path, not the string you were given.
  - Allowlist + denylist: the path must be inside the repo (allowlist), and it
    must not be a blocked name (denylist).
  - Defence in depth: several independent checks, so one bug doesn't open everything.
  - Enforce in code, not in the prompt: "please don't read .env" in a prompt can be
    ignored or overridden by injected text. A check in Python can't.
  - Bounded tool output: every result has a size cap to protect the context window.
  - Tool schema as prompt: the tool description is instructions for the model.
  - Literal search, not regex: a model-written regex can be slow enough to hang
    the process (catastrophic backtracking, "ReDoS"). A plain substring can't.
"""

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# The only folder the agent may see. Set it with set_repo_root() (agent.py does
# this from --repo), or with the AGENT_REPO_ROOT environment variable.
# The default is the made-up sample_repo/ that ships with this project.
DEFAULT_REPO = Path(__file__).resolve().parent / "sample_repo"
REPO_ROOT: Path = DEFAULT_REPO


def set_repo_root(path: str | os.PathLike) -> Path:
    """Point every tool at a different repository.

    .resolve() makes the path canonical: absolute, real casing, no ".." segments,
    symlinks and junctions followed. Every later check compares against this.
    """
    global REPO_ROOT
    root = Path(path).resolve()
    # Fail fast: if the root is wrong, crash now instead of giving confusing tool errors later.
    if not root.is_dir():
        raise RuntimeError(f"Repo root does not exist or is not a folder: {root}")
    REPO_ROOT = root
    return root


set_repo_root(os.environ.get("AGENT_REPO_ROOT", DEFAULT_REPO))

# Folder names that must never be listed, searched or read.
# They're compared case-insensitively, because on Windows "Node_Modules" and
# "node_modules" are the same folder.
BLOCKED_DIRS = {"node_modules", ".next", ".git"}

# Output caps (the "bounded tool output" concept).
MAX_LIST_ENTRIES = 300  # past this, list_files stops and says it truncated
MAX_DEPTH = 3           # how deep list_files may recurse in one call
MAX_READ_CHARS = 20_000 # read_file returns at most this many characters per call
MAX_FILE_BYTES = 1_000_000  # read_file and search_code skip files bigger than this
MAX_SEARCH_RESULTS = 50 # search_code stops after this many matching lines
MAX_LINE_CHARS = 200    # each search result line is cut to this length


class ToolError(Exception):
    """An *expected* failure the model should be told about (blocked path, missing file...).

    agent.py catches this and returns the message as a tool_result with
    is_error=True, so Claude can read it and try something else.

    Anything that is NOT a ToolError is a bug in our code. We let those crash
    loudly while developing so we notice them. (Concept: expected vs unexpected errors.)
    """


# ---------------------------------------------------------------------------
# Safety layer
# ---------------------------------------------------------------------------

def _normalise_name(name: str) -> str:
    """Prepare a file or folder name for comparison.

    Windows silently ignores trailing dots and spaces, so ".env." opens ".env"
    and "node_modules " opens "node_modules". We strip them, then casefold
    (an aggressive lowercase) so the comparison is case-insensitive.
    """
    return name.rstrip(". ").casefold()


def is_blocked_name(name: str) -> bool:
    """True if a single path segment is on the denylist."""
    n = _normalise_name(name)
    if n in BLOCKED_DIRS:
        return True
    # Blocks .env, .env.local, .env.production, and so on.
    # Note: this also blocks .env.example. That's deliberate: when in doubt, refuse.
    if n == ".env" or n.startswith(".env."):
        return True
    return False


def safe_path(user_path: str) -> Path:
    """Turn a path from the model into a safe, absolute Path inside REPO_ROOT.

    Raises ToolError if the path is outside the repo or touches a blocked name.
    Every tool must call this. It's the single gate between the model and your disk.
    """
    raw = (user_path or ".").strip()

    # Check 1: relative paths only.
    # .anchor is the drive plus root, so it catches "C:\Windows", "C:foo"
    # (a Windows drive-relative path), "\Users" and "\\server\share".
    if Path(raw).anchor:
        raise ToolError(
            f"Access denied: '{user_path}' is an absolute path. "
            "Use a path relative to the repo root, e.g. 'src' or 'package.json'."
        )

    # Check 2: no colons. This blocks Windows "alternate data streams" such as
    # ".env::$DATA", which is another way of naming the same file's contents.
    if ":" in raw:
        raise ToolError(f"Access denied: '{user_path}' contains ':' which is not allowed.")

    # Check 3: canonicalise. Join to the root, then resolve "..", ".",
    # symlinks and junctions into the real location on disk.
    try:
        resolved = (REPO_ROOT / raw).resolve()
    except OSError as e:  # e.g. characters Windows doesn't allow in paths
        raise ToolError(f"Invalid path '{user_path}': {e}") from e

    # Check 4: confinement, done on the RESOLVED path.
    # is_relative_to compares whole path segments. A plain string check like
    # str(resolved).startswith(str(REPO_ROOT)) would wrongly allow a sibling
    # folder called "<repo>-evil". The tests cover this case.
    if not resolved.is_relative_to(REPO_ROOT):
        raise ToolError(f"Access denied: '{user_path}' is outside the repository.")

    # Check 5: denylist, applied to every segment inside the repo.
    for part in resolved.relative_to(REPO_ROOT).parts:
        if is_blocked_name(part):
            raise ToolError(
                f"Access denied: '{user_path}' is inside a blocked location ('{part}'). "
                "node_modules, .next, .git and .env files cannot be accessed."
            )

    return resolved


def display_path(p: Path) -> str:
    """A repo-relative path with forward slashes, which is what the model sees and passes back."""
    rel = p.relative_to(REPO_ROOT).as_posix()
    return rel if rel != "." else "."


# ---------------------------------------------------------------------------
# Tool 1: list_files
# ---------------------------------------------------------------------------

def list_files(path: str = ".", depth: int = 1) -> str:
    """List a folder's contents, up to `depth` levels deep.

    Design choices (worth knowing why):
    - Each line is a full repo-relative path such as 'src/app/page.tsx', not an
      indented tree. The model can copy it straight into the next tool call.
      (Concept: shape tool output for the next tool call.)
    - Blocked entries are HIDDEN, not shown as "denied". If the model never sees
      node_modules, it won't waste turns trying to open it.
    - Folders end with '/' so files and folders are easy to tell apart.
    """
    target = safe_path(path)

    if not target.exists():
        raise ToolError(f"No such path: '{path}'. List a parent folder to see what exists.")
    if not target.is_dir():
        raise ToolError(f"'{path}' is a file, not a folder.")

    # The model sometimes sends depth as a string, or out of range. Clamp it.
    try:
        depth = int(depth)
    except (TypeError, ValueError):
        raise ToolError(f"depth must be a whole number from 1 to {MAX_DEPTH}, got {depth!r}.")
    depth = max(1, min(depth, MAX_DEPTH))

    lines: list[str] = []
    truncated = False

    def walk(folder: Path, level: int) -> None:
        # A nested function that calls itself is a recursive walk.
        nonlocal truncated
        try:
            # Folders first, then files, alphabetical, so the output is stable.
            entries = sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.casefold()))
        except PermissionError:
            lines.append(f"{display_path(folder)}/  [permission denied]")
            return

        for entry in entries:
            if truncated:
                return
            if is_blocked_name(entry.name):
                continue  # hidden from the model entirely
            # Defence in depth: a symlink or junction INSIDE the repo could point
            # OUTSIDE it. Resolve each entry and skip it if it escapes.
            try:
                if not entry.resolve().is_relative_to(REPO_ROOT):
                    continue
            except OSError:
                continue
            if len(lines) >= MAX_LIST_ENTRIES:
                truncated = True
                return

            if entry.is_dir():
                lines.append(display_path(entry) + "/")
                if level < depth:
                    walk(entry, level + 1)
            else:
                lines.append(display_path(entry))

    walk(target, 1)

    header = f"Contents of '{display_path(target)}' (depth {depth}):"
    if not lines:
        return header + "\n(empty folder)"
    body = "\n".join(lines)
    if truncated:
        body += (
            f"\n[truncated at {MAX_LIST_ENTRIES} entries: "
            "list a subfolder or use a smaller depth]"
        )
    return f"{header}\n{body}"


# ---------------------------------------------------------------------------
# Shared helpers for reading files
# ---------------------------------------------------------------------------

def _looks_binary(data: bytes) -> bool:
    """A NUL byte in the first 1 KB is a cheap, reliable sign of a binary file (images, .pyc...)."""
    return b"\x00" in data[:1024]


def _iter_repo_files(folder: Path):
    """Yield every file under `folder` that the agent is allowed to see.

    Uses os.walk and PRUNES blocked folders, so we never even open node_modules.
    (Pruning is also why search stays fast: node_modules can hold 100,000 files.)
    """
    for dirpath, dirnames, filenames in os.walk(folder):
        # Editing dirnames IN PLACE tells os.walk not to descend into those folders.
        dirnames[:] = sorted(d for d in dirnames if not is_blocked_name(d))
        for name in sorted(filenames):
            if is_blocked_name(name):
                continue
            path = Path(dirpath) / name
            try:
                # Defence in depth again: skip symlinks/junctions that point out of the repo.
                if not path.resolve().is_relative_to(REPO_ROOT):
                    continue
            except OSError:
                continue
            yield path


# ---------------------------------------------------------------------------
# Tool 2: read_file
# ---------------------------------------------------------------------------

def read_file(path: str, start_line: int = 1, end_line: int | None = None) -> str:
    """Return a text file's contents with line numbers, optionally just a range of lines.

    Design choices:
    - Line numbers are included ("12 | code") so Claude can cite exact lines
      and ask for a smaller range next time. (Shape output for the next call.)
    - Output is capped at MAX_READ_CHARS. If a file is longer, the result says
      which line it stopped at, so Claude can carry on from there.
    - Binary and very large files are refused with a ToolError, not dumped.
    """
    target = safe_path(path)
    if not target.exists():
        raise ToolError(f"No such file: '{path}'. Use list_files or search_code to find the right path.")
    if target.is_dir():
        raise ToolError(f"'{path}' is a folder. Use list_files to see what's in it.")
    if target.stat().st_size > MAX_FILE_BYTES:
        raise ToolError(f"'{path}' is larger than {MAX_FILE_BYTES:,} bytes. Use search_code to find the part you need.")

    data = target.read_bytes()
    if _looks_binary(data):
        raise ToolError(f"'{path}' looks like a binary file (image, compiled file...), so it can't be read as text.")

    # errors="replace" means odd bytes become a placeholder instead of crashing.
    lines = data.decode("utf-8", errors="replace").splitlines()
    total = len(lines)

    try:
        start = max(1, int(start_line))
        end = total if end_line is None else min(total, int(end_line))
    except (TypeError, ValueError):
        raise ToolError("start_line and end_line must be whole numbers.")
    if total == 0:
        return f"{display_path(target)} is empty."
    if start > total:
        raise ToolError(f"start_line {start} is past the end of the file ({total} lines).")
    if end < start:
        raise ToolError(f"end_line ({end}) is before start_line ({start}).")

    out: list[str] = []
    used = 0
    last = start - 1
    width = len(str(end))
    for n in range(start, end + 1):
        line = f"{n:>{width}} | {lines[n - 1]}"
        if len(line) > MAX_READ_CHARS:  # one giant line, e.g. a minified bundle
            line = line[:MAX_READ_CHARS] + " [line cut]"
        if used + len(line) > MAX_READ_CHARS and out:
            break
        out.append(line)
        used += len(line) + 1
        last = n

    header = f"{display_path(target)} (lines {start}-{last} of {total}):"
    body = "\n".join(out)
    if last < end:
        body += f"\n[truncated at {MAX_READ_CHARS:,} characters: call again with start_line={last + 1}]"
    return f"{header}\n{body}"


# ---------------------------------------------------------------------------
# Tool 3: search_code
# ---------------------------------------------------------------------------

def search_code(query: str, path: str = ".") -> str:
    """Find lines containing `query` (case-insensitive, plain text) under `path`.

    Returns 'path:line: text' lines, which Claude can pass straight to
    read_file(path, start_line=...) to see the surrounding code.
    """
    if not query or not str(query).strip():
        raise ToolError("query must not be empty.")
    needle = str(query).casefold()

    folder = safe_path(path)
    if not folder.exists():
        raise ToolError(f"No such path: '{path}'.")
    files = [folder] if folder.is_file() else _iter_repo_files(folder)

    results: list[str] = []
    files_searched = 0
    truncated = False
    for file in files:
        try:
            if file.stat().st_size > MAX_FILE_BYTES:
                continue
            data = file.read_bytes()
        except OSError:
            continue
        if _looks_binary(data):
            continue
        files_searched += 1
        for n, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), start=1):
            if needle in line.casefold():
                text = line.strip()
                if len(text) > MAX_LINE_CHARS:
                    text = text[:MAX_LINE_CHARS] + "..."
                results.append(f"{display_path(file)}:{n}: {text}")
                if len(results) >= MAX_SEARCH_RESULTS:
                    truncated = True
                    break
        if truncated:
            break

    scope = display_path(folder)
    if not results:
        return f"No matches for '{query}' in '{scope}' ({files_searched} files searched)."
    header = f"{len(results)} match(es) for '{query}' in '{scope}':"
    body = "\n".join(results)
    if truncated:
        body += f"\n[stopped at {MAX_SEARCH_RESULTS} matches: use a more specific query or path]"
    return f"{header}\n{body}"


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

# What Claude sees. The description is a prompt: it tells the model what the
# tool is for, how paths work, and when to use it.
LIST_FILES_TOOL = {
    "name": "list_files",
    "description": (
        "List files and folders in the repository. Paths are relative to the repo root; "
        "use '.' for the root. Each line is a full relative path, and folders end with '/'. "
        "Start here to orient yourself, then narrow down into subfolders. "
        f"Use depth (1-{MAX_DEPTH}) to see nested folders in one call. "
        "node_modules, .next, .git and .env files are hidden and cannot be accessed."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Folder path relative to the repo root, e.g. '.', 'src', 'src/app/api'.",
            },
            "depth": {
                "type": "integer",
                "description": f"How many levels deep to list (1-{MAX_DEPTH}). Default 1.",
                "minimum": 1,
                "maximum": MAX_DEPTH,
            },
        },
        "required": ["path"],
    },
}

READ_FILE_TOOL = {
    "name": "read_file",
    "description": (
        "Read a text file from the repository. Returns the contents with line numbers. "
        "Use start_line and end_line to read part of a long file; if the output is "
        "truncated, it tells you which start_line to use next. Paths are relative to the "
        "repo root, exactly as list_files and search_code print them. "
        ".env files and files inside node_modules, .next and .git cannot be read."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path relative to the repo root, e.g. 'lib/db.ts'."},
            "start_line": {"type": "integer", "description": "First line to return (1-based). Default 1.", "minimum": 1},
            "end_line": {"type": "integer", "description": "Last line to return. Default: end of file.", "minimum": 1},
        },
        "required": ["path"],
    },
}

SEARCH_CODE_TOOL = {
    "name": "search_code",
    "description": (
        "Search the repository for lines containing a piece of text (case-insensitive, "
        "plain text, NOT a regex). Returns up to "
        f"{MAX_SEARCH_RESULTS} results as 'path:line: text'. Use it to find where something "
        "is defined or used, e.g. a function name, 'process.env' or 'export async function GET'. "
        "Then use read_file with start_line to see the surrounding code. "
        "Short, distinctive queries work best."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Text to look for, e.g. 'calculateTotal'."},
            "path": {"type": "string", "description": "Folder or file to search in, relative to the repo root. Default '.'."},
        },
        "required": ["query"],
    },
}

# Pass this as tools=TOOL_DEFINITIONS in client.messages.create(...).
TOOL_DEFINITIONS = [LIST_FILES_TOOL, READ_FILE_TOOL, SEARCH_CODE_TOOL]

# Maps tool name -> Python function.
TOOL_FUNCTIONS = {
    "list_files": list_files,
    "read_file": read_file,
    "search_code": search_code,
}


def run_tool(name: str, tool_input: dict) -> str:
    """The dispatcher: run the named tool with the model's input and return a string.

    Raises ToolError for anything the model should be told about.
    """
    fn = TOOL_FUNCTIONS.get(name)
    if fn is None:
        raise ToolError(f"Unknown tool '{name}'. Available: {', '.join(TOOL_FUNCTIONS)}.")
    try:
        # ** unpacks the dict into keyword arguments: {"path": "src"} becomes fn(path="src")
        return fn(**tool_input)
    except TypeError as e:
        # The model sent arguments the function doesn't accept, or missed a required one.
        # (Trade-off: this could also hide a TypeError bug inside the tool. Acceptable for now.)
        raise ToolError(f"Bad arguments for {name}: {e}") from e


# ---------------------------------------------------------------------------
# Manual check, no API involved:
#   python tools.py list .              -> list the repo root
#   python tools.py read lib/db.ts      -> read a file
#   python tools.py search process.env  -> search
#   python tools.py read .env           -> should print a ToolError
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    cmd, *args = sys.argv[1:] or ["list", "."]
    fn = {"list": list_files, "read": read_file, "search": search_code}.get(cmd)
    print(f"REPO_ROOT = {REPO_ROOT}\n")
    if fn is None:
        sys.exit("usage: python tools.py [list|read|search] ARG")
    try:
        print(fn(*args))
    except ToolError as e:
        print("ToolError:", e)
