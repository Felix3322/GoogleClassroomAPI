#!/usr/bin/env python3
"""[Kept for the later Forms work — the Classroom API needs no OAuth; see classroom_quickstart.py.]

Run GoogleClassroomAPI end to end over the official OAuth APIs.

One-time setup (see docs/setup-oauth.md):
  1. In Google Cloud, enable the Classroom API and the Forms API.
  2. Create an OAuth client of type "Desktop app"; download it as client_secret.json.
  3. Add your teacher account as a test user on the OAuth consent screen.

Then:
  python examples/oauth_quickstart.py authorize client_secret.json   # one-time consent -> token.json
  python examples/oauth_quickstart.py courses                        # find your course id
  python examples/oauth_quickstart.py demo <course_id>               # publish a tiny quiz to the whole class
  python examples/oauth_quickstart.py answers <course_id>            # pull per-question answers
  python examples/oauth_quickstart.py withdraw <course_id>           # close the demo form

token.json holds a refresh token: treat it like a password. Nothing here needs
a browser after `authorize`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# Make the package importable when run straight from a checkout.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from google_classroom_api import Store, authorize, connect_oauth  # noqa: E402
from google_classroom_api.models import Lesson, Question, Section  # noqa: E402
from google_classroom_api.pipeline import ALL_STUDENTS  # noqa: E402

TOKEN_FILE = os.environ.get("GCA_OAUTH_TOKEN", "token.json")
STORE_FILE = os.environ.get("GCA_STORE", "gca.sqlite3")
DEMO_SITE_ID = "demo-lesson"
DEMO_GROUP = "demo"

DEMO = Lesson(
    site_id=DEMO_SITE_ID, title="GoogleClassroomAPI demo quiz",
    description="A two-question demo published by GoogleClassroomAPI.",
    sections=[Section(questions=[
        Question("q1", "single", "1. Which fraction equals one half?",
                 choices=["2/4", "2/3", "3/4"], correct=["2/4"], points=1,
                 feedback_right="Correct.", feedback_wrong="Not quite."),
        Question("q2", "short", "2. What is 2 + 2?", correct=["4", "four"], points=1),
    ])])


def _pipeline():
    if not os.path.exists(TOKEN_FILE):
        sys.exit(f"no {TOKEN_FILE}; run: python {sys.argv[0]} authorize client_secret.json")
    with open(TOKEN_FILE, "r", encoding="utf-8") as f:
        info = json.load(f)
    return connect_oauth(info, Store(STORE_FILE))


def cmd_authorize(args):
    info = authorize(args.client_secret)
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        json.dump(info, f)
    print(f"saved {TOKEN_FILE}. Keep it secret. Now: python {sys.argv[0]} courses")


def cmd_courses(args):
    pipeline = _pipeline()
    for c in pipeline.classroom.list_courses():
        print(f"{c['id']}\t{c['name']}  {c.get('section') or ''}")


def cmd_demo(args):
    pipeline = _pipeline()
    audience = ALL_STUDENTS
    if args.students:
        pipeline.sync_roster(int(args.course_id))
        audience = []
        for i, ident in enumerate(args.students.split(",")):
            site = f"s-{i}"
            pipeline.bind_student(int(args.course_id), site,
                                **({"email": ident} if "@" in ident else {"user_id": ident}))
            audience.append(site)
    result = pipeline.publish_lesson(DEMO, course_id=int(args.course_id), group=DEMO_GROUP,
                                   audience=audience, topic="GoogleClassroomAPI demo", points=10)
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    if result.dropped_form_settings:
        print("note: the official API could not set:", ", ".join(result.dropped_form_settings))


def cmd_answers(args):
    pipeline = _pipeline()
    out = pipeline.sync_responses(DEMO_SITE_ID, DEMO_GROUP)
    print(f"new answer records: {len(out['created'])}, updated: {len(out['updated'])}, "
          f"unknown respondents: {out['unknown_respondents']}")
    for rec in out["created"] + out["updated"]:
        print(f"  {rec['site_student_id']}  {rec['site_question_id']}={rec['raw_answer']}  [{rec['status']}]")


def cmd_withdraw(args):
    _pipeline().withdraw(DEMO_SITE_ID, DEMO_GROUP)
    print("demo form closed to new responses.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("authorize"); a.add_argument("client_secret"); a.set_defaults(fn=cmd_authorize)
    c = sub.add_parser("courses"); c.set_defaults(fn=cmd_courses)
    d = sub.add_parser("demo"); d.add_argument("course_id")
    d.add_argument("--students", help="comma-separated Google user ids or emails for named assignment")
    d.set_defaults(fn=cmd_demo)
    n = sub.add_parser("answers"); n.add_argument("course_id"); n.set_defaults(fn=cmd_answers)
    w = sub.add_parser("withdraw"); w.add_argument("course_id"); w.set_defaults(fn=cmd_withdraw)
    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
