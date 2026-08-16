from datetime import date, datetime
from typing import List, Optional
from sqlmodel import Field, SQLModel, Relationship


class Semester(SQLModel, table=True):
    __tablename__ = "semester"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)  # e.g. "2026-2"
    starts_on: date
    ends_on: date
    is_active: bool = Field(default=False)

    courses: List["Course"] = Relationship(back_populates="semester")


class Professor(SQLModel, table=True):
    """One signed-in professor. The Google `@uach.mx` email is the only identity key.

    Created on first sign-in — there is no invite, no admin approval and no password here.
    The gate that decides *who may exist at all* is `musai/web/auth.py`; by the time a row is
    written, the address has already passed the domain check and the student-local-part check.

    🔴 **This table is what makes `Course.professor_id` mean something.** Until 2026-08-14 that
    column was a nullable int pointing at nothing, and every course query was unscoped — which
    was harmless while the DB had one user and is a **cross-professor student-data leak** the
    moment it has two. Courses are scoped to their owner, and a NULL owner belongs to nobody
    rather than to everybody: an unowned row is invisible, not universally visible.

    `is_coordinator` is deliberately NOT `is_admin`. Per HANDOFF ▶ NEXT §3 the coordinator role
    grants the power to **act on** a colleague's course — never to **read** their students' data.
    Neither flag is self-grantable.
    """
    __tablename__ = "professor"

    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(index=True, unique=True)     # professor@uach.mx
    full_name: str = Field(default="")
    picture: Optional[str] = None
    is_admin: bool = Field(default=False)
    is_coordinator: bool = Field(default=False)
    # 🔴 Nullable, and `None` means "never chose" — NOT "chose English". Same distinction as
    # `Course.professor_id` above, and it exists for the same reason: so the default can change
    # without silently overriding everyone who actually made a choice. See
    # `musai/web/language.py`.
    language: Optional[str] = Field(default=None)     # "en" | "es" | None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_seen_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def moodle_username_guess(self) -> str:
        """`professor@uach.mx` → `professor`. A PRE-FILL, never the stored value.

        UACH Moodle usernames match the email local-part for every account measured so far,
        but that is a convention the university owns, not a rule MUSAI can enforce — so this
        only populates the field and the professor confirms or corrects it.
        """
        return (self.email or "").split("@")[0].strip().lower()


class ProfessorCredential(SQLModel, table=True):
    """One professor's login for one external system, encrypted at rest.

    🔴 **The password is never stored, logged, rendered or returned.** `secret_enc` holds a
    Fernet token and only `musai/security/vault.py` can turn it back into a password, using a
    key that lives in `.env` / Replit Secrets and never in this database. Losing the key makes
    every stored credential permanently unreadable — which is the correct failure, and why the
    Settings page can only ever *replace* a password, never show one.

    This is a real escalation of what MUSAI holds: before this table it knew only the owner's own
    `.env`. Two rails come with it — a credential is written only by the professor it belongs
    to, and `musai/automation/credentials.py` **refuses rather than falling back** to anyone
    else's account. The dangerous failure here is not an error; it is a restore that quietly
    runs as the wrong person.

    ⚠️ A stored password is not consent, and last semester's consent is not this semester's.
    Nothing in code can check that; the delete button is what makes withdrawing it possible.
    """
    __tablename__ = "professor_credential"

    id: Optional[int] = Field(default=None, primary_key=True)
    professor_id: int = Field(foreign_key="professor.id", index=True)
    system: str = Field(index=True)          # "moodle" | "sega"
    username: str = Field(default="")
    secret_enc: str = Field(default="")      # Fernet token — NEVER the password
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    last_used_at: Optional[datetime] = None
    last_ok_at: Optional[datetime] = None    # last time it actually authenticated

    def __repr__(self) -> str:  # keep the token out of tracebacks and pytest diffs
        return (f"ProfessorCredential(professor_id={self.professor_id}, system={self.system!r}, "
                f"username={self.username!r}, secret_enc=<hidden>)")


