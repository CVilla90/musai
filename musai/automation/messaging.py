"""Send one message to a group's participants. LOCAL RUNNER ONLY.

**Every other MUSAI write is a course page. This one reaches students, and it cannot be
unsent.** A wrong label is embarrassing for an hour; a wrong message is in 32 phones forever.
That asymmetry is why this module is mostly refusals.

## The path, as MEASURED (see `MESSAGING_HUB.md`, probes of 2026-08-07)

    1. GET  /user/index.php?id=<idc>&perpage=200        the roster + its checkboxes
    2. tick input.usercheckbox[name="user<uid>"]         enumerated ids, one per recipient
    3. select #formactionid → "messageselect.php"        auto-submits to action_redir.php
    4. tinyMCE.get('edit-messagebody').setContent(body)
    5. click input[name="preview"]                       ◀ A DRY RUN STOPS HERE
    6. click input[name="send"]                          the only step that delivers

Three things the spec assumed and the probe disproved, each of which would have shipped a bug:

* `GET /user/messageselect.php` returns **200 with no form on it** — the recipient list lives
  in the server session, so there is no "POST the ids directly" shortcut.
* `perpage=5000` renders 5000 empty `<tr>` — **counting rows counts the padding.** Count
  checkboxes.
* The compose page shows a **count**, never the names. So the recipient list a professor
  approves has to be printed by MUSAI from the roster it read, not screenshotted from Moodle.

## The rails

1. **Dry run is the default** and reaches Moodle's own *Vista previa*, never `send`.
2. **Recipients are an explicit enumerated list**, never `#checkallonpage` — whose id says
   *on page*, and which on a 32-student group ticks 20.
3. **The count is cross-checked against MUSAI's own enrolment** before anything is clicked,
   and again against Moodle's own heading. Any mismatch refuses.
4. **`only_me` sends through the real path to exactly one recipient, the owner.** That is the
   intended first live test, so it is built in rather than improvised.
5. **Self is excluded by default**, identified by the logged-in user id — discovered from the
   user menu, never hardcoded.
6. **One fresh browser context per batch.** Moodle keeps the recipient set in the session and
   says *"Agregado NUEVO receptor"*; whether a second dispatch adds to it or replaces it was
   never measured, because measuring it means putting a student in a send queue. A fresh
   context makes the question moot instead of answering it.
"""

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

from musai.automation._log import describe_exception, logger as log
from musai.automation._loop import ensure_subprocess_capable_loop
from musai.automation.credentials import resolve as resolve_identity
from musai.config import settings
from musai.coursebuild.publish import enter_course

SHOT_DIR = Path(__file__).resolve().parent.parent.parent / "screenshots"

PURPOSES = ("bienvenida", "seguimiento", "cierre", "aviso")

# A course email is `a<matricula>@uach.mx`. The owner's own is `professor@uach.mx` — no digits —
# which is also how "exclude self" recognises a teacher without a hardcoded id.
_STUDENT_EMAIL = re.compile(r"^a(\d{4,})@", re.I)

# Read every participant row: the checkbox that selects them, their Moodle user id, name and
# email. Rows are read from the checkbox outward, because Moodle pads the table to `perpage`
# with empty <tr> and a row-first scan counts the padding as people.
_ROSTER_JS = """
() => {
  const rows = [];
  document.querySelectorAll('input.usercheckbox').forEach(box => {
    const tr = box.closest('tr');
    if (!tr) return;
    const link = tr.querySelector('a[href*="/user/view.php"], a[href*="/user/profile.php"]');
    const m = link ? (link.getAttribute('href')||'').match(/[?&]id=(\\d+)/) : null;
    const cells = Array.from(tr.querySelectorAll('td')).map(
      td => (td.textContent||'').replace(/\\s+/g,' ').trim());
    const email = cells.find(c => c.indexOf('@') > 0) || '';
    const name = cells.find(c => c && c.indexOf('@') < 0 && c.length > 3) || '';
    rows.push({
      checkbox: box.name,
      user_id: m ? m[1] : (box.name || '').replace(/^user/, ''),
      name: name, email: email,
    });
  });
  const me = document.querySelector('.usermenu a[href*="/user/profile.php"]');
  const mm = me ? (me.getAttribute('href')||'').match(/[?&]id=(\\d+)/) : null;
  return {rows: rows, me: mm ? mm[1] : null};
}
"""

