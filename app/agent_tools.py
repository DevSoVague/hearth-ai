#!/usr/bin/env python3
# ============================ CONFIG — edit this ============================
MAX_READ_BYTES     = 200_000     # cap on read_file — don't blindly dump huge files to the model
MAX_OUTPUT_BYTES   = 20_000      # cap on run_command stdout/stderr each
DEFAULT_TIMEOUT_S  = 30          # default run_command timeout
MAX_TIMEOUT_S      = 300         # hard ceiling regardless of what's requested
SNAPSHOT_SIZE_CAP  = 100_000_000 # skip pre-run snapshot if project root exceeds this (bytes)
BACKUP_DIRNAME     = ".gemma_agent"   # support folder: backups/, snapshots/, run-sandboxed.sh
# ===========================================================================

import os
import shutil
import subprocess
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path

_write_lock = threading.Lock()


class PathViolation(Exception):
    """Raised whenever a requested path would escape the configured project root."""


# ── PATH SAFETY ──────────────────────────────────────────────────────────
def resolve_safe_path(project_root: Path, rel_path: str) -> Path:
    """Resolve `rel_path` against `project_root`, refusing anything that
    escapes the root — including via ".." traversal or a symlink pointing
    outside it. `Path.resolve()` dereferences symlinks before we ever check
    containment, which is what actually closes the symlink-escape hole; a
    plain string prefix check on the *unresolved* path would not.
    """
    if rel_path is None:
        raise PathViolation("path is required")
    rel_path = rel_path.strip()
    if rel_path.startswith("/") or rel_path.startswith("~") or rel_path.startswith("\\"):
        raise PathViolation("path must be relative to the project root")
    if "\x00" in rel_path:
        raise PathViolation("invalid path")

    root = project_root.resolve(strict=True)
    # resolve(strict=False): follows symlinks on existing components; for a
    # not-yet-existing leaf (create case) resolves as far as existing parents.
    candidate = (root / rel_path).resolve(strict=False)

    try:
        rel = candidate.relative_to(root)
    except ValueError:
        raise PathViolation("path escapes the project root")

    if rel.parts and rel.parts[0] == BACKUP_DIRNAME:
        raise PathViolation(f"{BACKUP_DIRNAME} is reserved and not directly accessible")

    return candidate


def _is_probably_binary(raw: bytes) -> bool:
    if b"\x00" in raw:
        return True
    try:
        raw.decode("utf-8")
        return False
    except UnicodeDecodeError:
        return True


# ── BACKUPS ──────────────────────────────────────────────────────────────
def make_backup(root: Path, target: Path) -> Path:
    """Copy `target`'s current contents into the backups vault before it gets
    overwritten. Returns the absolute backup path."""
    rel = target.relative_to(root)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup_path = root / BACKUP_DIRNAME / "backups" / rel.parent / f"{rel.name}.{ts}.bak"
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target, backup_path)
    return backup_path