class Course(SQLModel, table=True):
    __tablename__ = "course"

    id: Optional[int] = Field(default=None, primary_key=True)
    # 🔴 The owner. NULL means "nobody's" and the cockpit will not show it — see `Professor`.
    professor_id: Optional[int] = Field(default=None, index=True)
    semester_id: int = Field(foreign_key="semester.id", index=True)
    subject: str  # "Inglés I" – "Inglés IV"
    level: int    # 1–4
    group_code: str = Field(index=True)  # "1-LED-A"
    moodle_course_id: Optional[str] = None
    moodle_env: str = Field(default="prod")  # "staging" | "prod"
    sega_group_label: Optional[str] = None   # label as it appears in SEGA group list

    # ── Filled by the Moodle mapper (`musai/mapping.py`), blank on hand-made rows ──
    # `moodle_server` is the `data-server` the portal tile carries ("virtual3"), so a later run
    # can reach the course without walking the dashboard again. `moodle_fullname` is the tile's
    # own text — kept because a restore verifies the target against the course's LIVE name, and
    # a stored name is the only thing to compare a fresh read against.
    moodle_server: Optional[str] = None
    moodle_fullname: Optional[str] = None
    cycle: Optional[str] = None              # "PRIMER SEMESTRE" — as the tile states it
    mapped_at: Optional[datetime] = None

    # 🔴 When the gradebook was last ingested — and therefore how old the student count is.
    # The owner found 1-LED-A showing "Students 10" while the live course held 30+: MUSAI's
    # enrolment comes ONLY from a gradebook export (`grading/ingest.py`), never from the
    # participants page, so the number was a two-month-old snapshot presented as a fact with
    # no date on it. `None` means "never imported" and must render as that, not as zero.
    # ⚠️ The count is a property of the last FILE, not of the course. Anything reading it to
    # decide who is enrolled is reading a cache — `messaging.check_counts` is the model: it
    # treats a participant MUSAI has never heard of as evidence the roster is stale, and refuses.
    gradebook_ingested_at: Optional[datetime] = None

    semester: Optional[Semester] = Relationship(back_populates="courses")
    partials: List["Partial"] = Relationship(back_populates="course")
    enrollments: List["Enrollment"] = Relationship(back_populates="course")
    activities: List["Activity"] = Relationship(back_populates="course")


class Student(SQLModel, table=True):
    __tablename__ = "student"

    id: Optional[int] = Field(default=None, primary_key=True)
    matricula: str = Field(unique=True, index=True)
    full_name: str
    # Moodle keys people by user id (`user31033`); MUSAI keys them by matrícula, and nothing
    # joined the two until the participants page was read. It is derivable — course emails
    # are `a<matricula>@uach.mx` — but only from a page nobody was loading, so it is stored
    # the first time a roster read sees it. This is also what makes reading one student's
    # messages (v2) possible at all.
    moodle_user_id: Optional[str] = Field(default=None, index=True)

    enrollments: List["Enrollment"] = Relationship(back_populates="student")
    whatsapp_links: List["WhatsAppLink"] = Relationship(back_populates="student")


class Enrollment(SQLModel, table=True):
    __tablename__ = "enrollment"

    id: Optional[int] = Field(default=None, primary_key=True)
    student_id: int = Field(foreign_key="student.id", index=True)
    course_id: int = Field(foreign_key="course.id", index=True)

    student: Optional[Student] = Relationship(back_populates="enrollments")
    course: Optional[Course] = Relationship(back_populates="enrollments")


class WhatsAppLink(SQLModel, table=True):
    __tablename__ = "whatsapp_link"

    id: Optional[int] = Field(default=None, primary_key=True)
    student_id: int = Field(foreign_key="student.id", index=True)
    phone_e164: str = Field(index=True)  # e.g. "+526141234567"
    verified: bool = Field(default=False)
    source: str = Field(default="manual")  # "moodle_assignment" | "manual"
    bound_at: datetime = Field(default_factory=datetime.utcnow)

    student: Optional[Student] = Relationship(back_populates="whatsapp_links")


class Partial(SQLModel, table=True):
    __tablename__ = "partial"

    id: Optional[int] = Field(default=None, primary_key=True)
    course_id: int = Field(foreign_key="course.id", index=True)
    name: str  # "Parcial 1" | "Parcial 2" | "Examen Final Ordinario"
    sega_evaluacion: str  # "PARCIAL 1" | "PARCIAL 2" | "EXAMEN FINAL ORDINARIO"
    sega_date: Optional[str] = None  # "02/03/2026"
    weight_general: float = Field(default=0.60)
    weight_special: float = Field(default=0.20)
    weight_exam: float = Field(default=0.20)
    moodle_section_ref: Optional[str] = None

    course: Optional[Course] = Relationship(back_populates="partials")
    activities: List["Activity"] = Relationship(back_populates="partial")
    partial_grades: List["PartialGrade"] = Relationship(back_populates="partial")


