"""Original transactional installer; its overlap and privacy checks stay intact."""
from legacy_loader import populate as _populate

_populate(globals(), "installer")

# A full runtime contains almost 18,000 files. Enumerating every Windows process
# before each file adds roughly 85 seconds on the measured machine. Keep the
# existing process guard, with a bounded poll interval during one transaction.
import threading as _threading

_original_install = install
_original_closed = _closed
_original_atomic = _atomic
_install_poll = _threading.local()
PROCESS_POLL_INTERVAL = 0.25


class ApplicationsOpen(InstallError):
    """A positively identified application guard, eligible for a deferred retry."""


def _guard_closed(sandbox):
    try:
        return _original_closed(sandbox)
    except InstallError as exc:
        # Only the original guard's controlled open-process outcome is typed.
        # Snapshot/permission failures and unrelated installation errors remain
        # ordinary failures; a currently open app cannot hide another error.
        if str(exc).startswith("Save your work and close these applications: "):
            raise ApplicationsOpen(str(exc)) from exc
        raise


def is_applications_open(exc):
    """Allow waiting only before writes, or after a confirmed complete rollback.

    The recovered transaction explicitly chains its original guard exception
    after rollback. Retained edits/recovery-required outcomes must remain errors,
    even when an ApplicationsOpen exception appears deeper in their cause chain.
    Implicit exception contexts and arbitrary wrapper messages are not trusted.
    """
    seen = set()
    while id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, ApplicationsOpen):
            return True
        if (not isinstance(exc, InstallError)
                or not str(exc).startswith("Installation stopped. Previous files restored. Backup: ")):
            return False
        exc = exc.__cause__
        if exc is None:
            return False
    return False


def _closed(sandbox):
    state = getattr(_install_poll, "state", None)
    if state is None:
        return _guard_closed(sandbox)
    state["sandbox"] = sandbox
    now = time.monotonic()
    if now >= state["next"]:
        _guard_closed(sandbox)
        state["next"] = now + PROCESS_POLL_INTERVAL


def _atomic(file, data, expected):
    state = getattr(_install_poll, "state", None)
    if state is not None and Path(file).name == "install-receipt.json":
        # The receipt is the last file in the transaction. Force a fresh guard
        # inside the existing rollback boundary, even for a sub-250ms install.
        state["next"] = 0.0
        _guard_closed(state["sandbox"])
        state["next"] = time.monotonic() + PROCESS_POLL_INTERVAL
    return _original_atomic(file, data, expected)


def install(payload_root, base_root=None, dry_run=False):
    if getattr(_install_poll, "state", None) is not None:
        raise InstallError("An installation is already active on this thread.")
    _install_poll.state = {"next": 0.0, "sandbox": base_root is not None}
    try:
        return _original_install(payload_root, base_root=base_root, dry_run=dry_run)
    finally:
        del _install_poll.state
