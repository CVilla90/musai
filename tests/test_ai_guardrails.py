"""Cost guardrails for the PAID Gemini key.

The failure mode these protect against is financial, not functional: a faulty tool or a
confused model can loop, and every loop is billed. So the limits are asserted here rather
than trusted to review.
"""

from datetime import date

import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlalchemy.pool import StaticPool

from musai.ai import budget as bud
from musai.ai.gemini import (
    ANALYST,
    FLASH,
    PRICES,
    SUSAI_ADMIN,
    SUSAI_STUDENT,
    AiResult,
    Profile,
    classify,
    prices_for,
)
from musai.config import settings


@pytest.fixture
def sess(monkeypatch):
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    monkeypatch.setattr("musai.ai.budget.today_local", lambda: date(2026, 8, 6))
    with Session(eng) as s:
        yield s


# ── failure classification: what may and may not be retried ───────────────────
@pytest.mark.parametrize("err", [
    "429 RESOURCE_EXHAUSTED: quota exceeded",
    "400 INVALID_ARGUMENT: bad tool schema",
    "403 PERMISSION_DENIED",
    "404 NOT_FOUND: model is not found for API version v1beta",
])
def test_terminal_errors_are_never_retried(err):
    """Retrying these either burns money or is guaranteed to fail again."""
    _reason, retryable = classify(err)
    assert retryable is False


def test_only_server_side_errors_are_retryable():
    assert classify("503 UNAVAILABLE")[1] is True
    assert classify("500 INTERNAL")[1] is True


def test_unknown_errors_default_to_no_retry():
    """Unknown failures are not assumed cheap."""
    reason, retryable = classify("something nobody has seen before")
    assert (reason, retryable) == ("error", False)


def test_quota_is_terminal_not_transient():
    """429 contains no transient needle, but must still classify as terminal quota."""
    assert classify("429 RESOURCE_EXHAUSTED")[0] == "quota"


# ── profiles: every surface is capped ─────────────────────────────────────────
@pytest.mark.parametrize("profile", [ANALYST, SUSAI_STUDENT, SUSAI_ADMIN])
def test_every_profile_caps_tool_fanout_and_output(profile):
    """An uncapped profile is an unbounded bill."""
    assert 0 < profile.max_remote_calls <= 8
    assert 0 < profile.max_output_tokens <= 2000
    assert 0 < profile.timeout_s <= 60


def test_student_profile_is_the_tightest():
    """Students are the highest-volume surface, so they get the smallest envelope."""
    assert SUSAI_STUDENT.max_remote_calls <= ANALYST.max_remote_calls
    assert SUSAI_STUDENT.max_output_tokens <= ANALYST.max_output_tokens


# ── pricing ───────────────────────────────────────────────────────────────────
def test_default_model_is_priced_and_is_a_lite_tier():
    assert settings.gemini_model in PRICES
    pin, pout = prices_for(settings.gemini_model)
    assert pin > 0 and pout > 0
    flash_in, flash_out = PRICES[FLASH]
    assert pin < flash_in and pout < flash_out, "default should be cheaper than full Flash"


def test_gemini_3_5_flash_is_dominated_by_the_newer_flashes():
    """Documents WHY 3.5-flash is never the answer: it is dearer on BOTH axes than 3.6 and 3.7.

    It used to be dominated only on output (same input price, worse output). Re-checked
    2026-08-16: the 3.6/3.7 introductory price halved the input too, so the domination is now
    strict. If this test starts failing on the input comparison, the introductory price has
    expired and every cost estimate in the app just doubled — see PRICES.
    """
    in35, out35 = PRICES["gemini-3.5-flash"]
    for newer in ("gemini-3.6-flash", "gemini-3.7-flash"):
        pin, pout = PRICES[newer]
        assert pin <= in35 and pout < out35, f"{newer} is no longer strictly cheaper"


def test_every_model_we_might_select_has_a_known_price():
    """A model with no PRICES entry silently falls back to the configured default rate, so the
    cost shown to the professor would be a number about a different model. Anything nameable
    in .env or in a Profile override has to be priced here."""
    for model in (FLASH, "gemini-3.5-flash-lite", "gemini-3.7-flash", settings.gemini_model):
        assert model in PRICES, f"{model} has no price entry"


