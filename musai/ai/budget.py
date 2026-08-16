"""Daily AI spend budgets, per actor.

Two tiers, and the admin tier is a ceiling too — not an exemption. The scenario this exists
for is not only "another professor hammers the assistant"; it is equally "a faulty tool sends
The owner's own session into a loop at 3am". Both are capped, just at different heights.

Enforcement is deliberately BEFORE the call (`check`) and accounting AFTER (`record`), so a
single very expensive request can overshoot its cap once but never twice. That's the honest
trade: we cannot know a request's cost until it has run.

`actor` is a namespaced key so the same ledger serves every surface:
    web:carlos          the cockpit analyst
    wa:526141837420     a WhatsApp sender
"""

from datetime import date, datetime
from typing import Optional

from sqlmodel import Session, select

from musai.ai.gemini import prices_for
from musai.config import settings
from musai.models import AiUsage
from musai.semesters import today_local


class Budget:
    """Resolved caps for one actor."""

    def __init__(self, is_admin: bool):
        self.is_admin = is_admin
        self.max_tokens = (settings.ai_daily_tokens_admin if is_admin
                           else settings.ai_daily_tokens_user)
        self.max_requests = (settings.ai_daily_requests_admin if is_admin
                             else settings.ai_daily_requests_user)


def _row(sess: Session, actor: str, day: Optional[date] = None) -> AiUsage:
    day = day or today_local()
    row = sess.exec(
        select(AiUsage).where(AiUsage.actor == actor, AiUsage.day == day)
    ).first()
    if row is None:
        row = AiUsage(actor=actor, day=day)
        sess.add(row)
        sess.flush()
    return row


def usage_today(sess: Session, actor: str) -> AiUsage:
    return _row(sess, actor)


def check(sess: Session, actor: str, *, is_admin: bool = False) -> tuple[bool, str]:
    """(allowed, reason). Call BEFORE spending. Records a block when it refuses."""
    budget = Budget(is_admin)
    row = _row(sess, actor)
    if row.requests >= budget.max_requests:
        row.blocked += 1
        row.updated_at = datetime.utcnow()
        sess.add(row)
        return False, "daily_requests"
    if (row.tokens_in + row.tokens_out) >= budget.max_tokens:
        row.blocked += 1
        row.updated_at = datetime.utcnow()
        sess.add(row)
        return False, "daily_tokens"
    return True, ""


def record(sess: Session, actor: str, result) -> AiUsage:
    """Book what a call actually cost. `result` is an `ai.gemini.AiResult`."""
    row = _row(sess, actor)
    row.requests += max(1, getattr(result, "calls", 1))
    row.tokens_in += getattr(result, "tokens_in", 0)
    row.tokens_out += getattr(result, "tokens_out", 0)
    if not getattr(result, "ok", False):
        row.errors += 1
    row.updated_at = datetime.utcnow()
    sess.add(row)
    return row


def summary(sess: Session, actor: str, *, is_admin: bool = False) -> dict:
    """Human-readable budget state, for the cockpit and for logs."""
    budget = Budget(is_admin)
    row = _row(sess, actor)
    used = row.tokens_in + row.tokens_out
    # Priced at the configured default model; a profile override would shift this slightly.
    pin, pout = prices_for(settings.gemini_model)
    usd = None
    if pin > 0 or pout > 0:
        usd = round((row.tokens_in / 1e6) * pin + (row.tokens_out / 1e6) * pout, 4)
    return {
        "actor": actor,
        "day": str(row.day),
        "requests": row.requests,
        "requests_cap": budget.max_requests,
        "tokens": used,
        "tokens_cap": budget.max_tokens,
        "tokens_pct": round(100 * used / budget.max_tokens, 1) if budget.max_tokens else 0.0,
        "errors": row.errors,
        "blocked": row.blocked,
        "estimated_usd": usd,
    }
