"""Delete an activity, or an empty section. LOCAL RUNNER ONLY. The one operation with no undo.

Deliberately a **separate module** from `structure.py`, which promises "reversible by
construction". Everything here destroys. Importing it is meant to be a decision.

The owner's standing rule was *"prefer hide over delete; build deletion last and build it
paranoid"* (2026-08-08). It is built last, and this is what paranoid means here — every rail is
a refusal that costs a page load, against a mistake that costs a semester of student work:

1. 🔴 **`expect_name` is required.** The caller must state which activity it believes it is
   deleting, and the run refuses if the activity's own settings form disagrees. A mistyped cmid
   loads a perfectly valid form for the *wrong* activity, and every later check would pass —
   the same trap `ActivitySpec.cmid` had to be hardened against (COURSE_EDITING §5).
   ⚠️ **A `label` has no name field at all**, so for labels the caller quotes the label's own
   text instead. See `label_identity_matches` — the rail changes what it compares against, it
   does not relax.
2. 🔴 **Refuses to delete anything students can currently see.** Hide it, look at the course,
   then delete. `allow_visible=True` exists so the rail is a decision, not an obstacle.
3. 🔴 **Refuses if it finds — or cannot measure — user content.** Submissions and forum posts
   are what makes a delete irreversible in the way that matters. "Unmeasurable" refuses too:
   an unknown count is not a zero count.
4. **Dry run by default**, and a dry run proves every rail passes and the confirm form exists.
5. **`AuditLog` either way**, carrying what was measured *before* the delete — the row must be
   able to answer "what exactly did we destroy?" after the thing is gone.

### The two paths, and the correction that cost a live run

| | activity | section |
|---|---|---|
| trigger | `GET /course/mod.php?…&delete=<cmid>` | `GET /course/editsection.php?id=<sid>&sr=<n>&delete=1&sesskey=<k>` |
| what the GET does | renders a **confirm page** | renders a **confirm page** |
| the mutation is | the POST of the confirm form | the POST of the confirm form |

🔴 **CORRECTION, measured 2026-08-12 on 9067 (`scratchpad/probe_section_delete_9067.py`).**
This docstring and COURSE_EDITING §7 both said the section GET *"deletes, immediately, and
redirects"*, inferred from an accident on 2026-08-09 in which a probe opened one and 1-LED-A's
§7 vanished. **It does not.** Following the rendered *Eliminar sección* link lands on a page
titled *Eliminar sección* asking *"¿Está Usted absolutamente seguro…"* and carrying three forms
— and the mutation is the POST of the middle one (`confirm=1` + `delete=1` + `id=<sid>`).

The consequence was that `delete_section` **could not delete a section at all**: it issued the
GET, landed on the confirm page, re-read the course and correctly reported *"the delete did not
take"*. It failed safe and it failed honestly, which is why this was cheap to find — but a
writer whose post-check is the only thing telling you it did nothing is a writer that was never
finished. ⚠️ **The same trap as the activity path applies here too: that page's FIRST form is
the course SEARCH box**, so the form is selected by its payload and never by position.

⚠️ **A dry run still issues nothing at all**, even though the GET is now known to be harmless.
Two Moodles can differ, this one has already been misread once, and the cost of keeping the
stricter behaviour is zero.

⚠️ **Deleting a section renumbers every later one**, exactly like an insert. The result carries
`renumbered=True` and the caller owes the course a fresh read before any section-numbered
write.
"""

import re
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence, Tuple

from playwright.sync_api import TimeoutError as PWTimeout, sync_playwright

from musai.automation._log import describe_exception, logger as log
from musai.automation._loop import ensure_subprocess_capable_loop
from musai.automation.moodle_export import _shot
from musai.coursebuild.publish import editing_on, enter_course

SHOT_DIR = Path(__file__).resolve().parent.parent.parent / "screenshots"

