#!/usr/bin/env python3
"""Private local records and an explicit, encrypted one-way LATTE publication bridge.

This is an assistant-operated publisher, not a ChatGPT webhook or live two-way sync.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import tempfile
from urllib.parse import urlsplit
from uuid import uuid4

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ROOT = Path(__file__).resolve().parent
AAD = b"LATTE workspace v1"
COLLECTIONS = ("conversations", "proposals", "decisions", "documents", "activities")
SITE_URL = "https://littlewebjp.github.io/search-new-work/"
MAX_WORKSPACE_BYTES = 8_000_000


class SyncError(ValueError):
    """A safe-to-display error which never includes records or keys."""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _string(value, label, required=True):
    if not isinstance(value, str) or (required and not value.strip()):
        raise SyncError(f"{label}: nonempty text is required")


def _timestamp(value, label):
    _string(value, label)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError()
    except ValueError:
        raise SyncError(f"{label}: an ISO timestamp with timezone is required") from None


def _record_date(value, label):
    """Historical records may have a known calendar date but no known clock time."""
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            date.fromisoformat(value)
            return
        except ValueError:
            raise SyncError(f"{label}: invalid calendar date") from None
    _timestamp(value, label)


def _strings(value, label):
    if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
        raise SyncError(f"{label}: a text array is required")


def _https(value):
    if not isinstance(value, str) or any(c.isspace() for c in value):
        return False
    try:
        parsed = urlsplit(value)
        _ = parsed.port
        return parsed.scheme in {"https", "http"} and bool(parsed.hostname) and not parsed.username and not parsed.password
    except (ValueError, TypeError):
        return False


def validate_snapshot(s):
    if not isinstance(s, dict) or s.get("schema_version") != 1:
        raise SyncError("Unsupported workspace format")
    if type(s.get("revision")) is not int or s["revision"] < 0:
        raise SyncError("Invalid workspace revision")
    _timestamp(s["updated_at"], "updated_at")
    profile = s.get("profile")
    if not isinstance(profile, dict) or not isinstance(profile.get("facts"), list) or not isinstance(profile.get("preferences"), dict):
        raise SyncError("profile requires facts and preferences")
    fact_keys = set()
    for fact in profile["facts"]:
        if not isinstance(fact, dict):
            raise SyncError("Invalid profile fact")
        for key in ("key", "label", "value", "source"):
            _string(fact.get(key), f"fact.{key}")
        if fact["key"] in fact_keys or fact.get("status") not in {"confirmed", "reported", "unknown"}:
            raise SyncError("Invalid or duplicate profile fact")
        fact_keys.add(fact["key"])
    coverage = s.get("coverage")
    if not isinstance(coverage, dict):
        raise SyncError("coverage is required")
    for key in ("label", "description"):
        _string(coverage.get(key), f"coverage.{key}", required=False)
    if coverage.get("last_recorded_at") is not None:
        _timestamp(coverage["last_recorded_at"], "coverage.last_recorded_at")
    if type(coverage.get("record_count")) is not int or coverage["record_count"] < 0:
        raise SyncError("coverage.record_count is invalid")
    ids = {}
    for collection in COLLECTIONS:
        rows = s.get(collection)
        if not isinstance(rows, list):
            raise SyncError(f"{collection} must be an array")
        ids[collection] = set()
        for row in rows:
            if not isinstance(row, dict):
                raise SyncError(f"Invalid {collection} record")
            _string(row.get("id"), f"{collection}.id")
            if row["id"] in ids[collection]:
                raise SyncError(f"Duplicate {collection} id")
            ids[collection].add(row["id"])
    conversations = {r["id"]: r for r in s["conversations"]}
    for row in s["conversations"]:
        for key in ("title", "source"):
            _string(row.get(key), f"conversation.{key}")
        _record_date(row.get("at"), "conversation.at")
        if row.get("kind") not in {"exchange", "summary", "quoted_excerpt"}:
            raise SyncError("Invalid conversation kind")
        for key in ("user_text", "assistant_text", "summary"):
            if key in row:
                _string(row[key], f"conversation.{key}", required=False)
        if row["kind"] == "summary" and not row.get("summary"):
            raise SyncError("A summarized conversation requires summary text")
        if row["kind"] != "summary" and not (row.get("user_text") or row.get("assistant_text")):
            raise SyncError("An exchange requires recorded conversation text")
        _strings(row.get("tags", []), "conversation.tags")
    for row in s["proposals"]:
        for key in ("title", "summary", "recommendation", "next_action"):
            _string(row.get(key), f"proposal.{key}", required=key == "title")
        if row.get("kind") not in {"approach", "job", "document"} or row.get("status") not in {
                "awaiting_decision", "approved", "preparing", "ready_for_review", "waiting_to_apply", "applied", "deferred"}:
            raise SyncError("Invalid proposal kind or status")
        _strings(row.get("reasons", []), "proposal.reasons")
        _strings(row.get("unknowns", []), "proposal.unknowns")
        evidence = row.get("evidence", [])
        if not isinstance(evidence, list):
            raise SyncError("proposal.evidence must be an array")
        for ref in evidence:
            if not isinstance(ref, dict) or ref.get("conversation_id") not in conversations or ref.get("who") not in {"user", "assistant"}:
                raise SyncError("Proposal evidence must reference a recorded conversation")
            _string(ref.get("quote"), "evidence.quote")
            target = conversations[ref["conversation_id"]].get(ref["who"] + "_text", "")
            if ref["quote"] not in target:
                raise SyncError("Evidence quote is not present in the referenced speaker's text")
        if row["status"] in {"waiting_to_apply", "applied"}:
            job = row.get("job", {})
            if row["kind"] != "job" or not isinstance(job, dict) or not _https(job.get("source_url")) or row.get("packet_ready") is not True:
                raise SyncError("Application states require a sourced job and completed preparation packet")
            _timestamp(job.get("verified_at"), "job.verified_at")
            if row["status"] == "applied":
                _timestamp(row.get("submission_confirmed_at"), "submission_confirmed_at")
    def references(row, collection, field):
        refs = row.get(field, [])
        _strings(refs, field)
        if any(ref not in ids[collection] for ref in refs):
            raise SyncError(f"Unknown {field} reference")
    for row in s["decisions"]:
        for key in ("title", "choice", "reason"):
            _string(row.get(key), f"decision.{key}", required=key != "reason")
        _record_date(row.get("at"), "decision.at")
        references(row, "conversations", "conversation_ids")
        references(row, "proposals", "proposal_ids")
    for row in s["documents"]:
        for key in ("title", "body"):
            _string(row.get(key), f"document.{key}")
        if row.get("status") not in {"draft", "ready_for_review"}:
            raise SyncError("Invalid document status")
        references(row, "conversations", "conversation_ids")
        if row.get("proposal_id") and row["proposal_id"] not in ids["proposals"]:
            raise SyncError("Unknown document proposal reference")
    for row in s["activities"]:
        _record_date(row.get("at"), "activity.at")
        _string(row.get("text"), "activity.text")
        if row.get("kind") not in {"recorded", "proposed", "decided", "prepared", "published"}:
            raise SyncError("Invalid activity kind")
        for key, collection in (("conversation_id", "conversations"), ("proposal_id", "proposals")):
            if row.get(key) and row[key] not in ids[collection]:
                raise SyncError(f"Unknown activity {key}")
    if coverage["record_count"] != len(s["conversations"]):
        raise SyncError("coverage.record_count must match the recorded conversations")
    try:
        serialized = json.dumps(s, ensure_ascii=False, allow_nan=False).encode()
    except (TypeError, ValueError, RecursionError):
        raise SyncError("Workspace contains unsupported JSON values") from None
    if len(serialized) > MAX_WORKSPACE_BYTES:
        raise SyncError("Workspace exceeds the 8 MB record limit; existing data was preserved")
    return s


class WorkspaceSync:
    def __init__(self, data_dir=ROOT / ".latte", repo_dir=ROOT,
                 dashboard_dir=ROOT / "dashboard", remote="origin", branch="latte-site"):
        self.data_dir = Path(data_dir).resolve()
        self.repo_dir = Path(repo_dir).resolve()
        self.dashboard_dir = Path(dashboard_dir).resolve()
        self.remote, self.branch = remote, branch
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.data_dir, 0o700)
        with self.lock():
            key_path = self.data_dir / "sync-key.bin"
            if not (key_path.exists() or key_path.is_symlink()):
                protected = (self.workspace_path, self.data_dir / "publication.json")
                if any(path.exists() or path.is_symlink() for path in protected):
                    raise SyncError(
                        "The original private key is missing. Restore sync-key.bin from the original "
                        "private .latte backup before continuing. No replacement key was generated."
                    )
            try:
                fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            except FileExistsError:
                if key_path.is_symlink() or not stat.S_ISREG(key_path.stat().st_mode):
                    raise SyncError("Invalid private key file")
            else:
                with os.fdopen(fd, "wb") as file:
                    file.write(secrets.token_bytes(32))
                    file.flush()
                    os.fsync(file.fileno())
            self._key()
            if not self.workspace_path.exists():
                self._write(self.workspace_path, {
                    "schema_version": 1, "revision": 0, "updated_at": now(),
                    "coverage": {"label": "", "description": "", "last_recorded_at": None, "record_count": 0},
                    "profile": {"facts": [], "preferences": {}},
                    **{key: [] for key in COLLECTIONS},
                })
            self._read()
            fragment = base64.urlsafe_b64encode(self._key()).decode().rstrip("=")
            self._write_bytes(self.data_dir / "access-url.txt", (SITE_URL + "#key=" + fragment + "\n").encode())

    @property
    def workspace_path(self):
        return self.data_dir / "workspace.json"

    @contextmanager
    def lock(self):
        fd = os.open(self.data_dir / "sync.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "a+") as file:
            fcntl.flock(file, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(file, fcntl.LOCK_UN)

    @staticmethod
    def _write_bytes(path, content):
        path = Path(path)
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".latte-write-")
        try:
            with os.fdopen(fd, "wb") as file:
                file.write(content)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @classmethod
    def _write(cls, path, value):
        cls._write_bytes(path, json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode())

    def _key(self):
        key_path = self.data_dir / "sync-key.bin"
        fd = os.open(key_path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as file:
            key = file.read()
        if len(key) != 32:
            raise SyncError("Private key must be exactly 32 bytes; it was not replaced")
        os.chmod(key_path, 0o600)
        return key

    def _read(self):
        try:
            return validate_snapshot(json.loads(self.workspace_path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
            raise SyncError("Private workspace cannot be read; existing data was preserved") from None

    def read(self):
        with self.lock():
            return self._read()

    def record(self, payload):
        if not isinstance(payload, dict) or set(payload) - {"profile", "coverage", *COLLECTIONS}:
            raise SyncError("Record input contains unsupported fields")
        with self.lock():
            original = self._read()
            updated = deepcopy(original)
            for key in ("profile", "coverage"):
                if key in payload:
                    if not isinstance(payload[key], dict):
                        raise SyncError(f"{key} must be an object")
                    updated[key] = {**updated[key], **deepcopy(payload[key])}
            for collection in COLLECTIONS:
                if collection not in payload:
                    continue
                rows = payload[collection]
                if not isinstance(rows, list) or any(not isinstance(r, dict) or not isinstance(r.get("id"), str) for r in rows):
                    raise SyncError(f"Invalid {collection} merge input")
                if len({r["id"] for r in rows}) != len(rows):
                    raise SyncError(f"Duplicate {collection} merge id")
                index = {r["id"]: r for r in updated[collection]}
                for row in rows:
                    index[row["id"]] = deepcopy(row)
                updated[collection] = list(index.values())
            updated["coverage"]["record_count"] = len(updated["conversations"])
            validate_snapshot(updated)
            if updated != original:
                updated["revision"] += 1
                updated["updated_at"] = now()
                self._write(self.workspace_path, updated)
            return updated

    def consent(self):
        """Call only after the owner explicitly approves public encrypted storage."""
        with self.lock():
            self._write(self.data_dir / "publication-consent.json", {"encrypted_public_storage": True, "at": now()})

    def _build(self, output_dir):
        output = Path(output_dir).resolve()
        output.mkdir(parents=True, exist_ok=True, mode=0o700)
        if output == self.data_dir or output == self.dashboard_dir:
            raise SyncError("Build output must be a separate directory")
        for name in ("index.html", "styles.css", "app.js"):
            source = self.dashboard_dir / name
            if not source.is_file() or source.is_symlink():
                raise SyncError(f"Missing dashboard asset: {name}")
            self._write_bytes(output / name, source.read_bytes())
        avatar = self.dashboard_dir / "avatar.svg"
        if avatar.is_file() and not avatar.is_symlink():
            self._write_bytes(output / "avatar.svg", avatar.read_bytes())
        snapshot = self._read()
        plaintext = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")).encode()
        nonce = secrets.token_bytes(12)
        ciphertext = AESGCM(self._key()).encrypt(nonce, plaintext, AAD)
        self._write(output / "workspace.enc.json", {
            "format": "latte-encrypted-v1", "algorithm": "AES-256-GCM",
            "nonce": base64.b64encode(nonce).decode(), "ciphertext": base64.b64encode(ciphertext).decode(),
        })
        self._write_bytes(output / ".nojekyll", b"")
        return output, snapshot["revision"]

    def build(self, output_dir=None):
        with self.lock():
            output, _ = self._build(output_dir or self.data_dir / "publish")
            return output

    def _git(self, *args, input=None, env=None):
        result = subprocess.run(["git", *args], cwd=self.repo_dir, input=input, text=True,
                                env=env, capture_output=True)
        if result.returncode:
            raise SyncError(f"Git {args[0]} failed (exit {result.returncode}); no publication success was recorded")
        return result.stdout.strip()

    def publish(self, allow_encrypted_publication=False):
        with self.lock():
            consent_path = self.data_dir / "publication-consent.json"
            try:
                consent = json.loads(consent_path.read_text())
            except (OSError, ValueError):
                consent = {}
            if allow_encrypted_publication is not True or consent.get("encrypted_public_storage") is not True:
                raise SyncError("Public encrypted publication requires explicit owner consent and --allow-encrypted-publication")
            with tempfile.TemporaryDirectory(prefix="latte-publish-") as folder:
                output, revision = self._build(Path(folder) / "site")
                remote_lines = self._git("ls-remote", "--heads", self.remote, f"refs/heads/{self.branch}").splitlines()
                if len(remote_lines) != 1:
                    raise SyncError("Expected an existing, unambiguous publication branch")
                parent = remote_lines[0].split()[0]
                if len(parent) not in (40, 64) or any(c not in "0123456789abcdef" for c in parent):
                    raise SyncError("Invalid publication branch commit")
                temporary_ref = f"refs/latte-sync/{uuid4().hex}"
                try:
                    self._git("fetch", "--no-tags", self.remote, f"{parent}:{temporary_ref}")
                    env = {**os.environ, "GIT_INDEX_FILE": str(Path(folder) / "index")}
                    self._git("read-tree", parent, env=env)
                    for path in sorted(output.iterdir()):
                        blob = self._git("hash-object", "-w", str(path))
                        self._git("update-index", "--add", "--cacheinfo", f"100644,{blob},{path.name}", env=env)
                    tree = self._git("write-tree", env=env)
                    commit = self._git("-c", "user.name=LATTE", "-c", "user.email=latte@localhost", "commit-tree",
                                       tree, "-p", parent, input=f"Update encrypted LATTE workspace revision {revision}\n")
                    # No force. A concurrent remote update must reject this commit.
                    self._git("push", self.remote, f"{commit}:refs/heads/{self.branch}")
                    metadata = {"last_published_revision": revision, "commit": commit, "at": now()}
                    self._write(self.data_dir / "publication.json", metadata)
                    return metadata
                finally:
                    try:
                        self._git("update-ref", "-d", temporary_ref)
                    except SyncError:
                        # Ref cleanup is best effort; it must not turn a confirmed
                        # successful push into an apparent publication failure.
                        pass

    def status(self):
        with self.lock():
            snapshot = self._read()
            try:
                publication = json.loads((self.data_dir / "publication.json").read_text())
            except (OSError, ValueError):
                publication = {}
            return {"revision": snapshot["revision"], "record_count": len(snapshot["conversations"]),
                    "proposal_count": len(snapshot["proposals"]), "document_count": len(snapshot["documents"]),
                    "last_published_revision": publication.get("last_published_revision"),
                    "commit": publication.get("commit"), "deployment_verified": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default=str(ROOT / ".latte"))
    parser.add_argument("--repo-dir", default=str(ROOT))
    commands = parser.add_subparsers(dest="command", required=True)
    record = commands.add_parser("record")
    record.add_argument("--input", required=True)
    build = commands.add_parser("build")
    build.add_argument("--output-dir")
    grant = commands.add_parser("consent")
    grant.add_argument("--encrypted-public-storage", action="store_true", required=True)
    publish = commands.add_parser("publish")
    publish.add_argument("--allow-encrypted-publication", action="store_true", required=True)
    commands.add_parser("status")
    args = parser.parse_args()
    try:
        sync = WorkspaceSync(args.data_dir, repo_dir=args.repo_dir)
        if args.command == "record":
            try:
                payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raise SyncError("Unable to read private input JSON") from None
            sync.record(payload)
        elif args.command == "build":
            sync.build(args.output_dir)
        elif args.command == "consent":
            sync.consent()
        elif args.command == "publish":
            sync.publish(args.allow_encrypted_publication)
        print(json.dumps(sync.status()))
    except SyncError as error:
        parser.exit(1, f"LATTE: {error}\n")


if __name__ == "__main__":
    main()
