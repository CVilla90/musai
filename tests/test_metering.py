"""The MUSAI usage meter — what an action costs, and whose allowance it comes out of.

Two failure modes worth pinning, and neither is "the arithmetic is slightly off":

* **The bill lands on the wrong person.** Until 2026-08-16 every AI call in the app was billed
  to the literal string `web:carlos`, so a second professor would have spent the owner's budget
  and appeared in his usage. That is a leak as much as a billing bug — the Usage tab shows
  what someone did.
* **History changes price.** Gemini's introductory rate doubles on 2027-01-01. A ledger that
  re-prices old rows at today's card reports that last month got more expensive.
"""

from datetime import date, timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from musai import metering as met
from musai.config import settings
from musai.models import UsageEvent


@pytest.fixture
def sess():
    """A file-backed-shaped in-memory DB. StaticPool because the metering rollups read back
    what a previous statement wrote — see `feedback_a_flaky_test_is_a_finding`."""
    from sqlalchemy.pool import StaticPool

    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        yield s


# ── the rate card is arithmetic, not a vibe ───────────────────────────────────
def test_a_wall_second_costs_what_the_published_rates_say():
    """22 compute units on a 1 vCPU / 2 GiB machine, at $0.60 per million.

    Pinned because every job cost in the app is this number times a duration, so a typo here
    is wrong by a constant factor everywhere at once and looks plausible everywhere at once.
    """
    assert met.CU_PER_SECOND == pytest.approx(22.0)
    assert met.USD_PER_SECOND == pytest.approx(0.0000132)


def test_an_analyst_question_costs_about_a_tenth_of_a_cent():
    micro = met.price_micro_usd(requests=1, seconds=3.0, tokens_in=1400, tokens_out=200)
    assert 800 <= micro <= 1200, f"an analyst question priced at {micro} micro-USD"


def test_the_ai_dominates_an_analyst_question_not_the_compute():
    """The fact that decides what is worth metering: tokens are the bill, seconds are rounding.

    If this ever inverts — a much cheaper model, or a much slower one — the design of the meter
    should be revisited, not just the numbers.
    """
    total = met.price_micro_usd(requests=1, seconds=3.0, tokens_in=1400, tokens_out=200)
    compute_only = met.price_micro_usd(requests=1, seconds=3.0)
    assert compute_only / total < 0.10


def test_a_page_view_is_three_orders_of_magnitude_below_a_question():
    """Why page views are not metered at all: a ledger row per view would cost more to store
    than the view. If this gap ever closes, that decision needs re-making."""
    view = met.price_micro_usd(requests=1, seconds=0.03)
    question = met.price_micro_usd(requests=1, seconds=3.0, tokens_in=1400, tokens_out=200)
    assert question / max(view, 1) > 500


def test_a_real_action_is_never_free():
    """Rounded up, so a ledger of real work can never sum to zero — which is exactly what a
    broken meter also looks like."""
    assert met.price_micro_usd(requests=1, seconds=0.001) >= 1
    assert met.price_micro_usd(requests=0, seconds=0.0) == 0


# ── the receipt does not get re-priced ────────────────────────────────────────
def test_a_recorded_cost_survives_a_rate_change(sess, monkeypatch):
    """🔴 The whole reason `micro_usd` is a stored column and not a computed property.

    Gemini's 3.6/3.7 introductory input price doubles on 2027-01-01. Re-pricing history would
    make last month more expensive than it was, and the professor who reads that has no way to
    tell a rate change from an error.
    """
    met.record(sess, "prof@uach.mx", "analyst", tokens_in=100_000, tokens_out=10_000,
               model="gemini-3.5-flash-lite")
    sess.commit()
    before = met._sum_micro(sess, "prof@uach.mx", date(2000, 1, 1))
    assert before > 0

    # The rate card doubles under us.
    monkeypatch.setattr("musai.ai.gemini.PRICES",
                        {**__import__("musai.ai.gemini", fromlist=["PRICES"]).PRICES,
                         "gemini-3.5-flash-lite": (0.60, 5.00)})
    assert met._sum_micro(sess, "prof@uach.mx", date(2000, 1, 1)) == before


def test_every_row_stamps_the_rate_card_that_priced_it(sess):
    met.record(sess, "prof@uach.mx", "analyst", tokens_in=1000, tokens_out=100)
    sess.commit()
    row = sess.exec(select(UsageEvent)).first()
    assert row.rate_card == met.RATE_CARD and row.rate_card


