"""The Course Hub — a course's home page, generated from a form filled in ONCE.

The owner hand-wrote the original: ~200 lines of inline-styled HTML that looked great and could
not be handed to anyone else. His phone number appeared three times, the WhatsApp group link
twice, his photo twice, and every visible string was welded into the markup. "Make this yours"
meant editing HTML in eleven places and hoping you found them all.

Here the same page is a *function of a dict*. Anything repeated is typed once, and `FIELDS`
drives the web form, the validation and the documentation together — adding a field is one
entry here, not four edits spread across the app.

Two scopes, and the split is the whole point:

  * **profile** — who the professor is (name, photo, phone, bio). Typed once; every one of
    their courses reuses it. A new group costs zero re-typing.
  * **course** — what genuinely differs per group (title, that group's chat link, contents,
    weights).

**Printed once, too.** Typing the phone number once was only half of what the owner asked for.
Until 2026-08-10 the page still *printed* it three times (header pill, contact card, professor
card) and the group link twice — so a professor comparing their page against their phone had
three things to check and no way to know they were one field. The number and the group link
now render in **exactly one block**, and `test_the_phone_is_printed_in_exactly_one_place`
holds the line. Same reasoning retired the duplicate `help_instructions`.

Moodle-safety is render.py's contract: inline `style=` only, no JavaScript, no `class=`,
every professor-supplied string escaped. Two deliberate departures from the original:

  * **No `onerror=` image fallbacks.** Inline JS in a page students load is exactly what that
    rail exists for, and a fallback that needs scripts to run is not a fallback. Missing photo
    and missing logo are handled *server-side* here — the initials avatar is real markup.
  * **`clamp()` only where a phone would otherwise suffer.** Session 1 avoided fluid type as
    "one more declaration a sanitizer could quietly drop". That was a guess, and Vellum's two
    published books have since settled it: ~2,600 inline declarations survive this Moodle,
    clamp-sized headings included. So the few places a fixed value actually hurts on a 360px
    screen — the hero title, the professor's name, the outer padding — are fluid; everything
    else stays a plain px, because the layout does its own reflowing with wrapping flex and an
    auto-fit grid, and a declaration that buys nothing is still a declaration that can break.
"""

import re
from typing import Any, NamedTuple, Optional

from musai.coursebuild.render import _esc as esc, lint, marker

# Stable block id → republishing EDITS the same label instead of stacking a second copy.
# Never make this depend on the course or the professor; that is what makes an edit an edit.
BLOCK_ID = "course-hub"


# ── the field spec: one source of truth for the form, the merge and the docs ─────────────

class HubField(NamedTuple):
    key: str
    scope: str            # "profile" (typed once, all courses) | "course" (this group only)
    kind: str             # text | textarea | lines | url | phone | choice
    label: str            # Spanish: the reader of this form is a professor, not a developer
    help: str = ""
    rows: int = 3         # textarea/lines height
    choices: tuple = ()


FIELDS: tuple[HubField, ...] = (
    # — profile —
    HubField("professor_name", "profile", "text", "Tu nombre",
             "Como quieres que los estudiantes te vean."),
    HubField("professor_role", "profile", "text", "Tu puesto o título",
             "Ej.: Profesora de Inglés · Facultad de Ciencias de la Cultura Física."),
    HubField("photo_url", "profile", "url", "Foto (enlace)",
             "Opcional. Si la dejas vacía se dibujan tus iniciales en un círculo."),
    HubField("whatsapp_phone", "profile", "phone", "Tu WhatsApp",
             "El ÚNICO lugar donde vive tu número: se escribe aquí, sale en «Ayuda y "
             "contacto» y en ningún otro sitio de la página."),
    HubField("bio", "profile", "textarea", "Sobre ti",
             "Dos o tres líneas. Se ve al desplegar «Conoce a tu profesor/a».", rows=4),
    HubField("expectations", "profile", "lines", "Qué pueden esperar de ti",
             "Una por línea, máximo 6.", rows=4),
    HubField("promise", "profile", "text", "Tu promesa (una línea)",
             "La frase cálida del final. Déjala vacía si no la quieres."),
    HubField("help_instructions", "profile", "textarea", "Cómo pedirte ayuda",
             "Qué debe incluir el mensaje para que puedas responder rápido.", rows=3),
    HubField("institution", "profile", "text", "Institución",
             "Se muestra como texto si no pones logo."),
    HubField("logo_url", "profile", "url", "Logo de la institución (enlace)",
             "Opcional. Súbelo a Moodle y pega aquí su enlace."),

    # — course —
    HubField("course_title", "course", "text", "Título del curso",
             "Si lo dejas vacío se usa el nombre del grupo."),
    HubField("course_tagline", "course", "text", "Frase bajo el título",
             "Ej.: Todo lo importante en un solo lugar."),
    HubField("whatsapp_group_url", "course", "url", "Grupo de WhatsApp de este grupo",
             "El enlace «Invitar al grupo» de WhatsApp (https://chat.whatsapp.com/…). Es "
             "distinto en cada grupo — por eso vive aquí y no en tu perfil."),
    HubField("content_badge", "course", "text", "Etiqueta del contenido",
             "La pastilla pequeña junto a «Contenido». Ej.: Nivel A1."),
    HubField("description", "course", "textarea", "De qué trata el curso",
             "Dos o tres líneas.", rows=4),
    HubField("terms", "course", "lines", "Temario por parcial",
             "Una línea por parcial:  Parcial 1 | tema · tema · tema", rows=6),
    HubField("grading", "course", "lines", "Ponderación",
             "Una línea por rubro:  Actividades | 60 | tareas semanales en Moodle", rows=4),
    HubField("where_to_find", "course", "lines", "Dónde encontrar las cosas",
             "Una línea por punto:  Calificaciones | Moodle → Calificaciones", rows=4),
    HubField("lang", "course", "choice", "Idioma de la página",
             "Cambia los títulos fijos (Contenido, Ponderación…), no lo que tú escribes. "
             "En «Bilingüe» el inglés va primero y el español debajo, más pequeño y tenue — "
             "y en TU texto puedes hacer lo mismo escribiendo  English ~~español~~.",
             choices=("es", "en", "bilingual")),
    HubField("theme", "course", "choice", "Color",
             "", choices=("teal-blue", "indigo", "amber", "green", "wine")),
)

# Dropdowns show words, not slugs — `teal-blue` is our key, never the professor's problem.
CHOICE_LABELS = {
    "lang": {"es": "Español", "en": "English",
             "bilingual": "Bilingüe — inglés primero, español debajo"},
    "theme": {"teal-blue": "Turquesa y azul", "indigo": "Índigo", "amber": "Ámbar",
              "green": "Verde", "wine": "Vino"},
}

