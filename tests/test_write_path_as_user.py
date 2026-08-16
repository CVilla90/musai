"""Every writer MUSAI has can act for another professor — and none of them can do it by accident.

🔴 **Why this file exists (2026-08-13, English IV).** English I, II and III each had a course of
The owner's to build in, so the write path never needed an identity: `enter_course` logged in as
him and that was correct. English IV has **no course of his at all** — the academia is Colleague A ×2
and Colleague B ×2 (plus Colleague D, out of scope) — so the master course of this level is somebody
else's, and every structural write lands in it.

The dangerous failure is **not** an error. It is a parameter that is accepted, ignored, and the
run silently proceeds as the owner: `enter_course` would open *his* dashboard, fail to find the
tile, and the error would name the wrong cause — or, worse, find a same-numbered course of his
and write to it. So each test pins the value reaching **`enter_course`**, which is the only
thing that chooses an identity, and which refuses rather than falling back
(`credentials.resolve`).

Source-level assertions on purpose: these functions each drive a real browser, so a behavioural
test would have to fake all of Playwright to observe one keyword argument. The thing worth
pinning is the wiring, and the wiring is visible in the source.
"""

import inspect

import pytest

from musai.coursebuild.activity import create_activity
from musai.coursebuild.book import upsert_chapters
from musai.coursebuild.remove import delete_activity, delete_section
from musai.coursebuild.rename import rename_activity
from musai.coursebuild.structure import (
    rename_section, set_activity_visibility, set_section_visibility,
)
from musai.coursedates.apply import apply_plan

#: Every function that writes to a course, with the exact `enter_course` call its source must
#: contain. Two spellings exist because some writers open the page first and some let
#: `enter_course` make it — both are fine, and neither may drop the keyword.
WRITERS = [
    (create_activity, "enter_course(ctx, page, idc, as_user=as_user)"),
    (upsert_chapters, "enter_course(ctx, page, idc, as_user=as_user)"),
    (delete_activity, "enter_course(ctx, ctx.new_page(), idc, as_user=as_user)"),
    (delete_section, "enter_course(ctx, ctx.new_page(), idc, as_user=as_user)"),
    (rename_activity, "enter_course(ctx, ctx.new_page(), idc, as_user=as_user)"),
    (rename_section, "enter_course(ctx, page, idc, as_user=as_user)"),
    (set_activity_visibility, "enter_course(ctx, page, idc, as_user=as_user)"),
    (set_section_visibility, "enter_course(ctx, ctx.new_page(), idc, as_user=as_user)"),
    (apply_plan, "enter_course(ctx, page, idc, as_user=as_user, identity=identity)"),
]


@pytest.mark.parametrize("fn,call", WRITERS, ids=lambda v: getattr(v, "__name__", ""))
def test_the_professor_reaches_the_login_and_not_just_the_signature(fn, call):
    src = inspect.getsource(fn)
    assert "as_user" in inspect.signature(fn).parameters, (
        f"{fn.__name__} cannot act for another professor, so it cannot write to any INGLES IV "
        f"course — the owner owns none of them.")
    assert call in src, (
        f"{fn.__name__} accepts `as_user` but does not pass it to `enter_course`. That is worse "
        f"than not accepting it: the run would proceed as the owner and report success.")


#: Writers that additionally take a pre-resolved `identity` — the COCKPIT's road, where the
#: signed-in professor's own password comes out of the vault rather than out of `.env`.
IDENTITY_WRITERS = [apply_plan]


@pytest.mark.parametrize("fn", IDENTITY_WRITERS, ids=lambda v: getattr(v, "__name__", ""))
def test_a_resolved_identity_reaches_the_login_too(fn):
    """🔴 The same "accepts it but drops it" bug, on the parameter the web app uses.

    Dropping `identity` is worse than dropping `as_user`, because of where it falls back TO:
    `enter_course` with neither resolves `UACH_USERNAME`/`UACH_PASSWORD`, which is **The owner's
    account**. A colleague pressing *Aplicar* on her own course would have MUSAI sign in as him
    and write dates into her gradebook under his name. Found on 2026-08-14 — `apply_plan` was
    reached from the cockpit with no identity at all, invisible for exactly as long as the
    database had one user.
    """
    src = inspect.getsource(fn)
    assert "identity" in inspect.signature(fn).parameters, (
        f"{fn.__name__} cannot be driven from the cockpit: it has no way to be told whose "
        f"account to act as, so it falls back to whoever .env names.")
    assert "identity=identity" in src, (
        f"{fn.__name__} accepts `identity` but never forwards it to `enter_course`, so every "
        f"cockpit run silently authenticates as the .env account instead.")


@pytest.mark.parametrize("fn,_call", WRITERS, ids=lambda v: getattr(v, "__name__", ""))
def test_acting_for_someone_else_is_never_the_default(fn, _call):
    """`as_user=None` means *me*. A writer that defaulted to a username would put a colleague's
    account behind a command nobody wrote it into."""
    assert inspect.signature(fn).parameters["as_user"].default is None


def test_the_delete_rails_did_not_relax_when_the_identity_arrived():
    """🔴 The one thing that must NOT change. Deleting in someone else's course is the highest
    -stakes write this project makes: the owner cannot see the course, the professor did not run
    the command, and there is no undo. `expect_name` stays required, the visible check stays,
    and the user-content count stays — measured from the source of both delete paths."""
    for fn in (delete_activity, delete_section):
        sig = inspect.signature(fn)
        assert sig.parameters["expect_name"].default is inspect.Parameter.empty, (
            f"{fn.__name__} made `expect_name` optional — the identity rail was traded for a "
            f"convenience.")
    src = inspect.getsource(delete_activity)
    assert "allow_visible" in src and "user_content" in src


def test_a_backup_can_be_taken_for_another_professor():
    """A propagation needs a `.mbz` of the finished master, and this level's master belongs to
    Colleague A. Until 2026-08-13 `backup_course` read `settings.uach_*` directly, so a colleague's
    course could not be archived at all and `dump_targets_english_iv.py` said so out loud."""
    from musai.automation.backup import backup_course

    sig = inspect.signature(backup_course)
    assert "as_user" in sig.parameters and sig.parameters["as_user"].default is None
    src = inspect.getsource(backup_course)
    assert "resolve(as_user)" in src or "resolve_identity(as_user)" in src, (
        "the username must go through `credentials.resolve`, which refuses rather than falling "
        "back to the owner's own login")


def test_dates_can_be_written_for_a_colleague_but_only_by_a_named_script():
    """The asymmetry that survived: the *library* can, the *command line* cannot.

    See `test_coursedates.py::test_no_date_can_be_written_as_another_professor_FROM_A_COMMAND
    _LINE`. A library parameter is reached only by a script that names the professor and the
    course; a CLI flag is reached by a typo in a shell.
    """
    from musai.coursedates import __main__ as cli

    assert "as_user" in inspect.signature(apply_plan).parameters
    assert "--as-user" not in inspect.getsource(cli)
