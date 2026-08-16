"""Which language this request is rendered in, and where that choice is kept.

Two stores, and which one wins is the whole design:

| store | who it is for | lives for |
|---|---|---|
| `Professor.language` | a signed-in professor | forever, on every device |
| the `musai_lang` cookie | a visitor who has not signed in yet | this browser |

**The column wins whenever there is one.** The cookie exists only because the landing page is
read before MUSAI knows who is reading it — a colleague who clicks *Español* and then signs in
must not be thrown back into English by the act of signing in, which is the moment the setting
would look most broken.

🔴 **`Professor.language` is nullable and `NULL` means "never chose", not "chose English".**
This project has paid for that distinction twice: an unowned course read as everybody's, and a
landing page documented "creamy-light by default" while dark-mode Windows served ink every
time. ⭐ **A stored choice outranks a changed default forever.** If MUSAI ever defaults to
Spanish — which for a Mexican university is a fair argument — every professor who actively
picked English must still get English, and only a nullable column can tell those two apart from
each other.

⚠️ **No `Accept-Language` sniffing, deliberately.** The browser of a Mexican professor says
`es-MX` almost without exception, so sniffing would make Spanish the real default while the
documentation said English — the exact shape of the bug where a page is *documented*
creamy-light and *served* dark. The default is English because that is what MUSAI is written
in; anything else is a choice someone made, on the record.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session

from musai import i18n

router = APIRouter(tags=["language"])

#: The pre-sign-in store. A display preference, so it is deliberately not in the signed session
#: cookie: it must survive `auth_configured` being false, when `SessionMiddleware` is not even
#: installed and `request.session` raises.
COOKIE = "musai_lang"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365


def from_cookie(request: Request) -> Optional[str]:
    """The choice this browser made before signing in, if it made one."""
    try:
        return i18n.normalize(request.cookies.get(COOKIE))
    except Exception:                                             # noqa: BLE001
        return None


def remember(request: Request, lang: str) -> None:
    """Pin the resolved language onto this request so one page is rendered in one language.

    Every `t()` in a template asks for the language, and a template can render the nav twice.
    Resolving once per request also means a professor who changes it mid-request (they cannot,
    but a future route could) never gets half a page in each.
    """
    request.state.lang = lang


def current(request: Request) -> str:
    """The language for this request. Never raises — every template calls it.

    Resolution order, and each step exists for a reason:

    1. `request.state.lang` — set by `current_professor()` on any signed-in page, so the common
       case costs no query at all.
    2. the cookie — the landing page, and the first render after a choice.
    3. `i18n.DEFAULT` — English.
    """
    pinned = getattr(request.state, "lang", None)
    if pinned:
        return pinned
    lang = from_cookie(request) or i18n.DEFAULT
    remember(request, lang)
    return lang


def for_professor(request: Request, prof, sess: Session) -> str:
    """Resolve and pin the language for a signed-in professor, adopting a pre-sign-in choice.

    ⭐ The adoption is the part worth reading. A colleague lands on the marketing page, clicks
    *Español*, signs in — and at that instant MUSAI learns who they are and finds `language`
    NULL. Without this, the page they are looking at would flip back to English *because* they
    signed in. So a cookie choice made while signed out is written to the column the first time
    MUSAI can attribute it, and from then on it follows them to any device.

    🔴 It only ever fills a `NULL`. It cannot overwrite a stored choice, because a stale cookie
    on a shared machine must never be able to change what somebody else picked in Settings.
    """
    stored = i18n.normalize(getattr(prof, "language", None))
    if stored is None:
        chosen = from_cookie(request)
        if chosen:
            prof.language = chosen
            sess.add(prof)
            sess.commit()
            stored = chosen
    lang = stored or i18n.DEFAULT
    remember(request, lang)
    return lang


def set_cookie(response, lang: str) -> None:
    """Write the browser-local copy of the choice. Same value the column holds, when there is one.

    Kept in step with the column rather than dropped once a professor exists: the cookie is what
    renders `/` for them *after they sign out*, and a sign-out that silently reverts the app to
    English reads as the setting having been forgotten.
    """
    response.set_cookie(
        COOKIE, lang, max_age=COOKIE_MAX_AGE, samesite="lax", httponly=False, path="/",
    )


@router.get("/lang/{code}")
def choose(request: Request, code: str, next: str = "/"):
    """Switch language and go back to the page you were reading.

    🔴 **Public**, and `musai/web/auth.py` lists `/lang/` on `PUBLIC_PREFIXES` for it. That is a
    hole in a default-deny gate and it earns its exemption narrowly: the landing page is read
    signed out, so the picker has to work signed out, and this route **reads nothing**. It
    writes one of two constant strings — `i18n.normalize` drops everything else, so `/lang/fr`
    and `/lang/'; DROP` both fall back to English rather than reaching a store — and it answers
    with a redirect to a path `_safe_next` has already confirmed is same-site.

    An unknown code is a silent fall back to `DEFAULT` rather than a 404: this is a preference
    toggle, and a professor who lands here from a stale bookmark should get a working page in a
    real language, not an error about a URL they did not type.
    """
    from musai.web import auth as auth_mod

    lang = i18n.normalize(code) or i18n.DEFAULT
    response = RedirectResponse(url=auth_mod._safe_next(next), status_code=303)
    set_cookie(response, lang)

    # Signed in, so the choice must outlive the browser. Signed out, the cookie above is the
    # whole store until `for_professor()` adopts it at sign-in.
    #
    # ⚠️ `current_professor` (get-or-create), not a read-only lookup. A professor whose row does
    # not exist yet — signed in, but not having loaded a gated page — would otherwise have the
    # write silently do nothing, and only find out on their next device. The session is signed
    # and has already passed `_gate()`, so this is the same identity every other page mints.
    if auth_mod.current_user(request):
        from musai.db import engine
        from musai.web.deps import current_professor

        with Session(engine) as sess:
            prof = current_professor(request, sess)
            prof.language = lang
            sess.add(prof)
            sess.commit()
    remember(request, lang)
    return response
