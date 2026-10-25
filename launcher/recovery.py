"""Recover only the transaction named by an existing, owned pending receipt.

The original rollback validates the complete inventory, path ownership and
backup hashes, and preserves targets edited after the interrupted transaction.
An installed backup discovered without a pending receipt is never rolled back.
"""
from __future__ import annotations


def _remove_empty_created_parents(installer, backup, roots):
    """Remove only empty managed directories left by newly created files.

    File restoration alone leaves panel/CSXS behind after a fresh installation
    is interrupted. The CEP identity guard then rejects that otherwise empty
    target on retry. No recursive delete or unowned file removal is permitted.
    """
    journal = installer._read_json(backup / "transaction.json")
    candidates = set()
    for row in journal["files"]:
        if row["before"] is not None:
            continue
        target = installer._journal_target(row, roots)
        owner = next(root for root in roots.values() if root in target.parents)
        parent = target.parent
        while parent == owner or owner in parent.parents:
            candidates.add(parent)
            if parent == owner:
                break
            parent = parent.parent
    for path in sorted(candidates, key=lambda item: len(item.parts), reverse=True):
        path = installer._plain(path)
        if not path.is_dir():
            continue
        try:
            path.rmdir()
        except OSError:
            # A user-created file, retained edit or another nonempty directory
            # stays intact. Normal target identity checks still apply on retry.
            pass


def recover_pending(installer, base_root=None):
    roots, _local, _sandbox = installer._roots(base_root)
    pending = installer._plain(roots["launcher"] / "pending-transaction.json")
    if not pending.exists():
        return None
    if not pending.is_file():
        raise installer.InstallError("The unfinished update receipt is not a regular file; it was preserved.")
    expected = installer._hash(pending)
    # get_recovery_backup also offers manual rollback to the latest installed
    # backup when no pending receipt exists. The checks on both sides prevent
    # that unrelated fallback from becoming an automatic recovery operation.
    backup = installer.get_recovery_backup(base_root=base_root)
    if backup is None or installer._hash(pending) != expected:
        raise installer.InstallError("The unfinished update receipt changed; no automatic recovery was started.")
    result = installer.rollback(backup, base_root=base_root)
    if result.get("status") != "rolled_back" or result.get("retained"):
        raise installer.InstallError(
            "The unfinished update has later edits or files needing recovery. "
            "They were preserved; the new installation has not started. Backup: " + str(backup))
    if pending.exists():
        raise installer.InstallError(
            "The previous files were restored, but the unfinished receipt is still present. "
            "It was preserved; retry was not started. Backup: " + str(backup))
    _remove_empty_created_parents(installer, backup, roots)
    return result