#: Module types that hold no user-submitted content, so "is it empty?" is answerable without
#: opening them. ⚠️ They still hold the PROFESSOR's content — `expect_name` is what protects
#: that, not this set.
NO_USER_CONTENT = frozenset({"label", "book", "page", "url", "resource", "folder", "imscp"})

#: Labels on an assign's grading-summary table that count handed-in work, in both languages
#: this Moodle might render. Anything matching and non-zero refuses the delete.
#: ⚠️ Deliberately loose: an English *"Drafts (not submitted)"* also matches `submitted`, so a
#: course with drafts refuses. Refusing is the safe direction to be wrong in.
_SUBMITTED_LABEL = re.compile(r"enviad|entregad|submitted|submission", re.I)

#: The attempt count a quiz's overview report prints above its table, in the two languages this
#: Moodle renders. Measured 2026-08-12 on 9067: `mod/quiz/report.php?id=<cmid>&mode=overview`
#: opens with a line reading exactly ``Intentos: 0``.
#:
#: 🔴 **This is localized, and that is a known weakness accepted on purpose.** The language-free
#: instrument — one ``review.php?attempt=`` link per attempt row, the trick that makes the forum
#: probe robust — cannot stand alone here, because the report **pages**: a quiz with more
#: attempts than one page holds renders only the first page's links, so it can *undercount*, and
#: a probe that undercounts a delete rail is worse than no probe. So the count line is the
#: number and the links are the corroborator, and `parse_quiz_attempts` refuses when they
#: disagree.
_QUIZ_ATTEMPTS_LINE = re.compile(r"^[ \t]*(?:intentos|attempts)[ \t]*:[ \t]*(\d+)[ \t]*$",
                                 re.I | re.M)

_SESSKEY_JS = "() => (window.M && M.cfg && M.cfg.sesskey) || null"

# The activity's own settings form — the authoritative read of name, type and visibility
# (COURSE_EDITING §4: the course page is a view, this is the record).
# `intro` is read too, because a label has no name and its text is the only thing that can
# identify it — and, once it is gone, the only record of what was destroyed.
_MODEDIT_JS = """
() => ({
  name: (document.querySelector('input[name="name"]') || {}).value ?? null,
  modulename: (document.querySelector('input[name="modulename"]') || {}).value ?? null,
  visible: (document.querySelector('select[name="visible"]') || {}).value ?? null,
  intro: (() => {
    const ta = document.querySelector('[name="introeditor[text]"]');
    let h = ta ? ta.value : null;
    if (!h) {
      const ifr = document.querySelector('iframe#id_introeditor_ifr');
      h = ifr && ifr.contentDocument ? ifr.contentDocument.body.innerHTML : null;
    }
    if (!h) {
      const d = document.querySelector('#id_introeditor_editor');
      h = d ? d.innerHTML : '';
    }
    return (h || '').replace(/<[^>]+>/g, ' ').replace(/&nbsp;/g, ' ')
                    .replace(/\\s+/g, ' ').trim();
  })(),
})
"""

# 🔴 Read the delete link off the action menu rather than building it: the href carries the
# sesskey and the `sr` (section return), and Moodle has moved this path between versions. Same
# doctrine as hide/show in `structure.py`.
_FIND_DELETE_LINK_JS = """
(cmid) => {
  const want = new RegExp(`[?&]delete=${cmid}\\\\b`);
  for (const a of document.querySelectorAll('a[href]')) {
    if (want.test(a.getAttribute('href'))) return a.href;
  }
  return null;
}
"""

_MODULE_PRESENT_JS = """
([n, cmid]) => {
  const sec = document.querySelector('#section-' + n);
  const el = document.getElementById('module-' + cmid);
  return {in_section: !!(sec && el && sec.contains(el)),
          on_page: !!el,
          text: el ? (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 90) : null};
}
"""

# An assign's grading summary, as (label, value) pairs. Cells, not innerText: `innerText` on
# the row glues "Enviados" to "0" and a regex over that is one locale away from breaking.
_ASSIGN_SUMMARY_JS = """
() => [...document.querySelectorAll('table.generaltable tr')].map(
  tr => [...tr.querySelectorAll('th, td')].map(c => (c.innerText || '').trim()))
"""

