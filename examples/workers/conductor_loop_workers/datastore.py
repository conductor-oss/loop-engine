"""A tiny file-backed, process-safe store that simulates a backend system of record.

Workers run as separate processes, so the refund example needs a shared, durable
store with proper locking — exactly the kind of "real backend" a support agent must
read from and write to. State lives in ``.state/store.json`` next to this package and
is human-inspectable, so you can SEE that a refund was actually recorded (evidence),
not merely claimed by the model.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
from typing import Any, Callable

_STATE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".state")
_STORE_PATH = os.path.join(_STATE_DIR, "store.json")
_LOCK_PATH = os.path.join(_STATE_DIR, "store.lock")

# Seed data: accounts, orders (with purchase recency), and an empty refund ledger.
_SEED: dict[str, Any] = {
    "accounts": {
        "CUST-1001": {"customer_id": "CUST-1001", "name": "Dana Reyes",
                      "email": "dana@example.com", "status": "active"},
    },
    "orders": {
        "ORD-5001": {"order_id": "ORD-5001", "customer_id": "CUST-1001",
                     "total": 120.00, "currency": "USD", "status": "delivered",
                     "days_since_purchase": 12, "item": "Noise-cancelling headphones"},
        "ORD-5002": {"order_id": "ORD-5002", "customer_id": "CUST-1001",
                     "total": 80.00, "currency": "USD", "status": "delivered",
                     "days_since_purchase": 45, "item": "Mechanical keyboard"},
    },
    "refund_ledger": [],  # [{refund_id, order_id, amount, reason, status, revisions?}]
    "next_refund_seq": 1,  # persisted monotonic counter for refund ids (deletion-safe)
}


def _ensure() -> None:
    os.makedirs(_STATE_DIR, exist_ok=True)
    if not os.path.exists(_STORE_PATH):
        # Create atomically; tolerate a race with another worker process.
        tmp = _STORE_PATH + f".tmp.{os.getpid()}"
        with open(tmp, "w") as fh:
            json.dump(_SEED, fh, indent=2)
        try:
            os.link(tmp, _STORE_PATH)
        except FileExistsError:
            pass
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.remove(tmp)


@contextlib.contextmanager
def _locked():
    """Hold an exclusive cross-process lock for a read-modify-write transaction."""
    _ensure()
    with open(_LOCK_PATH, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def read() -> dict:
    _ensure()
    with open(_STORE_PATH) as fh:
        return json.load(fh)


def transact(fn: Callable[[dict], Any]) -> Any:
    """Run ``fn(store)`` under the lock and persist the (mutated) store. Returns fn's result."""
    with _locked():
        with open(_STORE_PATH) as fh:
            store = json.load(fh)
        result = fn(store)
        tmp = _STORE_PATH + f".tmp.{os.getpid()}"
        with open(tmp, "w") as fh:
            json.dump(store, fh, indent=2)
        os.replace(tmp, _STORE_PATH)  # atomic
        return result


def reset() -> None:
    """Reset to seed state (useful between demo runs)."""
    os.makedirs(_STATE_DIR, exist_ok=True)
    with _locked():
        tmp = _STORE_PATH + f".tmp.{os.getpid()}"
        with open(tmp, "w") as fh:
            json.dump(_SEED, fh, indent=2)
        os.replace(tmp, _STORE_PATH)
