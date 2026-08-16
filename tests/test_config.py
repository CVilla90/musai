"""Sanity checks on the settings schema."""

from musai.config import settings


def test_dry_run_default():
    # DRY_RUN must default to True — the most important safety rail
    assert settings.dry_run is True


def test_sega_fallback_to_uach_creds():
    # When SEGA creds not set, they fall back to UACH creds (same SSO)
    if not settings.uach_username:
        return  # no env loaded — skip
    assert settings.sega_username == settings.uach_username