BY_KEY = {f.key: f for f in FIELDS}
PROFILE_KEYS = tuple(f.key for f in FIELDS if f.scope == "profile")
COURSE_KEYS = tuple(f.key for f in FIELDS if f.scope == "course")

# A complete, impersonal starting point: a professor who changes nothing still gets a page
# that reads sensibly, and every placeholder is obviously a placeholder.
DEFAULTS: dict[str, str] = {
    "professor_name": "",
    "professor_role": "",
    "photo_url": "",
    "whatsapp_phone": "",
    "bio": "",
    "expectations": "",
    "promise": "",
    "help_instructions": "",
    "institution": "UACH",
    "logo_url": "",
    "course_title": "",
    "course_tagline": "",
    "whatsapp_group_url": "",
    "content_badge": "",
    "description": "",
    "terms": "",
    "grading": "",
    "where_to_find": "",
    "lang": "es",
    "theme": "teal-blue",
}


# ── fixed chrome, in the professor's language ───────────────────────────────────────────

STRINGS = {
    "es": {
        "hub": "Inicio del curso",
        "content": "Contenido del curso",
        "grading": "Ponderación",
        "help": "Ayuda y contacto",
        "contact": "Contacto:",
        "join": "Entrar al grupo de WhatsApp ↗",
        "open_group": "Abrir el grupo ↗",
        "fastest": "La forma más rápida de contactarme",
        "meet": "Conoce a tu profesor/a",
        "expand": "Toca para ver más",
        "more": "Más",
        "about": "Un poco sobre mí",
        "expect": "En este curso puedes esperar",
        "fastest_help": "La forma más rápida de recibir ayuda",
        "where": "Dónde encontrar las cosas",
        "need_help": "¿Necesitas ayuda?",
        "professor": "Tu profesor/a",
        "collapse": "Toca el encabezado otra vez para cerrar",
        "whatsapp": "WhatsApp:",
        "group": "Grupo de WhatsApp del curso",
        "group_pending": "Próximamente",
        "group_pending_note": "Te compartiré el enlace aquí mismo.",
    },
    "en": {
        "hub": "Course Hub",
        "content": "Course content",
        "grading": "Grading",
        "help": "Help & contact",
        "contact": "Contact:",
        "join": "Join the WhatsApp group ↗",
        "open_group": "Open the group ↗",
        "fastest": "Fastest way to reach me",
        "meet": "Meet your professor",
        "expand": "Tap to expand",
        "more": "More",
        "about": "A little about me",
        "expect": "In this course, you can expect",
        "fastest_help": "Fastest way to get help",
        "where": "Where to find things",
        "need_help": "Need help?",
        "professor": "Your professor",
        "collapse": "Click the header again to collapse",
        "whatsapp": "WhatsApp:",
        "group": "Course WhatsApp group",
        "group_pending": "Coming soon",
        "group_pending_note": "I will share the link right here.",
    },
}

# 🔴 The bilingual table is DERIVED, never typed. Two hand-maintained tables for the same
# eighteen labels is how one of them quietly falls a string behind the other; here a new
# entry in "en" and "es" is a new bilingual entry for free, and a key that exists in only
# one of them raises at import time rather than rendering the word "None" to a student.
def _bilingual(english: str, spanish: str) -> str:
    """`"Open the group ↗"` + `"Abrir el grupo ↗"` -> one label with a subordinate Spanish half.

    Two small rules, both from looking at the render: the trailing ↗ is an icon rather than a
    word, so repeating it prints what looks like two links; and a label that is the same in
    both languages ("WhatsApp:") must not be glossed against itself.
    """
    spanish = spanish.removesuffix(" ↗").strip()
    return english if spanish.casefold() == english.removesuffix(" ↗").casefold() \
        else f"{english} ~~{spanish}~~"


STRINGS["bilingual"] = {key: _bilingual(english, STRINGS["es"][key])
                        for key, english in STRINGS["en"].items()}

# The Spanish half of a bilingual label: dimmed and smaller, but NOT recoloured. Lifted
# verbatim from Vellum's `theme.SECONDARY_STYLE`, and for the same hard-won reason — a
# hard-coded grey reads fine on a white card and is almost invisible on the dark contact
# block, so the dimming has to be `opacity`, which composes with whatever it sits on.
# Sized in `em` so it stays proportional inside a 22px header and a 12px caption alike.
SECONDARY_STYLE = "opacity:.72;font-weight:400;font-size:.86em"

# The professor picks a mood; we own the hex. Same rule as render.PALETTES.
THEMES = {
    "teal-blue": {"a": "#0ea5a4", "b": "#2563eb", "link": "#1d4ed8",
                  "stripes": ("#2563eb", "#0ea5a4", "#f59e0b")},
    "indigo":    {"a": "#6366f1", "b": "#4338ca", "link": "#4338ca",
                  "stripes": ("#4338ca", "#6366f1", "#f59e0b")},
    "amber":     {"a": "#f59e0b", "b": "#b45309", "link": "#b45309",
                  "stripes": ("#b45309", "#f59e0b", "#0f766e")},
    "green":     {"a": "#10b981", "b": "#047857", "link": "#047857",
                  "stripes": ("#047857", "#10b981", "#f59e0b")},
    "wine":      {"a": "#e11d48", "b": "#9f1239", "link": "#9f1239",
                  "stripes": ("#9f1239", "#e11d48", "#f59e0b")},
}
DEFAULT_THEME = "teal-blue"

# Rotating tones for the ponderación rows, so a professor never picks a colour per row.
GRADE_TONES = (
    ("#eff6ff", "#dbeafe", "#1d4ed8", "#2563eb"),
    ("#fff7ed", "#fed7aa", "#c2410c", "#f97316"),
    ("#f0fdf4", "#bbf7d0", "#166534", "#22c55e"),
    ("#faf5ff", "#e9d5ff", "#6b21a8", "#a855f7"),
    ("#f1f5f9", "#e2e8f0", "#334155", "#64748b"),
)

CARD = ("background:#ffffff;border-radius:14px;padding:18px;"
        "box-shadow:0 6px 16px rgba(0,0,0,.06);border:1px solid #e5e7eb")
CARD_TITLE = "font-weight:900;color:#0f172a;font-size:16px"

