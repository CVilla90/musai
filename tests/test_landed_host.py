"""The landed-host rail: a write must never guess which Moodle it is aimed at.

🔴 Every entry point used to end its tile lookup with ``host = host or "virtual3.uach.mx"``.
That default was correct for every course MUSAI had touched — and the Enfermería job is the
first one where it is not. Measured 2026-08-22 on the colleague's own Tablero: nine tiles,
four on VIRTUAL3 (three of them courses MUSAI itself built) and five on AULAS1. One tile whose
``data-server`` failed to parse would have aimed "hide everything in this course" at a live
English course instead, under her name.

These tests use a fake page rather than a browser, because the property under test is
"what does it do when the read fails", and a real browser cannot be made to fail on demand.
"""

import pytest

from musai.automation.moodle_export import HostUnresolved, resolve_landed_host


class FakePage:
    """Minimal stand-in: a URL, a wwwroot, and the ability to make either unreadable."""

    def __init__(self, url="", wwwroot="", url_raises=False, eval_raises=False):
        self._url, self._wwwroot = url, wwwroot
        self._url_raises, self._eval_raises = url_raises, eval_raises

    @property
    def url(self):
        if self._url_raises:
            raise RuntimeError("page closed")
        return self._url

    def evaluate(self, _js):
        if self._eval_raises:
            raise RuntimeError("execution context destroyed")
        return self._wwwroot


AULAS1 = "https://aulas1.uach.mx/course/view.php?id=6253"
VIRTUAL3 = "https://virtual3.uach.mx/course/view.php?id=9069"


def test_reads_the_host_actually_landed_on():
    page = FakePage(url=AULAS1, wwwroot="https://aulas1.uach.mx")
    assert resolve_landed_host(page, hint="aulas1.uach.mx", idc="6253") == "aulas1.uach.mx"


def test_unreadable_host_refuses_instead_of_defaulting_to_virtual3():
    """The regression that matters. Nothing readable ⇒ stop, never pick a real server."""
    page = FakePage(url_raises=True, eval_raises=True)
    with pytest.raises(HostUnresolved) as e:
        resolve_landed_host(page, hint=None, idc="6253")
    assert "virtual3.uach.mx" in str(e.value)  # names what it is refusing to assume


def test_missing_data_server_no_longer_means_virtual3():
    """A tile with no data-server is exactly the accident case: the hint is empty, but the
    browser is standing on aulas1, and that is what must be used."""
    page = FakePage(url=AULAS1, wwwroot="https://aulas1.uach.mx")
    assert resolve_landed_host(page, hint=None, idc="6253") == "aulas1.uach.mx"


def test_disagreement_between_url_and_wwwroot_refuses():
    """Two instruments, and they must corroborate. A redirect we do not understand is not a
    thing to resolve by preference."""
    page = FakePage(url=AULAS1, wwwroot="https://virtual3.uach.mx")
    with pytest.raises(HostUnresolved) as e:
        resolve_landed_host(page, idc="6253")
    assert "aulas1.uach.mx" in str(e.value) and "virtual3.uach.mx" in str(e.value)


def test_landing_beats_a_lying_tile():
    """The tile is a claim made before navigating; the URL is a fact measured after."""
    page = FakePage(url=AULAS1, wwwroot="https://aulas1.uach.mx")
    assert resolve_landed_host(page, hint="virtual3.uach.mx", idc="6253") == "aulas1.uach.mx"


def test_either_instrument_alone_is_enough():
    assert resolve_landed_host(FakePage(url=VIRTUAL3, eval_raises=True)) == "virtual3.uach.mx"
    assert resolve_landed_host(FakePage(url_raises=True,
                                        wwwroot="https://aulas1.uach.mx")) == "aulas1.uach.mx"


def test_no_writer_still_carries_the_old_default():
    """Scope the system, not the file — the fallback lived in five places, not one."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent / "musai"
    offenders = []
    for py in root.rglob("*.py"):
        for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"') or "``" in line:
                continue  # prose about the fix, not the fix undone
            if 'or "virtual3.uach.mx"' in line or 'host = "virtual3' in line:
                offenders.append(f"{py.relative_to(root)}:{i}")
    assert not offenders, f"the guessed-host default is back in: {offenders}"
