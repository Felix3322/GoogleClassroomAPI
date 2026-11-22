"""The whole flow against the real Google servers over OAuth: lesson → form →
assignment → answers. These are the tests that count.

Skipped unless these are set:

    GCA_OAUTH_TOKEN      path to an authorized_user JSON (from `authorize(...)`)
    GCA_LIVE_COURSE_ID   numeric id of a scratch class that account teaches
    GCA_LIVE_STUDENT_IDS optional, comma-separated Google user ids or emails of
                         test students in that class, to exercise assigning to
                         named students

    pytest tests/live/test_pipeline_live.py -v

It creates one form and one assignment in the class. Use a scratch class.
Everything is checked by reading it back from Google in a fresh OAuth session,
so a pass means the server really stored it.
"""

from __future__ import annotations

import json
import os
import time

import pytest

from google_classroom_api import Store, connect_oauth
from google_classroom_api.models import Lesson, Question, Section
from google_classroom_api.pipeline import ALL_STUDENTS

TOKEN = os.environ.get("GCA_OAUTH_TOKEN")
COURSE_ID = os.environ.get("GCA_LIVE_COURSE_ID")
STUDENT_IDS = [s for s in os.environ.get("GCA_LIVE_STUDENT_IDS", "").split(",") if s.strip()]

pytestmark = pytest.mark.skipif(not (TOKEN and COURSE_ID),
                                reason="set GCA_OAUTH_TOKEN and GCA_LIVE_COURSE_ID")

Q1 = Question("q1", "single", "1. Which fraction equals one half?", choices=["2/4", "2/3", "3/4"],
              correct=["2/4"], points=1, feedback_right="Correct.", feedback_wrong="Not quite.")
Q2 = Question("q2", "short", "2. What is 2 + 2?", correct=["4", "four"], points=1)


def _token_info():
    with open(TOKEN, "r", encoding="utf-8") as f:
        return json.load(f)


def test_publish_is_repeatable_and_reordering_keeps_question_ids(tmp_path):
    course = int(COURSE_ID)
    pipeline = connect_oauth(_token_info(), Store(str(tmp_path / "state.sqlite3")))
    stamp = int(time.time())

    def lesson(*questions):
        return Lesson(site_id=f"live-{stamp}", title=f"cfb oauth lesson {stamp}",
                      description="Created by a live test.", sections=[Section(questions=list(questions))])

    first = pipeline.publish_lesson(lesson(Q1, Q2), course_id=course, group="g6", audience=ALL_STUDENTS,
                                  topic="cfb live topic", points=10)
    assert first.created_form and first.created_assignment and first.assignment_state == "PUBLISHED"
    assert first.responder_url and first.assignment_link

    # Read the form back in a fresh OAuth session.
    fresh = connect_oauth(_token_info(), Store(str(tmp_path / "read.sqlite3")))
    form = fresh.forms.read_form(first.form_id)
    expected_ids = [q["item_id"] for q in first.questions]
    assert [i["itemId"] for i in form.get("items", [])] == expected_ids
    assert form["info"]["title"] == f"cfb oauth lesson {stamp}"

    # Publishing again changes nothing and creates nothing.
    again = pipeline.publish_lesson(lesson(Q1, Q2), course_id=course, group="g6", audience=ALL_STUDENTS,
                                  topic="cfb live topic", points=10)
    assert not again.created_form and not again.created_assignment and again.form_ops_sent == 0
    assert (again.form_id, again.assignment_id) == (first.form_id, first.assignment_id)

    # Swapping the questions keeps their Google ids and reorders the form.
    swapped = pipeline.publish_lesson(lesson(Q2, Q1), course_id=course, group="g6", audience=ALL_STUDENTS,
                                    topic="cfb live topic", points=10)
    ids = {q["site_question_id"]: q["question_id"] for q in first.questions}
    assert {q["site_question_id"]: q["question_id"] for q in swapped.questions} == ids
    reordered = connect_oauth(_token_info(), Store(str(tmp_path / "read2.sqlite3"))).forms.read_form(first.form_id)
    swapped_item_ids = [q["item_id"] for q in swapped.questions]
    assert [i["itemId"] for i in reordered.get("items", [])] == swapped_item_ids

    # Collecting twice in a row never yields the same answer twice.
    pipeline.sync_responses(f"live-{stamp}", "g6")
    assert pipeline.sync_responses(f"live-{stamp}", "g6")["created"] == []

    # Submission states are readable.
    subs = pipeline.sync_submissions(f"live-{stamp}", "g6")
    assert isinstance(subs, list)

    pipeline.withdraw(f"live-{stamp}", "g6")  # closes the form; should not raise


@pytest.mark.skipif(not STUDENT_IDS, reason="set GCA_LIVE_STUDENT_IDS to test named-student assignment")
def test_can_assign_to_named_students(tmp_path):
    course = int(COURSE_ID)
    pipeline = connect_oauth(_token_info(), Store(str(tmp_path / "named.sqlite3")))
    pipeline.sync_roster(course)
    # Bind each given Google id/email to a site id and target exactly those.
    site_ids = []
    for i, ident in enumerate(STUDENT_IDS):
        site_id = f"s-{i}"
        if "@" in ident:
            pipeline.bind_student(course, site_id, email=ident)
        else:
            pipeline.bind_student(course, site_id, user_id=ident)
        site_ids.append(site_id)
    stamp = int(time.time())
    lesson = Lesson(site_id=f"named-{stamp}", title=f"cfb named {stamp}",
                    sections=[Section(questions=[Q1])])
    result = pipeline.publish_lesson(lesson, course_id=course, group="indiv", audience=site_ids,
                                   topic="cfb live topic", points=5)
    assert result.recipients == site_ids and result.assignment_state == "PUBLISHED"