# Non-localized by construction: every discussion is a link to discuss.php?d=<id>.
_FORUM_DISCUSSIONS_JS = """
() => document.querySelectorAll('a[href*="/mod/forum/discuss.php?d="]').length
"""

# A quiz's overview report, read three independent ways so they can be made to agree.
_QUIZ_REPORT_JS = """
() => {
  const main = document.querySelector('#region-main') || document.body;
  return {
    text: ((main.innerText || '').replace(/\\s+\\n/g, '\\n').trim()).slice(0, 1200),
    review_links: document.querySelectorAll('a[href*="/mod/quiz/review.php?attempt="]').length,
    table_rows: document.querySelectorAll('table.generaltable tbody tr').length,
  };
}
"""

_SECTION_JS = """
(n) => {
  const sec = document.querySelector('#section-' + n);
  if (!sec) return {exists: false};
  const link = document.querySelector('a[href*="editsection.php"]');
  const sum = sec.querySelector('.summarytext') || sec.querySelector('[class*="summary"]');
  return {
    exists: true,
    sid: link ? new URL(link.href, location.href).searchParams.get('id') : null,
    name: ((sec.querySelector('h3, .sectionname') || {}).textContent || '').trim(),
    activities: [...sec.querySelectorAll('[id^="module-"]')]
        .filter(m => !m.id.endsWith('_shim'))
        .map(m => m.id.replace('module-', '')),
    summary_text: sum ? (sum.innerText || '').replace(/\\s+/g, ' ').trim() : '',
    delete_href: (() => {
        for (const a of document.querySelectorAll('a[href*="editsection.php"]')) {
          if (/[?&]delete=1\\b/.test(a.getAttribute('href'))) return a.href;
        }
        return null; })(),
  };
}
"""


class DeleteRefused(RuntimeError):
    """A rail failed. **Nothing has been deleted** when this is raised."""


#: 🔴 Measured on 9048, 2026-08-10: a `label`'s `modedit.php` has **no `input[name="name"]` at
#: all** — Moodle derives the name it displays from the intro HTML. So rail 1's equality check
#: is not merely wrong for labels, it is *unsatisfiable*: a label could never be deleted, and
#: a tab holding one could never be removed, because `delete_section` refuses a populated
#: section.
#:
#: The rail is kept by changing what it compares against, never by relaxing it — the caller
#: must quote a distinctive run of the label's own visible text. A substring match is looser
#: than equality by construction, so it is fenced with a length floor: too short a quote is
#: refused as too weak to identify a two-thousand-character label. Precise, never permissive.
LABEL_QUOTE_MIN = 15


def label_identity_matches(intro_text: str, expect_name: str) -> Tuple[bool, str]:
    """`(ok, why)` — does `expect_name` distinctively identify this label?

    Pure, so the rail guarding an irreversible delete is testable without a browser.
    """
    quote = " ".join((expect_name or "").split())
    if len(quote) < LABEL_QUOTE_MIN:
        return False, (
            f"a label is identified by quoting its own text, and {quote!r} is {len(quote)} "
            f"characters — under the {LABEL_QUOTE_MIN}-character floor that keeps a substring "
            "match from being weaker than the equality rail it stands in for")
    hay = " ".join((intro_text or "").split())
    if not hay:
        return False, "the settings form carried no intro text to match against"
    if quote.casefold() not in hay.casefold():
        return False, f"{quote!r} does not appear in this label's text ({hay[:80]!r}…)"
    return True, f"quoted text found in the label's own intro ({len(hay)} chars)"