# A short note to whoever opens this page in Moodle's HTML editor. It names WHERE to change
# things and never WHAT they currently are: the owner's own hand-written version repeated the phone
# number twice more inside comments, which quietly undid `_contact_block`'s one-place rule —
# `test_the_phone_is_printed_in_exactly_one_place` counts the whole document, comments included,
# and would have failed. It also carries the one trap that actually bit: the photo is a single
# `data:` URI pasted into TWO <img> tags, and replacing it in one (or in neither, leaving the
# placeholder) is how 1-LED-A spent an afternoon serving a broken image to its students.
EDIT_NOTE = (
    "<!-- Generated by MUSAI from the Course Hub form. Edit it there and republish: every value "
    "below is typed once and printed once, and a hand-edit here is overwritten on the next "
    "publish.\n     If you must edit by hand: the professor's name, phone, bio and the WhatsApp "
    "group link are the only things meant to change.\n     TRAP: the photo is ONE data:image "
    "URI pasted into TWO image tags (search for data:image). Replace it in both or in "
    "neither — never leave a placeholder, it renders as a broken image. -->")

MAX_TERMS, MAX_GRADING, MAX_WHERE, MAX_EXPECT = 8, 6, 6, 6


# ── input handling ──────────────────────────────────────────────────────────────────────

_GLOSS = re.compile(r"~~(.+?)~~", re.S)


def fmt(text: Any) -> str:
    """Escape, then turn `English ~~español~~` into English + a smaller, dimmer Spanish.

    The one piece of markup a professor gets, and it exists because the owner's house style for
    student-facing material is English first with the Spanish subordinate underneath. It is
    deliberately NOT gated on `lang`: the flag chooses our fixed chrome's language, but the
    professor's own sentence carries its own gloss wherever they typed one.

    Escaping happens FIRST and the span is added after, so `~~<script>~~` glosses the escaped
    text and can never open a tag. `~` survives `html.escape` untouched, which is what makes
    that ordering safe rather than lucky.

    Only ever call this in a text node. In an attribute (`alt=`, `href=`) use `esc`.
    """
    return _GLOSS.sub(
        lambda m: f'<span style="{SECONDARY_STYLE}">{m.group(1)}</span>', esc(text))


def _split_gloss(text: str) -> tuple[str, str]:
    """`"English ~~español~~"` -> `("English", "español")`, both still unescaped.

    `fmt` renders a gloss *inline*, which is right for a heading or a sentence and wrong for
    the temario, where each side is a dozen topics and the two runs merge into one blob. Only
    the content card uses this; everywhere else the inline span is the house style.
    """
    m = _GLOSS.search(str(text or ""))
    if not m:
        return str(text or "").strip(), ""
    return (str(text or "")[:m.start()].strip(), m.group(1).strip())


def _lines(text: str, limit: int) -> list[str]:
    return [ln.strip() for ln in str(text or "").splitlines() if ln.strip()][:limit]


def _rows(text: str, cells: int, limit: int) -> list[list[str]]:
    """`a | b | c` per line, padded to `cells` so a renderer never index-errors on a typo."""
    out = []
    for line in _lines(text, limit):
        parts = [p.strip() for p in line.split("|")]
        out.append((parts + [""] * cells)[:cells])
    return out


def _safe_url(url: str) -> str:
    """Only https links leave this module. Anything else becomes no link at all.

    The professor types this box, so it is untrusted input: `javascript:` in a page a hundred
    students open is the one thing here that could actually hurt someone.
    """
    u = str(url or "").strip()
    return u if re.match(r"^https://[^\s\"'<>]+$", u) else ""


def _safe_img(url: str) -> str:
    """Images may also be inline data: URIs — proven to survive this Moodle and same-origin."""
    u = str(url or "").strip()
    if re.match(r"^data:image/(png|jpeg|jpg|gif|webp);base64,[A-Za-z0-9+/=]+$", u):
        return u
    return _safe_url(u)


def _digits(phone: str) -> str:
    return re.sub(r"\D", "", str(phone or ""))


def _wa_me(phone: str) -> str:
    """A tappable link from the same number that is printed. Typed once, used twice."""
    d = _digits(phone)
    return f"https://wa.me/{d}" if 8 <= len(d) <= 15 else ""


def _pct(value: str) -> Optional[int]:
    m = re.search(r"\d+", str(value or ""))
    if not m:
        return None
    return max(0, min(100, int(m.group())))


def _initials(name: str) -> str:
    parts = [p for p in re.split(r"\s+", str(name or "").strip()) if p]
    return "".join(p[0].upper() for p in parts[:2]) or "?"


def theme(name: str) -> dict:
    return THEMES.get(str(name or "").lower(), THEMES[DEFAULT_THEME])


def strings(lang: str) -> dict:
    return STRINGS.get(str(lang or "").lower(), STRINGS["es"])


def resolve(profile: Optional[dict] = None, course_data: Optional[dict] = None,
            *, course: Any = None) -> dict:
    """DEFAULTS ← profile ← this course. Only non-empty values override, so a half-filled
    course form never blanks out the profile the professor already completed."""
    out = dict(DEFAULTS)
    for source in (profile or {}, course_data or {}):
        for key, value in source.items():
            if key in DEFAULTS and str(value or "").strip():
                out[key] = str(value).strip()
    if course is not None and not out["course_title"]:
        subject = getattr(course, "subject", "") or ""
        group = getattr(course, "group_code", "") or ""
        out["course_title"] = " · ".join(p for p in (subject, group) if p)
    return out


def validate(data: dict) -> list[str]:
    """Warnings for the form — things worth telling a professor, none of them fatal."""
    notes = []
    if not str(data.get("professor_name", "")).strip():
        notes.append("Falta tu nombre: la tarjeta dirá solo «Tu profesor/a».")
    # Contact is the one thing whose *absence* is invisible on the rendered page: the block
    # simply does not appear, so the page looks finished and a student has no way to reach
    # anyone. The professor has to be told here, because the preview cannot show them a gap.
    if not str(data.get("whatsapp_phone", "")).strip():
        notes.append("Sin WhatsApp la página no muestra ninguna forma de contactarte. "
                     "Se escribe una sola vez, en «Tu WhatsApp».")
    elif not _wa_me(data.get("whatsapp_phone", "")):
        notes.append("Ese teléfono no parece completo — incluye la lada (ej. +52 614 123 4567).")
    if not str(data.get("whatsapp_group_url", "")).strip():
        notes.append("Falta el enlace del grupo de WhatsApp de este grupo: mientras tanto la "
                     "página muestra «Próximamente» a los estudiantes, no un botón que "
                     "funcione.")
    for key in ("photo_url", "logo_url", "whatsapp_group_url"):
        raw = str(data.get(key, "")).strip()
        if raw and not (_safe_img(raw) if key.endswith("photo_url") or key == "logo_url"
                        else _safe_url(raw)):
            notes.append(f"«{BY_KEY[key].label}» se ignoró: solo se aceptan enlaces https://.")
    weights = [p for p in (_pct(r[1]) for r in _rows(data.get("grading", ""), 3, MAX_GRADING))
               if p is not None]
    if weights and sum(weights) != 100:
        notes.append(f"La ponderación suma {sum(weights)}%, no 100%.")
    return notes


