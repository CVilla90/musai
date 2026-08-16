"""Español — every sentence MUSAI shows a professor.

Written, not generated. The tone target is the one the English already has: a colleague
explaining something, not a system emitting a notice. Mexican Spanish, `tú` throughout — this
is a tool a professor uses on their own courses, and `usted` would put a counter between them
and it.

🔴 **Four kinds of sentence are load-bearing and were translated word by word, not by feel.**
A translation that softens one of these is a safety regression with a green tick on it:

1. **"MUSAI can read these passwords."** The Settings page exists to say the uncomfortable
   thing out loud. *"MUSAI puede leer estas contraseñas"* — not *"tiene acceso a"*, which is
   the sentence someone writes when they want it to sound better than it is.
2. **The dry-run badge.** *Simulacro* is the word Carlos's own dry-run buttons already use in
   the course pages, so the badge and the button agree. **LIVE** is *EN VIVO · escrituras
   activas* — never *activo*, which reads as "working" rather than "armed".
3. **Every refusal.** A refusal must still name the thing refused and must not apologise its
   way into sounding like a temporary glitch.
4. **Anything about a restore deleting.** *"se eliminan primero"* — the passive is kept because
   the English is passive and the point is the deletion, not who does it.

⭐ Terminology follows **Moodle's own Spanish UI and SEGA's**, not a dictionary: a professor
reading MUSAI has the Moodle tab open next to it, and a word that disagrees with the tab next
door is worse than an English word they already know.

| English | Spanish | why |
|---|---|---|
| course | curso | Moodle |
| activity | actividad | Moodle |
| section / tab | sección / pestaña | Moodle onetopic |
| backup | respaldo | Moodle es-MX says *copia de seguridad*; UACH staff say *respaldo* |
| restore | restaurar | Moodle |
| grade | calificación | SEGA |
| partial | parcial | SEGA |
| gradebook | libro de calificaciones | Moodle |
| dry run | simulacro | already on the buttons |
| job | tarea | |
| settings | ajustes | |

Keys are the English source sentence, whitespace-normalised — see `musai/i18n/__init__.py`.
"""

