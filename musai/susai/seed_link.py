"""Seed a VERIFIED WhatsAppLink so SUSAI recognizes a test number as a real student.

For dev only — lets the owner talk to SUSAI as if he were an enrolled student (no real students
needed until August). Uses the RW engine (an admin/dev action), stores the canonical phone.

Usage:
  python -m musai.susai.seed_link --phone 526141837420                 # first student in 1-LED-A
  python -m musai.susai.seed_link --phone 526141837420 --group 1-LED-A
  python -m musai.susai.seed_link --phone 526141837420 --matricula 348521
  python -m musai.susai.seed_link --phone 526141837420 --name "ARLETH"
"""

import argparse

from sqlmodel import Session, select

from musai.db import engine
from musai.models import Course, Enrollment, Student, WhatsAppLink
from musai.susai.identity import canonical


def main() -> None:
    ap = argparse.ArgumentParser(description="Seed a verified WhatsAppLink for SUSAI testing.")
    ap.add_argument("--phone", required=True, help="phone in any form, e.g. 526141837420")
    ap.add_argument("--group", default="1-LED-A", help="group to pick a student from (default 1-LED-A)")
    ap.add_argument("--matricula", help="exact matrícula to link")
    ap.add_argument("--name", help="substring of the student's name to link")
    ap.add_argument("--remove", action="store_true",
                    help="unlink this phone (delete its WhatsAppLink) instead of linking")
    args = ap.parse_args()

    phone = canonical(args.phone)
    with Session(engine) as s:
        if args.remove:
            links = [l for l in s.exec(select(WhatsAppLink)).all()
                     if canonical(l.phone_e164) == phone]
            for l in links:
                s.delete(l)
            s.commit()
            print(f"✓ Removed {len(links)} WhatsAppLink(s) for {phone}.")
            return
        student = None
        if args.matricula:
            student = s.exec(select(Student).where(Student.matricula == args.matricula)).first()
        elif args.name:
            cands = [st for st in s.exec(select(Student)).all()
                     if args.name.lower() in st.full_name.lower()]
            student = cands[0] if cands else None
        else:
            from musai.semesters import course_for
            course = course_for(s, args.group)  # active semester
            if course:
                enr = s.exec(select(Enrollment).where(Enrollment.course_id == course.id)
                             .order_by(Enrollment.id)).first()
                student = s.get(Student, enr.student_id) if enr else None

        if not student:
            print("✗ No matching student found. Seed the DB first (python -m musai.seed).")
            return

        existing = next((l for l in s.exec(select(WhatsAppLink)).all()
                         if canonical(l.phone_e164) == phone), None)
        link = existing or WhatsAppLink(student_id=student.id, phone_e164=phone)
        link.student_id = student.id
        link.phone_e164 = phone
        link.verified = True
        link.source = "manual"
        s.add(link)
        s.commit()
        print(f"✓ Linked {phone} → {student.full_name} ({student.matricula}) [verified] "
              f"— SUSAI will now treat this number as that student.")


if __name__ == "__main__":
    main()