# ── the page ────────────────────────────────────────────────────────────────────────────

def _photo(url: str, name: str, *, size: int, radius: str, border: str) -> str:
    """One photo field, two sizes, and a real fallback — no script involved."""
    src = _safe_img(url)
    if src:
        return (f'<img src="{esc(src)}" width="{size}" height="{size}" alt="{esc(name)}" '
                f'style="border-radius:{radius};object-fit:cover;display:inline-block;'
                f'border:{border}" />')
    return (f'<span style="display:inline-flex;align-items:center;justify-content:center;'
            f'width:{size}px;height:{size}px;border-radius:{radius};background:#e2e8f0;'
            f'color:#0f172a;font-weight:900;font-size:{max(14, size // 3)}px;border:{border}">'
            f'{esc(_initials(name))}</span>')


def _header(d: dict, t: dict, s: dict) -> str:
    logo = _safe_img(d["logo_url"])
    if logo:
        brand = (f'<img src="{esc(logo)}" width="56" height="56" alt="{esc(d["institution"])}" '
                 f'style="border-radius:12px;background:#ffffff;padding:6px;'
                 f'display:inline-block;object-fit:contain" />')
    elif d["institution"]:
        brand = ('<span style="display:inline-block;font-weight:900;color:#0f172a;'
                 'background:#ffffff;border-radius:12px;padding:10px 12px">'
                 f'{esc(d["institution"])}</span>')
    else:
        brand = ""

    # No contact details here, on purpose. The phone and the group link live in exactly one
    # block — `_card_contact` — so that "change it in one place" is true of the page a
    # professor is *looking at*, not only of the form they filled in.
    parts = [
        '<div style="display:flex;gap:14px;align-items:center;'
        'flex-wrap:wrap;padding:14px 16px;border-radius:12px;'
        f'background:linear-gradient(135deg,{t["a"]},{t["b"]});color:#ffffff">',
        brand, '<div style="flex:1;min-width:200px">',
        '<div style="font-weight:900;letter-spacing:.2px;color:#ffffff;'
        f'font-size:clamp(19px,4.4vw,22px)">{fmt(d["course_title"] or s["hub"])}</div>',
    ]
    if d["course_tagline"]:
        parts.append('<div style="opacity:.92;color:#ffffff;font-size:14px">'
                     f'{fmt(d["course_tagline"])}</div>')
    parts.append("</div></div>")
    return "".join(parts)


def _card_content(d: dict, t: dict, s: dict) -> str:
    terms = _rows(d["terms"], 2, MAX_TERMS)
    if not (d["description"] or terms or d["content_badge"]):
        return ""
    parts = [f'<div style="{CARD}">',
             '<div style="display:flex;align-items:center;justify-content:space-between;'
             'gap:10px;flex-wrap:wrap">',
             f'<div style="{CARD_TITLE}">{fmt(s["content"])}</div>']
    if d["content_badge"]:
        parts.append('<span style="font-size:12px;padding:4px 8px;border-radius:999px;'
                     'background:#ecfeff;color:#0f766e;border:1px solid #a5f3fc;'
                     f'font-weight:900">{fmt(d["content_badge"])}</span>')
    parts.append("</div>")
    if d["description"]:
        parts.append('<div style="margin-top:10px;padding:10px;border-radius:12px;'
                     'background:#f8fafc;border:1px dashed #cbd5e1;color:#334155;'
                     f'font-size:14px">{fmt(d["description"])}</div>')
    if terms:
        # The terms sit SIDE BY SIDE once there is room. Stacked, each one is a 14-item run-on
        # sentence the full width of the card, and the card was the tallest thing on the page by
        # a factor of two. Three columns turn the temario into something a student scans instead
        # of reads — and it is what lets this card be full-width without a wall of long lines.
        # ⚠️ The basis is a READABILITY FLOOR, not a breakpoint aimed at one screen. 190px is
        # about 26 characters at 14px — the narrowest a column of short `·`-separated topics
        # still reads — and picking the floor rather than a target width is what keeps three
        # columns at every width Moodle actually gives this page (940px inside the card with the
        # nav drawer closed, 782px with it open, 624px at the last width before the page itself
        # goes single-column). It costs nothing at the wide end: `1fr` stretches the columns to
        # 304px and 251px there regardless of the minimum.
        #
        # ⚠️ Unlike the cards below, an uneven LAST ROW here is accepted. The term count is data
        # (three today, up to MAX_TERMS), so no arrangement can be orphan-proof for every count —
        # and these are list blocks with a coloured rule, not cards. A short final column reads
        # as ordinary multi-column text; a short final *card* reads as a hole in the page.
        parts.append('<div style="margin-top:12px;display:grid;'
                     'grid-template-columns:repeat(auto-fit,minmax(min(190px,100%),1fr));'
                     'gap:14px;font-size:14px">')
        for i, (label, topics) in enumerate(terms):
            colour = t["stripes"][i % len(t["stripes"])]
            english, spanish = _split_gloss(topics)
            parts.append(f'<div style="border-left:4px solid {colour};padding-left:12px">'
                         '<div style="font-weight:900;color:#0f172a;margin-bottom:4px">'
                         f'{fmt(label)}</div>'
                         f'<div style="color:#334155">{esc(english)}</div>')
            if spanish:
                parts.append(f'<div style="color:#334155;margin-top:4px;{SECONDARY_STYLE}">'
                             f'{esc(spanish)}</div>')
            parts.append("</div>")
        parts.append("</div>")
    parts.append("</div>")
    return "".join(parts)


