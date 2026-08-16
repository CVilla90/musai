"""Record every batch — the only thing that makes a re-send detectable.

A published label carries `musai:block:<slug>`, so republishing edits in place. **A message
has no such marker**, in Moodle or anywhere else: once sent it is 32 separate notifications
with no shared identity. So the record here is not history, it is the guard — and it has to
be written even on a dry run, or "have I already sent this?" has no answer between the run
that failed and the run that retries.
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence

from sqlmodel import Session, select

from musai.automation.messaging import PURPOSES, body_hash
from musai.models import (
    AuditLog, Course, Enrollment, MessageBatch, MessageRecipient, Student,
)

# How long an identical body counts as "already said". Long enough to cover a professor
# clicking twice or a job being retried; short enough that a genuine weekly reminder with
# the same wording is not blocked forever.
REPEAT_WINDOW_HOURS = 12


def enrolled_students(sess: Session, course_id: int) -> List[Student]:
    """MUSAI's own enrolment — the number the page's checkbox count is checked against."""
    rows = sess.exec(
        select(Student).join(Enrollment, Enrollment.student_id == Student.id)
        .where(Enrollment.course_id == course_id).order_by(Student.full_name)).all()
    return list(rows)


def recent_duplicate(sess: Session, course_id: int, body: str,
                     *, hours: int = REPEAT_WINDOW_HOURS) -> Optional[MessageBatch]:
    """A batch with this exact body, really sent to this course, inside the window.

    Only non-dry-run, successful batches count: a dry run said nothing to anybody, and a
    failed send is the case where retrying is precisely what you want to do.
    """
    since = datetime.utcnow() - timedelta(hours=hours)
    return sess.exec(
        select(MessageBatch)
        .where(MessageBatch.course_id == course_id,
               MessageBatch.body_hash == body_hash(body),
               MessageBatch.dry_run == False,          # noqa: E712 — SQL, not Python
               MessageBatch.ok == True,                # noqa: E712
               MessageBatch.created_at >= since)
        .order_by(MessageBatch.id.desc())).first()


def record(sess: Session, *, course: Course, purpose: str, body: str, result: Dict,
           actor: str = "carlos") -> MessageBatch:
    """Persist one batch and everyone it touched — included and excluded alike."""
    if purpose not in PURPOSES:
        purpose = "aviso"

    by_matricula = {s.matricula: s for s in enrolled_students(sess, course.id)}
    batch = MessageBatch(
        course_id=course.id, semester_id=course.semester_id, purpose=purpose, body=body,
        actor=actor, dry_run=bool(result.get("dry_run", True)),
        only_me=bool(result.get("only_me")),
        recipient_count=len(result.get("recipients", [])),
        expected_count=int(result.get("expected") or 0),
        moodle_count=result.get("moodle_count"),
        ok=bool(result.get("ok")), error=result.get("error"),
        body_hash=body_hash(body),
        sent_at=(datetime.utcnow()
                 if result.get("ok") and not result.get("dry_run") else None),
    )
    sess.add(batch)
    sess.commit()
    sess.refresh(batch)

    for group, included in ((result.get("recipients", []), True),
                            (result.get("excluded", []), False)):
        for r in group:
            student = by_matricula.get(r.get("matricula"))
            sess.add(MessageRecipient(
                batch_id=batch.id, student_id=student.id if student else None,
                moodle_user_id=r.get("moodle_user_id"), matricula=r.get("matricula"),
                full_name=r.get("full_name") or "", included=included,
                excluded_reason=None if included else r.get("excluded_reason")))

    # Learn the matrícula ↔ Moodle-user-id join while it is in front of us. It is the thing
    # that makes reading one student's messages (v2) possible, and the roster page is the
    # only place it appears.
    for r in result.get("recipients", []) + result.get("excluded", []):
        student = by_matricula.get(r.get("matricula"))
        if student and r.get("moodle_user_id") and not student.moodle_user_id:
            student.moodle_user_id = str(r["moodle_user_id"])
            sess.add(student)

    sess.add(AuditLog(
        actor=actor, action="message.send", target=f"course:{course.id}",
        env=course.moodle_env, dry_run=batch.dry_run,
        detail_json=_detail(batch, result)))
    sess.commit()
    sess.refresh(batch)
    return batch


def _detail(batch: MessageBatch, result: Dict) -> str:
    import json
    return json.dumps({
        "purpose": batch.purpose,
        "body": batch.body,
        "only_me": batch.only_me,
        "recipients": [{"id": r.get("moodle_user_id"), "name": r.get("full_name"),
                        "matricula": r.get("matricula")}
                       for r in result.get("recipients", [])],
        "excluded": [{"name": r.get("full_name"), "why": r.get("excluded_reason")}
                     for r in result.get("excluded", [])],
        "moodle_count": result.get("moodle_count"),
        "screenshot": result.get("screenshot"),
    }, ensure_ascii=False)


def history(sess: Session, course_id: int, limit: int = 25) -> List[MessageBatch]:
    return list(sess.exec(
        select(MessageBatch).where(MessageBatch.course_id == course_id)
        .order_by(MessageBatch.id.desc()).limit(limit)).all())


def rubric_counts(sess: Session, course_id: int) -> Dict[str, int]:
    """How the professor-evaluation rubric reads this course's messaging.

    Criterion 6 is *"al menos dos mensajes de seguimiento"* — a count of a KIND, which is
    exactly why `purpose` is a column. Dry runs are excluded: the rubric asks what students
    received, not what was rehearsed.
    """
    out = {p: 0 for p in PURPOSES}
    for b in sess.exec(select(MessageBatch).where(
            MessageBatch.course_id == course_id,
            MessageBatch.dry_run == False,          # noqa: E712
            MessageBatch.ok == True)).all():        # noqa: E712
        out[b.purpose] = out.get(b.purpose, 0) + 1
    return out


def recipients_of(sess: Session, batch_id: int) -> Sequence[MessageRecipient]:
    return sess.exec(select(MessageRecipient)
                     .where(MessageRecipient.batch_id == batch_id)).all()
