"""Tests for publishing into a course SECTION SUMMARY.

The live finding this path exists for (2026-08-07): a Moodle course's home page is the
**section summary**, not an activity. MUSAI's hub was publishing as a label, which stacked a
second hub underneath the owner's own hand-written page instead of replacing it.

Three things here are worth more than the rest, because each one has a live counterpart that
already bit this project once:

* **The refusal.** A summary MUSAI did not write is somebody's real work. Overwriting it takes
  a second, deliberate key.
* **The section id comes from the link.** For 1-LED-A section 0 there are two plausible ids in
  the page — 107684 (a file area) and 127099 (the real `course_section.id`). Deriving it any
  other way picks the wrong one.
* **The submit button is matched by id.** The section form's FIRST submit is the course search
  box. `input[type=submit]").first` there is a no-op that looks exactly like a failed save —
  the same trap the activity-delete page sprang.

The browser is faked (see `FakePage`) rather than mocked away entirely, so the flow — read,
back up, decide, fill, save, verify — is actually executed.
"""

from datetime import date

import pytest
from sqlmodel import Session

from musai.coursebuild import jobs, publish_section
from musai.coursebuild.publish_section import _is_foreign, publish_section_summary
from musai.coursebuild.render import MARKER_PREFIX
from musai.models import Course, Semester

PROFESSOR_PAGE = ('<div style="font-family: Arial"><!-- HEADER -->'
                  '<div>English I (A1) · Course Hub</div></div>')
MUSAI_PAGE = f"<!-- {MARKER_PREFIX}course-hub --><div>MUSAI hub</div>"
NEW_HTML = f"<!-- {MARKER_PREFIX}course-hub --><div>the new hub</div>"

EDIT_HREF = "https://virtual3.uach.mx/course/editsection.php?id=127099&sr=0"


# ── the foreign-content rule ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("previous, foreign", [
    (None, False),                       # never populated
    ("", False),                         # empty is free to take
    ("   \n  ", False),                  # whitespace is empty
    (MUSAI_PAGE, False),                 # ours — updating our own page is routine
    (PROFESSOR_PAGE, True),              # 🔴 the owner's real page
    ("<p>Bienvenidos al curso</p>", True),   # any hand-written note counts
])
def test_is_foreign_only_protects_content_musai_did_not_write(previous, foreign):
    assert _is_foreign(previous) is foreign


def test_a_musai_marker_anywhere_in_the_summary_makes_it_ours():
    """The marker is an HTML comment and may sit after a wrapper div, not only at the front."""
    assert _is_foreign(f'<div style="x">text</div><!-- {MARKER_PREFIX}course-hub -->') is False


# ── a fake Moodle, small enough to read ─────────────────────────────────────────────────

class FakeLocator:
    def __init__(self, page, selector):
        self.page, self.selector = page, selector

    @property
    def first(self):
        return self

    def count(self):
        return 1 if self.selector in self.page.present else 0

    def get_attribute(self, _name):
        return self.page.present.get(self.selector)

    def click(self, **_kw):
        self.page.clicked.append(self.selector)


class FakePage:
    """Just enough Playwright surface for publish_section_summary to run end to end."""

    def __init__(self, summary, *, present=None, marker_after_save=True):
        self.summary = summary
        self.marker_after_save = marker_after_save
        self.present = present if present is not None else {
            "#section-0 a[href*=\"editsection.php\"]": EDIT_HREF,
            "#id_submitbutton": "Guardar cambios",
        }
        self.clicked, self.visited, self.shots = [], [], []
        self.saved_content = None

    # navigation
    def goto(self, url, **_kw):
        self.visited.append(url)

    def wait_for_load_state(self, *_a, **_kw):
        pass

    def locator(self, selector):
        return FakeLocator(self, selector)

    def screenshot(self, path=None, **_kw):
        self.shots.append(path)

    def content(self):
        return (f"<!-- {MARKER_PREFIX}course-hub -->" if self.marker_after_save else "<html>")

    def evaluate(self, script, arg=None):
        if "M.cfg.sesskey" in script:
            return "SESSKEY"
        if "getContent" in script:
            return self.summary
        if "setContent" in script:
            self.saved_content = arg
            return "tinymce"
        raise AssertionError(f"unexpected evaluate: {script[:60]}")