# ── whose bill is it ──────────────────────────────────────────────────────────
def test_spend_is_per_professor(sess):
    """🔴 The regression that made this feature necessary. One professor's questions must
    never appear in another's usage — the tab shows what someone did, not only what they owe."""
    met.record(sess, "ana@uach.mx", "analyst", tokens_in=500_000, tokens_out=50_000)
    met.record(sess, "beto@uach.mx", "analyst", tokens_in=1000, tokens_out=100)
    sess.commit()
    ana = met.month_to_date(sess, "ana@uach.mx")
    beto = met.month_to_date(sess, "beto@uach.mx")
    assert ana["micro_usd"] > beto["micro_usd"] * 10
    assert met.month_to_date(sess, "nadie@uach.mx")["micro_usd"] == 0


def test_last_months_spend_does_not_count_against_this_month(sess):
    """The allowance resets on the 1st, so the rollup must start there and not 30 days back."""
    today = date(2026, 8, 16)
    met.record(sess, "prof@uach.mx", "analyst", tokens_in=900_000, tokens_out=0,
               day=date(2026, 7, 20))
    met.record(sess, "prof@uach.mx", "analyst", tokens_in=1000, tokens_out=0,
               day=date(2026, 8, 3))
    sess.commit()
    mtd = met.month_to_date(sess, "prof@uach.mx", today=today)
    assert mtd["since"] == "2026-08-01"
    # August's 1000 tokens = 300 micro-USD. July's 900k would have added 270,000 — three
    # orders of magnitude — so a leaking window is not a subtle failure here.
    assert mtd["micro_usd"] == 300


# ── the allowance ─────────────────────────────────────────────────────────────
def test_the_admin_is_unlimited_and_still_metered(sess):
    """An untracked account is the one nobody notices, and it is the one that runs every
    experiment. Unlimited means no cap, never no ledger."""
    met.record(sess, "owner@uach.mx", "analyst", tokens_in=10_000_000, tokens_out=1_000_000)
    sess.commit()
    mtd = met.month_to_date(sess, "owner@uach.mx", is_admin=True)
    assert mtd["unlimited"] is True
    assert mtd["cap_micro_usd"] is None
    assert mtd["over"] is False
    assert mtd["micro_usd"] > 0, "unlimited must not mean unmeasured"


def test_the_allowance_is_quoted_in_questions_not_only_dollars(sess):
    """"$0.10" is not a quantity a professor can plan with. The screen needs a countable."""
    mtd = met.month_to_date(sess, "nuevo@uach.mx")
    assert mtd["remaining_questions"] > 50
    assert mtd["remaining_questions"] < 500


def test_a_professor_can_be_over_without_being_blocked(sess, monkeypatch):
    """🔴 Measure before enforcing. The durations behind these estimates have never been
    observed over a real month, and refusing a colleague's restore on a guess is worse than
    an overspend of a few cents. The DAILY budget is what actually stops a runaway loop."""
    monkeypatch.setattr(settings, "usage_enforce", False)
    met.record(sess, "gastador@uach.mx", "analyst",
               tokens_in=settings.usage_free_micro_usd * 100, tokens_out=0)
    sess.commit()
    assert met.month_to_date(sess, "gastador@uach.mx")["over"] is True
    assert met.check(sess, "gastador@uach.mx") == (True, "")

    monkeypatch.setattr(settings, "usage_enforce", True)
    assert met.check(sess, "gastador@uach.mx") == (False, "monthly_allowance")


def test_enforcement_never_refuses_the_admin(sess, monkeypatch):
    monkeypatch.setattr(settings, "usage_enforce", True)
    met.record(sess, "owner@uach.mx", "analyst",
               tokens_in=settings.usage_free_micro_usd * 100, tokens_out=0)
    sess.commit()
    assert met.check(sess, "owner@uach.mx", is_admin=True) == (True, "")


def test_the_warning_comes_before_the_wall(sess):
    """80% is a warning, not a refusal — a professor should learn they are close while they can
    still plan around it."""
    cap = settings.usage_free_micro_usd
    met.record(sess, "casi@uach.mx", "analyst", tokens_in=0, tokens_out=0,
               seconds=(cap * 0.85 / 1e6) / met.USD_PER_SECOND)
    sess.commit()
    mtd = met.month_to_date(sess, "casi@uach.mx")
    assert mtd["warn"] is True and mtd["over"] is False


# ── the breakdown ─────────────────────────────────────────────────────────────
def test_the_breakdown_names_the_dearest_action_first(sess):
    met.record(sess, "prof@uach.mx", "analyst", tokens_in=1000, tokens_out=100)
    met.record(sess, "prof@uach.mx", "course_restore", seconds=900.0)
    sess.commit()
    rows = met.breakdown(sess, "prof@uach.mx")
    assert rows[0]["kind"] == "course_restore"
    assert rows[0]["label"] == "Course restore"
    assert sum(r["pct_of_total"] for r in rows) == pytest.approx(100.0, abs=0.2)