def test_unknown_model_falls_back_to_configured_prices():
    assert prices_for("gemini-99-imaginary") == (
        settings.gemini_price_in_per_mtok,
        settings.gemini_price_out_per_mtok,
    )


def test_estimated_cost_uses_the_models_own_price():
    lite = AiResult(ok=True, tokens_in=1_000_000, tokens_out=0, model="gemini-3.5-flash-lite")
    flash = AiResult(ok=True, tokens_in=1_000_000, tokens_out=0, model=FLASH)
    assert lite.estimated_usd() == pytest.approx(0.30)
    # 0.75, not 1.50: the 3.6/3.7 introductory input price, which expires 2026-12-31.
    assert flash.estimated_usd() == pytest.approx(PRICES[FLASH][0])
    assert flash.estimated_usd() == pytest.approx(0.75)


# ── budget enforcement ────────────────────────────────────────────────────────
def test_non_admin_is_capped_far_below_admin():
    assert bud.Budget(False).max_tokens < bud.Budget(True).max_tokens
    assert bud.Budget(False).max_requests < bud.Budget(True).max_requests


def test_admin_is_capped_too():
    """A runaway loop in the owner's own session is exactly what this must stop."""
    b = bud.Budget(True)
    assert b.max_tokens > 0 and b.max_requests > 0


def test_budget_blocks_once_tokens_are_spent(sess):
    actor = "wa:521999"
    assert bud.check(sess, actor)[0] is True
    bud.record(sess, actor, AiResult(
        ok=True, tokens_in=settings.ai_daily_tokens_user, tokens_out=0, calls=1))
    allowed, why = bud.check(sess, actor)
    assert (allowed, why) == (False, "daily_tokens")


def test_budget_blocks_on_request_count_too(sess):
    """Cheap-but-frequent calls must also be stoppable."""
    actor = "wa:521888"
    for _ in range(settings.ai_daily_requests_user):
        bud.record(sess, actor, AiResult(ok=True, tokens_in=1, tokens_out=1, calls=1))
    allowed, why = bud.check(sess, actor)
    assert (allowed, why) == (False, "daily_requests")


def test_record_counts_every_billed_round_trip_not_just_one(sess):
    """A turn that made 3 billed calls must cost 3 against the request budget."""
    actor = "web:carlos"
    bud.record(sess, actor, AiResult(ok=True, tokens_in=10, tokens_out=5, calls=3))
    assert bud.usage_today(sess, actor).requests == 3


def test_failures_are_counted_and_still_charged(sess):
    """A failed call still consumed tokens; it must not be free in the ledger."""
    actor = "web:carlos"
    bud.record(sess, actor, AiResult(ok=False, reason="empty", tokens_in=900,
                                     tokens_out=0, calls=2))
    row = bud.usage_today(sess, actor)
    assert row.errors == 1
    assert row.tokens_in == 900
    assert row.requests == 2


def test_blocked_attempts_are_recorded(sess):
    actor = "wa:521777"
    bud.record(sess, actor, AiResult(
        ok=True, tokens_in=settings.ai_daily_tokens_user, tokens_out=0, calls=1))
    bud.check(sess, actor)
    assert bud.usage_today(sess, actor).blocked == 1


def test_budget_is_per_actor(sess):
    """One student burning their budget must not affect another."""
    bud.record(sess, "wa:111", AiResult(
        ok=True, tokens_in=settings.ai_daily_tokens_user, tokens_out=0, calls=1))
    assert bud.check(sess, "wa:111")[0] is False
    assert bud.check(sess, "wa:222")[0] is True


def test_summary_reports_spend(sess):
    actor = "web:carlos"
    bud.record(sess, actor, AiResult(ok=True, tokens_in=1000, tokens_out=200, calls=1))
    s = bud.summary(sess, actor, is_admin=True)
    assert s["tokens"] == 1200
    assert s["requests"] == 1
    assert s["tokens_cap"] == settings.ai_daily_tokens_admin
    assert s["estimated_usd"] is not None