CATALOGUE: dict[str, str] = {
    # ── the shell: nav, badges, the meter, the loader ────────────────────────────────────
    "Courses": "Cursos",
    "Assistant": "Asistente",
    "Settings": "Ajustes",
    "Sign out": "Cerrar sesión",
    # 🔴 The badge. Same word as the Simulacro buttons inside the course pages, on purpose.
    "DRY-RUN · no writes": "SIMULACRO · sin escrituras",
    "LIVE · writes enabled": "EN VIVO · escrituras activas",
    "No write reaches Moodle or SEGA while this is on.":
        "Con esto encendido, ninguna escritura llega a Moodle ni a SEGA.",
    "{pct}% of free usage": "{pct}% del uso gratuito",
    "${usd} · no limit": "${usd} · sin límite",
    "No monthly limit on this account — still measured, because an untracked account is the "
    "one nobody notices.":
        "Esta cuenta no tiene límite mensual — se mide de todos modos, porque la cuenta que "
        "nadie mide es la que nadie nota.",
    "About {n} more assistant questions left this month.":
        "Te quedan unas {n} preguntas al asistente este mes.",
    "Resets on the 1st.": "Se reinicia el día 1.",
    "Working…": "Trabajando…",
    "— browser jobs can take a minute": "— las tareas con navegador pueden tardar un minuto",
    "Language": "Idioma",
    "MUSAI in English or Spanish. Your choice is saved to your account, so it follows you to "
    "any computer you sign in from.":
        "MUSAI en inglés o español. Tu elección se guarda en tu cuenta, así que te sigue a "
        "cualquier computadora donde inicies sesión.",

    # ── the cockpit ──────────────────────────────────────────────────────────────────────
    # ⭐ "Panel", not "Cabina". The cockpit metaphor is MUSAI's, and it is a good one in the
    # documentation — but a professor's home screen has to be labelled with the word they would
    # use, and "Cabina" on its own reads as a phone booth.
    "Cockpit": "Panel",
    "Your course in {sem}.": "Tu curso en {sem}.",
    "Your {n} courses in {sem}.": "Tus {n} cursos en {sem}.",
    "Semester": "Semestre",
    "past": "pasado",
    "now": "ahora",
    "Update from Moodle": "Actualizar desde Moodle",
    "Group": "Grupo",
    "Subject": "Materia",
    "Backup &amp; restore": "Respaldo y restauración",
    "Open →": "Abrir →",
    "No courses in {sem}.": "No hay cursos en {sem}.",
    "MUSAI can only read the courses on your Moodle dashboard today, which is this semester's.":
        "MUSAI sólo puede leer los cursos que hoy aparecen en tu tablero de Moodle, que son "
        "los de este semestre.",
    "Back to {sem}": "Volver a {sem}",
    "No courses loaded for {sem} yet": "Todavía no hay cursos cargados para {sem}",
    "MUSAI can read your courses straight from Moodle, but password storage is not set up on "
    "this server yet, so it cannot sign in as you. That is a one-line fix in <code>.env</code> "
    "— <a href='/settings'>Settings</a> explains it.":
        "MUSAI puede leer tus cursos directamente de Moodle, pero en este servidor todavía no "
        "está configurado el guardado de contraseñas, así que no puede iniciar sesión como tú. "
        "Se arregla con una línea en <code>.env</code> — en "
        "<a href='/settings'>Ajustes</a> se explica cómo.",
    "MUSAI reads your course list from <code>campusvirtual.uach.mx</code> — the same dashboard "
    "you see when you sign in to Moodle. To do that it needs your Moodle password, because "
    "Moodle has no other way to let an app act for you.":
        "MUSAI lee tu lista de cursos de <code>campusvirtual.uach.mx</code> — el mismo tablero "
        "que ves al entrar a Moodle. Para eso necesita tu contraseña de Moodle, porque Moodle "
        "no tiene ninguna otra forma de permitir que una aplicación actúe por ti.",
    "Add my Moodle password": "Guardar mi contraseña de Moodle",
    "You choose whether it is stored. Nothing is written to any of your courses by reading "
    "them.":
        "Tú decides si se guarda. Leer tus cursos no escribe nada en ninguno de ellos.",
    "Load them from your Moodle dashboard. MUSAI signs in as <code>{user}</code>, reads the "
    "courses listed there, and creates them here. It reads only — nothing is written to any "
    "course.":
        "Cárgalos desde tu tablero de Moodle. MUSAI inicia sesión como <code>{user}</code>, "
        "lee los cursos que aparecen ahí y los crea aquí. Sólo lee — no escribe nada en "
        "ningún curso.",
    "Load my courses from Moodle": "Cargar mis cursos desde Moodle",
    "Takes about half a minute.": "Tarda alrededor de medio minuto.",

    # ── the course workspace shell ───────────────────────────────────────────────────────
    "← All courses": "← Todos los cursos",
    "Moodle course id": "id del curso en Moodle",
    "Open in Moodle ↗": "Abrir en Moodle ↗",
    "No Moodle id — re-map from the cockpit": "Sin id de Moodle — vuelve a mapear desde el panel",
    "Moodle calls it {name}": "Moodle lo llama {name}",
    "Course sections": "Secciones del curso",
    "Overview": "Resumen",
    "Activities": "Actividades",
    "Dates": "Fechas",
    "Grades": "Calificaciones",
    "Content": "Contenido",
    "Transfer": "Traslado",
    "Messages": "Mensajes",

    # ── the assistant ────────────────────────────────────────────────────────────────────
    "← Cockpit": "← Panel",
    "read-only": "sólo lectura",
    "No <code>GEMINI_API_KEY</code> set in <code>MUSAI/.env</code> — the assistant is offline.":
        "No hay <code>GEMINI_API_KEY</code> en <code>MUSAI/.env</code> — el asistente está "
        "fuera de servicio.",
    "Ask about your groups — status, individual students, trends across partials, who's at "
    "risk — or about MUSAI itself: what it can do, and how.":
        "Pregunta por tus grupos — cómo van, un alumno en particular, la tendencia entre "
        "parciales, quién está en riesgo — o por MUSAI mismo: qué puede hacer, y cómo.",
    # ⚠️ The group code and the partial's name stay exactly as they are: they are what the
    # course is called in Moodle and in SEGA, and a translated example would not match anything.
    "How is 1-LED-A doing in Parcial 1?": "¿Cómo va 1-LED-A en el Parcial 1?",
    "Who is at risk in 1-LED-A?": "¿Quién está en riesgo en 1-LED-A?",
    "Trend of 1-LED-A across partials": "Tendencia de 1-LED-A entre parciales",
    "How do I set dates on every activity?": "¿Cómo pongo fechas en todas las actividades?",
    "What happens when I restore a course?": "¿Qué pasa cuando restauro un curso?",
    "Ask the assistant…": "Pregúntale al asistente…",
    "Ask": "Preguntar",
    "looking it up…": "buscándolo…",
    "The assistant reads through read-only tools only — your own groups' grades, and MUSAI's "
    "help topics. It can never change anything, and it sees no course but yours. For a "
    "question about the app it answers from a help topic and cites it; if none covers your "
    "question it says so rather than guessing.":
        "El asistente lee únicamente con herramientas de sólo lectura — las calificaciones de "
        "tus propios grupos y los temas de ayuda de MUSAI. Nunca puede cambiar nada, y no ve "
        "ningún curso más que los tuyos. Si le preguntas por la aplicación, responde a partir "
        "de un tema de ayuda y lo cita; si ninguno cubre tu pregunta, lo dice en lugar de "
        "inventar.",
    "Today: {req}/{req_cap} requests, {tok}/{tok_cap} tokens":
        "Hoy: {req}/{req_cap} peticiones, {tok}/{tok_cap} tokens",
    "{tok} tok · {pct}% of today": "{tok} tok · {pct}% de hoy",

    # 🔴 A refusal, and it says what did NOT happen rather than that something went wrong.
    # "No se pudo enviar" would read as a failure worth retrying; the retry is the problem.
    "Nothing was sent": "No se envió nada",

    # ── Settings ▸ shell ─────────────────────────────────────────────────────────────────
    "Signed in as {email}": "Sesión iniciada como {email}",
    "Passwords": "Contraseñas",
    "Usage": "Uso",
    "Selected": "Elegido",
    "Currently showing": "Lo que se está mostrando",
    "You have not chosen yet, so MUSAI is showing you its default. Pick one and it stays "
    "picked — including if the default ever changes.":
        "Todavía no has elegido, así que MUSAI te muestra su idioma predeterminado. Elige uno "
        "y se queda elegido — incluso si algún día cambia el predeterminado.",
    "Only what MUSAI itself says changes. Your courses, activity names, grades and anything "
    "Moodle or SEGA sends back stay exactly as they are written there.":
        "Sólo cambia lo que dice MUSAI. Tus cursos, los nombres de las actividades, las "
        "calificaciones y todo lo que devuelven Moodle o SEGA se quedan tal como están "
        "escritos ahí.",

    # ── Settings ▸ Passwords ─────────────────────────────────────────────────────────────
    # 🔴 The uncomfortable paragraph, and the reason this page exists. Translated literally on
    # purpose: "MUSAI puede leer estas contraseñas" — not "tiene acceso a", which is the phrase
    # you reach for when you want the sentence to sound better than the fact.
    "What MUSAI does with these": "Qué hace MUSAI con ellas",
    "Moodle has no way to grant an app access on your behalf — no API key, no app password. So "
    "to read your course list, make a backup or run a restore <em>as you</em>, MUSAI signs in "
    "with your password the same way you do.":
        "Moodle no tiene forma de darle acceso a una aplicación en tu nombre — no hay llave de "
        "API ni contraseña de aplicación. Así que para leer tu lista de cursos, hacer un "
        "respaldo o correr una restauración <em>como tú</em>, MUSAI inicia sesión con tu "
        "contraseña igual que lo haces tú.",
    "That means <strong>MUSAI can read these passwords</strong>, and it is worth knowing that "
    "before you store one. They are encrypted with a key that lives outside the database, they "
    "are never shown back to you or written to any log, and <strong>Delete removes them for "
    "good</strong>. Storing them is optional — you can type one in each time instead, and "
    "nothing is kept.":
        "Eso significa que <strong>MUSAI puede leer estas contraseñas</strong>, y vale la pena "
        "saberlo antes de guardar una. Se cifran con una llave que vive fuera de la base de "
        "datos, nunca se te muestran de vuelta ni se escriben en ninguna bitácora, y "
        "<strong>Eliminar las borra para siempre</strong>. Guardarlas es opcional — puedes "
        "escribirla cada vez y no se conserva nada.",
    "Password storage is not set up on this server":
        "En este servidor no está configurado el guardado de contraseñas",
    "<code>CREDENTIAL_KEY</code> is missing from <code>.env</code>, so nothing can be encrypted "
    "— and MUSAI will not store a password in the clear instead. Generate one:":
        "Falta <code>CREDENTIAL_KEY</code> en <code>.env</code>, así que no hay con qué cifrar "
        "— y MUSAI no va a guardar una contraseña en claro a cambio. Genera una:",
    "Put it in <code>.env</code> and restart fully — <code>--reload</code> does not reread it.":
        "Ponla en <code>.env</code> y reinicia por completo — <code>--reload</code> no la "
        "vuelve a leer.",
    # `SYSTEM_INFO[…]["why"]`, from `musai/professors.py`. 🔴 *Guardar* and *Confirmar* are the
    # SEGA buttons and keep their exact names: this sentence is a promise about which button is
    # clicked, and a promise about a button nobody can find is not a promise.
    "Reads your course list, creates backups and runs restores as you.":
        "Lee tu lista de cursos, crea respaldos y corre restauraciones como tú.",
    "Uploads partial grades. MUSAI only ever clicks Guardar, never Confirmar.":
        "Sube calificaciones parciales. MUSAI sólo hace clic en Guardar, nunca en Confirmar.",
    "Stored · works": "Guardada · funciona",
    "Stored · not tested yet": "Guardada · sin probar",
    "Not stored": "No guardada",
    "Username <strong>{user}</strong> · saved {saved}":
        "Usuario <strong>{user}</strong> · guardada el {saved}",
    "· last worked {when}": "· funcionó por última vez el {when}",
    "Username": "Usuario",
    "Password": "Contraseña",
    "New password (replaces the stored one)":
        "Contraseña nueva (reemplaza la guardada)",
    "Replace": "Reemplazar",
    "Save": "Guardar",
    "Your Moodle username is usually the part of your UACH email before the @ — "
    "<code>{guess}</code> for you. Change it if yours differs.":
        "Tu usuario de Moodle suele ser la parte de tu correo UACH antes de la @ — en tu caso "
        "<code>{guess}</code>. Cámbialo si el tuyo es distinto.",
    "Test this password": "Probar esta contraseña",
    "Delete": "Eliminar",
    # 🔴 A confirmation that names what stops working, not one that asks "¿estás seguro?".
    "Delete the stored {label} password? MUSAI will not be able to act on your behalf until "
    "you enter it again.":
        "¿Eliminar la contraseña guardada de {label}? MUSAI no podrá actuar en tu nombre hasta "
        "que la vuelvas a escribir.",
    "MUSAI signs in to Moodle only as you, and only for your own courses. Every backup and "
    "restore is recorded against <code>{email}</code>.":
        "MUSAI entra a Moodle únicamente como tú, y sólo para tus propios cursos. Cada "
        "respaldo y cada restauración quedan registrados a nombre de <code>{email}</code>.",

    # ── Settings ▸ Usage ─────────────────────────────────────────────────────────────────
    "used this month": "usados este mes",
    "No monthly limit on this account — measured anyway, because an untracked account is the "
    "one whose spend nobody notices.":
        "Esta cuenta no tiene límite mensual — se mide de todos modos, porque la cuenta que "
        "nadie mide es aquella cuyo gasto nadie nota.",
    "That is <strong>{pct}%</strong> of your free {cap} — about <strong>{n}</strong> more "
    "assistant questions.":
        "Es el <strong>{pct}%</strong> de tus {cap} gratuitos — unas <strong>{n}</strong> "
        "preguntas más al asistente.",
    "Since {day} · resets on the 1st": "Desde el {day} · se reinicia el día 1",
    "Nothing is blocked at the limit yet — this is measuring, not enforcing. The daily AI "
    "budget is the one that actually stops a runaway.":
        "Todavía no se bloquea nada al llegar al límite — esto mide, no restringe. El "
        "presupuesto diario de IA es el que de verdad detiene un descontrol.",
    "Where it went": "En qué se fue",
    "Last 30 days, dearest first.": "Últimos 30 días, de lo más caro a lo más barato.",
    "Action": "Acción",
    "Times": "Veces",
    "Tokens": "Tokens",
    "Minutes": "Minutos",
    "Cost": "Costo",
    "Nothing metered yet. Ask the assistant a question or run a backup and it will show up "
    "here.":
        "Todavía no se ha medido nada. Hazle una pregunta al asistente o corre un respaldo y "
        "aparecerá aquí.",
    "What things cost": "Cuánto cuesta cada cosa",
    "Typical, not a promise — a restore takes what Moodle's queue takes.":
        "Lo típico, no una promesa — una restauración tarda lo que tarde la cola de Moodle.",
    "effectively free": "prácticamente gratis",
    "{n} per free month": "{n} por mes gratuito",
    "<strong>Opening pages is not counted.</strong> One page view costs about $0.0000008 — a "
    "millionth of a dollar, and one twelve-hundredth of a single assistant question. Recording "
    "it would cost more than the page view itself, so MUSAI meters only the two things that "
    "actually cost money: <strong>AI answers</strong> and <strong>browser jobs</strong>.":
        "<strong>Abrir páginas no cuenta.</strong> Ver una página cuesta unos $0.0000008 — una "
        "millonésima de dólar, y un mil doscientosavo de una sola pregunta al asistente. "
        "Registrarlo costaría más que la propia visita, así que MUSAI mide sólo las dos cosas "
        "que de verdad cuestan dinero: <strong>las respuestas de IA</strong> y <strong>las "
        "tareas con navegador</strong>.",
    "How this is calculated": "Cómo se calcula esto",
    "MUSAI usage is our own number, covering both vendors, so you never have to think about "
    "which one a click reached. Rate card <code>{version}</code>:":
        "El uso de MUSAI es un número nuestro que cubre a los dos proveedores, para que nunca "
        "tengas que pensar a cuál llegó un clic. Tarifario <code>{version}</code>:",
    "<strong>{model}</strong> — ${in_} per million tokens in, ${out} out.":
        "<strong>{model}</strong> — ${in_} por millón de tokens de entrada, ${out} de salida.",
    "<strong>Server time</strong> — ${per_mcu} per million compute units; a {vcpu} vCPU / {gib} "
    "GiB machine burns {cu} per second, so ${per_s} a second.":
        "<strong>Tiempo de servidor</strong> — ${per_mcu} por millón de unidades de cómputo; "
        "una máquina de {vcpu} vCPU / {gib} GiB consume {cu} por segundo, o sea ${per_s} por "
        "segundo.",
    "<strong>Requests</strong> — ${per_m} per million.":
        "<strong>Peticiones</strong> — ${per_m} por millón.",
    "Each line above is priced when it happens and never re-priced afterwards, so a rate "
    "change tomorrow cannot make last month look more expensive than it was.":
        "Cada línea de arriba se cotiza en el momento en que ocurre y nunca se vuelve a "
        "cotizar, así que un cambio de tarifa mañana no puede hacer que el mes pasado parezca "
        "más caro de lo que fue.",
    "Recent activity": "Actividad reciente",

    # ── metered kinds (`musai/metering.py::KINDS`, rendered by settings.html ▸ Usage) ─────
    "Assistant question": "Pregunta al asistente",
    "A question to the AI assistant over your gradebook.":
        "Una pregunta al asistente de IA sobre tu libro de calificaciones.",
    "Content composed": "Contenido redactado",
    "AI-composed HTML for a course block.":
        "HTML redactado por IA para un bloque del curso.",
    "Content published": "Contenido publicado",
    "Writing composed content into a Moodle course.":
        "Escribir el contenido redactado dentro de un curso de Moodle.",
    "Student assistant": "Asistente para alumnos",
    "A student's WhatsApp question answered by SUSAI.":
        "Una pregunta de un alumno por WhatsApp respondida por SUSAI.",
    "Course mapping": "Mapeo de cursos",
    "Reading your course list from Moodle.":
        "Leer tu lista de cursos desde Moodle.",
    "Course backup": "Respaldo del curso",
    "Downloading a course archive from Moodle.":
        "Descargar el archivo de un curso desde Moodle.",
    "Course restore": "Restauración del curso",
    "Restoring an archive into a course — the big one.":
        "Restaurar un archivo dentro de un curso — la operación grande.",
    "Password test": "Prueba de contraseña",
    "Signing in once to check a stored password.":
        "Iniciar sesión una vez para comprobar una contraseña guardada.",
    "Messages sent": "Mensajes enviados",
    "Sending a message to a group's students.":
        "Enviar un mensaje a los alumnos de un grupo.",
    "Opening any page": "Abrir cualquier página",
    "free in practice": "gratis en la práctica",
    "~15 min on Moodle's side": "~15 min del lado de Moodle",

    # ── the assistant's own messages (`musai/assistant/agent.py::_MESSAGES`) ─────────────
    # 🔴 Every one of these appears INSTEAD of an answer. They name what is wrong and what
    # would fix it, and none of them apologise their way into sounding temporary when they are
    # not. The `.env` variable names, the file paths and the console names stay in English:
    # they are things to type and things to click, not prose.
    "No Gemini API key set (GEMINI_API_KEY in MUSAI/.env). The assistant is offline.":
        "No hay llave de API de Gemini (GEMINI_API_KEY en MUSAI/.env). El asistente está "
        "fuera de servicio.",
    "Gemini says the API quota is exhausted. Check billing/limits in the Google AI console — "
    "MUSAI will not retry automatically.":
        "Gemini dice que la cuota de la API está agotada. Revisa la facturación y los límites "
        "en la consola de Google AI — MUSAI no va a reintentar por su cuenta.",
    "Gemini rejected the API key. Check GEMINI_API_KEY in MUSAI/.env.":
        "Gemini rechazó la llave de API. Revisa GEMINI_API_KEY en MUSAI/.env.",
    "The configured model was not found. Check GEMINI_MODEL in .env (currently '{model}').":
        "No se encontró el modelo configurado. Revisa GEMINI_MODEL en .env (ahora mismo "
        "'{model}').",
    "Gemini rejected the request as malformed — this is a MUSAI bug, not a usage problem. "
    "Check the logs.":
        "Gemini rechazó la petición por estar mal formada — esto es un error de MUSAI, no un "
        "problema de uso. Revisa las bitácoras.",
    "Gemini had a server-side error and the single retry also failed. Try again in a moment.":
        "Gemini tuvo un error de su lado y el único reintento también falló. Inténtalo de "
        "nuevo en un momento.",
    # 🔴 Does NOT say "reformula tu pregunta". Measured: the tools had already returned the
    # complete answer and only the summary was missing, so rephrasing pointed at the one
    # action that could never work.
    "I pulled the data but couldn't summarise it. The raw lookup is above; rephrasing usually "
    "will not help — check that the data is imported.":
        "Saqué los datos pero no pude resumirlos. La consulta en crudo está arriba; "
        "reformular la pregunta normalmente no ayuda — revisa que los datos estén importados.",
    "Today's AI token budget for this account is used up. It resets tomorrow.":
        "El presupuesto de tokens de IA de hoy para esta cuenta se acabó. Se reinicia mañana.",
    "Today's AI request budget for this account is used up. It resets tomorrow.":
        "El presupuesto de peticiones de IA de hoy para esta cuenta se acabó. Se reinicia "
        "mañana.",
    "This month's free MUSAI usage is used up. It resets on the 1st — see Settings ▸ Usage for "
    "where it went.":
        "El uso gratuito de MUSAI de este mes se acabó. Se reinicia el día 1 — en "
        "Ajustes ▸ Uso puedes ver en qué se fue.",
    "Assistant error: {reason}": "Error del asistente: {reason}",

    # ── the landing page ─────────────────────────────────────────────────────────────────
    # The first thing a colleague sees, and the only screen where the language can be chosen
    # before signing in. It is translated in full for that reason.
    "MUSAI — the console behind your courses": "MUSAI — la consola detrás de tus cursos",
    "MUSAI reads your Moodle gradebook, computes each partial and saves it into SEGA. "
    "Professor sign-in, @uach.mx only.":
        "MUSAI lee tu libro de calificaciones de Moodle, calcula cada parcial y lo guarda en "
        "SEGA. Acceso para profesores, sólo @uach.mx.",
    "Professor console": "Consola del profesor",
    "Every course you teach, on <em>one console</em>.":
        "Todos los cursos que das, en <em>una sola consola</em>.",
    "MUSAI reads your Moodle gradebook, computes each partial and saves it into SEGA. It "
    "builds the course, dates every activity, and writes to your groups — <strong>then stops, "
    "and waits for you to confirm.</strong>":
        "MUSAI lee tu libro de calificaciones de Moodle, calcula cada parcial y lo guarda en "
        "SEGA. Arma el curso, le pone fechas a cada actividad y escribe a tus grupos — "
        "<strong>y ahí se detiene, a esperar a que tú confirmes.</strong>",
    # 🔴 Same badge as the cockpit's, same word: *Simulacro*. The landing page and the app must
    # not name the same state two different ways.
    "Dry-run · no writes": "Simulacro · sin escrituras",
    "Live · writes enabled": "En vivo · escrituras activas",
    "Every write to Moodle and SEGA is simulated until you say otherwise.":
        "Toda escritura a Moodle y a SEGA se simula mientras no digas lo contrario.",
    "Writes to Moodle and SEGA are enabled.":
        "Las escrituras a Moodle y a SEGA están activas.",
    "Switch between the light and the dark treatment":
        "Cambiar entre el tratamiento claro y el oscuro",
    "Dark": "Oscuro",
    "Light": "Claro",

    # ── the landing page ▸ refusals ──────────────────────────────────────────────────────
    "Configuration incomplete": "Configuración incompleta",
    "is not set in <code>.env</code>. Until then every cockpit route answers 503 — MUSAI seals "
    "itself rather than serving the gradebook without a gate. Set the value and restart "
    "uvicorn fully; <code>--reload</code> does not reread <code>.env</code>.":
        "no está definido en <code>.env</code>. Hasta entonces toda ruta del panel responde "
        "503 — MUSAI se sella a sí mismo antes que servir el libro de calificaciones sin "
        "puerta. Define el valor y reinicia uvicorn por completo; <code>--reload</code> no "
        "vuelve a leer <code>.env</code>.",
    "are not set in <code>.env</code>. Until then every cockpit route answers 503 — MUSAI "
    "seals itself rather than serving the gradebook without a gate. Set the values and restart "
    "uvicorn fully; <code>--reload</code> does not reread <code>.env</code>.":
        "no están definidos en <code>.env</code>. Hasta entonces toda ruta del panel responde "
        "503 — MUSAI se sella a sí mismo antes que servir el libro de calificaciones sin "
        "puerta. Define los valores y reinicia uvicorn por completo; <code>--reload</code> no "
        "vuelve a leer <code>.env</code>.",
    "Wrong account": "Cuenta equivocada",
    "<code>{email}</code> is not an <b>@{domain}</b> address. MUSAI accepts university accounts "
    "only. Sign in again and pick your <b>@{domain}</b> account — or switch accounts in Google "
    "first.":
        "<code>{email}</code> no es una dirección <b>@{domain}</b>. MUSAI acepta únicamente "
        "cuentas universitarias. Vuelve a entrar y elige tu cuenta <b>@{domain}</b> — o cambia "
        "de cuenta en Google primero.",
    "That account is not an <b>@{domain}</b> address. MUSAI accepts university accounts only. "
    "Sign in again and pick your <b>@{domain}</b> account — or switch accounts in Google "
    "first.":
        "Esa cuenta no es una dirección <b>@{domain}</b>. MUSAI acepta únicamente cuentas "
        "universitarias. Vuelve a entrar y elige tu cuenta <b>@{domain}</b> — o cambia de "
        "cuenta en Google primero.",
    "Student account": "Cuenta de alumno",
    "<code>{email}</code> is a student address.":
        "<code>{email}</code> es una dirección de alumno.",
    "That is a student address.": "Esa es una dirección de alumno.",
    "MUSAI is the professor's console — your courses, grades and deadlines live in Moodle, and "
    "SUSAI answers on WhatsApp. If you are a professor, sign in with your staff "
    "<b>@{domain}</b> account instead.":
        "MUSAI es la consola del profesor — tus cursos, calificaciones y fechas de entrega "
        "están en Moodle, y SUSAI responde por WhatsApp. Si eres profesor, entra con tu cuenta "
        "de personal <b>@{domain}</b>.",
    "Address not verified": "Dirección sin verificar",
    "Google has not verified the email on that account, so MUSAI cannot treat it as proof of "
    "who you are. Verify it with Google, then sign in again.":
        "Google no ha verificado el correo de esa cuenta, así que MUSAI no puede tomarlo como "
        "prueba de quién eres. Verifícalo con Google y vuelve a entrar.",
    "Session ended": "Sesión terminada",
    "You were signed out after a week of inactivity. Sign in to pick up where you left off.":
        "Se cerró tu sesión tras una semana de inactividad. Entra de nuevo para seguir donde "
        "te quedaste.",
    "Sign-in did not complete": "El acceso no se completó",
    "Google returned before the round trip finished. Try again — if it repeats, check that "
    "this exact origin is listed as an authorized redirect URI on the OAuth client.":
        "Google regresó antes de que terminara el viaje de ida y vuelta. Inténtalo otra vez — "
        "si se repite, revisa que este origen exacto esté listado como URI de redirección "
        "autorizada en el cliente de OAuth.",
    "Signed out": "Sesión cerrada",
    "Your session on this browser is closed.":
        "Tu sesión en este navegador está cerrada.",
    "Sign in with Google": "Entrar con Google",
    "Only <b>@{domain}</b> accounts. Students never sign in here.":
        "Sólo cuentas <b>@{domain}</b>. Los alumnos nunca entran aquí.",
    "Sign-in unavailable": "Acceso no disponible",
    "The console is sealed until it is configured.":
        "La consola queda sellada hasta que se configure.",

    # ── the landing page ▸ what it does ──────────────────────────────────────────────────
    "What it does": "Qué hace",
    "Four jobs, each one a full afternoon by hand.":
        "Cuatro trabajos, cada uno una tarde entera a mano.",
    "Build": "Armar",
    "Copy a course that works": "Copiar un curso que ya funciona",
    "Restore a finished course into another group — sections, dates, filters and tab "
    "visibility intact — then count it back from a fresh login, because a restore's own report "
    "has been wrong more often than it has been right.":
        "Restaura un curso terminado dentro de otro grupo — secciones, fechas, filtros y "
        "visibilidad de pestañas intactos — y luego lo vuelve a contar desde una sesión nueva, "
        "porque el reporte de la propia restauración se ha equivocado más veces de las que ha "
        "acertado.",
    "Schedule": "Calendarizar",
    "Date every activity": "Ponerle fecha a cada actividad",
    "Cut the semester into partials and write open and close dates across every quiz, "
    "assignment and forum. Each one is read back from its own settings form, which is the only "
    "reading that tells the truth.":
        "Parte el semestre en parciales y escribe fechas de apertura y cierre en cada examen, "
        "tarea y foro. Cada una se vuelve a leer desde su propio formulario de configuración, "
        "que es la única lectura que dice la verdad.",
    "Grade": "Calificar",
    "Compute the partial, exactly": "Calcular el parcial, exacto",
    "General 60, Special 20, Exam 20. The exact machine grade is kept untouched; curves and "
    "extra credit are separate, visible layers on top of it, never edits to the original.":
        "General 60, Especial 20, Examen 20. La calificación exacta que calcula la máquina "
        "queda intacta; las curvas y los puntos extra son capas aparte y visibles encima de "
        "ella, nunca ediciones al original.",
    "Reach": "Alcanzar",
    "Write to a whole group": "Escribirle a un grupo entero",
    "One message to every student in a course, recorded before it is sent so that a retry can "
    "never deliver it twice. Who was skipped is stored too, with the reason.":
        "Un mensaje a cada alumno de un curso, registrado antes de enviarse para que un "
        "reintento nunca pueda entregarlo dos veces. También se guarda a quién se omitió, y "
        "por qué.",

    # ── the landing page ▸ the three rails ───────────────────────────────────────────────
    "The three rails": "Los tres rieles",
    "A cut track is a position the code cannot reach. An unbroken one is a switch you can "
    "throw.":
        "Una vía cortada es una posición a la que el código no puede llegar. Una vía entera es "
        "un interruptor que sí puedes mover.",
    "Rail 1": "Riel 1",
    "Rail 2": "Riel 2",
    "Rail 3": "Riel 3",
    "Save, never confirm": "Guardar, nunca confirmar",
    "The SEGA adapter can click <i>Guardar</i>. It has no code path to <i>Confirmar</i> — not a "
    "disabled button, not a warning dialog, simply no route there at all.":
        "El adaptador de SEGA puede hacer clic en <i>Guardar</i>. No tiene ninguna ruta de "
        "código hacia <i>Confirmar</i> — no es un botón deshabilitado ni un cuadro de "
        "advertencia: simplemente no hay camino hasta ahí.",
    "Track: locked at Save. Confirm is unreachable — the track is cut.":
        "Vía: fija en Guardar. Confirmar es inalcanzable — la vía está cortada.",
    "Save": "Guardar",
    "Confirm": "Confirmar",
    "Confirming a grade is <b>yours</b>, and stays a human act.":
        "Confirmar una calificación es <b>tuyo</b>, y sigue siendo un acto humano.",
    "Dry-run by default": "Simulacro por omisión",
    "Every write to a live system is simulated first and prints what it would have done. This "
    "is the one rail with a far end you can actually reach — deliberately, because a real "
    "write has to be a decision somebody made out loud.":
        "Toda escritura a un sistema en vivo se simula primero e imprime lo que habría hecho. "
        "Éste es el único riel cuyo extremo sí puedes alcanzar — a propósito, porque una "
        "escritura de verdad tiene que ser una decisión que alguien tomó en voz alta.",
    "Track: currently at Dry-run. Live is reachable but not selected.":
        "Vía: ahora en Simulacro. En vivo es alcanzable pero no está seleccionado.",
    "Track: currently at Live. Writes are enabled.":
        "Vía: ahora en En vivo. Las escrituras están activas.",
    "Dry-run": "Simulacro",
    "Live": "En vivo",
    "Right now: <b>simulating. Nothing reaches Moodle or SEGA.</b>":
        "Ahora mismo: <b>simulando. Nada llega a Moodle ni a SEGA.</b>",
    "Right now: <b>writes are enabled.</b> Someone set this on purpose.":
        "Ahora mismo: <b>las escrituras están activas.</b> Alguien lo dejó así a propósito.",
    "SUSAI is read-only": "SUSAI es de sólo lectura",
    "The student assistant runs in its own process, as its own database role. It can append to "
    "a conversation and nothing else. It cannot see the grading code, let alone run it.":
        "El asistente de los alumnos corre en su propio proceso, con su propio rol de base de "
        "datos. Puede agregar a una conversación y nada más. No alcanza a ver el código de "
        "calificación, mucho menos a ejecutarlo.",
    "Track: locked at Read. Write is unreachable — the track is cut.":
        "Vía: fija en Leer. Escribir es inalcanzable — la vía está cortada.",
    "Read": "Leer",
    "Write": "Escribir",
    "Enforced by the <b>database</b>, not by the prompt.":
        "Lo impone la <b>base de datos</b>, no el prompt.",

    # ── the landing page ▸ SUSAI and the footer ──────────────────────────────────────────
    "The other half": "La otra mitad",
    "Your students never see this console.": "Tus alumnos nunca ven esta consola.",
    "SUSAI · on WhatsApp": "SUSAI · en WhatsApp",
    "Students ask WhatsApp, not you.": "Los alumnos le preguntan a WhatsApp, no a ti.",
    "MUSAI inspires the professor; SUSAI supports the students. Deadlines, grades and what is "
    "due next — answered on the number they already have open, at eleven at night, without a "
    "login and without reaching you.":
        "MUSAI inspira al profesor; SUSAI apoya a los alumnos. Fechas de entrega, "
        "calificaciones y qué sigue — respondido en el número que ya traen abierto, a las once "
        "de la noche, sin iniciar sesión y sin llegarte a ti.",
    "It reads. It never grades, never edits a course, and never sees a roster it was not asked "
    "about.":
        "Lee. Nunca califica, nunca edita un curso y nunca ve una lista por la que no se le "
        "preguntó.",
    "Example WhatsApp exchange with SUSAI":
        "Ejemplo de conversación de WhatsApp con SUSAI",
    "Read-only · no grade was changed in this conversation":
        "Sólo lectura · en esta conversación no se cambió ninguna calificación",
    "Μοῦσαι — the Muses, for the professor": "Μοῦσαι — las Musas, para el profesor",
}