def parse_submitted_count(rows: Sequence[Sequence[str]]) -> Optional[int]:
    """How many submissions an assign's grading summary reports, or None if it does not say.

    Pure, so the locale handling is testable without a browser. Returns the **largest**
    matching count — with several matching rows the safe answer is the one that refuses.

    Measured shape (1-LED-A, cmid 1060306, 2026-08-09):
    ``[['Participantes', '10'], ['Enviados', '0'], ['Necesita calificarse', '0'], …]``
    """
    best: Optional[int] = None
    for row in rows:
        if len(row) < 2:
            continue
        label, value = row[0], row[-1]
        if not _SUBMITTED_LABEL.search(label or ""):
            continue
        m = re.fullmatch(r"\s*(\d+)\s*", value or "")
        if m:
            n = int(m.group(1))
            best = n if best is None else max(best, n)
    return best


def parse_quiz_attempts(report_text: str, review_links: int,
                        table_rows: int) -> Tuple[Optional[int], str]:
    """`(count, how)` for a quiz, from its overview report. `None` means **unmeasurable**.

    Pure, so the rail guarding an irreversible delete is testable without a browser — the same
    shape as `parse_submitted_count`, and for the same reason.

    🔴 **The honest limitation, written down rather than hidden: the non-zero case has never
    been observed on this Moodle.** Probed 2026-08-12 on 9067 (`probe_quiz_attempts.py`) —
    a doomed duplicate *and* both real exams, which had been sitting open on last semester's
    dates all week — and every one read `Intentos: 0`, 0 review links, 0 rows. The obvious
    place to see a real attempt was **idc 7741, last semester's 3-LMH-A**, and that course no
    longer exists on `virtual3` (*"No se puede encontrar registro de datos en la tabla course"*),
    so the owner's account cannot reach one.

    An instrument only ever seen reading zero is exactly what the module docstring warns about,
    so this does not get to *decide* a zero on its own evidence. It requires **three independent
    readings to agree on zero** — the printed count, the per-attempt links, and the table rows —
    and returns the count itself the moment any of them is non-zero, which refuses. Anything it
    cannot read is `None`, which also refuses. The only outcome it can produce cheaply is the
    one that stops the delete.
    """
    m = _QUIZ_ATTEMPTS_LINE.search(report_text or "")
    if not m:
        return None, ("the overview report printed no 'Intentos: N' / 'Attempts: N' line — "
                      "without the count itself this cannot tell an empty quiz from an "
                      "unreadable page")
    counted = int(m.group(1))
    if counted:
        return counted, "attempt count printed by the overview report"
    # The count line says zero. Believe it only if nothing else on the page contradicts it.
    if review_links or table_rows:
        return None, (f"the report says 0 attempts but renders {review_links} review link(s) "
                      f"and {table_rows} table row(s) — the readings disagree, and a delete "
                      "rail does not get to pick the convenient one")
    return 0, "attempt count 0, corroborated by 0 review links and 0 table rows"


def _shot_path(kind: str, dry_run: bool, label: str) -> Path:
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    return SHOT_DIR / (f"{kind}_{'dryrun' if dry_run else 'live'}_{label}_"
                       f"{datetime.now():%Y%m%d_%H%M%S}.png")


def _audit(action: str, target: str, dry_run: bool, detail: dict, out: dict) -> None:
    """Record the attempt. A failure to record must never be mistaken for a failure to act."""
    try:
        from sqlmodel import Session

        from musai.audit import log as audit_log
        from musai.db import engine

        with Session(engine) as sess:
            audit_log(sess, action, actor="carlos", target=target, dry_run=dry_run,
                      detail=detail)
            sess.commit()
    except Exception as e:                                   # pragma: no cover - env dependent
        out["audit_error"] = describe_exception(e)


