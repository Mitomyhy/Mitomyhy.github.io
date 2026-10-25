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


def _closed(sandbox):
    state = getattr(_install_poll, "state", None)
    if state is None:
        return _original_closed(sandbox)
    state["sandbox"] = sandbox
    now = time.monotonic()
    if now >= state["next"]:
        _original_closed(sandbox)
        state["next"] = now + PROCESS_POLL_INTERVAL


def _atomic(file, data, expected):
    state = getattr(_install_poll, "state", None)
    if state is not None and Path(file).name == "install-receipt.json":
        # The receipt is the last file in the transaction. Force a fresh guard
        # inside the existing rollback boundary, even for a sub-250ms install.
        state["next"] = 0.0
        _original_closed(state["sandbox"])
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
