"""Reset the simulated backend store to its seed state.

    python3 -m conductor_loop_workers.reset

Run between demo runs of the refund example: the ledger in .state/store.json is
durable (that is the point — it is the system of record the evaluator verifies),
so a previous run's refund would otherwise still be on file.
"""
from . import datastore

if __name__ == "__main__":
    datastore.reset()
    store = datastore.read()
    print(f"store reset: {len(store['accounts'])} account(s), "
          f"{len(store['orders'])} order(s), empty refund ledger.")