def _card_grading(d: dict, t: dict, s: dict) -> str:
    rows = _rows(d["grading"], 3, MAX_GRADING)
    if not rows:
        return ""
    parts = [f'<div style="{CARD}">',
             f'<div style="{CARD_TITLE}">{fmt(s["grading"])}</div>']
    for i, (label, raw_pct, note) in enumerate(rows):
        bg, border, ink, bar = GRADE_TONES[i % len(GRADE_TONES)]
        pct = _pct(raw_pct)
        parts.append(f'<div style="margin-top:10px;padding:10px 12px;border-radius:12px;'
                     f'background:{bg};border:1px solid {border};color:#0f172a">'
                     '<div style="display:flex;justify-content:space-between;'
                     'align-items:center;gap:10px">'
                     f'<span style="font-weight:900;color:#0f172a">{fmt(label)}</span>')
        if pct is not None:
            parts.append(f'<span style="font-weight:900;color:{ink}">{pct}%</span>')
        parts.append("</div>")
        if note:
            parts.append('<div style="font-size:12px;opacity:.9;margin-top:4px;'
                         f'color:#334155">{fmt(note)}</div>')
        if pct is not None:
            parts.append('<div style="height:8px;background:#e5e7eb;border-radius:999px;'
                         'overflow:hidden;margin-top:8px">'
                         f'<div style="height:8px;width:{pct}%;background:{bar}"></div></div>')
        parts.append("</div>")
    parts.append("</div>")
    return "".join(parts)


def _contact_block(d: dict, s: dict) -> str:
    """🔴 THE one place the WhatsApp number and the group link are printed.

    Two rows, each labelled, because they are two different things a student gets wrong:
    the number is *the professor* (profile scope — same in all their groups) and the link is
    *this group's* chat (course scope). Anything that wants to show either of them again
    should link here instead; a second copy is a second thing to update.
    """
    phone, group = d["whatsapp_phone"], _safe_url(d["whatsapp_group_url"])
    if not (phone or group):
        return ""

    def group_row(inner: str) -> str:
        return ('<div style="margin-top:12px;padding-top:12px;'
                'border-top:1px solid rgba(255,255,255,.14)">'
                f'<div style="font-size:13px;opacity:.9;color:#e5e7eb">{fmt(s["group"])}</div>'
                f'<div style="margin-top:8px">{inner}</div></div>')
    parts = ['<div style="margin-top:10px;padding:12px;border-radius:14px;'
             'background:#0b1220;color:#e5e7eb">']
    if phone:
        wa = _wa_me(phone)
        shown = (f'<a href="{esc(wa)}" target="_blank" rel="noreferrer" '
                 f'style="color:#ffffff;text-decoration:none;word-break:break-word">'
                 f'{esc(phone)}</a>' if wa else esc(phone))
        parts.append(f'<div style="font-size:13px;opacity:.9;color:#e5e7eb">'
                     f'{fmt(s["fastest"])}</div>'
                     '<div style="margin-top:6px;font-size:16px;font-weight:900;color:#ffffff">'
                     f'<span style="opacity:.9">{fmt(s["whatsapp"])}</span> {shown}</div>')
    if group:
        parts.append(group_row(
            f'<a href="{esc(group)}" target="_blank" rel="noreferrer" '
            'style="display:inline-block;background:#22c55e;color:#052e16;'
            'text-decoration:none;font-weight:900;padding:10px 14px;border-radius:12px">'
            f'{fmt(s["open_group"])}</a>'))
    else:
        # 🔴 the owner's idea, 2026-08-10, and it fixes a real defect: an EMPTY group link used to
        # render nothing at all, so the page looked finished while a student had no idea a group
        # was even coming. `validate()` told the professor; it could not tell the student.
        #
        # Deliberately NOT a link. His hand-written version was an `<a href>` to a placeholder,
        # which TinyMCE resolved to `https://virtual3.uach.mx/YOUR_GROUP_LINK_HERE` — a real,
        # clickable link to a 404 on the university's own domain. A pill that cannot be clicked
        # says the same thing and cannot mislead. Dashed border because "unfinished" should look
        # unfinished; it is the one element on the page that is meant to be replaced.
        parts.append(group_row(
            '<span style="display:inline-block;background:#064e3b;color:#a7f3d0;'
            'border:1px dashed #34d399;font-weight:900;padding:10px 14px;border-radius:12px">'
            f'{fmt(s["group_pending"])}</span>'
            '<div style="margin-top:6px;font-size:12px;opacity:.75;color:#e5e7eb">'
            f'{fmt(s["group_pending_note"])}</div>'))
    parts.append("</div>")
    return "".join(parts)


def _card_where(d: dict, t: dict, s: dict) -> str:
    """«Dónde encontrar las cosas» — its own card since 2026-08-10, and not only for balance.

    It used to be a sub-box inside *Help & contact*, which made that card twice the height of
    *Grading* beside it and left a hole in the layout. But it also never belonged there: this
    is orientation — *where things live in Moodle* — and a student reads it when nothing is
    wrong. Contact is what they reach for when something is. Two questions, two cards.
    """
    where = _rows(d["where_to_find"], 2, MAX_WHERE)
    if not where:
        return ""
    parts = [f'<div style="{CARD}">',
             f'<div style="{CARD_TITLE}">{fmt(s["where"])}</div>',
             '<ul style="margin:10px 0 0 18px;padding:0;color:#334155;font-size:14px">']
    for label, text in where:
        head = f"<strong>{fmt(label)}:</strong> " if label else ""
        parts.append(f'<li style="margin-bottom:6px">{head}{fmt(text)}</li>')
    parts.append("</ul></div>")
    return "".join(parts)


def _card_help(d: dict, t: dict, s: dict) -> str:
    contact = _contact_block(d, s)
    if not (contact or d["help_instructions"]):
        return ""
    parts = [f'<div style="{CARD}">', f'<div style="{CARD_TITLE}">{fmt(s["help"])}</div>',
             contact]
    if d["help_instructions"]:
        parts.append('<div style="margin-top:12px;padding:12px;border-radius:12px;'
                     'border:1px solid #e5e7eb;background:#f8fafc;font-size:14px">'
                     f'<div style="font-weight:900;color:#0f172a">{fmt(s["need_help"])}</div>'
                     '<div style="margin-top:6px;color:#334155">'
                     f'{fmt(d["help_instructions"])}</div></div>')
    parts.append("</div>")
    return "".join(parts)