# The ONLY machine-readable confirmation of how many people Moodle thinks it will message.
# Parse the trailing integer, never the phrase: it is localized and inflects for plurals.
_HEADING_JS = """
() => {
  const h = document.querySelector('#region-main h2, [role=main] h2');
  const t = h ? h.textContent.replace(/\\s+/g,' ').trim() : null;
  const m = t ? t.match(/(\\d+)\\s*$/) : null;
  return {text: t, count: m ? parseInt(m[1], 10) : null};
}
"""

_FILL_JS = """
(body) => {
  if (window.tinyMCE && tinyMCE.get('edit-messagebody')) {
    tinyMCE.get('edit-messagebody').setContent(body);
    tinyMCE.get('edit-messagebody').save();
    return 'tinymce';
  }
  const ta = document.querySelector('textarea[name=messagebody]');
  if (ta) { ta.value = body; return 'textarea'; }
  return 'none';
}
"""

_TICKED_JS = """
() => Array.from(document.querySelectorAll('input.usercheckbox'))
        .filter(i => i.checked).map(i => i.name)
"""


class MessagingRefused(RuntimeError):
    """A rail said no. Never retried automatically — a refusal is information."""


@dataclass
class Recipient:
    moodle_user_id: str
    checkbox: str
    full_name: str = ""
    email: str = ""
    matricula: Optional[str] = None
    included: bool = True
    excluded_reason: Optional[str] = None


@dataclass
class Roster:
    recipients: List[Recipient] = field(default_factory=list)
    me: Optional[str] = None

    @property
    def included(self) -> List[Recipient]:
        return [r for r in self.recipients if r.included]

    @property
    def excluded(self) -> List[Recipient]:
        return [r for r in self.recipients if not r.included]


def body_hash(body: str) -> str:
    """Identity of a message for the idempotency guard — whitespace-insensitive."""
    return hashlib.sha256(re.sub(r"\s+", " ", body or "").strip().encode()).hexdigest()[:32]