class Activity(SQLModel, table=True):
    __tablename__ = "activity"

    id: Optional[int] = Field(default=None, primary_key=True)
    course_id: int = Field(foreign_key="course.id", index=True)
    partial_id: Optional[int] = Field(default=None, foreign_key="partial.id", index=True)
    name: str
    category: str  # "general" | "special" | "exam" | "forum"
    moodle_item_name: Optional[str] = None  # raw Moodle column base name
    max_points: float = Field(default=100.0)
    ai_gradable: bool = Field(default=False)
    rubric: Optional[str] = None

    course: Optional[Course] = Relationship(back_populates="activities")
    partial: Optional[Partial] = Relationship(back_populates="activities")
    grades: List["Grade"] = Relationship(back_populates="activity")


class Grade(SQLModel, table=True):
    __tablename__ = "grade"

    id: Optional[int] = Field(default=None, primary_key=True)
    student_id: int = Field(foreign_key="student.id", index=True)
    activity_id: int = Field(foreign_key="activity.id", index=True)
    value: float  # percentage 0–100
    source: str = Field(default="moodle_csv")  # "moodle_csv" | "ai" | "manual"
    status: str = Field(default="draft")  # "draft" | "saved"
    needs_review: bool = Field(default=False)
    graded_at: datetime = Field(default_factory=datetime.utcnow)
    note: Optional[str] = None

    activity: Optional["Activity"] = Relationship(back_populates="grades")


class PartialGrade(SQLModel, table=True):
    __tablename__ = "partial_grade"

    id: Optional[int] = Field(default=None, primary_key=True)
    student_id: int = Field(foreign_key="student.id", index=True)
    partial_id: int = Field(foreign_key="partial.id", index=True)
    value_0_10: float  # the EXACT machine grade — never mutated by a curve
    components_json: str = Field(default="{}")  # JSON: {general, special, exam, breakdown}
    computed_at: datetime = Field(default_factory=datetime.utcnow)
    sega_status: str = Field(default="none")  # "none" | "dry_run_ok" | "saved"
    uploaded_at: Optional[datetime] = None

    # Three distinguishable layers (PLAN §6 "Exactness, and explicit curves"):
    #   value_0_10      — the EXACT machine grade (never mutated)
    #   final_value_0_10 — curve/override result ("curve base"); None = use exact
    #   extra_points     — additive human extra-credit (e.g. cultural participation)
    # What uploads to SEGA = clamp(curve_base + extra_points).
    curve_mode: str = Field(default="none")   # "none" | "auto" | "manual"
    final_value_0_10: Optional[float] = None
    curve_note: Optional[str] = None
    extra_points: float = Field(default=0.0)
    extra_note: Optional[str] = None

    partial: Optional[Partial] = Relationship(back_populates="partial_grades")

    @property
    def curve_base(self) -> float:
        """The curved/overridden grade (before extra credit); exact if no curve."""
        return self.final_value_0_10 if self.final_value_0_10 is not None else self.value_0_10

    @property
    def sega_value(self) -> float:
        """The grade that uploads: curve base + extra credit, clamped to [0, 10]."""
        return round(max(0.0, min(10.0, self.curve_base + (self.extra_points or 0.0))), 1)