def atomic_write(target: Path, content: str):
    """Write-to-temp-then-replace so a crash mid-write can't corrupt the file."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + f".tmp-{os.getpid()}")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, target)


# ── FILE OPS ─────────────────────────────────────────────────────────────
def list_dir(root: Path, rel_path: str):
    target = resolve_safe_path(root, rel_path or ".")
    if not target.exists():
        return {"ok": False, "error": "directory not found"}
    if not target.is_dir():
        return {"ok": False, "error": "not a directory"}
    entries = []
    for e in sorted(os.scandir(target), key=lambda e: (not e.is_dir(), e.name.lower())):
        if e.name == BACKUP_DIRNAME and target == root:
            continue  # never surface the support folder to the model
        try:
            st = e.stat()
            entries.append({
                "name": e.name,
                "type": "dir" if e.is_dir() else "file",
                "size": None if e.is_dir() else st.st_size,
                "modified": int(st.st_mtime),
            })
        except OSError:
            continue
    rel = target.relative_to(root)
    return {"ok": True, "path": "" if rel == Path(".") else str(rel), "entries": entries}


def read_file(root: Path, rel_path: str):
    target = resolve_safe_path(root, rel_path)
    if not target.exists():
        return {"ok": False, "error": "file not found"}
    if target.is_dir():
        return {"ok": False, "error": "is a directory"}
    raw = target.read_bytes()
    if _is_probably_binary(raw[:8192]):
        return {"ok": False, "error": "binary file — cannot read as text"}
    truncated = len(raw) > MAX_READ_BYTES
    text = raw[:MAX_READ_BYTES].decode("utf-8", errors="replace")
    return {"ok": True, "path": rel_path, "content": text, "truncated": truncated, "size": len(raw)}


def _unified_diff(old: str, new: str, path: str) -> str:
    import difflib
    return "".join(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=path, tofile=path + " (proposed)",
    ))


def preview_write(root: Path, rel_path: str, content: str):
    target = resolve_safe_path(root, rel_path)
    is_new = not target.exists()
    old = "" if is_new else target.read_text(encoding="utf-8", errors="replace")
    diff = _unified_diff(old, content, rel_path)
    return {"ok": True, "path": rel_path, "is_new": is_new, "diff": diff or "(no changes)"}


def preview_edit(root: Path, rel_path: str, old_string: str, new_string: str):
    target = resolve_safe_path(root, rel_path)
    if not target.exists():
        return {"ok": False, "error": "file not found — use write_file to create it"}
    content = target.read_text(encoding="utf-8", errors="replace")
    n = content.count(old_string)
    if n == 0:
        return {"ok": False, "error": "old_string not found in file — re-read the file and try again"}
    if n > 1:
        return {"ok": False, "error": f"old_string appears {n} times — must be unique; include more surrounding context"}
    proposed = content.replace(old_string, new_string, 1)
    diff = _unified_diff(content, proposed, rel_path)
    return {"ok": True, "path": rel_path, "diff": diff or "(no changes)"}


def write_file(root: Path, rel_path: str, content: str):
    target = resolve_safe_path(root, rel_path)
    with _write_lock:
        existed = target.exists()
        backup_path = None
        if existed:
            if target.is_dir():
                return {"ok": False, "error": "path is a directory"}
            backup_path = make_backup(root, target)
        atomic_write(target, content)
    return {
        "ok": True, "path": rel_path, "created": not existed,
        "backup_path": str(backup_path.relative_to(root)) if backup_path else None,
        "bytes_written": len(content.encode("utf-8")),
    }


def edit_file(root: Path, rel_path: str, old_string: str, new_string: str):
    target = resolve_safe_path(root, rel_path)
    with _write_lock:
        if not target.exists():
            return {"ok": False, "error": "file not found — use write_file to create it"}
        # Re-validate at apply time — the file may have changed since preview
        # (another tool call, or an external edit) between the approval card
        # being shown and the user clicking Approve.
        content = target.read_text(encoding="utf-8", errors="replace")
        n = content.count(old_string)
        if n == 0:
            return {"ok": False, "error": "old_string no longer matches — file changed since preview; ask the model to re-read and retry"}
        if n > 1:
            return {"ok": False, "error": f"old_string now appears {n} times — file changed since preview; ask the model to re-read and retry"}
        backup_path = make_backup(root, target)
        atomic_write(target, content.replace(old_string, new_string, 1))
    return {
        "ok": True, "path": rel_path,
        "backup_path": str(backup_path.relative_to(root)),
        "bytes_written": None,
    }


# ── SANDBOX ──────────────────────────────────────────────────────────────
# NOTE on approach: a `(deny default)`-based allowlist profile was tried
# first and rejected — it SIGABRTs on ordinary commands (dyld can't read
# its own shared-cache/frameworks without an exhaustive, fragile allowlist).
# Verified empirically on macOS 15.7.4 (see conversation) that the reliable
# shape is `(allow default)` + narrow, targeted `deny` rules: full normal
# execution stays intact, and file-write / network are denied everywhere
# except an explicit set of exceptions via `(require-not (require-any ...))`.
# This still delivers the actual safety property that matters here: nothing
# the sandboxed process runs can write outside the project root or reach
# the network, regardless of what it tries.
_WRITE_LITERAL_EXCEPTIONS = ["/dev/null", "/dev/tty", "/dev/dtracehelper"]


def build_sandbox_profile(root: Path) -> str:
    """Seatbelt (sandbox-exec) profile confining a process to `root`:
    writes allowed only inside the project root, plus the system per-user
    temp dir (interpreters/toolchains routinely cache scratch files there;
    it's per-session and not a meaningful escape) and a couple of harmless
    device-file exceptions scripts commonly redirect to. Network denied
    entirely. Everything else (reads, process exec) behaves normally."""
    import tempfile
    root_s = str(root.resolve())
    tmp_s = tempfile.gettempdir()
    literals = " ".join(f'(literal "{p}")' for p in _WRITE_LITERAL_EXCEPTIONS if os.path.exists(p))
    return (
        "(version 1)\n"
        "(allow default)\n"
        "(deny file-write*\n"
        "  (require-not (require-any\n"
        f'    (subpath "{root_s}")\n'
        f'    (subpath "{tmp_s}")\n'
        f"    {literals}\n"
        "  )))\n"
        "(deny network*)\n"
    )


def run_in_sandbox(root: Path, command: str, timeout_s: int = DEFAULT_TIMEOUT_S):
    timeout_s = max(1, min(timeout_s, MAX_TIMEOUT_S))
    profile = build_sandbox_profile(root)
    try:
        proc = subprocess.run(
            ["sandbox-exec", "-p", profile, "/bin/bash", "-lc", command],
            cwd=str(root), timeout=timeout_s,
            capture_output=True, text=True,
        )
        stdout = proc.stdout[:MAX_OUTPUT_BYTES]
        stderr = proc.stderr[:MAX_OUTPUT_BYTES]
        return {
            "ok": True, "exit_code": proc.returncode,
            "stdout": stdout, "stdout_truncated": len(proc.stdout) > MAX_OUTPUT_BYTES,
            "stderr": stderr, "stderr_truncated": len(proc.stderr) > MAX_OUTPUT_BYTES,
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as e:
        return {
            "ok": True, "exit_code": None,
            "stdout": (e.stdout or "")[:MAX_OUTPUT_BYTES], "stdout_truncated": False,
            "stderr": (e.stderr or "")[:MAX_OUTPUT_BYTES], "stderr_truncated": False,
            "timed_out": True,
        }
    except FileNotFoundError:
        return {"ok": False, "error": "sandbox-exec not found on this system"}


def snapshot_project(root: Path) -> dict:
    """Zip the project root (excluding the support folder + common VCS dirs)
    before a run_command call, so file changes made outside the write/edit
    tools' backup path are still recoverable. Skipped above SNAPSHOT_SIZE_CAP."""
    total = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in (BACKUP_DIRNAME, ".git", "node_modules", ".venv")]
        for f in filenames:
            try:
                total += (Path(dirpath) / f).stat().st_size
            except OSError:
                pass
        if total > SNAPSHOT_SIZE_CAP:
            return {"ok": False, "skipped": True, "reason": f"project root exceeds {SNAPSHOT_SIZE_CAP // 1_000_000}MB snapshot cap"}

    ts = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    snap_dir = root / BACKUP_DIRNAME / "snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    snap_path = snap_dir / f"pre-run-{ts}.zip"
    with zipfile.ZipFile(snap_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in (BACKUP_DIRNAME, ".git", "node_modules", ".venv")]
            for f in filenames:
                p = Path(dirpath) / f
                zf.write(p, p.relative_to(root))
    return {"ok": True, "skipped": False, "snapshot_path": str(snap_path.relative_to(root))}


# ── MANUAL RUN HELPER SCRIPT (the "toggle between" other side) ────────────
RUN_SCRIPT_TEMPLATE = '''#!/bin/bash
# Auto-generated by Gemma Agent — runs a command sandboxed to this project
# root exactly like the in-chat "Allow command execution" tool does:
# read/write confined here, no network. Regenerated whenever the project
# root is (re)set from the Agent overlay.
set -e
HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE"
exec sandbox-exec -p '{profile}' /bin/bash -lc "$*"
'''


def write_run_script(root: Path):
    profile = build_sandbox_profile(root).replace("'", "'\\''")
    script = RUN_SCRIPT_TEMPLATE.format(profile=profile)
    support = root / BACKUP_DIRNAME
    support.mkdir(parents=True, exist_ok=True)
    script_path = support / "run-sandboxed.sh"
    script_path.write_text(script, encoding="utf-8")
    script_path.chmod(0o755)
    return script_path