@pytest.fixture
def fake_browser(monkeypatch, tmp_path):
    """Point the module at a FakePage and keep screenshots/backups inside tmp_path."""
    holder = {}

    class _Ctx:
        def new_page(self):
            return holder["page"]

        def close(self):
            pass

    class _Browser:
        def new_context(self, **_kw):
            return _Ctx()

        def close(self):
            pass

    class _PW:
        chromium = type("c", (), {"launch": staticmethod(lambda **_kw: _Browser())})()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(publish_section, "sync_playwright", lambda: _PW())
    # `**kw` so the stub keeps matching when the real signature grows — `as_user` was added
    # 2026-08-11 and a positional-only fake fails with a TypeError that surfaces as
    # "the save was never clicked", i.e. it points at the assertion rather than the stub.
    monkeypatch.setattr(publish_section, "enter_course",
                        lambda ctx, page, idc, **kw: (holder["page"], "virtual3.uach.mx"))
    monkeypatch.setattr(publish_section, "editing_on",
                        lambda *a, **kw: None)
    monkeypatch.setattr(publish_section, "SHOT_DIR", tmp_path / "shots")
    monkeypatch.setattr(publish_section, "BACKUP_DIR", tmp_path / "backups")
    return holder


def run(fake_browser, page, **kw):
    fake_browser["page"] = page
    return publish_section_summary(idc="9023", html=NEW_HTML, section=0, **kw)


# ── the refusal rail ────────────────────────────────────────────────────────────────────

def test_a_live_publish_refuses_to_destroy_the_professors_own_page(fake_browser):
    page = FakePage(PROFESSOR_PAGE)
    out = run(fake_browser, page, dry_run=False)

    assert out["ok"] is False
    assert out["would_overwrite_foreign"] is True
    assert "overwrite_foreign=True" in out["error"]
    assert page.clicked == [], "nothing may be submitted when the publish is refused"


def test_the_refusal_tells_you_where_the_backup_is(fake_browser):
    """A refusal that does not say how to recover is only half a safeguard."""
    out = run(fake_browser, FakePage(PROFESSOR_PAGE), dry_run=False)
    assert out["previous_backup"] and out["previous_backup"] in out["error"]


def test_overwrite_foreign_is_the_deliberate_escalation(fake_browser):
    page = FakePage(PROFESSOR_PAGE)
    out = run(fake_browser, page, dry_run=False, overwrite_foreign=True)

    assert out["ok"] is True
    assert page.clicked == ["#id_submitbutton"]
    assert page.saved_content == NEW_HTML


def test_updating_musais_own_page_needs_no_escalation(fake_browser):
    """Republishing the hub is routine — the rail protects the professor, not MUSAI."""
    page = FakePage(MUSAI_PAGE)
    out = run(fake_browser, page, dry_run=False)

    assert out["ok"] is True
    assert out["would_overwrite_foreign"] is False
    assert page.clicked == ["#id_submitbutton"]


def test_an_empty_summary_is_free_to_take(fake_browser):
    page = FakePage("")
    out = run(fake_browser, page, dry_run=False)
    assert out["ok"] is True and page.clicked == ["#id_submitbutton"]


# ── the dry run ─────────────────────────────────────────────────────────────────────────

def test_a_dry_run_previews_even_when_it_would_overwrite_foreign_content(fake_browser):
    """Seeing what WOULD happen is the whole point — the dry run must not refuse."""
    page = FakePage(PROFESSOR_PAGE)
    out = run(fake_browser, page, dry_run=True)

    assert out["ok"] is True
    assert out["would_overwrite_foreign"] is True
    assert page.saved_content == NEW_HTML, "the editor is filled so the screenshot is real"
    assert page.clicked == [], "a dry run never submits"
    assert out["screenshot"]


def test_a_dry_run_still_backs_up_what_it_would_replace(fake_browser):
    out = run(fake_browser, FakePage(PROFESSOR_PAGE), dry_run=True)
    from pathlib import Path
    assert Path(out["previous_backup"]).read_text(encoding="utf-8") == PROFESSOR_PAGE


