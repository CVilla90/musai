"""What one MUSAI action costs, and how much of a professor's free allowance is left.

Every professor gets a small monthly allowance of **MUSAI usage** — not "Gemini usage" and not
"Replit usage", but our own number covering both, so a professor never has to think about which
vendor a click reached. This module is the one place that knows the exchange rate.

## What is metered, and what deliberately is not

Measured 2026-08-16, at the rates in `RATE_CARD`:

    page view (cockpit)          $0.0000008      125,628 of them fit in $0.10
    map my courses (~30 s)       $0.00040
    assistant question           $0.00096            104 of them fit in $0.10
    course backup (~3 min)       $0.0024
    build: compose a block       $0.0031
    course restore (~15 min)     $0.0119               8 of them fit in $0.10

**A page view is 1/1206 of an assistant question and 1/14,850 of a restore**, and an assistant
question is 96 % Gemini, 4 % compute. So only two things are written to the ledger: **AI calls
and browser jobs.**

🔴 The rejected alternative was a ledger row per HTTP request, so that "every button shows its
cost". A row per page view costs more compute to store than the page view it measures — a meter
that costs more than the electricity — and 40 buttons reading `$0.0000008` teach the reader to
ignore the meter, which is the one thing a meter must not do. The Usage tab says in words that
browsing is free. **Where the money isn't is useful information too, and it fits in a sentence.**

## Two rules that outlive this file

* **Price at the time of the event and store the receipt.** `UsageEvent.micro_usd` is written
  once. Gemini's 3.6/3.7 introductory price doubles 2027-01-01; re-pricing history at today's
  card would report that last month got more expensive while nobody touched it.
* **Measure before enforcing.** `settings.usage_enforce` is off. The daily token/request budget
  in `musai.ai.budget` already stops the failure that actually burns money (a loop). A monthly
  ceiling enforced on estimated job durations locks a colleague out of their own gradebook.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from sqlmodel import Session, func, select

from musai.config import settings
from musai.models import UsageEvent
from musai.semesters import today_local

#: Bump when a rate below changes. Stamped onto every row so an old number stays explainable.
RATE_CARD = "2026-08-16"

# ── Replit Autoscale ──────────────────────────────────────────────────────────
# docs.replit.com/billing/deployment-pricing, read 2026-08-16.
USD_PER_COMPUTE_UNIT = 0.60 / 1e6
USD_PER_REQUEST = 0.40 / 1e6
CU_PER_CPU_SECOND = 18.0
CU_PER_GIB_SECOND = 2.0

#: The container we size for. Autoscale bills the machine you configured, not the CPU you
#: happened to use, so these are the two numbers that turn wall-clock into money.
CONTAINER_VCPU = 1.0
CONTAINER_GIB = 2.0

#: 22 compute units per wall-second on a 1 vCPU / 2 GiB machine → $0.0000132/s.
CU_PER_SECOND = CU_PER_CPU_SECOND * CONTAINER_VCPU + CU_PER_GIB_SECOND * CONTAINER_GIB
USD_PER_SECOND = CU_PER_SECOND * USD_PER_COMPUTE_UNIT

MICRO = 1_000_000  # micro-USD per USD


# ── kinds ─────────────────────────────────────────────────────────────────────
#: Label and one-line explanation per metered kind, for the Usage tab. A kind missing here
#: still records — it just shows under its raw name, which is better than not recording.
KINDS = {
    "assistant": ("Assistant question", "A question to the AI assistant over your gradebook."),
    "build_compose": ("Content composed", "AI-composed HTML for a course block."),
    "course_publish": ("Content published", "Writing composed content into a Moodle course."),
    "susai": ("Student assistant", "A student's WhatsApp question answered by SUSAI."),
    "map_courses": ("Course mapping", "Reading your course list from Moodle."),
    "course_backup": ("Course backup", "Downloading a course archive from Moodle."),
    "course_restore": ("Course restore", "Restoring an archive into a course — the big one."),
    "credential_check": ("Password test", "Signing in once to check a stored password."),
    "messages": ("Messages sent", "Sending a message to a group's students."),
}


def label_for(kind: str) -> str:
    return KINDS.get(kind, (kind.replace("_", " ").title(), ""))[0]


def blurb_for(kind: str) -> str:
    return KINDS.get(kind, (kind, ""))[1]


# ── pricing ───────────────────────────────────────────────────────────────────
def price_micro_usd(*, requests: int = 1, seconds: float = 0.0,
                    tokens_in: int = 0, tokens_out: int = 0, model: str = "") -> int:
    """Cost of one action in millionths of a USD, at today's rate card.

    Rounded UP to the nearest micro-dollar when anything at all was consumed, so that a real
    action never books as free. A ledger of zeroes that sums to zero is indistinguishable from
    a broken meter.
    """
    from musai.ai.gemini import prices_for

    usd = requests * USD_PER_REQUEST + seconds * USD_PER_SECOND
    if tokens_in or tokens_out:
        pin, pout = prices_for(model or settings.gemini_model)
        usd += (tokens_in / 1e6) * pin + (tokens_out / 1e6) * pout
    micro = usd * MICRO
    if micro <= 0:
        return 0
    return max(1, int(micro + 0.5))


def record(sess: Session, actor: str, kind: str, *, detail: str = "",
           requests: int = 1, seconds: float = 0.0, tokens_in: int = 0,
           tokens_out: int = 0, model: str = "", day: Optional[date] = None) -> UsageEvent:
    """Book one priced action. The caller commits.

    Never raises into the caller's path: a metering failure must not break a restore. The
    worst outcome of a lost row is an understated bill, and that is strictly better than a
    professor's course write dying because the accountant tripped.
    """
    ev = UsageEvent(
        actor=actor, day=day or today_local(), kind=kind, detail=detail[:200],
        tokens_in=tokens_in, tokens_out=tokens_out, model=model or "",
        seconds=round(seconds, 2), requests=requests,
        micro_usd=price_micro_usd(requests=requests, seconds=seconds, tokens_in=tokens_in,
                                  tokens_out=tokens_out, model=model),
        rate_card=RATE_CARD,
    )
    sess.add(ev)
    return ev


def record_safely(actor: str, kind: str, **kw) -> None:
    """`record` + its own session + its own commit, swallowing every failure.

    For the call sites that are NOT in a request handler — the job worker thread, most
    importantly, where an exception has nothing above it to catch and would be reported to the
    professor as the restore having failed.
    """
    from musai.db import engine

    try:
        with Session(engine) as sess:
            record(sess, actor, kind, **kw)
            sess.commit()
    except Exception:                                            # noqa: BLE001
        from musai.automation._log import logger as log

        log.warning(f"usage: could not record {kind} for {actor} (not fatal)")


# ── allowance ─────────────────────────────────────────────────────────────────
def month_start(today: Optional[date] = None) -> date:
    d = today or today_local()
    return d.replace(day=1)


def _sum_micro(sess: Session, actor: str, since: date) -> int:
    total = sess.exec(
        select(func.coalesce(func.sum(UsageEvent.micro_usd), 0))
        .where(UsageEvent.actor == actor, UsageEvent.day >= since)
    ).one()
    return int(total or 0)


def allowance_micro_usd(is_admin: bool) -> Optional[int]:
    """The monthly free allowance, or `None` for unlimited.

    The owner is unlimited **and still metered**: an untracked account is the one whose spend
    nobody notices, and it is the account that runs every experiment.
    """
    if is_admin:
        return None
    return max(0, int(settings.usage_free_micro_usd))


def month_to_date(sess: Session, actor: str, *, is_admin: bool = False,
                  today: Optional[date] = None) -> dict:
    """Month-to-date spend for one professor, plus what is left of the allowance.

    `remaining_questions` is the number that belongs on screen. "$0.10" means nothing to a
    professor; "about 100 more questions this month" is the same fact and is actionable.
    """
    start = month_start(today)
    spent = _sum_micro(sess, actor, start)
    cap = allowance_micro_usd(is_admin)
    pct = round(100 * spent / cap, 1) if cap else None
    per_question = price_micro_usd(requests=1, seconds=3.0, tokens_in=1400, tokens_out=200)
    left = None if cap is None else max(0, cap - spent)
    return {
        "actor": actor,
        "since": str(start),
        "micro_usd": spent,
        "usd": spent / MICRO,
        "cap_micro_usd": cap,
        "cap_usd": None if cap is None else cap / MICRO,
        "pct": pct,
        "unlimited": cap is None,
        "over": bool(cap is not None and spent >= cap),
        "warn": bool(pct is not None and pct >= 80),
        "remaining_micro_usd": left,
        # How many more assistant questions the remaining allowance buys. `None` = unlimited.
        "remaining_questions": None if left is None else int(left // max(1, per_question)),
        "per_question_micro_usd": per_question,
    }


def breakdown(sess: Session, actor: str, *, days: int = 30,
              today: Optional[date] = None) -> list[dict]:
    """Spend grouped by kind over the trailing window, dearest first."""
    since = (today or today_local()) - timedelta(days=days)
    rows = sess.exec(
        select(UsageEvent.kind,
               func.count(UsageEvent.id),
               func.coalesce(func.sum(UsageEvent.micro_usd), 0),
               func.coalesce(func.sum(UsageEvent.tokens_in + UsageEvent.tokens_out), 0),
               func.coalesce(func.sum(UsageEvent.seconds), 0.0))
        .where(UsageEvent.actor == actor, UsageEvent.day >= since)
        .group_by(UsageEvent.kind)
    ).all()
    out = [{"kind": k, "label": label_for(k), "blurb": blurb_for(k), "count": int(n),
            "micro_usd": int(micro), "usd": int(micro) / MICRO,
            "tokens": int(tok), "seconds": float(secs or 0.0)}
           for k, n, micro, tok, secs in rows]
    out.sort(key=lambda r: r["micro_usd"], reverse=True)
    total = sum(r["micro_usd"] for r in out)
    for r in out:
        r["pct_of_total"] = round(100 * r["micro_usd"] / total, 1) if total else 0.0
    return out


def recent(sess: Session, actor: str, *, limit: int = 25) -> list[dict]:
    rows = sess.exec(
        select(UsageEvent).where(UsageEvent.actor == actor)
        .order_by(UsageEvent.id.desc()).limit(limit)
    ).all()
    return [{"day": str(e.day), "at": e.created_at, "kind": e.kind, "label": label_for(e.kind),
             "detail": e.detail, "tokens": e.tokens_in + e.tokens_out, "seconds": e.seconds,
             "micro_usd": e.micro_usd, "usd": e.micro_usd / MICRO, "model": e.model}
            for e in rows]


def check(sess: Session, actor: str, *, is_admin: bool = False) -> tuple[bool, str]:
    """(allowed, reason) for the MONTHLY allowance.

    ⚠️ Returns `True` unless `settings.usage_enforce` is on, which it is not by default. The
    numbers behind the allowance are estimates of job durations, not measurements; enforcing
    them before a month of real rows exist would refuse work on a guess. Measure, then enforce.
    """
    mtd = month_to_date(sess, actor, is_admin=is_admin)
    if not settings.usage_enforce or mtd["unlimited"] or not mtd["over"]:
        return True, ""
    return False, "monthly_allowance"


# ── the rate card, for the Usage tab ──────────────────────────────────────────
def rate_card() -> dict:
    """Everything the Usage tab needs to show its work. A cost the reader cannot check is a
    number they have to trust, and this one is an estimate."""
    from musai.ai.gemini import prices_for

    pin, pout = prices_for(settings.gemini_model)
    return {
        "version": RATE_CARD,
        "model": settings.gemini_model,
        "gemini_in_per_mtok": pin,
        "gemini_out_per_mtok": pout,
        "usd_per_million_cu": USD_PER_COMPUTE_UNIT * 1e6,
        "usd_per_million_requests": USD_PER_REQUEST * 1e6,
        "cu_per_second": CU_PER_SECOND,
        "usd_per_second": USD_PER_SECOND,
        "vcpu": CONTAINER_VCPU,
        "gib": CONTAINER_GIB,
        "enforced": settings.usage_enforce,
    }


#: What the common actions cost, for the "what things cost" table. Seconds are typical
#: durations, not promises — a restore is a queued PHP job on UACH's server (COURSE_EDITING §7)
#: and MUSAI cannot make it faster, only stop you waiting on it.
TYPICAL = [
    ("Opening any page", dict(requests=1, seconds=0.03), "free in practice"),
    ("assistant", dict(requests=1, seconds=3.0, tokens_in=1400, tokens_out=200), ""),
    ("build_compose", dict(requests=1, seconds=6.0, tokens_in=2500, tokens_out=900), ""),
    ("map_courses", dict(requests=1, seconds=30.0), ""),
    ("course_backup", dict(requests=1, seconds=180.0), ""),
    ("messages", dict(requests=1, seconds=300.0), ""),
    ("course_restore", dict(requests=1, seconds=900.0), "~15 min on Moodle's side"),
]


def typical_costs(is_admin: bool = False) -> list[dict]:
    cap = allowance_micro_usd(is_admin) or int(settings.usage_free_micro_usd)
    out = []
    for name, kw, note in TYPICAL:
        micro = price_micro_usd(**kw)
        out.append({
            "label": KINDS.get(name, (name, ""))[0],
            "micro_usd": micro,
            "usd": micro / MICRO,
            "pct": round(100 * micro / cap, 2) if cap else 0.0,
            "per_allowance": int(cap // micro) if micro and cap else None,
            "note": note,
        })
    return out
