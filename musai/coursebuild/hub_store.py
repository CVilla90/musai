"""Where the course hub's two dicts live. Pure persistence — nothing here renders HTML.

The scope split from `hub.FIELDS` is enforced *here*, at the storage boundary, not only in the
form: `save_profile` can only ever write profile keys and `save_course` only course keys. That
is what stops the phone number from quietly acquiring seven per-course copies again — the
exact failure the original hand-written page had.
"""

import json
from datetime import datetime
from typing import Any, Optional

from sqlmodel import Session, select

from musai.coursebuild.hub import COURSE_KEYS, PROFILE_KEYS, resolve
from musai.models import CourseHub, HubProfile

# Same namespaced actor key the AI ledger uses. Becomes the signed-in professor's email when
# Google sign-in lands (AUTH_SETUP.md); `test_default_owner_matches_the_app_actor` pins it.
DEFAULT_OWNER = "web:carlos"


def _loads(raw: str) -> dict:
    """A corrupt blob must not take the page down — an empty profile still renders."""
    try:
        data = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _clean(data: dict, keys: tuple) -> dict:
    return {k: str(data.get(k, "") or "").strip() for k in keys}


def load_profile(sess: Session, owner: str = DEFAULT_OWNER) -> dict:
    row = sess.exec(select(HubProfile).where(HubProfile.owner == owner)).first()
    return _loads(row.data_json) if row else {}


def save_profile(sess: Session, data: dict, owner: str = DEFAULT_OWNER) -> dict:
    kept = _clean(data, PROFILE_KEYS)
    row = sess.exec(select(HubProfile).where(HubProfile.owner == owner)).first()
    if row is None:
        row = HubProfile(owner=owner)
    row.data_json = json.dumps(kept, ensure_ascii=False)
    row.updated_at = datetime.utcnow()
    sess.add(row)
    sess.commit()
    return kept


def load_course(sess: Session, course_id: int) -> dict:
    row = sess.exec(select(CourseHub).where(CourseHub.course_id == course_id)).first()
    return _loads(row.data_json) if row else {}


def save_course(sess: Session, course_id: int, data: dict) -> dict:
    kept = _clean(data, COURSE_KEYS)
    row = sess.exec(select(CourseHub).where(CourseHub.course_id == course_id)).first()
    if row is None:
        row = CourseHub(course_id=course_id)
    row.data_json = json.dumps(kept, ensure_ascii=False)
    row.updated_at = datetime.utcnow()
    sess.add(row)
    sess.commit()
    return kept


def load_merged(sess: Session, course: Any, owner: str = DEFAULT_OWNER) -> dict:
    """What the renderer gets: defaults ← the professor's profile ← this course."""
    course_id = getattr(course, "id", None)
    return resolve(load_profile(sess, owner),
                   load_course(sess, course_id) if course_id else {},
                   course=course)


def profile_owner_for(course: Any, fallback: str = DEFAULT_OWNER) -> str:
    """Seam for the multi-professor future: `Course.professor_id` already exists, so when
    sign-in lands this returns that professor's key instead of the single-user fallback."""
    professor_id: Optional[int] = getattr(course, "professor_id", None)
    return f"professor:{professor_id}" if professor_id else fallback
