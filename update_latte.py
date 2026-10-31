#!/usr/bin/env python3
"""Apply private memory and an explicitly separate, limited page update in one step.

This helper never grants publication permission or copies private memory to the page.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
import fcntl
import hashlib
import json
import os
from pathlib import Path

from sync_latte import COLLECTIONS, ROOT, SyncError, WorkspaceSync, validate_snapshot


def merge_snapshot(original, patch):
    if not isinstance(patch, dict) or set(patch) - {"profile", "coverage", *COLLECTIONS}:
        raise SyncError("Update contains unsupported record fields")
    candidate = deepcopy(original)
    for key in ("profile", "coverage"):
        if key in patch:
            if not isinstance(patch[key], dict):
                raise SyncError("Record metadata must be an object")
            metadata = deepcopy(patch[key])
            if key == "profile" and isinstance(metadata.get("preferences"), dict):
                metadata["preferences"] = {**candidate["profile"]["preferences"], **metadata["preferences"]}
            candidate[key] = {**candidate[key], **metadata}
    for collection in COLLECTIONS:
        if collection not in patch:
            continue
        rows = patch[collection]
        if not isinstance(rows, list) or any(not isinstance(row, dict) or not isinstance(row.get("id"), str) for row in rows):
            raise SyncError("Invalid update collection")
        if len({row["id"] for row in rows}) != len(rows):
            raise SyncError("Duplicate update record id")
        indexed = {row["id"]: row for row in candidate[collection]}
        for row in rows:
            merged = deepcopy(indexed.get(row["id"], {}))
            for key, value in row.items():
                # Row patches are small, explicit edits. Arrays still replace;
                # only the known flat nested objects merge field by field.
                if key in {"dashboard", "job", "user_decision"} and isinstance(value, dict):
                    previous = merged.get(key)
                    merged[key] = {**(previous if isinstance(previous, dict) else {}), **deepcopy(value)}
                else:
                    merged[key] = deepcopy(value)
            indexed[row["id"]] = merged
        candidate[collection] = list(indexed.values())
    candidate["coverage"]["record_count"] = len(candidate["conversations"])
    return validate_snapshot(candidate)


def expanded_record_patch(patch, candidate):
    """WorkspaceSync.record replaces rows, so pass fully preflighted records."""
    expanded = deepcopy(patch)
    if "profile" in patch:
        expanded["profile"] = deepcopy(candidate["profile"])
    for collection in COLLECTIONS:
        if collection in patch:
            indexed = {row["id"]: row for row in candidate[collection]}
            expanded[collection] = [deepcopy(indexed[row["id"]]) for row in patch[collection]]
    return expanded


class LatteUpdate:
    def __init__(self, data_dir=ROOT / ".latte", repo_dir=ROOT, dashboard_dir=ROOT / "dashboard", remote="origin", branch="latte-site"):
        self.data_dir = Path(data_dir)
        self.page_dir = self.data_dir / "page-sync"
        self.repo_dir, self.dashboard_dir = Path(repo_dir), Path(dashboard_dir)
        self.remote, self.branch = remote, branch

    @contextmanager
    def lock(self):
        if self.data_dir.is_symlink() or not self.data_dir.is_dir():
            raise SyncError("Existing private memory is required; nothing was initialized")
        fd = os.open(self.data_dir / "update.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "a+") as file:
            fcntl.flock(file, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(file, fcntl.LOCK_UN)

    @staticmethod
    def read_existing(directory):
        if directory.is_symlink() or not directory.is_dir():
            raise SyncError("Existing record directory is required; nothing was initialized")
        for name in ("workspace.json", "sync-key.bin"):
            p = directory / name
            if p.is_symlink() or not p.is_file():
                raise SyncError("Restore the original workspace and key before updating")
        if len((directory / "sync-key.bin").read_bytes()) != 32:
            raise SyncError("The existing private key is invalid; it was not replaced")
        try:
            return validate_snapshot(json.loads((directory / "workspace.json").read_text(encoding="utf-8")))
        except (OSError, ValueError, KeyError, TypeError):
            raise SyncError("Existing records cannot be read; nothing was changed") from None

    @staticmethod
    def _metadata(path):
        if path.is_symlink():
            raise SyncError("Unexpected metadata link; nothing was changed")
        try:
            value = json.loads(path.read_text())
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def _consent(self):
        if self._metadata(self.page_dir / "publication-consent.json").get("encrypted_public_storage") is not True:
            raise SyncError("The limited page store needs explicit owner publication consent; no records were changed")

    @staticmethod
    def check_page_scope(original, patch, allow_new_page_proposals):
        if any(key in patch for key in ("assets", "documents")) or original.get("assets") or original.get("documents"):
            raise SyncError("This limited page updater cannot publish documents or archived files")
        profile = patch.get("profile", {})
        if not isinstance(profile, dict) or set(profile) - {"facts", "preferences"}:
            raise SyncError("Invalid page profile update")
        if "facts" in profile:
            facts = profile["facts"]
            known = {f["key"] for f in original["profile"]["facts"]}
            if not isinstance(facts, list) or any(not isinstance(f, dict) or f.get("key") not in known for f in facts):
                raise SyncError("A new public profile fact needs separate authorization")
        if "preferences" in profile:
            prefs = profile["preferences"]
            if not isinstance(prefs, dict) or set(prefs) - set(original["profile"]["preferences"]):
                raise SyncError("A new public preference needs separate authorization")
        if "proposals" in patch:
            rows = patch["proposals"]
            known = {p["id"] for p in original["proposals"]}
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise SyncError("Invalid page proposal update")
            if not allow_new_page_proposals and any(row.get("id") not in known for row in rows):
                raise SyncError("New page proposals require --allow-new-page-proposals and an owner request")

    def _fingerprint(self, snapshot):
        digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode())
        for name in ("index.html", "styles.css", "app.js", "avatar.svg"):
            source = self.dashboard_dir / name
            if source.is_symlink() or (name != "avatar.svg" and not source.is_file()):
                raise SyncError("Dashboard assets cannot be read safely")
            digest.update(name.encode())
            digest.update(hashlib.sha256(source.read_bytes()).digest() if source.is_file() else b"absent")
        # Metadata hashes are sufficient for the permitted limited feed (which has
        # no assets); retain the archive component if that policy changes later.
        for asset in sorted(snapshot.get("assets", []), key=lambda a: a["id"]):
            digest.update((asset["id"] + asset["sha256"]).encode())
        return digest.hexdigest()

    def _sync(self, directory):
        return WorkspaceSync(directory, repo_dir=self.repo_dir, dashboard_dir=self.dashboard_dir, remote=self.remote, branch=self.branch)

    def apply(self, payload, publish=False, allow_new_page_proposals=False):
        if not isinstance(payload, dict) or not payload or set(payload) - {"memory", "page"}:
            raise SyncError("Input must explicitly separate memory and page updates")
        with self.lock():
            originals, candidates = {}, {}
            for key, directory in (("memory", self.data_dir), ("page", self.page_dir)):
                if key not in payload:
                    continue
                originals[key] = self.read_existing(directory)
                if key == "page":
                    if not isinstance(payload[key], dict):
                        raise SyncError("Page update must be an object")
                    self.check_page_scope(originals[key], payload[key], allow_new_page_proposals)
                candidates[key] = merge_snapshot(originals[key], payload[key])
            if "page" in payload and publish:
                # Check before either store is changed, including memory.
                self._consent()
            # Validate source files before any record mutation as well.
            if "page" in candidates:
                self._fingerprint(candidates["page"])
            changed = {key: candidates[key] != originals[key] for key in candidates}
            page = None
            for key, directory in (("memory", self.data_dir), ("page", self.page_dir)):
                if key not in payload:
                    continue
                if changed[key]:
                    result = self._sync(directory).record(expanded_record_patch(payload[key], candidates[key]))
                else:
                    result = originals[key]
                if key == "page":
                    page = result
            report = {"memory_changed": changed.get("memory", False), "page_changed": changed.get("page", False),
                      "publish_status": "skipped_no_page_changes", "revision": None, "commit": None}
            if page is None:
                return report
            fingerprint = self._fingerprint(page)
            metadata = self._metadata(self.page_dir / "publication.json")
            cache = self._metadata(self.page_dir / "update-publication-cache.json")
            current = (metadata.get("last_published_revision") == page["revision"]
                       and isinstance(metadata.get("commit"), str) and bool(metadata["commit"])
                       and cache.get("fingerprint") == fingerprint
                       and cache.get("revision") == page["revision"]
                       and cache.get("commit") == metadata["commit"])
            report["revision"] = page["revision"]
            if current:
                report.update(publish_status="unchanged", commit=metadata["commit"])
                return report
            if not publish:
                report["publish_status"] = "pending"
                return report
            sync = self._sync(self.page_dir)
            metadata = sync.publish(allow_encrypted_publication=True)
            # Write cache only after a confirmed successful push. A failed push
            # leaves the local revision pending and can be retried with same input.
            sync._write(self.page_dir / "update-publication-cache.json", {
                "fingerprint": fingerprint, "revision": page["revision"], "commit": metadata["commit"]})
            report.update(publish_status="published", commit=metadata["commit"])
            return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--data-dir", default=str(ROOT / ".latte"))
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--allow-new-page-proposals", action="store_true")
    args = parser.parse_args()
    try:
        try:
            payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise SyncError("Cannot read update JSON; nothing was changed") from None
        result = LatteUpdate(args.data_dir).apply(payload, args.publish, args.allow_new_page_proposals)
        print(json.dumps(result))
    except (SyncError, OSError) as error:
        parser.exit(1, "LATTE update failed; no publication success is claimed. " +
                    (str(error) if isinstance(error, SyncError) else "Check private storage permissions.") + "\n")


if __name__ == "__main__":
    main()