def _professor(d: dict, t: dict, s: dict) -> str:
    """The expandable card. `<details>`/`<summary>` were probed live on 2026-08-07 and
    survive this Moodle's sanitizer, inline styles included."""
    name = d["professor_name"] or s["professor"]
    expectations = _lines(d["expectations"], MAX_EXPECT)
    if not (d["professor_name"] or d["bio"] or expectations or d["photo_url"]):
        return ""

    # A disclosure has to have something behind it. `bio`, `expectations` and `promise` are the
    # ONLY things the expanded body adds — everything else in it repeats the header. So a
    # profile carrying just a name and a phone (exactly what a colleague sends when asked for
    # only those) rendered "Tap to expand ▾" above a panel identical to the header: a control
    # that visibly does nothing. Measured on Colleague A's page, 2026-08-11. Where there is nothing
    # more to show, draw the same card without the affordance rather than make a promise the
    # page cannot keep.
    has_more = bool(d["bio"] or expectations or d["promise"])

    # `flex-wrap:wrap` is a NO-OP on a wide screen and the whole fix on a phone. Measured
    # 2026-08-11 at 360px: the title's `min-width:180px` + a 12px gap + the ~118px nowrap
    # "More ▾" pill needs 310px inside a 282px row, so the pill hung 23px off the right edge
    # and the WHOLE PAGE scrolled sideways (scrollWidth 383 vs clientWidth 360). Wrapping drops
    # the pill onto its own line instead. No media query is available here — Moodle's KSES
    # filter strips <style>, so every rule is inline and unconditional.
    head_style = ('display:flex;align-items:center;justify-content:space-between;gap:12px;'
                  'flex-wrap:wrap;padding:6px;border-radius:12px')
    parts = [
        f'<div style="margin-top:12px;{CARD}">',
        '<details style="border-radius:12px">' if has_more else '<div>',
        (f'<summary style="list-style:none;cursor:pointer;{head_style}">' if has_more
         else f'<div style="{head_style}">'),
        '<span style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">',
        _photo(d["photo_url"], name, size=54, radius="999px", border="3px solid #e2e8f0"),
        '<span style="min-width:180px;flex:1">',
        '<span style="display:block;font-weight:900;color:#0f172a;font-size:15px">'
        f'{fmt(s["meet"])}'
        # `inline-block` + `nowrap`: as a plain inline span the pill broke ACROSS TWO LINES at
        # 700px — half a rounded blue capsule at the end of one line, the other half at the
        # start of the next. It is a button-shaped label; it wraps as a unit or not at all.
        + (f' <span style="display:inline-block;white-space:nowrap;'
           'margin-left:8px;font-size:12px;font-weight:900;'
           'padding:3px 8px;border-radius:999px;background:#eff6ff;color:#1d4ed8;'
           f'border:1px solid #dbeafe">{fmt(s["expand"])}</span>' if has_more else '')
        + '</span>',
    ]
    if d["professor_name"] or d["professor_role"]:
        subtitle = " · ".join(p for p in (d["professor_name"], d["professor_role"]) if p)
        parts.append('<span style="display:block;margin-top:4px;color:#334155;font-size:14px">'
                     f'{fmt(subtitle)}</span>')
    parts.append("</span></span>")
    if has_more:
        parts.append('<span style="display:inline-flex;align-items:center;gap:8px;'
                     'padding:10px 12px;border-radius:999px;border:1px solid #e5e7eb;'
                     'background:#f8fafc;color:#0f172a;'
                     'font-weight:900;font-size:12px;white-space:nowrap">'
                     f'<span style="opacity:.75">{fmt(s["more"])}</span>'
                     '<span style="display:inline-block;width:28px;height:28px;'
                     f'border-radius:999px;background:linear-gradient(135deg,{t["a"]},{t["b"]});'
                     'color:#ffffff;'
                     'line-height:28px;text-align:center;font-size:14px">▾</span></span>')
    parts.append("</summary>" if has_more else "</div>")

    # A card with nothing behind the header stops here: header, closed wrapper, no panel and no
    # "click again to collapse" hint for a thing that never opened.
    if not has_more:
        parts.append("</div></div>")
        return "".join(parts)

    # `justify-content:center` is deliberately a NO-OP on a wide screen and the whole fix on a
    # narrow one. Next to the text the photo cannot move: the text block is `flex:1`, so it
    # eats every pixel of free space and there is nothing left to distribute. Once the row
    # wraps — a phone, or Moodle's own narrow column — the photo is alone on its line with all
    # that space to itself, and without this it hugs the left edge with a void beside it.
    # One declaration, two layouts, and no media query (inline styles cannot carry one).
    parts.append('<div style="margin-top:10px;padding:12px;border-radius:14px;'
                 'background:#f8fafc;border:1px solid #e2e8f0">'
                 '<div style="display:flex;gap:14px;align-items:flex-start;flex-wrap:wrap;'
                 'justify-content:center">'
                 '<div style="text-align:center;flex:0 0 auto">')
    parts.append(_photo(d["photo_url"], name, size=110, radius="18px",
                        border="4px solid #ffffff"))
    parts.append('</div><div style="flex:1;min-width:210px">')
    parts.append('<div style="font-weight:900;color:#0f172a;'
                 f'font-size:clamp(15px,3.6vw,17px)">{esc(name)}</div>')
    if d["professor_role"]:
        parts.append('<div style="color:#64748b;font-size:13px;margin-top:2px">'
                     f'{fmt(d["professor_role"])}</div>')
    if d["bio"]:
        parts.append('<div style="margin-top:8px;font-weight:900;color:#0f172a;font-size:14px">'
                     f'{fmt(s["about"])}</div>'
                     '<div style="margin-top:4px;color:#334155;font-size:14px">'
                     f'{fmt(d["bio"])}</div>')
    if expectations:
        parts.append('<div style="margin-top:10px;padding:10px 12px;border-radius:12px;'
                     'background:#ffffff;border:1px solid #e5e7eb;font-size:14px">'
                     f'<div style="font-weight:900;color:#0f172a">{fmt(s["expect"])}</div>'
                     '<ul style="margin:8px 0 0 18px;padding:0;color:#334155">')
        parts.extend(f"<li>{fmt(item)}</li>" for item in expectations)
        parts.append("</ul></div>")
    # No contact box here. It used to repeat the phone AND `help_instructions`, both of which
    # `_card_help` already shows — see the module docstring.
    if d["promise"]:
        parts.append('<div style="margin-top:10px;text-align:center;font-size:12px;'
                     'color:#475569"><span style="display:inline-block;padding:8px 10px;'
                     'border-radius:999px;background:#ffffff;border:1px solid #e2e8f0">'
                     f'{fmt(d["promise"])}</span></div>')
    parts.append("</div></div></div></details>")
    parts.append('<div style="margin-top:10px;font-size:12px;color:#64748b;text-align:center">'
                 f'{fmt(s["collapse"])}</div></div>')
    return "".join(parts)