def test_the_breakdown_is_scoped_to_one_professor(sess):
    met.record(sess, "ana@uach.mx", "course_restore", seconds=900.0)
    met.record(sess, "beto@uach.mx", "analyst", tokens_in=1000, tokens_out=100)
    sess.commit()
    assert [r["kind"] for r in met.breakdown(sess, "beto@uach.mx")] == ["analyst"]


def test_an_unknown_kind_still_records_rather_than_crashing(sess):
    """A kind added to a route and forgotten in KINDS must show under its raw name. Losing the
    row is the one outcome that is worse than an ugly label."""
    met.record(sess, "prof@uach.mx", "some_new_thing", seconds=5.0)
    sess.commit()
    rows = met.breakdown(sess, "prof@uach.mx")
    assert rows[0]["kind"] == "some_new_thing"
    assert rows[0]["label"] == "Some New Thing"


def test_a_metering_failure_never_reaches_the_caller(monkeypatch):
    """`record_safely` runs on the job worker thread, where an exception is reported to the
    professor as the restore having failed. An accounting error must not do that."""
    monkeypatch.setattr(met, "record",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ledger down")))
    met.record_safely("prof@uach.mx", "course_restore", seconds=900.0)  # must not raise


# ── the typical-cost table on screen ──────────────────────────────────────────
def test_the_typical_table_tells_a_professor_what_a_restore_costs_them():
    rows = {r["label"]: r for r in met.typical_costs()}
    restore = rows["Course restore"]
    assert 5 <= restore["pct"] <= 25, "a restore should read as a real slice of the month"
    assert restore["per_allowance"] and restore["per_allowance"] < 30

    browsing = rows["Opening any page"]
    assert browsing["per_allowance"] > 10_000, "browsing must read as free"


def test_the_rate_card_shows_its_working():
    card = met.rate_card()
    assert card["model"] == settings.gemini_model
    assert card["gemini_in_per_mtok"] > 0 and card["gemini_out_per_mtok"] > 0
    assert card["usd_per_million_cu"] == pytest.approx(0.60)
    assert card["enforced"] is settings.usage_enforce


def test_every_metered_kind_has_a_human_label():
    """A raw table name in front of a professor is a bug report waiting to happen."""
    for kind, (label, blurb) in met.KINDS.items():
        assert label and not label.islower(), kind
        assert blurb.endswith("."), kind


# ── the Usage tab, end to end ─────────────────────────────────────────────────
@pytest.fixture
def client(sign_in, monkeypatch):
    """Signed in, against the suite's own database copy. Follows no redirects, so the auth
    gate turning a page into a 303 shows up as a failure instead of as the landing page —
    `feedback_a_redirect_swallows_a_negative_test`."""
    from fastapi.testclient import TestClient

    from musai.web.app import app

    return sign_in(TestClient(app, follow_redirects=False))


def test_the_usage_tab_renders_and_shows_the_rate_card(client):
    r = client.get("/settings?tab=usage")
    assert r.status_code == 200
    assert "used this month" in r.text
    assert settings.gemini_model in r.text
    # Needles kept inside one source line: the template wraps, so a phrase spanning a line
    # break fails on formatting rather than on content and teaches you to loosen the test.
    assert "million compute units" in r.text
    assert "per million tokens in" in r.text


def test_the_usage_tab_says_out_loud_that_browsing_is_not_counted(client):
    """🔴 The one sentence that keeps the meter honest. A number labelled "usage" that silently
    omits most of what you clicked is worse than no number: it invites the reader to conclude
    their browsing is being counted and is free."""
    r = client.get("/settings?tab=usage")
    assert "Opening pages is not counted" in r.text


def test_the_usage_tab_admits_it_does_not_enforce(client, monkeypatch):
    monkeypatch.setattr(settings, "usage_enforce", False)
    r = client.get("/settings?tab=usage")
    assert "measuring, not enforcing" in r.text


def test_an_unknown_tab_falls_back_instead_of_rendering_a_blank_page(client):
    r = client.get("/settings?tab=nonsense")
    assert r.status_code == 200
    assert "can read these passwords" in r.text


def test_the_passwords_tab_is_still_the_default(client):
    r = client.get("/settings")
    assert r.status_code == 200
    assert "can read these passwords" in r.text
    assert 'name="password"' in r.text


def test_the_navbar_meter_links_to_the_usage_tab(client):
    """It is the only affordance that answers "why is that number what it is?"."""
    r = client.get("/settings")
    assert '/settings?tab=usage' in r.text


def test_a_signed_out_visitor_gets_no_meter_and_no_crash():
    """🔴 The meter is a template global on every page. If it raised when signed out — and
    `current_professor` does 401 when signed out — it would take down the landing page to
    report an accounting total."""
    from fastapi.testclient import TestClient

    from musai.web.app import app

    r = TestClient(app, follow_redirects=False).get("/")
    assert r.status_code == 200
    assert "of free usage" not in r.text