class HubProfile(SQLModel, table=True):
    """Who a professor is, for the course-hub page. Typed ONCE; every course reuses it.

    This table is the whole reason the hub is not copy-pasted HTML: the phone number that
    appears three times on the rendered page lives in exactly one row here.

    ``owner`` is the same namespaced actor key the AI ledger uses (``web:carlos`` today).
    When Google sign-in lands it becomes the signed-in professor's email and nothing else
    about this table changes.

    The fields themselves are a JSON blob, not columns, deliberately: the hub's field list
    will keep growing as colleagues ask for things, and none of those additions should need
    a migration on a live Postgres.
    """
    __tablename__ = "hub_profile"

    id: Optional[int] = Field(default=None, primary_key=True)
    owner: str = Field(index=True, unique=True)
    data_json: str = Field(default="{}")
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class CourseHub(SQLModel, table=True):
    """What differs per group: the title, that group's chat link, contents, weights."""
    __tablename__ = "course_hub"

    id: Optional[int] = Field(default=None, primary_key=True)
    course_id: int = Field(foreign_key="course.id", index=True, unique=True)
    data_json: str = Field(default="{}")
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class CourseSchedule(SQLModel, table=True):
    """The *Cronograma*: when each tab's activities open and close.

    Two JSON blobs rather than columns, for two different reasons.

    ``data_json`` holds the teaching window, the number of periods and — the part that must
    persist — **the professor's corrections to the guessed tab map**. The guess is only ever a
    pre-fill; once someone has said "Watch and Write belongs to Parcial 2", that decision has
    to outlive the next re-read of the course.

    ``snapshot_json`` is the last structure read from Moodle (tabs, activities, module types).
    Caching it is what makes re-cutting the calendar free: without it every tweak costs 14
    page loads, because `format_onetopic` renders one tab per request.
    """
    __tablename__ = "course_schedule"

    id: Optional[int] = Field(default=None, primary_key=True)
    course_id: int = Field(foreign_key="course.id", index=True, unique=True)
    data_json: str = Field(default="{}")
    snapshot_json: str = Field(default="{}")
    read_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class MessageBatch(SQLModel, table=True):
    """One composed message and the send it produced (or the dry run that did not).

    🔴 **This table exists in v1 even though v1 only writes to it**, for a reason that is not
    tidiness: **Moodle offers no marker that makes a re-send idempotent.** A published label
    carries `musai:block:<slug>`, so republishing edits in place — a *message* has nothing of
    the kind. MUSAI's own record is therefore the only way it can know it already said this,
    and without it a double-click messages 32 students twice.

    ``purpose`` is a column rather than something inferred from the body because the
    professor-evaluation rubric's criterion 6 is *"al menos dos mensajes de seguimiento"* —
    a COUNT of a KIND. No amount of reading message bodies back recovers that reliably.
    """
    __tablename__ = "message_batch"

    id: Optional[int] = Field(default=None, primary_key=True)
    course_id: int = Field(foreign_key="course.id", index=True)
    semester_id: Optional[int] = Field(default=None, foreign_key="semester.id", index=True)
    purpose: str = Field(default="aviso", index=True)  # bienvenida|seguimiento|cierre|aviso
    body: str = ""
    actor: str = Field(default="carlos")
    dry_run: bool = Field(default=True)
    only_me: bool = Field(default=False)
    recipient_count: int = Field(default=0)
    expected_count: int = Field(default=0)   # what MUSAI's own enrolment said
    moodle_count: Optional[int] = None       # what Moodle's page said ("Agregado … N")
    ok: bool = Field(default=False)
    error: Optional[str] = None
    body_hash: str = Field(default="", index=True)   # idempotency guard
    created_at: datetime = Field(default_factory=datetime.utcnow)
    sent_at: Optional[datetime] = None

    recipients: List["MessageRecipient"] = Relationship(back_populates="batch")


class MessageRecipient(SQLModel, table=True):
    """Who a batch went to — **and who it deliberately did not, with the reason.**

    Recording exclusions is not symmetry for its own sake. "the owner was excluded (self)" and
    "three students were not on the page" are different facts, and only the second is a bug
    report. A table that stored only inclusions makes them indistinguishable.
    """
    __tablename__ = "message_recipient"

    id: Optional[int] = Field(default=None, primary_key=True)
    batch_id: int = Field(foreign_key="message_batch.id", index=True)
    student_id: Optional[int] = Field(default=None, foreign_key="student.id", index=True)
    moodle_user_id: Optional[str] = Field(default=None, index=True)
    matricula: Optional[str] = None
    full_name: str = ""
    included: bool = Field(default=True)
    excluded_reason: Optional[str] = None

    batch: Optional[MessageBatch] = Relationship(back_populates="recipients")


# SUSAI tables
class Conversation(SQLModel, table=True):
    __tablename__ = "conversation"

    id: Optional[int] = Field(default=None, primary_key=True)
    phone_e164: str = Field(index=True)
    student_id: Optional[int] = Field(default=None, foreign_key="student.id", index=True)
    status: str = Field(default="open")  # "open" | "unverified" | "blocked"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    messages: List["Message"] = Relationship(back_populates="conversation")