def render_hub(data: dict) -> str:
    """The whole page. Every repeated value comes from exactly one key of `data`."""
    d = {**DEFAULTS, **{k: str(v or "").strip() for k, v in (data or {}).items()
                        if k in DEFAULTS}}
    t, s = theme(d["theme"]), strings(d["lang"])

    content = _card_content(d, t, s)
    # Placed, not sorted. `Help & contact` is the tallest of the three, so it takes a column on
    # its own and the two shorter ones stack opposite it — which is what makes the two columns
    # end at roughly the same place instead of leaving a hole under `Grading`.
    left = [c for c in (_card_grading(d, t, s), _card_where(d, t, s)) if c]
    right = [c for c in (_card_help(d, t, s),) if c]
    body = [
        marker(BLOCK_ID),
        EDIT_NOTE,
        '<div style="font-family:Arial,Helvetica,sans-serif;color:#1f2937;background:#f3f4f6;'
        'line-height:1.5;padding:clamp(12px,3vw,20px);border-radius:14px;'
        'box-shadow:0 10px 25px rgba(0,0,0,.08);max-width:980px;margin:0 auto;font-size:15px">',
        _header(d, t, s),
    ]

    # 🔴 THE ORPHAN, and why the shape changed (the owner, 2026-08-10: *"the help & contact card is
    # all alone on one side"*). Three cards in one `auto-fit` grid is fine at three columns and
    # fine at one — and broken at **two**, where it lays out 2 + 1 and the last card sits at half
    # width with an empty half beside it. Two columns is not an edge case here: it is what Moodle
    # gives the page whenever the navigation drawer is open (measured: 858px container → cards of
    # 405px, the third one alone).
    #
    # The fix is structural, not a tuned breakpoint: **make the count even.** The temario is by
    # far the biggest card and reads best wide, so it goes full width with its terms in columns;
    # the two short cards pair off below. A grid of TWO items can only ever be 2×1 or 1×2 — there
    # is no width at which one of them is orphaned, at any container size, on any device.
    if content:
        body.append(f'<div style="margin-top:14px">{content}</div>')

    columns = [col for col in (left, right) if col]
    if len(columns) == 1:
        # Only one column's worth of cards survived (a half-filled form). Stack them full width
        # rather than leaving a single card at half width — which is the orphan all over again.
        body.append('<div style="display:grid;gap:14px;margin-top:14px">'
                    + "".join(columns[0]) + "</div>")
    elif columns:
        # `min(340px,100%)` rather than a bare 340px: an auto-fit track keeps its minimum even
        # when the container is narrower than it, so on a 320px phone inside Moodle's own
        # padding the plain version pushes the whole page into a horizontal scroll.
        # `align-items:start` so a short column ends where its content ends — a card stretched
        # to match its neighbour, with 200px of blank white at the bottom, is the same "wasted
        # space" complaint again, just wearing a border.
        body.append('<div style="display:grid;'
                    'grid-template-columns:repeat(auto-fit,minmax(min(340px,100%),1fr));'
                    'gap:14px;margin-top:14px;align-items:start">')
        for col in columns:
            body.append('<div style="display:grid;gap:14px;align-items:start">'
                        + "".join(col) + "</div>")
        body.append("</div>")
    body.append(_professor(d, t, s))
    body.append("</div>")
    return "".join(body)


def render_hub_checked(data: dict) -> str:
    """Render, then refuse to hand back anything render.lint() would flag."""
    out = render_hub(data)
    problems = lint(out)
    if problems:
        raise ValueError("Course hub is not Moodle-safe: " + "; ".join(problems))
    return out


# 🔴 The temario is READ OFF THE COURSE, not invented. Every topic below is a quiz that is
# actually sitting in that parcial's tab of the restored English I course (measured
# 2026-08-10 from `scratchpad/structure_9023.json`, 16 + 14 + 6 grammar quizzes). The
# version this replaced was plausible and wrong in the way that matters: it advertised
# `Adjetivos` and `Imperativos` under Parcial 2 when both are Parcial 1 quizzes, and gave
# Parcial 3 the whole of Parcial 2's possessives and family — plus `Gustos`, which lives in
# the *hidden* "Other resources" tab and no student can open. A student planning their week
# from this page would have studied the wrong term.
TERMS_EN = ("The alphabet · Numbers · Colors · Personal pronouns · Verb to be (+ questions) · "
            "Countries and nationalities · Articles a/an/the · Adjectives · Demonstratives · "
            "Common verbs · Do/Does · Wh- questions · Imperatives · Classroom objects")
TERMS_ES = ("El alfabeto · Números · Colores · Pronombres personales · Verbo to be (y "
            "preguntas) · Países y nacionalidades · Artículos a/an/the · Adjetivos · "
            "Demostrativos · Verbos comunes · Do/Does · Preguntas Wh- · Imperativos · "
            "Objetos del salón")
TERMS2_EN = ("There is / There are · Jobs and occupations · Possessive adjectives and "
             "pronouns · The possessive case ('s / s') · Family members · Simple present: "
             "he/she/it · Simple present: negatives · Prepositions of place · Prepositions "
             "of time (at/on/in) · Parts of the body · Clothes")
TERMS2_ES = ("There is / There are · Profesiones y oficios · Adjetivos y pronombres "
             "posesivos · El caso posesivo ('s / s') · La familia · Presente simple: "
             "he/she/it · Presente simple: negativos · Preposiciones de lugar · "
             "Preposiciones de tiempo (at/on/in) · Partes del cuerpo · La ropa")
TERMS3_EN = ("Simple present: questions and short answers · Have / Has · Comparative "
             "adjectives · Superlative adjectives · As … as · Can / Can't")
TERMS3_ES = ("Presente simple: preguntas y respuestas cortas · Have / Has · Adjetivos "
             "comparativos · Adjetivos superlativos · As … as (igualdad) · "
             "Can / Can't (habilidad)")


#: The profile a **template** course carries: the real card, with obviously fake values in it.
#:
#: 🔴 the owner, 2026-08-11, rejecting the alternative: a course meant to be copied to another
#: professor should render the **ordinary page**, complete, with `Nombre Apellido` and
#: `+52 614 000 0000` sitting where the real ones go. That is what English I's template did and
#: it is what he wants. The version this replaced drew a separate amber "three things to fill
#: in" card instead and dropped the professor card entirely — which showed the receiving
#: professor a *different page* from the one their students would eventually see, and hid the
#: layout they were actually inheriting.
#:
#: ⚠️ **Placeholder, not personal.** These values are literal and typed here; nothing is read
#: from `hub_store.load_profile`, so a template can never pick up a real professor's name,
#: photo or phone. `photo_url` stays empty on purpose — the initials avatar (`NA`) reads as
#: unfinished, while somebody else's face would not.
#:
#: ⚠️ Derived from `example_data("bilingual")` rather than retyped, except the name: the house
#: rule is that anything repeated is typed once, and two copies of a bio is how one of them
#: goes stale. `Nombre Apellido` beats `Alex Doe` because it is not mistakable for a real
#: person who might simply be left in place.
PLACEHOLDER_NAME = "Nombre Apellido"


