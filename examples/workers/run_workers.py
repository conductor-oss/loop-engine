#!/usr/bin/env python3
"""Start all loop-engine example workers.

    export CONDUCTOR_SERVER_URL=http://localhost:8080/api   # already set in this env
    python run_workers.py

Workers poll Conductor for SIMPLE tasks until interrupted. Each @worker_task in the
conductor_loop_workers package is discovered via scan_for_annotated_workers=True.
The task DEFINITIONS (retries/timeouts) are registered separately (taskdefs/, via
`conductor task create`) — production practice, rather than auto-registering from code.
"""
import logging
import signal
import sys

from conductor.client.automator.task_handler import TaskHandler
from conductor.client.configuration.configuration import Configuration

# Importing the package registers every @worker_task with the SDK's global registry.
import conductor_loop_workers  # noqa: F401

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("conductor_loop.run")


def main() -> int:
    config = Configuration()  # reads CONDUCTOR_SERVER_URL / CONDUCTOR_AUTH_* from env
    log.info("Starting workers against %s", config.host)
    handler = TaskHandler(configuration=config, scan_for_annotated_workers=True)

    def _shutdown(signum, _frame):
        log.info("Signal %s received; stopping workers.", signum)
        try:
            handler.stop_processes()
        finally:
            sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    handler.start_processes()
    log.info("Workers polling: python_code_runner, clean_dataset, data_quality_check, "
             "account_lookup, issue_refund, verify_refund")
    handler.join_processes()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
