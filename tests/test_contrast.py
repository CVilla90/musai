"""Every colour pair the interface actually puts together clears WCAG AA.

⭐ `feedback_check_the_pair_not_the_colour`. A palette that looked right went live in six of
The owner's courses and failed WCAG on **11 of 37 pairs**, because it had been reviewed one colour
at a time. A colour has no contrast; only a pair does.

Two places this bites hardest in MUSAI:

* **The light landing page was derived from the dark one** (2026-08-14). `--teal #45E0C8` is
  beautiful on near-navy and measures 1.6:1 on cream — a signal that means "locked / verified"
  turning invisible on the theme served by default.
* **Six feature accents, each with its own tint** (DESIGN_DIRECTION §2.1). That is eighteen
  pairs nobody would check by hand, and the chip style `--accent-deep on --accent-tint` is the
  smallest type on the screen.

Measured, not eyeballed, and in a test rather than a script — because the failure this guards
against is a palette *drifting*, which no single review catches.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

TEMPLATES = Path(__file__).resolve().parent.parent / "musai" / "web" / "templates"

AA_NORMAL, AA_LARGE = 4.5, 3.0


# ── the maths ─────────────────────────────────────────────────────────────────
def _rgb(hexcolor: str) -> tuple[float, float, float]:
    h = hexcolor.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _lin(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(hexcolor: str) -> float:
    r, g, b = (_lin(c) for c in _rgb(hexcolor))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(fg: str, bg: str) -> float:
    a, b = luminance(fg), luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def palette(source: str, opener: str) -> dict[str, str]:
    """`--name: #hex;` declarations inside one CSS block, by brace matching.

    🔴 Brace-matched rather than regexed to the next `}`: the dark palette lives inside an
    `@media` block, so a naive scan stops at the wrong brace and silently returns half a
    palette — which would make this whole file pass by measuring nothing.
    """
    start = source.index(opener)
    depth, i = 0, start
    while True:
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    return {m.group(1): m.group(2)
            for m in re.finditer(r"--([\w-]+)\s*:\s*(#[0-9A-Fa-f]{3,6})\s*;",
                                 source[start:i])}


def _landing() -> str:
    return (TEMPLATES / "landing.html").read_text(encoding="utf-8")


def _base() -> str:
    return (TEMPLATES / "base.html").read_text(encoding="utf-8")


# ── the landing page ──────────────────────────────────────────────────────────
#: (fg, bg, what it is, is the type large?)
LANDING_PAIRS = [
    ("fg", "ground", "body text on the page", False),
    ("fg", "ground-raised", "body text on a panel", False),
    ("fg-soft", "ground", "the lede and long-form copy", False),
    ("fg-soft", "sunken", "the 'them' chat bubble", False),
    ("dim", "ground", "secondary copy, rail bodies", False),
    ("dim", "ground-raised", "secondary copy on a panel", False),
    ("dimmer", "ground", "the RAIL n label, the footer, the OFF end of each track", False),
    ("teal", "ground", "the locked/verified signal", False),
    ("teal", "ground-raised", "the signal on a panel", False),
    ("indigo", "ground", "the emphasised word in the headline", True),
    ("warn", "ground", "the LIVE state", False),
    ("btn-fg", "btn-bg", "the sign-in button", False),
    ("black", "ground", "the wordmark, the headline, every card and rail title", False),
]

#: The seven accents the landing page shares with the cockpit's tabs. Each one is
#: rendered as TYPE — a card's numeral on its own soft fill, and the band-head
#: marker — so each is measured at 4.5:1 rather than the 3:1 non-text bar.
#: 🔴 `--c-clay` exists because the lighter cockpit clay (#C2703D) measures 3.51:1
#: on cream and fails outright. Same defect as the 3.70:1 button, one surface over:
#: a value tuned for a 2px underline was about to be reused for 0.7rem type.
LANDING_ACCENTS = ["indigo", "teal", "violet", "green", "ochre", "clay", "plum"]

LANDING_THEMES = [("light", ":root{"), ("dark", ':root[data-theme="dark"]{')]


@pytest.mark.parametrize("theme,opener", LANDING_THEMES)
@pytest.mark.parametrize("accent", LANDING_ACCENTS)
def test_every_landing_accent_reads_on_the_ground_and_on_its_own_fill(theme, opener, accent):
    pal = palette(_landing(), opener)
    fg, soft = f"c-{accent}", f"c-{accent}-soft"
    assert fg in pal and soft in pal, f"{theme}: --{fg} or --{soft} is not declared"
    for bg, what in ((pal["ground"], "the page"),
                     (pal["ground-raised"], "a panel"),
                     (pal[soft], "its own soft fill (the card numeral)")):
        r = ratio(pal[fg], bg)
        assert r >= AA_NORMAL, (
            f"{theme}: --{fg} ({pal[fg]}) on {bg} is {r:.2f}:1, needs {AA_NORMAL} — {what}.")


def test_the_landing_page_has_no_prefers_color_scheme_branch():
    """🔴 The regression this exists to stop, 2026-08-14.

    The page used to swap to the ink palette on `prefers-color-scheme: dark`. That made
    "creamy-light by default" true of the stylesheet and false of the screen: the owner's
    Windows is in dark mode, so he opened the landing page to the dark treatment every
    time while the README described cream. Nobody had chosen dark — the OS had.

    ⭐ A default the environment can overrule is not a default. Dark is still here and
    still measured, but only `[data-theme="dark"]` reaches it, and only a button sets that.

    ⚠️ Comments are stripped before the check, because the note in the stylesheet explaining
    this decision names the media query — and a test that cannot tell a live rule from the
    comment warning against it fails on its own documentation.
    """
    src = re.sub(r"/\*.*?\*/", "", _landing(), flags=re.DOTALL)          # CSS comments
    src = re.sub(r"^\s*//.*$", "", src, flags=re.MULTILINE)              # JS line comments
    assert "prefers-color-scheme" not in src, (
        "the landing page reads the system colour scheme again — that re-opens the bug "
        "where the OS silently overrode the cream default and dark was served to a "
        "reader who never asked for it.")


@pytest.mark.parametrize("theme,opener", LANDING_THEMES)
@pytest.mark.parametrize("fg,bg,what,large", LANDING_PAIRS,
                         ids=lambda v: v if isinstance(v, str) else "")
def test_the_landing_page_reads_in_both_treatments(theme, opener, fg, bg, what, large):
    pal = palette(_landing(), opener)
    assert fg in pal and bg in pal, f"{theme}: --{fg} or --{bg} is not declared"
    r = ratio(pal[fg], pal[bg])
    need = AA_LARGE if large else AA_NORMAL
    assert r >= need, (
        f"{theme}: --{fg} ({pal[fg]}) on --{bg} ({pal[bg]}) is {r:.2f}:1, needs {need}. "
        f"That pair is {what}.")


def test_both_landing_palettes_declare_the_same_roles():
    """A role defined in one theme and missing in the other inherits the OTHER theme's value —
    which is how a dark page ends up with one cream-coloured element nobody can explain."""
    light = set(palette(_landing(), ":root{"))
    dark = set(palette(_landing(), ':root[data-theme="dark"]{'))
    # The light `:root` also carries the type and layout tokens, which the dark block has no
    # business restating; compare only what the dark block claims to own.
    assert dark <= light, f"the dark palette declares roles light does not: {sorted(dark - light)}"
    colour_roles = {"ground", "ground-raised", "sunken", "hairline", "fg", "fg-soft",
                    "dim", "dimmer", "faint", "indigo", "teal", "warn",
                    "btn-bg", "btn-fg", "btn-edge",
                    # `--black` is the role "heaviest ink on the page", so on the dark
                    # treatment it has to hold a CREAM value. Left out, the wordmark and
                    # every card title inherit #11101A onto a #12131C ground: 1.1:1.
                    "black",
                    *(f"c-{a}" for a in LANDING_ACCENTS),
                    *(f"c-{a}-soft" for a in LANDING_ACCENTS)}
    assert colour_roles <= dark, (
        f"the dark palette never overrides {sorted(colour_roles - dark)}, so those keep their "
        f"LIGHT values on a dark ground.")


# ── the cockpit ───────────────────────────────────────────────────────────────
COCKPIT_PAIRS = [
    ("ink", "paper", "body text on the page", False),
    ("ink", "surface", "body text on a card", False),
    ("ink-soft", "paper", "secondary copy", False),
    ("ink-soft", "surface", "secondary copy on a card", False),
    ("muted", "paper", "labels, metadata, the eyebrow", False),
    ("muted", "surface", "labels on a card", False),
]


@pytest.mark.parametrize("fg,bg,what,large", COCKPIT_PAIRS,
                         ids=lambda v: v if isinstance(v, str) else "")
def test_the_cockpit_neutrals_read(fg, bg, what, large):
    pal = palette(_base(), ":root {")
    r = ratio(pal[fg], pal[bg])
    need = AA_LARGE if large else AA_NORMAL
    assert r >= need, (
        f"--{fg} ({pal[fg]}) on --{bg} ({pal[bg]}) is {r:.2f}:1, needs {need} — {what}.")


def _feature_accents() -> dict[str, dict[str, str]]:
    """Every `[data-feature="x"]` block's accent trio, plus the `:root` default."""
    src = _base()
    root = palette(src, ":root {")
    out = {"courses": {k: root[k] for k in ("accent", "accent-deep", "accent-tint")}}
    for name in re.findall(r'\[data-feature="(\w+)"\]', src):
        out[name] = palette(src, f'[data-feature="{name}"]')
    return out


