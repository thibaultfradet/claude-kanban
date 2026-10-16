"""Read-only "source control" view of a ticket: files changed since the ticket
started, and the before/after content of each one. Never touches the index,
the working tree or the stash."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .models import OCCUPIED_STATUSES, Ticket, TicketStatus

GIT_TIMEOUT = 15
MAX_FILE_BYTES = 1024 * 1024
WORKTREE = "WORKTREE"


class ChangesError(Exception):
    pass


def _git(args: List[str], cwd: Path, input_text: Optional[str] = None) -> bytes:
    try:
        res = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            input=input_text.encode() if input_text is not None else None,
            timeout=GIT_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise ChangesError(f"git {args[0]} : délai de {GIT_TIMEOUT} s dépassé")
    except OSError as exc:
        raise ChangesError(f"git {args[0]} : {exc}")
    if res.returncode != 0:
        msg = res.stderr.decode("utf-8", "replace").strip() or f"code {res.returncode}"
        raise ChangesError(f"git {args[0]} : {msg}")
    return res.stdout


def _split_z(out: bytes) -> List[str]:
    return [p.decode("utf-8", "surrogateescape") for p in out.split(b"\0") if p]


def _repo_root(project_path: Path) -> Path:
    out = _git(["rev-parse", "--show-toplevel"], project_path)
    return Path(out.decode("utf-8", "surrogateescape").strip())


def _scope(ticket: Ticket, root: Path) -> Tuple[Optional[str], str]:
    base = ticket.head_sha_at_start or None
    if ticket.status != TicketStatus.termine:
        return base, WORKTREE
    if ticket.commit_sha:
        return base, ticket.commit_sha
    return base, _git(["rev-parse", "HEAD"], root).decode().strip()


def _count_lines(data: bytes) -> int:
    if not data:
        return 0
    return data.count(b"\n") + (0 if data.endswith(b"\n") else 1)


def _collect(root: Path, base: str, target: str) -> List[dict]:
    rev_args = [base] if target == WORKTREE else [base, target]
    names = _split_z(_git(["diff", "--name-status", "-z", "-M", *rev_args, "--"], root))
    nums = _split_z(_git(["diff", "--numstat", "-z", "-M", *rev_args, "--"], root))

    files: Dict[str, dict] = {}
    i = 0
    while i < len(names):
        code = names[i]
        letter = code[0]
        if letter in ("R", "C"):
            old_path, path = names[i + 1], names[i + 2]
            i += 3
        else:
            old_path, path = None, names[i + 1]
            i += 2
        status = {"A": "A", "D": "D", "R": "R", "C": "A"}.get(letter, "M")
        files[path] = {
            "path": path,
            "old_path": old_path if status == "R" else None,
            "status": status,
            "additions": 0,
            "deletions": 0,
            "binary": False,
        }

    # numstat -z: "add\tdel\tpath" or, for renames, "add\tdel\t" then old, new.
    i = 0
    while i < len(nums):
        add, dele, path = nums[i].split("\t", 2)
        if path:
            i += 1
        else:
            path = nums[i + 2]
            i += 3
        entry = files.get(path)
        if entry is None:
            continue
        if add == "-":
            entry["binary"] = True
        else:
            entry["additions"] = int(add)
            entry["deletions"] = int(dele)

    if target == WORKTREE:
        untracked = _split_z(_git(["ls-files", "--others", "--exclude-standard", "-z"], root))
        for path in untracked:
            if path in files or path.endswith("/"):
                continue
            full = root / path
            binary = False
            additions = 0
            if full.is_symlink():
                additions = 1
            elif full.is_file():
                with open(full, "rb") as fh:
                    head = fh.read(8000)
                    binary = b"\0" in head
                    if not binary:
                        fh.seek(0)
                        additions = _count_lines(fh.read())
            files[path] = {
                "path": path,
                "old_path": None,
                "status": "A",
                "additions": additions,
                "deletions": 0,
                "binary": binary,
            }

    return sorted(files.values(), key=lambda f: f["path"])


def _public(entry: dict) -> dict:
    return {k: entry[k] for k in ("path", "old_path", "status", "additions", "deletions")}


def _list(ticket: Ticket, project_path: Path) -> Tuple[dict, Optional[Path], List[dict]]:
    payload = {
        "ticket_id": ticket.id,
        "status": ticket.status.value,
        "base": None,
        "target": WORKTREE,
        "live": ticket.status == TicketStatus.en_cours,
        "files": [],
    }
    if not ticket.head_sha_at_start:
        if ticket.status == TicketStatus.termine and ticket.commit_sha:
            payload["target"] = ticket.commit_sha
        return payload, None, []

    root = _repo_root(project_path)
    base, target = _scope(ticket, root)
    entries = _collect(root, base, target)
    payload.update(base=base, target=target, files=[_public(e) for e in entries])
    return payload, root, entries


def list_changes(ticket: Ticket, project_path: Path) -> dict:
    return _list(ticket, project_path)[0]


def _blob_info(root: Path, rev: str, path: str) -> Tuple[str, int]:
    out = _git(["cat-file", "--batch-check"], root, input_text=f"{rev}:{path}\n")
    parts = out.decode("utf-8", "surrogateescape").strip().split()
    if len(parts) != 3:
        raise ChangesError(f"git cat-file : objet introuvable {rev}:{path}")
    return parts[1], int(parts[2])


def _read_rev(root: Path, rev: str, path: str) -> Tuple[Optional[bytes], bool]:
    """(content, too_large); content is None for non-blob entries (submodules)."""
    kind, size = _blob_info(root, rev, path)
    if kind != "blob":
        return None, False
    if size > MAX_FILE_BYTES:
        return b"", True
    return _git(["show", f"{rev}:{path}"], root), False


def _read_disk(root: Path, path: str) -> Tuple[Optional[bytes], bool]:
    full = root / path
    if full.is_symlink():
        # Git stores a symlink as its target path; never follow it outside the repo.
        return os.readlink(full).encode("utf-8", "surrogateescape"), False
    if not full.is_file():
        return None, False
    if full.stat().st_size > MAX_FILE_BYTES:
        return b"", True
    return full.read_bytes(), False


def file_change(ticket: Ticket, project_path: Path, path: str) -> Optional[dict]:
    """Before/after content of one changed file, or None if `path` is not part of
    this ticket's changes (so nothing outside that list can ever be read)."""
    payload, root, entries = _list(ticket, project_path)
    entry = next((e for e in entries if e["path"] == path), None)
    if entry is None:
        return None

    base, target = payload["base"], payload["target"]
    original: Optional[bytes] = b""
    modified: Optional[bytes] = b""
    too_large = False
    binary = entry["binary"]

    if not binary:
        if entry["status"] != "A":
            original, big = _read_rev(root, base, entry["old_path"] or path)
            too_large |= big
        if entry["status"] != "D":
            if target == WORKTREE:
                modified, big = _read_disk(root, path)
            else:
                modified, big = _read_rev(root, target, path)
            too_large |= big
        if original is None or modified is None:
            binary = True
        elif not too_large and (b"\0" in original or b"\0" in modified):
            binary = True

    if binary or too_large:
        original = modified = b""

    return {
        "path": path,
        "old_path": entry["old_path"],
        "status": entry["status"],
        "original": original.decode("utf-8", "replace"),
        "modified": modified.decode("utf-8", "replace"),
        "binary": binary,
        "too_large": too_large and not binary,
    }