def placeholder_profile() -> dict:
    """The profile scope of a course template. Pure; never touches the database."""
    example = example_data("bilingual")
    profile = {key: example[key] for key in PROFILE_KEYS if example.get(key)}
    profile["professor_name"] = PLACEHOLDER_NAME
    profile["photo_url"] = ""
    return profile


def example_data(lang: str = "es") -> dict:
    """A filled-in example — what the 'Ver ejemplo' button loads, and what the tests render."""
    if lang == "bilingual":
        return {
            **DEFAULTS, "lang": "bilingual",
            "professor_name": "Alex Doe", "professor_role": "English Professor ~~Profesor/a "
                                                            "de Inglés~~",
            "whatsapp_phone": "+52 614 000 0000",
            "bio": "I have taught English at this university since 2015. I like helping "
                   "students gain confidence with simple, practical English. "
                   "~~Doy clases de inglés en esta universidad desde 2015. Me gusta ayudar a "
                   "que cada estudiante gane confianza con un inglés simple y práctico.~~",
            "expectations": "Clear instructions and examples ~~Instrucciones y ejemplos "
                            "claros~~\nSupport when you get stuck, no judgement ~~Apoyo "
                            "cuando te atores, sin juicios~~\nSteady practice that actually "
                            "helps you speak ~~Práctica constante que sí sirve para hablar~~",
            "promise": "My promise: I will always try to make this course feel doable. "
                       "~~Mi promesa: siempre intentaré que este curso se sienta posible.~~",
            "help_instructions": "Send me a message with your name, your group and what you "
                                 "need (a screenshot helps). ~~Mándame un mensaje con tu "
                                 "nombre, tu grupo y lo que necesitas (si puedes, una "
                                 "captura de pantalla).~~",
            "course_title": "English I (A1) ~~Inglés I (A1)~~",
            "course_tagline": "Everything important in one place ~~Todo lo importante en un "
                              "solo lugar~~",
            "content_badge": "A1",
            "description": "A beginner course built around the grammar and vocabulary you "
                           "need to communicate every day. ~~Curso para principiantes, "
                           "centrado en la gramática y el vocabulario que necesitas para "
                           "comunicarte todos los días.~~",
            "terms": f"Term 1 ~~Parcial 1~~ | {TERMS_EN} ~~{TERMS_ES}~~\n"
                     f"Term 2 ~~Parcial 2~~ | {TERMS2_EN} ~~{TERMS2_ES}~~\n"
                     f"Term 3 ~~Parcial 3~~ | {TERMS3_EN} ~~{TERMS3_ES}~~",
            "grading": "Activities ~~Actividades~~ | 60 | Weekly tasks in Moodle ~~Tareas "
                       "semanales en Moodle~~\n"
                       "Special activity ~~Actividad especial~~ | 20 | Project or extra "
                       "graded activity ~~Proyecto o actividad evaluada extra~~\n"
                       "Exam ~~Examen~~ | 20 | Term or final exam ~~Evaluación parcial o "
                       "final~~",
            "where_to_find": "Activities ~~Actividades~~ | In each term's tab in Moodle "
                             "~~En la pestaña de cada parcial en Moodle~~\n"
                             "Grades ~~Calificaciones~~ | Moodle → Grades ~~Moodle → "
                             "Calificaciones~~\n"
                             "Course content ~~Contenido~~ | The term lists on this page "
                             "~~Las listas por parcial de esta página~~",
        }
    if lang == "en":
        return {
            **DEFAULTS, "lang": "en",
            "professor_name": "Alex Doe", "professor_role": "English Professor",
            "whatsapp_phone": "+52 614 000 0000",
            "bio": "I have taught English at the university since 2015. I enjoy helping "
                   "students build confidence with simple, practical English.",
            "expectations": "Clear instructions and examples\nSupport when you are stuck\n"
                            "Consistent practice that helps you speak",
            "promise": "I will always try to make this course feel doable.",
            "help_instructions": "Send a message with your name, your group and what you need "
                                 "(a screenshot helps).",
            "course_title": "English I (A1)",
            "course_tagline": "Everything important in one place",
            "content_badge": "Level A1",
            "description": "A beginner course focused on the grammar and vocabulary you need "
                           "for everyday communication.",
            "terms": f"Term 1 | {TERMS_EN}\nTerm 2 | {TERMS2_EN}\nTerm 3 | {TERMS3_EN}",
            "grading": "Activities | 60 | Weekly tasks in Moodle\n"
                       "Special activity | 20 | Project or extra evaluated activity\n"
                       "Exam | 20 | Term or final evaluation",
            "where_to_find": "Activities | In each term's tab in Moodle\n"
                             "Grades | Moodle → Grades\n"
                             "Course content | The term lists on this page",
        }
    return {
        **DEFAULTS, "lang": "es",
        "professor_name": "Nombre Apellido", "professor_role": "Profesor/a de Inglés",
        "whatsapp_phone": "+52 614 000 0000",
        "bio": "Doy clases de inglés en la universidad desde 2015. Me gusta ayudar a que cada "
               "estudiante gane confianza con un inglés simple y práctico.",
        "expectations": "Instrucciones y ejemplos claros\nApoyo cuando te atores, sin juicios\n"
                        "Práctica constante que sí sirve para hablar",
        "promise": "Mi promesa: siempre intentaré que este curso se sienta posible.",
        "help_instructions": "Mándame un mensaje con tu nombre, tu grupo y lo que necesitas "
                             "(si puedes, una captura de pantalla).",
        "course_title": "Inglés I (A1)",
        "course_tagline": "Todo lo importante en un solo lugar",
        "content_badge": "Nivel A1",
        "description": "Curso para principiantes, centrado en la gramática y el vocabulario "
                       "que necesitas para comunicarte todos los días.",
        "terms": f"Parcial 1 | {TERMS_ES}\nParcial 2 | {TERMS2_ES}\nParcial 3 | {TERMS3_ES}",
        "grading": "Actividades | 60 | Tareas semanales en Moodle\n"
                   "Actividad especial | 20 | Proyecto o actividad evaluada extra\n"
                   "Examen | 20 | Evaluación parcial o final",
        "where_to_find": "Actividades | En la pestaña de cada parcial en Moodle\n"
                         "Calificaciones | Moodle → Calificaciones\n"
                         "Contenido | Las listas por parcial de esta página",
    }