def _count_user_content(vpage, host: str, modname: str, cmid: str) -> Tuple[Optional[int], str]:
    """`(count, how)`. `count is None` means **unmeasurable**, which refuses just like non-zero.

    Only the types this project actually deletes are implemented. A new type must be measured
    and added here rather than defaulted to zero — defaulting to zero is how a deleter learns
    to lie.
    """
    if modname in NO_USER_CONTENT:
        return 0, f"{modname} holds no user-submitted content"
    if modname == "assign":
        vpage.goto(f"https://{host}/mod/assign/view.php?id={cmid}",
                   wait_until="domcontentloaded", timeout=60000)
        try:
            vpage.wait_for_load_state("networkidle", timeout=25000)
        except PWTimeout:
            pass
        n = parse_submitted_count(vpage.evaluate(_ASSIGN_SUMMARY_JS))
        if n is None:
            return None, "the grading summary named no submission count"
        return n, "grading summary"
    if modname == "forum":
        vpage.goto(f"https://{host}/mod/forum/view.php?id={cmid}",
                   wait_until="domcontentloaded", timeout=60000)
        try:
            vpage.wait_for_load_state("networkidle", timeout=25000)
        except PWTimeout:
            pass
        return int(vpage.evaluate(_FORUM_DISCUSSIONS_JS)), "discuss.php links"
    if modname == "quiz":
        # ⚠️ NOT `mod/quiz/view.php`. That page shows a student's own attempts, so for a
        # professor it reads empty on a quiz the whole class has sat. The overview REPORT is
        # the teacher-side record, and it is what the *Resultados* link in the quiz's own
        # action menu points at — measured, not assumed.
        vpage.goto(f"https://{host}/mod/quiz/report.php?id={cmid}&mode=overview",
                   wait_until="domcontentloaded", timeout=60000)
        try:
            vpage.wait_for_load_state("networkidle", timeout=25000)
        except PWTimeout:
            pass
        info = vpage.evaluate(_QUIZ_REPORT_JS)
        return parse_quiz_attempts(info["text"], int(info["review_links"]),
                                   int(info["table_rows"]))
    return None, f"no content probe is implemented for {modname!r}"