# ── the two ids, and the three buttons ──────────────────────────────────────────────────

def test_the_section_id_is_read_from_the_link_not_guessed(fake_browser):
    """127099 is the real course_section.id; 107684 is a file area that also appears on the
    page. Only the link carries the right one."""
    out = run(fake_browser, FakePage(""), dry_run=True)
    assert out["section_id"] == "127099"


def test_it_fails_closed_when_the_edit_link_is_missing(fake_browser):
    """No link means editing did not engage, or the wrong section is displayed. Guessing an
    id here would edit SOME section — just not necessarily the one asked for."""
    page = FakePage("", present={"#id_submitbutton": "Guardar cambios"})
    out = run(fake_browser, page, dry_run=True)

    assert out["ok"] is False
    assert "Editar sección" in out["error"]
    assert page.saved_content is None


def test_it_fails_closed_when_the_form_did_not_load(fake_browser):
    page = FakePage("", present={"#section-0 a[href*=\"editsection.php\"]": EDIT_HREF})
    out = run(fake_browser, page, dry_run=True)

    assert out["ok"] is False and "#id_submitbutton" in out["error"]


def test_the_save_is_clicked_by_id_never_by_position(fake_browser):
    """The FIRST submit on the section form is the course search box."""
    page = FakePage("")
    run(fake_browser, page, dry_run=False)
    assert page.clicked == ["#id_submitbutton"]
    assert all(c.startswith("#id_") for c in page.clicked)


def test_a_missing_marker_after_save_is_reported_not_swallowed(fake_browser):
    page = FakePage("", marker_after_save=False)
    out = run(fake_browser, page, dry_run=False)

    assert out["ok"] is True
    assert out["verified"] is False
    assert any("not found" in s for s in out["steps"])


# ── job routing ─────────────────────────────────────────────────────────────────────────

@pytest.fixture
def course(session: Session, monkeypatch):
    sem = Semester(name="test-sem-2", is_active=True,
                   starts_on=date(2026, 8, 10), ends_on=date(2026, 12, 18))
    session.add(sem)
    session.commit()
    session.refresh(sem)
    c = Course(group_code="1-LED-A", subject="INGLES I", level=1, semester_id=sem.id,
               moodle_course_id="9023")
    session.add(c)
    session.commit()
    session.refresh(c)
    monkeypatch.setattr(jobs, "engine", session.get_bind())
    return c


def test_the_job_routes_the_hub_to_the_section_summary(monkeypatch, course):
    """The hub must not go back to being a label by accident."""
    calls = {}
    import musai.coursebuild.publish_section as ps_mod

    def fake_summary(course_, html, **kw):
        calls.update(target="summary", **kw)
        return {"ok": True, "steps": []}

    monkeypatch.setattr(ps_mod, "publish_summary_for_course", fake_summary)
    job_id = jobs.create_job(course.id, NEW_HTML, 0, True, jobs.SECTION_SUMMARY, True)
    jobs._run(job_id, course.id, NEW_HTML, 0, True, jobs.SECTION_SUMMARY, True)

    assert calls["target"] == "summary"
    assert calls["overwrite_foreign"] is True
    assert calls["section"] == 0


def test_the_builder_still_publishes_labels(monkeypatch, course):
    """`/build` is unchanged — content *inside* a course is still an activity."""
    calls = {}
    import musai.coursebuild.publish as p_mod

    monkeypatch.setattr(p_mod, "publish_for_course",
                        lambda course_, html, **kw: calls.update(target="label", **kw)
                        or {"ok": True, "steps": []})
    job_id = jobs.create_job(course.id, NEW_HTML, 0, True)
    jobs._run(job_id, course.id, NEW_HTML, 0, True)

    assert calls["target"] == "label"


def test_the_jobs_default_target_is_the_label(course):
    """Defaulting to the summary would silently change what `/build` does."""
    job_id = jobs.create_job(course.id, NEW_HTML, 0, True)
    assert jobs.get_job(job_id)["params"]["target"] == jobs.LABEL
    assert jobs.get_job(job_id)["params"]["overwrite_foreign"] is False