@pytest.mark.parametrize("feature", sorted(_feature_accents()))
def test_every_feature_accent_reads_where_it_is_actually_used(feature):
    """Three pairs per accent, and the chip is the one that catches people out.

    `.chip` is `--accent-deep` on `--accent-tint` at 0.7rem — the smallest type in the cockpit,
    on the lowest-contrast pair in the palette. `.btn-accent` is white on `--accent`, which is
    the opposite problem: a light ink chosen to look good as a tint is unreadable under white.
    """
    pal = _feature_accents()[feature]
    base = palette(_base(), ":root {")
    checks = [
        (pal["accent-deep"], pal["accent-tint"], AA_NORMAL, "the chip / current tab label"),
        # `.btn-accent` fills with `--accent-deep`. It used to fill with `--accent`, which is
        # how the clay button reached 3.70:1 — measure the pair the CSS actually renders, not
        # the one the variable is named after.
        ("#FFFFFF", pal["accent-deep"], AA_NORMAL, "white text on a filled accent button"),
        (pal["accent-deep"], base["surface"], AA_NORMAL, "an accent link on a white card"),
        # The lighter `--accent` only ever draws graphics — the 2px tab underline, the status
        # dots, the focus ring — so it answers to WCAG's non-text bar of 3:1, not 4.5:1.
        (pal["accent"], base["surface"], AA_LARGE, "the tab underline and dots on a card"),
        (pal["accent"], base["paper"], AA_LARGE, "the tab underline and dots on the page"),
    ]
    for fg, bg, need, what in checks:
        r = ratio(fg, bg)
        assert r >= need, (
            f"{feature}: {fg} on {bg} is {r:.2f}:1, needs {need} — {what}.")


def test_danger_reads_and_is_not_reused_as_an_accent():
    """🔴 Danger red is spent only on things that destroy something (DESIGN_DIRECTION §2.1).
    If a feature accent ever equals it, a red button stops meaning what it means."""
    base = palette(_base(), ":root {")
    assert ratio("#FFFFFF", base["danger"]) >= AA_NORMAL, "white on a danger button"
    assert ratio(base["danger"], base["danger-tint"]) >= AA_NORMAL, "danger text on its tint"

    danger = base["danger"].lower()
    for feature, pal in _feature_accents().items():
        assert pal["accent"].lower() != danger, (
            f"the {feature} accent is the danger colour — a red control has to mean one thing.")
