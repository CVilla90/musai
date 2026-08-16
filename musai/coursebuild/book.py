"""Write the CHAPTERS of a Moodle `book`. LOCAL RUNNER ONLY.

`activity.py` creates the book itself — it is just another module type on `modedit.php`.
A book's *content*, though, does not live on that form at all: each chapter is a separate
record behind its own form at `mod/book/edit.php`, so publishing an 12-chapter book is one
activity write plus twelve chapter writes. That is why this is its own module.

Field names are not guessed. They were read off the live forms on 2026-08-09
(`scratchpad/probe_book_forms.py` → `probe_book_forms.json`): the chapter form carries
`title`, `content_editor[text]` (the rich editor, id `id_content_editor`), a `subchapter`
checkbox shadowed by a hidden input of the same name, and hidden `cmid` / `id` / `pagenum`.

### The rails

1. **Dry-run by default** (CLAUDE.md rail 2). A dry run fills a chapter form, screenshots it
   and navigates away without submitting; it never opens a URL that writes.
2. 🔴 **This module cannot delete a chapter.** `mod/book/delete.php` is not referenced here
   and must not be. Chapters found in the book but absent from the spec are *reported* as
   `extra_chapters`, never removed — deleting a chapter destroys content a professor may have
   edited by hand. Removal is `coursebuild/remove.py`'s kind of decision, made explicitly.
   🔴 The neighbouring `mod/book/show.php?...&chapterid=` toggles a chapter's visibility **on
   GET**, exactly like `editsection.php?delete=1`. It is not referenced here either.
3. 🔴 **Idempotent by chapter TITLE.** A book has no `musai:block:` marker to walk — the
   chapter body is not rendered on the course page, and Moodle's own identity (`chapterid`) is
   assigned at creation and unknown to the author. The title is the only key the author
   controls and the professor can see. Consequence, stated plainly: **rename a chapter by hand
   in Moodle and a re-run appends a second copy** rather than updating it. That is the same
   limitation `activity.py` documents for assigns, and the same fix applies — pass the ids.
4. **Read back after saving.** A refused save still navigates.
5. 🔴 **The read-back checks the `<iframe>`, not just the title.** MUSAI's own
   `coursebuild/render.lint` has always refused iframes as *"unverified against this
   sanitizer"* — nobody had ever put one through. A Vellum chapter embeds a YouTube player, so
   the first live run of this module is the measurement that settles it. `verify_chapter`
   reports `iframe_in` vs `iframe_out`; a chapter that went in with an embed and came back
   without one is reported as `sanitized`, not as a success.

Nothing here writes a date, a grade or a visibility flag. The book's own `visible` belongs to
`activity.py`; chapter-level visibility belongs to nothing in MUSAI, by rail 2 above.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence

from playwright.sync_api import TimeoutError as PWTimeout, sync_playwright

from musai.automation._log import describe_exception, logger as log
from musai.automation._loop import ensure_subprocess_capable_loop
from musai.automation.moodle_export import _shot
from musai.coursebuild.publish import editing_on, enter_course

SHOT_DIR = Path(__file__).resolve().parent.parent.parent / "screenshots"

#: Every control this module may write on the chapter form. Everything else — `tags[]`,
#: `content_editor[format]`, the hidden `cmid`/`id`/`pagenum` Moodle fills itself — keeps
#: whatever Moodle put there.
ALLOWED_CHAPTER_FIELDS = frozenset({"title", "subchapter"})

#: Hosts an `<iframe>` in chapter HTML may point at. Anything else is refused before a browser
#: exists: a book chapter is student-facing, and an embed is the one construct in this HTML
#: that executes code from a third party.
ALLOWED_IFRAME_HOSTS = (
    "youtube-nocookie.com", "www.youtube-nocookie.com",
    "youtube.com", "www.youtube.com", "youtu.be",
)

_RE_SCRIPT = re.compile(r"<\s*script", re.I)
_RE_STYLE_TAG = re.compile(r"<\s*style", re.I)
_RE_LINK_TAG = re.compile(r"<\s*link", re.I)
_RE_ON_HANDLER = re.compile(r"\son[a-z]+\s*=", re.I)
_RE_JS_URL = re.compile(r"(?:href|src)\s*=\s*['\"]\s*javascript:", re.I)

# 🔴 An attribute can only exist between `<` and `>`. Run the handler check over the whole
# document and it reads the PROSE too, where ordinary English trips it constantly:
#
#     "Near + one = this"   ->  matches ` one =`
#     "if only x = y"       ->  matches ` only =`
#
# That is not hypothetical: on 2026-08-10 `plan_chapters` refused the entire First Term book
# over the sentence *Near + one = this* in chapter 11. Searching tag interiors only loses no
# coverage whatsoever, and a rail that cries wolf gets routed around, which is worse than a
# rail that is merely absent. Same fix, same day, in `Vellum/vellum/moodle_safe.py`.
_RE_TAG = re.compile(r"<[a-zA-Z/!][^>]*>", re.S)


def tags_only(html: str) -> str:
    """Just the markup: every `<...>`, joined. Text nodes are dropped."""
    return "\n".join(_RE_TAG.findall(html or ""))
_RE_IFRAME = re.compile(r"<\s*iframe\b[^>]*>", re.I)
_RE_IFRAME_SRC = re.compile(r"<\s*iframe\b[^>]*?\ssrc\s*=\s*['\"]([^'\"]+)['\"]", re.I)


class BookRefused(RuntimeError):
    """A precondition failed. Nothing has been written when this is raised."""


@dataclass
class ChapterSpec:
    """One chapter. `content_html` is a complete Moodle-safe fragment (Vellum's output)."""

    title: str
    content_html: str
    subchapter: bool = False


def _host_of(url: str) -> str:
    return re.sub(r"^(?:https?:)?//", "", url.strip()).split("/", 1)[0].lower()


def lint_chapter_html(html: str) -> list[str]:
    """Problems that would get stripped, execute, or leak. Empty list = good.

    Deliberately *not* `coursebuild/render.lint`. That one refuses `<iframe>` outright and
    refuses `class="`, because it guards MUSAI's own generated banners, where neither is ever
    wanted. A Vellum chapter is a different kind of artifact: it is allowed exactly one
    external construct, a YouTube embed, and it is checked by **host** rather than banned.
    """
    problems: list[str] = []
    if not (html or "").strip():
        problems.append("chapter content is empty")
    if _RE_SCRIPT.search(html or ""):
        problems.append("contains <script>")
    if _RE_STYLE_TAG.search(html or ""):
        problems.append("contains <style> (this Moodle only keeps inline style=)")
    if _RE_LINK_TAG.search(html or ""):
        problems.append("contains <link> (no external stylesheets)")
    markup = tags_only(html or "")
    if _RE_ON_HANDLER.search(markup):
        problems.append("contains an inline event handler (onclick=...)")
    if _RE_JS_URL.search(markup):
        problems.append("contains a javascript: URL")

    embeds = _RE_IFRAME.findall(html or "")
    srcs = _RE_IFRAME_SRC.findall(html or "")
    if len(srcs) != len(embeds):
        problems.append(f"{len(embeds) - len(srcs)} <iframe> without a src attribute")
    for src in srcs:
        host = _host_of(src)
        if not any(host == h or host.endswith("." + h) for h in ALLOWED_IFRAME_HOSTS):
            problems.append(f"<iframe> to a non-allowed host: {host}")
    return problems


def plan_chapters(chapters: Sequence[ChapterSpec]) -> list[dict]:
    """Validate the whole book before a browser exists. Pure — fully testable.

    Refusing here costs a millisecond. Refusing on chapter 9 of 12 costs a login, eight saved
    chapters and a book that is half one thing and half another.
    """
    if not chapters:
        raise BookRefused("A book needs at least one chapter.")

    seen: dict[str, int] = {}
    planned: list[dict] = []
    for i, ch in enumerate(chapters):
        title = (ch.title or "").strip()
        if not title:
            raise BookRefused(f"Chapter {i} has no title; Moodle rejects a blank one.")
        # 🔴 Rail 3 is *title-keyed*, so two chapters sharing a title would make the second
        # one overwrite the first and the book would silently lose a chapter. Same shape as
        # the AMBIGUOUS activity-name problem in COURSE_EDITING §5.
        if title in seen:
            raise BookRefused(
                f"Chapters {seen[title]} and {i} are both titled {title!r}. Chapter identity "
                "here is the title, so a duplicate title means a re-run cannot tell them apart."
            )
        seen[title] = i

        problems = lint_chapter_html(ch.content_html)
        if problems:
            raise BookRefused(f"Chapter {i} ({title!r}) failed the lint: {'; '.join(problems)}")

        planned.append({
            "index": i,
            "title": title,
            "subchapter": bool(ch.subchapter),
            "html": ch.content_html,
            "bytes": len(ch.content_html),
            "iframes": len(_RE_IFRAME.findall(ch.content_html)),
        })
    return planned


# --------------------------------------------------------------------------- page scripting
# The book's table of contents, as {chapterid, title}. This is what makes a re-run update
# chapter "3 - Possessives" instead of appending a second one. Read from the rendered book,
# not from the course page: a book's chapters are not part of the course DOM at all.
#
# 🔴 **The chapter being displayed is not a link.** Moodle renders it `<strong>How much/many
# </strong>` while every *other* chapter is an `<a href="...&chapterid=N">`. Collecting
# anchors therefore silently drops exactly one chapter — the first one, on a bare
# `view.php?id=<cmid>` — which is why the first live run reported "saved ... but it is not in
# the table of contents" about two chapters that had both saved perfectly.
# Its id is still on the page: every self-referencing link (the language switcher, *Activar
# edición*) points at `view.php?id=<cmid>&chapterid=<current>`. So read the `<li>` list for
# order and titles, and take the current chapter's id from a link OUTSIDE the TOC block.
#
# Titles prefer the anchor's `title` attribute, which is the raw chapter name; the visible
# text carries Moodle's own "1. " when the book is numbered. `activity._book_fields` sets
# numbering to none, but a book made by hand will not have.
_READ_TOC_JS = """
() => {
  const toc = document.querySelector('.block_book_toc, [class*="book_toc"]');
  if (!toc) return {url: location.href, chapters: [], toc_found: false};

  // 🔴 `view.php` is not decoration in this selector. With editing on, each <li> also
  // carries action icons, and `delete.php?...&chapterid=` and `show.php?...&chapterid=`
  // both match a bare `chapterid=` search. Reading an id out of a delete URL is one
  // careless refactor away from following it.
  const CHAP = 'a[href*="view.php"][href*="chapterid="]';
  const idOf = (a) => {
    const m = (a.getAttribute('href') || '').match(/chapterid=(\\d+)/);
    return m ? m[1] : null;
  };

  let currentId = null;
  for (const a of document.querySelectorAll(CHAP)) {
    if (toc.contains(a)) continue;                 // that is some OTHER chapter
    currentId = idOf(a);
    if (currentId) break;
  }

  const strip = (s) => (s || '').trim().split('\\n')[0].trim().replace(/^\\d+(\\.\\d+)*\\.\\s*/, '');
  const chapters = [];
  for (const li of toc.querySelectorAll('li')) {
    const a = li.querySelector(CHAP);
    if (a) {
      chapters.push({chapterid: idOf(a),
                     title: (a.getAttribute('title') || '').trim() || strip(a.innerText),
                     current: false});
    } else {
      // 🔴 Read the <strong>, never the <li>. In editing mode the <li> also contains the
      // action icons, so `li.innerText` came back as
      // 'Mover abajo capítulo"Start here"' — which matched no spec title, so the chapter
      // was created a SECOND time instead of being updated.
      const strong = li.querySelector('strong');
      chapters.push({chapterid: currentId,
                     title: strip(strong ? strong.innerText : li.innerText),
                     current: true});
    }
  }
  return {url: location.href, chapters: chapters, toc_found: true, current: currentId};
}
"""

# Put HTML into the chapter's rich editor. Same shape as publish._set_editor but for
# `id_content_editor`; kept separate because the two ids are the only difference and a
# parameter on a shared helper would have to be threaded through publish.py's callers.
_SET_CHAPTER_EDITOR_JS = """
(html) => {
  try {
    if (window.tinyMCE && tinyMCE.get('id_content_editor')) {
      tinyMCE.get('id_content_editor').setContent(html);
      const ta = document.querySelector('textarea[name="content_editor[text]"]');
      if (ta) ta.value = html;          // keep the POST body in sync
      return 'tinymce';
    }
    const ta = document.querySelector('textarea[name="content_editor[text]"]');
    if (ta) { ta.value = html; return 'textarea'; }
    return 'no-editor';
  } catch (e) { return 'err:' + e.message; }
}
"""

# Same discipline as activity._SET_FIELD_JS: skip the hidden twin, click a checkbox instead of
# assigning to it, and read the value straight back.
_SET_FIELD_JS = """
([name, value]) => {
  const els = [...document.querySelectorAll(`[name="${CSS.escape(name)}"]`)]
      .filter(e => e.type !== 'hidden');
  const el = els[els.length - 1];      // the mform's control, not another form's
  if (!el) return {ok: false, why: 'not-found'};
  if (el.disabled) return {ok: false, why: 'disabled'};
  if (el.type === 'checkbox') {
    const want = (value === true || value === 'true' || value === '1');
    if (el.checked !== want) el.click();
    return {ok: el.checked === want, got: el.checked ? '1' : '0'};
  }
  el.value = String(value);
  el.dispatchEvent(new Event('input', {bubbles: true}));
  return {ok: el.value === String(value), got: el.value};
}
"""

# A refused save stays on edit.php and renders its errors inline.
_SAVE_REFUSED_JS = """
() => {
  if (!location.pathname.includes('/mod/book/edit.php')) return null;
  const errs = [...document.querySelectorAll('.error, .invalid-feedback, [id$="_error"]')]
      .map(e => e.innerText.trim()).filter(Boolean);
  return {url: location.href, errors: errs.slice(0, 6)};
}
"""


def _read_toc(vpage, host: str, cmid: str) -> list[dict]:
    """The book's chapters as [{chapterid, title}]. Empty for a book with no chapters yet."""
    vpage.goto(f"https://{host}/mod/book/view.php?id={cmid}",
               wait_until="domcontentloaded", timeout=60000)
    try:
        vpage.wait_for_load_state("networkidle", timeout=20000)
    except PWTimeout:
        pass
    # An empty book bounces straight to the "add the first chapter" form, which has no TOC.
    if "/mod/book/edit.php" in vpage.url:
        return []
    toc = vpage.evaluate(_READ_TOC_JS)
    # A chapter whose id could not be resolved is worse than one that is missing: it would be
    # treated as "already there" and then never updated. Drop it and say so.
    return [c for c in toc["chapters"] if c.get("chapterid")]


def verify_chapter(vpage, host: str, cmid: str, chapterid: str, sent_html: str) -> dict:
    """Re-open a saved chapter's own form and compare what came back with what went in.

    The authoritative reading of a chapter is its edit form, for the same reason
    COURSE_EDITING gives for `modedit.php`: the rendered page is a theme's opinion, the form
    is the stored value.
    """
    vpage.goto(f"https://{host}/mod/book/edit.php?cmid={cmid}&id={chapterid}",
               wait_until="domcontentloaded", timeout=60000)
    try:
        vpage.wait_for_load_state("networkidle", timeout=20000)
    except PWTimeout:
        pass
    got = vpage.evaluate(
        """() => {
            const t = document.querySelector('input[name="title"]');
            const c = document.querySelector('textarea[name="content_editor[text]"]');
            return {title: t ? t.value : null, html: c ? c.value : null};
        }"""
    )
    stored = got.get("html") or ""
    sent_iframes = len(_RE_IFRAME.findall(sent_html))
    got_iframes = len(_RE_IFRAME.findall(stored))
    return {
        "title": got.get("title"),
        "bytes_sent": len(sent_html),
        "bytes_stored": len(stored),
        "iframe_in": sent_iframes,
        "iframe_out": got_iframes,
        # 🔴 rail 5: an embed that went in and did not come back is the sanitizer eating it,
        # and it must not read as a clean save.
        "sanitized": sent_iframes > 0 and got_iframes == 0,
        "style_attrs_stored": stored.count("style="),
    }


def upsert_chapters(
    *,
    idc: str,
    cmid: str,
    chapters: Sequence[ChapterSpec],
    dry_run: bool = True,
    headless: bool = True,
    group_label: str = "",
    as_user: str | None = None,
    on_step=None,
) -> dict:
    """Create or update every chapter of book `cmid`, in order. Never deletes (rail 2)."""

    planned = plan_chapters(chapters)        # refuse before a browser exists

    out: dict = {"ok": False, "dry_run": dry_run, "idc": idc, "cmid": cmid,
                 "chapters": [], "extra_chapters": [], "screenshot": None, "steps": []}

    def step(msg: str) -> None:
        out["steps"].append(msg)
        log.step(msg)
        if on_step:
            try:
                on_step(msg)
            except Exception:
                pass

    log.header(f"Book chapters -> idc={idc} cmid={cmid} ({len(planned)} chapters) "
               f"{group_label} {'[DRY RUN]' if dry_run else '[LIVE]'}")

    if ensure_subprocess_capable_loop():
        log.info("Restored the Proactor event-loop policy so the browser can start.")

    browser = ctx = page = None
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=headless)
            ctx = browser.new_context(viewport={"width": 1400, "height": 1200})
            page = ctx.new_page()
            vpage, host = enter_course(ctx, page, idc, as_user=as_user)
            editing_on(vpage, host, idc)
            step("Editing mode on")

            existing = _read_toc(vpage, host, cmid)
            by_title = {c["title"]: c["chapterid"] for c in existing}
            step(f"Book has {len(existing)} chapter(s) already: "
                 + (", ".join(c['title'] for c in existing) or "(none)"))

            wanted_titles = {c["title"] for c in planned}
            out["extra_chapters"] = [c for c in existing if c["title"] not in wanted_titles]
            if out["extra_chapters"]:
                step(f"NOT TOUCHING {len(out['extra_chapters'])} chapter(s) not in the spec: "
                     + ", ".join(c["title"] for c in out["extra_chapters"]))

            for item in planned:
                title, html = item["title"], item["html"]
                chapterid = by_title.get(title)
                mode = "update" if chapterid else "create"
                rec = {"title": title, "mode": mode, "chapterid": chapterid,
                       "bytes": item["bytes"], "iframes": item["iframes"], "ok": False}
                out["chapters"].append(rec)

                if chapterid:
                    url = f"https://{host}/mod/book/edit.php?cmid={cmid}&id={chapterid}"
                else:
                    # `pagenum` = insert after this page. Appending in spec order keeps the
                    # book's order identical to the manifest's without ever moving anything.
                    after = len(by_title)
                    url = f"https://{host}/mod/book/edit.php?cmid={cmid}&pagenum={after}"

                vpage.goto(url, wait_until="domcontentloaded", timeout=60000)
                try:
                    vpage.wait_for_load_state("networkidle", timeout=25000)
                except PWTimeout:
                    pass
                if not vpage.locator("#id_submitbutton").count():
                    _shot(vpage, "book_chapter_no_form")
                    raise RuntimeError(f"The chapter form did not load for {title!r}.")

                how = vpage.evaluate(_SET_CHAPTER_EDITOR_JS, html)
                if how.startswith("err") or how == "no-editor":
                    _shot(vpage, "book_chapter_no_editor")
                    raise RuntimeError(f"Could not set the content of {title!r} ({how}).")

                # 🔴 `subchapter` is written ONLY when it is wanted. Moodle omits the
                # checkbox entirely on the **first** chapter of a book — a book cannot open
                # with a sub-chapter, so the control does not exist and only its hidden twin
                # (value "0") is posted. Writing the default would therefore fail on chapter
                # one of every book, which is exactly what the first dry run reported.
                writes = [("title", title)]
                if item["subchapter"]:
                    writes.append(("subchapter", True))

                refused = []
                for name, value in writes:
                    if name not in ALLOWED_CHAPTER_FIELDS:
                        raise BookRefused(f"{name!r} is not in ALLOWED_CHAPTER_FIELDS.")
                    res = vpage.evaluate(_SET_FIELD_JS, [name, value])
                    if not res.get("ok"):
                        why = res.get("why") or res.get("got")
                        if name == "subchapter" and why == "not-found":
                            why = ("not-found — Moodle hides this control on a book's first "
                                   "chapter; a sub-chapter cannot be chapter one")
                        refused.append(f"{name} ({why})")
                if refused:
                    _shot(vpage, "book_chapter_field_refused")
                    raise RuntimeError(f"{title!r}: the form would not take " +
                                       "; ".join(refused))

                if dry_run:
                    if out["screenshot"] is None:
                        shot = SHOT_DIR / (f"book_dryrun_{group_label or idc}_"
                                           f"{datetime.now():%Y%m%d_%H%M%S}.png")
                        shot.parent.mkdir(parents=True, exist_ok=True)
                        vpage.screenshot(path=str(shot), full_page=True)
                        out["screenshot"] = str(shot)
                    rec["ok"] = True
                    step(f"DRY RUN {mode:6} {title!r} — {item['bytes']} bytes, "
                         f"{item['iframes']} iframe(s), editor via {how}, NOT saved")
                    continue

                vpage.locator("#id_submitbutton").first.click(timeout=15000)
                try:
                    vpage.wait_for_load_state("networkidle", timeout=90000)
                except PWTimeout:
                    pass

                still = vpage.evaluate(_SAVE_REFUSED_JS)
                if still is not None:
                    _shot(vpage, "book_chapter_save_refused")
                    raise RuntimeError(
                        f"Moodle refused to save {title!r} (still on edit.php): "
                        + ("; ".join(still["errors"]) or "no message shown"))

                # Re-read the TOC so a freshly created chapter gets its real id, and so the
                # next create appends after this one.
                existing = _read_toc(vpage, host, cmid)
                by_title = {c["title"]: c["chapterid"] for c in existing}
                rec["chapterid"] = by_title.get(title)
                if not rec["chapterid"]:
                    step(f"WARNING: saved {title!r} but it is not in the table of contents")
                    continue

                rec["verify"] = verify_chapter(vpage, host, cmid, rec["chapterid"], html)
                rec["ok"] = not rec["verify"]["sanitized"]
                step(f"{mode:6} {title!r} -> chapter {rec['chapterid']} "
                     f"({rec['verify']['bytes_stored']} bytes stored, "
                     f"iframe {rec['verify']['iframe_in']}->{rec['verify']['iframe_out']}, "
                     f"{rec['verify']['style_attrs_stored']} style attrs)"
                     + ("  ⚠️ SANITIZED" if rec["verify"]["sanitized"] else ""))

            out["ok"] = all(c["ok"] for c in out["chapters"])
            out["book_url"] = f"https://{host}/mod/book/view.php?id={cmid}"
            return out
        except Exception as e:
            out["error"] = describe_exception(e)
            step(f"ERROR: {out['error']}")
            try:
                if page is not None:
                    _shot(page, "book_error")
            except Exception:
                pass
            log.error(f"Book chapters failed: {out['error']}")
            return out
        finally:
            for closeable in (ctx, browser):
                if closeable is not None:
                    try:
                        closeable.close()
                    except Exception:
                        pass


def load_vellum_book(build_dir: Path, manifest: dict) -> list[ChapterSpec]:
    """Turn a built Vellum book into ChapterSpecs, titles taken from `navTitle`.

    `build_dir` is `Vellum/build/<book>/chapters/`; `manifest` is the parsed `book.json`
    plus a `titles` list in chapter order. Kept here rather than in Vellum so Vellum stays
    delivery-agnostic — it renders HTML and knows nothing about Moodle.
    """
    files = sorted(Path(build_dir).glob("*.html"))
    titles = manifest.get("titles") or []
    if len(titles) != len(files):
        raise BookRefused(
            f"{len(files)} chapter file(s) in {build_dir} but {len(titles)} title(s) in the "
            "manifest — refusing to guess which title belongs to which file.")
    return [ChapterSpec(title=t, content_html=f.read_text(encoding="utf-8"))
            for t, f in zip(titles, files)]