class Message(SQLModel, table=True):
    __tablename__ = "message"

    id: Optional[int] = Field(default=None, primary_key=True)
    conversation_id: int = Field(foreign_key="conversation.id", index=True)
    direction: str  # "in" | "out"
    role: str       # "user" | "assistant" | "system"
    body: str
    wa_message_id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    conversation: Optional[Conversation] = Relationship(back_populates="messages")


class UsageCounter(SQLModel, table=True):
    __tablename__ = "usage_counter"

    id: Optional[int] = Field(default=None, primary_key=True)
    phone_e164: str = Field(index=True)
    day: date = Field(index=True)
    msg_count: int = Field(default=0)
    ai_count: int = Field(default=0)


class AiUsage(SQLModel, table=True):
    """Per-actor, per-day Gemini accounting — the spend ledger.

    Distinct from ``UsageCounter``, which counts SUSAI *messages* for conversational rate
    limiting. This counts TOKENS and API round-trips, because message counts do not bound
    cost: one question with function calling can fan out into several billed calls.

    ``actor`` is a namespaced key, e.g. ``web:carlos``, ``wa:526141837420``.
    """
    __tablename__ = "ai_usage"

    id: Optional[int] = Field(default=None, primary_key=True)
    actor: str = Field(index=True)
    day: date = Field(index=True)
    requests: int = Field(default=0)     # billed API round-trips (incl. tool-call turns)
    tokens_in: int = Field(default=0)
    tokens_out: int = Field(default=0)
    errors: int = Field(default=0)
    blocked: int = Field(default=0)      # times the budget refused a call
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class UsageEvent(SQLModel, table=True):
    """One priced action — the receipt line behind the Usage tab.

    Distinct from ``AiUsage``, which is a per-day *counter* enforcing the daily cap. This is an
    itemised ledger: one row per thing the professor did that cost money, so "where did my
    allowance go?" is answerable by kind and by day rather than as a single total.

    🔴 **``micro_usd`` is computed when the event happens and never recomputed.** Gemini's
    3.6/3.7 introductory price doubles on 2027-01-01 and Replit's rates move on their own
    schedule, so a ledger that re-prices history at today's card would report that last month
    got more expensive while nobody did anything. The receipt is the record; the rate card is
    not. ``rate_card`` names the version that priced this row, so an old number can always be
    explained.

    Integer millionths of a dollar, not a float: the whole point of the number is a percentage
    of a $0.10 budget, which is exactly the magnitude where accumulated float error surfaces.

    ⚠️ Only actions that cost something measurable are written here — AI calls and browser
    jobs. A page view costs $0.0000008, and a row per page view would cost more to store than
    the view it measures. See `musai/metering.py`.
    """
    __tablename__ = "usage_event"

    id: Optional[int] = Field(default=None, primary_key=True)
    actor: str = Field(index=True)       # the signed-in professor's email, or wa:<phone>
    day: date = Field(index=True)        # local day, for monthly rollups without TZ surprises
    kind: str = Field(index=True)        # "assistant" | "course_restore" | …
    detail: str = Field(default="")      # free text for the tab, e.g. a group code
    tokens_in: int = Field(default=0)
    tokens_out: int = Field(default=0)
    model: str = Field(default="")       # which model priced the tokens
    seconds: float = Field(default=0.0)  # wall-clock of the work, for compute
    requests: int = Field(default=1)
    micro_usd: int = Field(default=0)    # millionths of a USD, priced at event time
    rate_card: str = Field(default="")   # rate-card version that produced micro_usd
    created_at: datetime = Field(default_factory=datetime.utcnow)


class JobRequest(SQLModel, table=True):
    """Phase 4: cockpit → local runner job queue."""
    __tablename__ = "job_request"

    id: Optional[int] = Field(default=None, primary_key=True)
    kind: str  # e.g. "upload_partial"
    params_json: str = Field(default="{}")
    status: str = Field(default="pending")  # "pending"|"running"|"done"|"failed"
    requested_by: str = Field(default="carlos")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: Optional[datetime] = None
    result_json: str = Field(default="{}")


class AuditLog(SQLModel, table=True):
    __tablename__ = "audit_log"

    id: Optional[int] = Field(default=None, primary_key=True)
    actor: str = Field(default="system")  # "system" | "susai" | "carlos"
    action: str
    target: Optional[str] = None
    env: Optional[str] = None  # "staging" | "prod" | None
    dry_run: bool = Field(default=True)
    detail_json: str = Field(default="{}")
    created_at: datetime = Field(default_factory=datetime.utcnow)
