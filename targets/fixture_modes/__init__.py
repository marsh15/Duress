"""Fixture-mode control target (plan C.1).

These modes exist only to prove the oracle's positive controls. They are not
models of a real integration.
"""

MODES = (
    "no_sig_check",
    "ack_without_effect",
    "reject_all",
    "crash_on_boot",
    "hang_on_shutdown",
    "dup_then_hang",
    "spawn_orphan",
    "mark_duplicate_captured",
)