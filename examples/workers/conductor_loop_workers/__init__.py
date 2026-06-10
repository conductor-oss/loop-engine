"""Python workers backing the loop-engine examples.

Importing this package registers every @worker_task so a TaskHandler started with
scan_for_annotated_workers=True will discover them. See run_workers.py.
"""
from . import code_runner      # noqa: F401  python_code_runner
from . import data_quality     # noqa: F401  clean_dataset, data_quality_check
from . import refund           # noqa: F401  account_lookup, issue_refund, verify_refund

__all__ = ["code_runner", "data_quality", "refund"]