def delete_activity(*, idc: str, section: int, cmid: str, expect_name: str,
                    dry_run: bool = True, headless: bool = True, allow_visible: bool = False,
                    group_label: str = "", as_user: str | None = None, on_step=None) -> dict:
    """Delete one activity, by cmid, having proved it is the one the caller named.

    `expect_name` is not a convenience — it is the rail. See the module docstring.

    🔴 `as_user` (2026-08-13) deletes inside **another professor's** course. The rails do not
    change and must not: `expect_name`, the visibility check and the user-content count all
    still run, and they matter more here, not less — a wrong `cmid` in a colleague's course is
    a deletion the owner cannot see and did not authorise.
    """
    if not (expect_name or "").strip():
        raise DeleteRefused(
            "expect_name is required: a delete must name the activity it believes it is "
            "destroying, so a wrong cmid fails loudly instead of quietly.")

    out: dict = {"ok": False, "dry_run": dry_run, "idc": idc, "section": section,
                 "cmid": str(cmid), "expect_name": expect_name, "found_name": None,
                 "modname": None, "visible": None, "user_content": None,
                 "content_source": None, "intro_text": None, "screenshot": None, "steps": []}

    def step(msg: str) -> None:
        out["steps"].append(msg)
        log.step(msg)
        if on_step:
            try:
                on_step(msg)
            except Exception:
                pass

    log.header(f"DELETE cmid={cmid} in section {section} (idc={idc}) "
               f"{'[DRY RUN]' if dry_run else '[LIVE — IRREVERSIBLE]'}")
    ensure_subprocess_capable_loop()

    browser = ctx = None
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=headless)
            ctx = browser.new_context(viewport={"width": 1400, "height": 1100})
            vpage, host = enter_course(ctx, ctx.new_page(), idc, as_user=as_user)
            editing_on(vpage, host, idc, section=section)
            step(f"Editing mode on (section {section} displayed)")

            present = vpage.evaluate(_MODULE_PRESENT_JS, [section, str(cmid)])
            if not present["in_section"]:
                _shot(vpage, "delete_not_in_section")
                raise DeleteRefused(
                    f"cmid {cmid} is not inside section {section} "
                    f"(on_page={present['on_page']}). Refusing: a wrong section number after a "
                    "renumbering is exactly how the wrong activity gets deleted.")
            step(f"On the page: {present['text'][:60]!r}")

            # Rail 1 — the settings form must agree about which activity this is.
            vpage.goto(f"https://{host}/course/modedit.php?update={cmid}",
                       wait_until="domcontentloaded", timeout=60000)
            try:
                vpage.wait_for_load_state("networkidle", timeout=25000)
            except PWTimeout:
                pass
            info = vpage.evaluate(_MODEDIT_JS)
            out["found_name"] = info["name"]
            out["modname"] = info["modulename"]
            out["visible"] = info["visible"]
            # The audit row has to outlive the thing it describes. For a label the intro text
            # IS the content, so it is recorded before the delete, not merely matched against.
            out["intro_text"] = (info.get("intro") or "")[:1000] or None
            if (info["modulename"] or "") == "label":
                ok, why = label_identity_matches(info.get("intro") or "", expect_name)
                if not ok:
                    raise DeleteRefused(f"cmid {cmid} is a label and {why}. Refusing.")
                step(f"Confirmed label by its own text — {why}")
            elif (info["name"] or "").strip() != expect_name.strip():
                raise DeleteRefused(
                    f"cmid {cmid} is named {info['name']!r}, not {expect_name!r}. Refusing.")
            else:
                step(f"Confirmed {info['modulename']} {info['name']!r} "
                     f"(visible={info['visible']})")

            # Rail 2 — never delete something students can see right now.
            if str(info["visible"]) == "1" and not allow_visible:
                raise DeleteRefused(
                    f"{info['name']!r} is VISIBLE to students. Hide it, look at the course, "
                    "then delete — or pass allow_visible=True to say that was the intent.")

            # Rail 3 — never delete something that holds work.
            count, how = _count_user_content(vpage, host, info["modulename"] or "", str(cmid))
            out["user_content"], out["content_source"] = count, how
            if count is None:
                raise DeleteRefused(
                    f"Cannot measure whether {info['name']!r} holds student work ({how}). "
                    "An unknown count is not a zero count.")
            if count > 0:
                raise DeleteRefused(
                    f"{info['name']!r} holds {count} item(s) of student work ({how}). "
                    "Refusing — hiding keeps them, deleting does not.")
            step(f"Holds no student work ({how})")

            # 🔴 The href is read, never built: it carries the sesskey and the section return.
            editing_on(vpage, host, idc, section=section)
            url = vpage.evaluate(_FIND_DELETE_LINK_JS, str(cmid))
            if not url:
                _shot(vpage, "delete_no_link")
                raise DeleteRefused(
                    f"No delete={cmid} link in the action menu. Moodle renders it there; this "
                    "code does not build one.")
            step("Found the delete link in the action menu")

            shot = _shot_path("delete", dry_run, f"{group_label or idc}_cm{cmid}")
            vpage.screenshot(path=str(shot), full_page=True)
            out["screenshot"] = str(shot)

            if dry_run:
                step("DRY RUN — every rail passed, link NOT followed, nothing deleted")
                out["ok"] = True
                return out

            # For an ACTIVITY the GET is only the confirm page; the POST is the mutation.
            vpage.goto(url, wait_until="domcontentloaded", timeout=60000)
            form = (f"form:has(input[name='confirm'][value='1'])"
                    f":has(input[name='delete'][value='{cmid}'])")
            # 🔴 Selected by payload, never by position: this page's FIRST form is the course
            # SEARCH box (COURSE_EDITING §6 — the third page in this project with that shape).
            btn = vpage.locator(f"{form} input[type=submit], {form} button[type=submit]").first
            if not btn.count():
                _shot(vpage, "delete_no_confirm_form")
                raise DeleteRefused(
                    "The confirm page carried no form with confirm=1 and this cmid. Refusing to "
                    "submit a form chosen any other way.")
            step("Confirm page loaded; the Sí form matched on its payload")
            btn.click(timeout=20000)
            try:
                vpage.wait_for_load_state("networkidle", timeout=60000)
            except PWTimeout:
                pass

            # Verify from two independent places: gone from the section, and no settings form.
            editing_on(vpage, host, idc, section=section)
            after = vpage.evaluate(_MODULE_PRESENT_JS, [section, str(cmid)])
            vpage.goto(f"https://{host}/course/modedit.php?update={cmid}",
                       wait_until="domcontentloaded", timeout=60000)
            still = vpage.evaluate(_MODEDIT_JS)
            out["verified"] = {"on_course_page": after["on_page"],
                               "settings_form_name": still["name"]}
            if after["on_page"] or still["name"] is not None:
                raise DeleteRefused(
                    f"Submitted the delete, but cmid {cmid} is still there "
                    f"(on_page={after['on_page']}, form_name={still['name']!r}).")
            step("Verified gone: absent from the section AND no settings form")
            out["ok"] = True
            return out
        except Exception as e:
            out["error"] = describe_exception(e)
            step(f"REFUSED/ERROR: {out['error']}")
            log.error(f"Delete failed: {out['error']}")
            return out
        finally:
            _audit("coursebuild_delete_activity",
                   f"idc:{idc} section:{section} cmid:{cmid}", dry_run,
                   {k: v for k, v in out.items() if k != "steps"}, out)
            for closeable in (ctx, browser):
                if closeable is not None:
                    try:
                        closeable.close()
                    except Exception:
                        pass


