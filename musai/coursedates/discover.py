"""Read a course's shape: its tabs, and every activity in them. READ-ONLY.

Separate from `apply.py` on purpose — this never writes, so it is safe to run against any
course at any time, including a colleague's once that question is settled.

The tab list is read from the course's own tab strip rather than by counting upward from
section 0, because sections are **not contiguous**: 1-LED-A has an empty §12 with a populated
§13 behind it. Scanning until the first empty section is how the restore's verification came
to report "ZERO activities" about a restore that had just placed 80.

`format_onetopic` renders exactly one tab at a time, so each tab costs one page load. That is
the whole reason the plan is built from a saved snapshot: 14 loads once, not 14 per question.
"""

from typing import Callable, Dict, List, Optional

from playwright.sync_api import sync_playwright

from musai.automation._log import logger as log
from musai.automation._loop import ensure_subprocess_capable_loop
from musai.coursebuild.publish import enter_course

_TABS_JS = """
() => {
  const seen = new Map();
  document.querySelectorAll('.nav-tabs a, .onetopic-tab-body a, a[href*="course/view.php"]')
    .forEach(a => {
      const m = (a.getAttribute('href') || '').match(/[?&]section=(\\d+)/);
      if (!m) return;
      const txt = (a.textContent || '').replace(/\\s+/g, ' ').trim();
      if (!txt || txt.length > 90) return;
      const n = parseInt(m[1], 10);
      if (!seen.has(n)) seen.set(n, {section: n, label: txt,
                                     hidden: /dimmed|hidden/.test(a.className || '')});
    });
  return Array.from(seen.values()).sort((a, b) => a.section - b.section);
}
"""

_SECTION_JS = """
(n) => {
  const sec = document.querySelector('#section-' + n);
  const acts = [];
  document.querySelectorAll('[id^="module-"]').forEach(el => {
    const cls = el.className || '';
    const m = cls.match(/modtype_([a-z]+)/);
    const nameEl = el.querySelector('.instancename') || el.querySelector('.activityname a')
                || el.querySelector('a.aalink');
    // 🔴 `.instancename` contains a screen-reader-only <span> naming the ACTIVITY TYPE, so
    // its raw textContent is "Alphabet Examen", "Watch and Write Foro", "First Term Libro".
    // Reading it whole appended a Spanish type word to all 80 names, which made every one
    // of them fail to match the gradebook column it came from. Clone and strip, rather than
    // trimming known suffixes — the words are localized and the list is not knowable.
    let name = '';
    if (nameEl) {
      const clone = nameEl.cloneNode(true);
      clone.querySelectorAll('.accesshide, .visually-hidden, .sr-only').forEach(x => x.remove());
      name = clone.textContent.replace(/\\s+/g, ' ').trim();
    }
    // 🔴 An ACTIVITY's hidden state is NOT on its className on this Moodle — measured
    // 2026-08-09 (`scratchpad/probe_cleanup_9023.py`): seven activities whose settings forms
    // all read `visible=0` came back with no `hidden` class, so this reader reported every
    // one of them as SHOWN. (A SECTION does carry the class — §15 "Other resources" reads
    // correctly — which is exactly why the bug survived: half the flag worked.)
    // The state is on the page as TEXT, in the badge Moodle renders for a teacher.
    // Both signals are kept and `hidden_by` names which fired, so the next person to doubt
    // this can see the evidence instead of re-measuring it.
    const byClass = /(^|\\s)hidden(\\s|$)/.test(cls);
    const byBadge = /Oculto para los estudiantes|Hidden from students/i
                      .test(el.innerText || '');
    acts.push({
      cmid: el.id.replace('module-', ''),
      modname: m ? m[1] : null,
      name: name,
      hidden: byClass || byBadge,
      hidden_by: byClass ? (byBadge ? 'class+badge' : 'class')
                         : (byBadge ? 'badge' : null),
    });
  });
  const title = sec ? sec.querySelector('h3, .sectionname') : null;
  return {name: title ? title.textContent.trim() : '',
          hidden: sec ? /(^|\\s)hidden(\\s|$)/.test(sec.className || '') : false,
          activities: acts};
}
"""


def read_course_structure(
    idc: str,
    *,
    headless: bool = True,
    as_user: Optional[str] = None,
    identity=None,
    on_step: Optional[Callable[[str], None]] = None,
) -> Dict:
    """Return `{"idc", "tabs", "sections"}` — the input `build_plan` expects.

    🔴 `as_user` (2026-08-12) reads a course belonging to ANOTHER PROFESSOR, via
    `credentials.resolve`, which refuses rather than falling back to the owner's login. Added for
    English III's propagation: after restoring the master into Colleague A's, Colleague B's and Colleague C's
    groups, the dates that came with it have to be *verified* in each course, and the owner's
    account cannot open their activities at all.

    🔴 `identity` (2026-08-14) is the **cockpit's** road: a `MoodleIdentity` already resolved
    from the signed-in professor's own vault entry. It is not interchangeable with `as_user`,
    which resolves a *delegate* password out of `.env` — passing a web professor's username as
    `as_user` looks right and raises `CredentialsMissing` for someone whose password is stored.
    See `coursebuild.publish.enter_course`.

    ⚠️ **Reading is all this enables.** `musai.coursedates.__main__` still has no `--as-user`,
    so no date can be *written* to a colleague's course from the CLI — deliberately: a wrong
    date in someone else's course is a wrong grade in someone else's gradebook.
    """

    def step(msg: str) -> None:
        log.step(msg)
        if on_step:
            on_step(msg)

    ensure_subprocess_capable_loop()
    # The cleanup lives INSIDE `with sync_playwright()`: an outer `finally` runs after
    # playwright has already stopped, and `ctx.close()` then raises "Event loop is closed"
    # over the top of whatever really happened.
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context(viewport={"width": 1400, "height": 1000})
        try:
            page = ctx.new_page()
            vpage, host = enter_course(ctx, page, idc, as_user=as_user, identity=identity)

            tabs: List[Dict] = vpage.evaluate(_TABS_JS)
            # A tab strip that names sections the course does not have (the language menu and
            # the editing toggle both carry `section=0`) is filtered by asking each one.
            step(f"{len(tabs)} pestañas encontradas.")

            sections: List[Dict] = []
            for t in tabs:
                n = t["section"]
                vpage.goto(f"https://{host}/course/view.php?id={idc}&section={n}",
                           wait_until="domcontentloaded", timeout=60000)
                vpage.wait_for_load_state("networkidle", timeout=25000)
                data = vpage.evaluate(_SECTION_JS, n)
                # 🔴 The section HEADING wins over the tab strip's text. The strip also carries
                # the editing toggle and the language menu, which both link to `section=0` —
                # trusting it would name section 0 "Activar edición" and hand the tab map a
                # control label to classify.
                sections.append({"section": n, "name": data["name"] or t["label"],
                                 "hidden": t.get("hidden") or data["hidden"],
                                 "activities": data["activities"]})
                step(f"§{n} {t['label'][:34]} — {len(data['activities'])} actividades")

            return {"idc": idc, "tabs": tabs, "sections": sections}
        finally:
            ctx.close()
            browser.close()
