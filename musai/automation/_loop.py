"""Windows event-loop policy guard for the Playwright adapters.

Why this exists (found 2026-08-07, from a publish that failed with a blank error):

``uvicorn --reload`` calls ``asyncio_setup(use_subprocess=True)``, and on win32 that installs
``WindowsSelectorEventLoopPolicy`` process-wide (see ``uvicorn/loops/asyncio.py``). Playwright's
SYNC api builds its own event loop with ``asyncio.new_event_loop()``, which honours that global
policy — and a ``SelectorEventLoop`` on Windows **cannot spawn a subprocess**, which is exactly
what launching the browser is. So ``chromium.launch()`` died with a bare ``NotImplementedError``
inside the worker thread, before a single step had run.

Running the same publish from the CLI always worked — no uvicorn, so the default Proactor policy
was still in place. That asymmetry is the tell: it was never the browser, the credentials, or
Moodle. **The cockpit's own start command was the trigger**, and only when reloading.

Restoring the Proactor policy is safe: uvicorn's loop is created and running before the app is
even imported, and this never touches a RUNNING loop — only the factory for loops created after
it. Call it before ``sync_playwright()``, from every adapter, because any of them may one day be
driven from a background job (the .mbz restore is next in line).
"""

import asyncio
import sys


def ensure_subprocess_capable_loop() -> bool:
    """Guarantee event loops created after this call can spawn subprocesses.

    Returns True if the policy was actually changed, so callers can log it.
    """
    if sys.platform != "win32":
        return False  # every POSIX loop can spawn subprocesses
    proactor = asyncio.WindowsProactorEventLoopPolicy  # win32-only attribute
    if isinstance(asyncio.get_event_loop_policy(), proactor):
        return False
    asyncio.set_event_loop_policy(proactor())
    return True