def to_html(body: str) -> str:
    """The compose form is `format=1` (HTML), so a plain-text body would collapse.

    The owner writes plain text with emoji and likes it that way (`MESSAGING_HUB.md`), so the
    conversion happens here rather than by asking him to type markup. Escaping first means a
    student's message can never carry markup he did not intend.
    """
    esc = (body or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    paras = [p.strip() for p in re.split(r"\n\s*\n", esc) if p.strip()]
    return "".join(f"<p>{p.replace(chr(10), '<br>')}</p>" for p in paras) or "<p></p>"


def build_roster(raw: Dict, *, expected_matriculas: Sequence[str],
                 exclude_self: bool = True, only_me: bool = False) -> Roster:
    """Turn the page's rows into a decided recipient list, with reasons for every exclusion.

    `expected_matriculas` is MUSAI's own enrolment. It is what makes this trustworthy: the
    roster page carries **no role column**, so nothing on it distinguishes a student from a
    teacher. Matching against the enrolment does.
    """
    me = raw.get("me")
    roster = Roster(me=me)

    for row in raw.get("rows", []):
        uid = str(row.get("user_id") or "")
        m = _STUDENT_EMAIL.match(row.get("email") or "")
        rec = Recipient(moodle_user_id=uid, checkbox=row.get("checkbox") or f"user{uid}",
                        full_name=row.get("name") or "", email=row.get("email") or "",
                        matricula=m.group(1) if m else None)

        if only_me:
            rec.included = bool(me and uid == me)
            if not rec.included:
                rec.excluded_reason = "modo sólo-a-mí"
        elif me and uid == me and exclude_self:
            rec.included, rec.excluded_reason = False, "es la cuenta que envía"
        elif rec.matricula is None:
            rec.included, rec.excluded_reason = False, "no tiene correo de estudiante"
        elif expected_matriculas and rec.matricula not in set(expected_matriculas):
            rec.included = False
            rec.excluded_reason = "no está inscrito en MUSAI para este grupo"

        roster.recipients.append(rec)
    return roster


def check_counts(roster: Roster, *, expected: int, only_me: bool,
                 allow_stale_roster: bool = False) -> None:
    """Rail 3. Refuse on any disagreement between the page and MUSAI's own enrolment.

    This is free accuracy that exists only because the gradebook was already ingested — no
    scraper working from the page alone could catch a short recipient list, which is exactly
    the failure `#checkallonpage` produces on a group that paginates.

    🔴 **Two directions, not one.** The first version only compared counts of the
    intersection, and that is one-sided: if MUSAI knows 3 students and Moodle's course holds
    10, the intersection is 3, the counts agree, and seven students silently get nothing.
    That is not hypothetical — it is what 1-LED-A looks like right now, because MUSAI's
    enrolment is last semester's and the 2026-2 cohort enrolled afterwards. So a
    student-shaped participant MUSAI has never heard of is treated as **evidence that the
    roster is stale**, and refused, rather than quietly dropped.
    """
    got = len(roster.included)
    if got == 0:
        raise MessagingRefused("La lista de destinatarios quedó vacía.")
    if only_me:
        if got != 1:
            raise MessagingRefused(f"El modo sólo-a-mí seleccionó {got} destinatarios.")
        return
    if expected and got != expected:
        missing = expected - got
        raise MessagingRefused(
            f"MUSAI esperaba {expected} destinatario(s) y la página ofrece {got}"
            + (f" — faltan {missing}. ¿La lista está paginada?" if missing > 0
               else " — hay de más. Revisa la inscripción."))

    unknown = [r for r in roster.excluded
               if r.matricula and r.excluded_reason
               and "no está inscrito" in r.excluded_reason]
    if unknown and not allow_stale_roster:
        names = ", ".join((r.full_name or r.matricula) for r in unknown[:5])
        raise MessagingRefused(
            f"En Moodle hay {len(unknown)} estudiante(s) que MUSAI no conoce ({names}"
            f"{' …' if len(unknown) > 5 else ''}). El mensaje sólo llegaría a {got} de "
            f"{got + len(unknown)}. Vuelve a importar el libro de calificaciones antes de "
            f"enviar.")


def _shot(page, tag: str) -> str:
    SHOT_DIR.mkdir(exist_ok=True)
    path = SHOT_DIR / f"message_{tag}_{datetime.now():%Y%m%d_%H%M%S}.png"
    try:
        page.screenshot(path=str(path), full_page=True)
    except Exception:                                        # a shot is evidence, not a step
        return ""
    return str(path)


def send_message(
    *,
    idc: str,
    body: str,
    expected_matriculas: Sequence[str],
    dry_run: bool = True,
    only_me: bool = False,
    exclude_self: bool = True,
    headless: bool = True,
    group_label: str = "",
    as_user: Optional[str] = None,
    on_step: Optional[Callable[[str], None]] = None,
) -> Dict:
    """Compose and (optionally) send. `dry_run=True` stops on Moodle's own preview.

    Returns a dict carrying the decided recipient list, so the caller can record who was
    included AND who was excluded with the reason — the thing the page itself never shows.

    🔴 `as_user` (2026-08-11) sends from **another professor's** account, resolved through
    `credentials.resolve` (`MOODLE_PWD_<USERNAME>`), which refuses rather than falling back to
    The owner's login. Without it this function silently logged in as the owner: on a colleague's
    course that fails loudly at `_find_tile`, but on a course he *shares* it would have sent
    91 students a message signed by the wrong professor — and a message cannot be unsent.
    Consent is not a code path; Moodle attributes the message to whoever's account is used.
    """
    if not (body or "").strip():
        raise MessagingRefused("El mensaje está vacío.")

    # 🔴 The DRY_RUN gate, enforced a SECOND time here.
    #
    # `jobs.start()` already checks it, and that check was added after a test POST reached
    # this function with `dry_run=False` and delivered a real message. One gate in the
    # caller protects the callers that exist today; this one protects the next entry point
    # somebody adds — a CLI, a scheduled reminder, a retry helper — none of which will
    # remember to re-implement it. There is exactly one switch (`DRY_RUN` in `.env`) and two
    # places that refuse to move without it.
    if not dry_run and settings.dry_run:
        raise MessagingRefused(
            "DRY_RUN está activo: MUSAI no envía mensajes reales. "
            "Enviar es la única acción de MUSAI que no se puede deshacer.")

    def step(msg: str) -> None:
        log.step(msg)
        if on_step:
            on_step(msg)

    # Resolved BEFORE a browser exists, so a missing delegate password fails here rather than
    # after a login as the wrong person. Same shape as `restore.py`'s `on_behalf_of`.
    identity = resolve_identity(as_user)

    out: Dict = {"ok": False, "dry_run": dry_run, "only_me": only_me, "idc": idc,
                 "recipients": [], "excluded": [], "moodle_count": None,
                 "expected": len(expected_matriculas), "screenshot": "", "sent_at": None,
                 "as_user": identity.username, "acting_for_another": not identity.is_self}

    ensure_subprocess_capable_loop()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        # RAIL 6: a fresh context per batch. Moodle holds the recipient set in the session.
        ctx = browser.new_context(viewport={"width": 1500, "height": 1100})
        try:
            page = ctx.new_page()
            step("Entrando a Moodle…")
            vpage, host = enter_course(ctx, page, idc, as_user=as_user)

            # `perpage=200`, NOT 5000: Moodle materializes one <tr> per perpage slot.
            step("Leyendo la lista de participantes…")
            vpage.goto(f"https://{host}/user/index.php?id={idc}&perpage=200",
                       wait_until="domcontentloaded", timeout=60000)
            vpage.wait_for_load_state("networkidle", timeout=30000)

            raw = vpage.evaluate(_ROSTER_JS)
            roster = build_roster(raw, expected_matriculas=expected_matriculas,
                                  exclude_self=exclude_self, only_me=only_me)
            out["recipients"] = [r.__dict__ for r in roster.included]
            out["excluded"] = [r.__dict__ for r in roster.excluded]
            out["me"] = roster.me

            if only_me and not roster.me:
                raise MessagingRefused(
                    "No pude identificar la cuenta que envía, así que el modo sólo-a-mí no "
                    "tiene a quién escribir.")

            check_counts(roster, expected=len(expected_matriculas), only_me=only_me)
            step(f"{len(roster.included)} destinatario(s): "
                 + ", ".join(r.full_name or r.moodle_user_id for r in roster.included[:6])
                 + (" …" if len(roster.included) > 6 else ""))

            # RAIL 2: tick an enumerated list. `#checkallonpage` is never touched.
            for rec in roster.included:
                vpage.locator(f'input.usercheckbox[name="{rec.checkbox}"]').check()

            ticked = set(vpage.evaluate(_TICKED_JS))
            want = {r.checkbox for r in roster.included}
            if ticked != want:
                raise MessagingRefused(
                    f"Las casillas marcadas no son las previstas "
                    f"(marcadas {len(ticked)}, previstas {len(want)}).")

            step("Abriendo el formulario de mensaje…")
            with vpage.expect_navigation(wait_until="domcontentloaded", timeout=60000):
                vpage.select_option("#formactionid", "messageselect.php")
            vpage.wait_for_load_state("networkidle", timeout=30000)

            heading = vpage.evaluate(_HEADING_JS)
            out["moodle_count"] = heading.get("count")
            if heading.get("count") is not None and heading["count"] != len(want):
                raise MessagingRefused(
                    f"Moodle dice {heading['count']} destinatario(s) y MUSAI seleccionó "
                    f"{len(want)}. No se envía nada.")

            step("Escribiendo el mensaje…")
            filled = vpage.evaluate(_FILL_JS, to_html(body))
            if filled == "none":
                raise MessagingRefused("No encontré el cuadro del mensaje.")

            step("Abriendo la vista previa de Moodle…")
            with vpage.expect_navigation(wait_until="domcontentloaded", timeout=60000):
                vpage.locator('input[name="preview"]').click()
            vpage.wait_for_load_state("networkidle", timeout=30000)
            out["screenshot"] = _shot(vpage, f"{group_label or idc}_"
                                             f"{'dryrun' if dry_run else 'live'}")

            if dry_run:
                step("SIMULACRO — se llegó a la vista previa y NO se envió.")
                out["ok"] = True
                return out

            step(f"Enviando a {len(want)} destinatario(s)…")

            # 🔴 MEASURED 2026-08-12, and it is the whole reason 1MH-B was sent twice.
            #
            # Moodle fans this send out SYNCHRONOUSLY — one message row per recipient, inside
            # the request. 18 recipients took over 30 s; 35 took about 90 s. The old code was
            # `.click()` inside `expect_navigation(timeout=90000)`, and the 90 s never applied:
            # Playwright's own click auto-waits for scheduled navigations with its **default
            # 30 s**, so the click raised long before the outer wait was consulted.
            #
            # The exception then said "Timeout … waiting for scheduled navigations", which was
            # read as *the host died*. It had not: on 2026-08-12 the same timeout fired with
            # the host answering in 1.5 s, and all 18 recipients received the message exactly
            # once. **The send had already succeeded every time.**
            #
            # Two changes, and the second matters more than the first:
            #   1. `no_wait_after=True` so the click returns immediately and the *navigation*
            #      wait is the one that governs, scaled by how many people are being written.
            #   2. A timeout here is **UNKNOWN, never FAILED.** `ok=None` and `delivery
            #      _unknown=True` — because the natural response to "failed" is to retry, and
            #      a retry here delivers a second copy to everyone who already has one.
            wait_s = max(180_000, 6_000 * len(want))
            timed_out = False
            try:
                with vpage.expect_navigation(wait_until="domcontentloaded", timeout=wait_s):
                    vpage.locator('input[name="send"]').click(no_wait_after=True)
                vpage.wait_for_load_state("networkidle", timeout=30000)
            except PWTimeout:
                timed_out = True

            out["sent_at"] = datetime.utcnow().isoformat()
            try:
                out["after_send"] = _shot(vpage, f"{group_label or idc}_sent")
            except Exception:
                out["after_send"] = ""

            if timed_out:
                out["ok"] = None
                out["delivery_unknown"] = True
                out["error"] = (
                    f"Se hizo clic en «Enviar mensaje» a las {out['sent_at']} y la navegación "
                    f"posterior no terminó en {wait_s // 1000}s. ESTO NO SIGNIFICA QUE NO SE "
                    "ENVIÓ — casi siempre sí se envió. NO REINTENTAR: verificar en la bandeja "
                    "de mensajes (scratchpad/audit_drawer_susana.py) cuántas copias existen.")
                log.warning("⚠ ENVÍO DE RESULTADO DESCONOCIDO — verificar, NO reintentar.")
                return out

            out["ok"] = True
            step("Enviado.")
            return out
        except Exception as exc:
            out["error"] = describe_exception(exc)
            raise
        finally:
            # Inside the `with`: an outer finally runs after playwright has stopped and
            # replaces the real error with "Event loop is closed".
            ctx.close()
            browser.close()
