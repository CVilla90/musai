"""Local Playwright runner (stub).

This module is the entry point for:  python -m musai.runner

It will eventually:
  1. Poll JobRequest table for pending jobs queued from the cockpit.
  2. Run Moodle adapter (login, export gradebook, scrape submissions).
  3. Run SEGA adapter (DRY-RUN by default; SAVE on explicit human action).
  4. Write results + AuditLog rows to the shared Postgres.

Phase 0: prints a startup message and exits gracefully.
Phase 4: full job poller (see PLAN §11).
"""

import sys
from musai.config import settings


def main() -> None:
    print("MUSAI local runner — Phase 0 stub")
    print(f"  dry_run  : {settings.dry_run}")
    print(f"  moodle   : {settings.moodle_base_url_prod}")
    print(f"  sega     : {settings.sega_base_url}")
    print()
    print("No job poller implemented yet (Phase 4).")
    print("Run individual adapters from musai.automation.* directly.")


if __name__ == "__main__":
    main()