def delete_section(*, idc: str, section: int, expect_name: str, dry_run: bool = True,
                   headless: bool = True, allow_summary: bool = False,
                   group_label: str = "", as_user: str | None = None, on_step=None) -> dict:
    """Delete one **empty** section (tab).

    🔴 Unlike an activity, the trigger URL *is* the deletion — there is no confirm page to
    inspect and abandon. Every rail below therefore runs first, and a dry run issues nothing.

    ⚠️ Succeeds with `renumbered=True`: every later section has shifted down by one and any
    caller holding section numbers is now holding stale ones.
    """
    if not (expect_name or "").strip():
        raise DeleteRefused("expect_name is required — see delete_activity.")

    out: dict = {"ok": False, "dry_run": dry_run, "idc": idc, "section": section,
                 "expect_name": expect_name, "found_name": None, "section_id": None,
                 "activities": None, "summary_text": None, "renumbered": False,
                 "screenshot": None, "steps": []}

    def step(msg: str) -> None:
        out["steps"].append(msg)
        log.step(msg)
        if on_step:
            try:
                on_step(msg)
            except Exception:
                pass

    log.header(f"DELETE section {section} of idc={idc} "
               f"{'[DRY RUN]' if dry_run else '[LIVE — IRREVERSIBLE]'}")
    ensure_subprocess_capable_loop()

    browser = ctx = None
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=headless)
            ctx = browser.new_context(viewport={"width": 1400, "height": 1100})
            vpage, host = enter_course(ctx, ctx.new_page(), idc, as_user=as_user)
            editing_on(vpage, host, idc, section=section)

            info = vpage.evaluate(_SECTION_JS, section)
            if not info.get("exists"):
                raise DeleteRefused(f"Section {section} does not exist on this course page.")
            out["section_id"] = info["sid"]
            out["found_name"] = info["name"]
            out["activities"] = info["activities"]
            out["summary_text"] = info["summary_text"]
            step(f"§{section} sid={info['sid']} {info['name']!r} "
                 f"({len(info['activities'])} activities)")

            if (info["name"] or "").strip() != expect_name.strip():
                raise DeleteRefused(
                    f"§{section} is named {info['name']!r}, not {expect_name!r}. Refusing — "
                    "section numbers shift under inserts and deletes, so the name is the only "
                    "check that a renumbering has not moved the target.")

            # 🔴 The rail with no escape hatch. Deleting a populated section destroys every
            # activity in it, with every submission and grade behind them.
            if info["activities"]:
                raise DeleteRefused(
                    f"§{section} holds {len(info['activities'])} activities "
                    f"({', '.join(info['activities'][:5])}). This function deletes EMPTY "
                    "sections only. Delete or relocate the activities first.")

            if info["summary_text"] and not allow_summary:
                raise DeleteRefused(
                    f"§{section} has a summary ({info['summary_text'][:70]!r}) — that is the "
                    "professor's tab banner. Pass allow_summary=True to destroy it too.")
            step("Empty: no activities, no summary")

            url = info.get("delete_href")
            if url:
                step("Found Moodle's own delete link")
            else:
                # The one place this project builds a URL instead of reading it: the link lives
                # behind a JS-rendered action menu that may not be in the DOM, and the GET is
                # the mutation, so `sr` must be controlled rather than inherited.
                key = vpage.evaluate(_SESSKEY_JS)
                if not key:
                    raise DeleteRefused("No sesskey on the page; cannot build the delete URL.")
                url = (f"https://{host}/course/editsection.php?id={info['sid']}"
                       f"&sr=0&delete=1&sesskey={key}")
                step("No rendered delete link; built one from the live sesskey")

            shot = _shot_path("delsection", dry_run, f"{group_label or idc}_s{section}")
            vpage.screenshot(path=str(shot), full_page=True)
            out["screenshot"] = str(shot)

            if dry_run:
                step("DRY RUN — every rail passed. 🔴 The URL is NOT opened: for a section the "
                     "GET is the deletion, so there is nothing to fill and abandon.")
                out["ok"] = True
                return out

            before = vpage.evaluate(_SECTION_JS, section)["sid"]
            vpage.goto(url, wait_until="domcontentloaded", timeout=60000)
            try:
                vpage.wait_for_load_state("networkidle", timeout=45000)
            except PWTimeout:
                pass

            # 🔴 The GET is NOT the mutation — see the module docstring's correction. It renders
            # a confirm page, and the POST of its middle form is what deletes. Selected by
            # payload, never by position: the first form on that page is the course search box.
            confirm = (f"form:has(input[name='confirm'][value='1'])"
                       f":has(input[name='delete'][value='1'])"
                       f":has(input[name='id'][value='{info['sid']}'])")
            btn = vpage.locator(f"{confirm} input[type=submit], "
                                f"{confirm} button[type=submit]").first
            if btn.count():
                step("Confirm page loaded; the Eliminar form matched on its payload")
                btn.click(timeout=20000)
                try:
                    vpage.wait_for_load_state("networkidle", timeout=60000)
                except PWTimeout:
                    pass
                step("Confirm POST submitted (this was the mutation)")
            else:
                # Kept, not deleted: a Moodle that really does delete on the GET would land
                # somewhere with no such form, and the verification below is what decides.
                step("No confirm form on the landing page — treating the GET as the mutation "
                     "and letting the verification decide")

            editing_on(vpage, host, idc, section=section)
            after = vpage.evaluate(_SECTION_JS, section)
            out["verified"] = {"sid_before": before, "sid_now": after.get("sid"),
                               "name_now": after.get("name")}
            if after.get("exists") and after.get("sid") == before:
                raise DeleteRefused(
                    f"§{section} still has section_id {before} — the delete did not take.")
            out["renumbered"] = True
            step(f"Verified: sid {before} is gone; §{section} now reads "
                 f"{after.get('name')!r}. 🔴 Every later section has renumbered — re-read the "
                 "course before any further section-numbered write.")
            out["ok"] = True
            return out
        except Exception as e:
            out["error"] = describe_exception(e)
            step(f"REFUSED/ERROR: {out['error']}")
            log.error(f"Section delete failed: {out['error']}")
            return out
        finally:
            _audit("coursebuild_delete_section", f"idc:{idc} section:{section}", dry_run,
                   {k: v for k, v in out.items() if k != "steps"}, out)
            for closeable in (ctx, browser):
                if closeable is not None:
                    try:
                        closeable.close()
                    except Exception:
                        pass


__all__ = ["DeleteRefused", "delete_activity", "delete_section", "parse_submitted_count",
           "parse_quiz_attempts", "label_identity_matches", "LABEL_QUOTE_MIN",
           "NO_USER_CONTENT"]
