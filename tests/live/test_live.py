"""Live tests against the real Google servers. These are the ones that count.

They are skipped unless you point them at a signed-in session and at
throwaway objects you own:

    GCA_COOKIE_FILE     Netscape cookies.txt exported from a browser where the
                        test account is signed in
    GCA_LIVE_FORM_ID    id of a scratch Google Form owned by that account
    GCA_LIVE_COURSE_ID  numeric id of a scratch Classroom class it teaches

    pytest tests/live -v

Every check reads the result back from Google in a fresh session, so a pass
means the server really stored the change. The tests add and remove a question
on the form, flip settings and put them back, and leave one topic and one
draft assignment in the class.
"""

from __future__ import annotations

import os
import time

import pytest

from google_classroom_api.web import ClassroomWeb, FormsWeb, WebSession
from google_classroom_api.web import classroom as cw

COOKIES = os.environ.get("GCA_COOKIE_FILE")
FORM_ID = os.environ.get("GCA_LIVE_FORM_ID")
COURSE_ID = os.environ.get("GCA_LIVE_COURSE_ID")

# The web (cookie) backend only still works for Classroom. Google binds the
# Docs/Forms session to the device, so exported cookies cannot drive the form
# editor from a non-browser client (it is redirected to a read-only / login
# page). The TestForm checks below therefore only pass when GCA_COOKIE_FILE is
# somehow a live, device-bound session; they are opt-in via GCA_WEB_FORMS=1 and
# are expected to fail with ordinary exported cookies. Use the OAuth forms
# backend for anything headless (see tests/live/test_pipeline_live.py).
WEB_FORMS = os.environ.get("GCA_WEB_FORMS") == "1"

pytestmark = pytest.mark.skipif(not COOKIES, reason="set GCA_COOKIE_FILE to run against real Google")


@pytest.fixture(scope="module")
def session() -> WebSession:
    return WebSession.from_cookie_file(COOKIES)


def fresh(session: WebSession):
    return FormsWeb(session).open(FORM_ID)


@pytest.mark.skipif(not (FORM_ID and WEB_FORMS),
                    reason="web Forms editing needs a device-bound browser session; set GCA_LIVE_FORM_ID "
                           "and GCA_WEB_FORMS=1 to attempt it (exported cookies are expected to fail)")
class TestForm:
    def test_settings_are_stored_by_google(self, session):
        before = fresh(session).settings()
        try:
            editor = fresh(session)
            editor.set_quiz(is_quiz=True, release_grades_immediately=not before["release_grades_immediately"],
                            show_correct_answers=not before["show_correct_answers"])
            editor.set_after_submit(allow_response_edits=not before["allow_response_edits"])
            editor.set_responder(shuffle_questions=not before["shuffle_questions"],
                                 progress_bar=not before["progress_bar"])
            after = fresh(session).settings()
            assert after["is_quiz"] is True
            for key in ("release_grades_immediately", "show_correct_answers", "allow_response_edits",
                        "shuffle_questions", "progress_bar"):
                assert after[key] == (not before[key]), key
        finally:
            editor = fresh(session)
            editor.set_quiz(is_quiz=before["is_quiz"],
                            release_grades_immediately=before["release_grades_immediately"],
                            show_correct_answers=before["show_correct_answers"])
            editor.set_after_submit(allow_response_edits=before["allow_response_edits"])
            editor.set_responder(shuffle_questions=before["shuffle_questions"],
                                 progress_bar=before["progress_bar"])
        assert fresh(session).settings() == before

    def test_question_keeps_its_ids_through_move_and_rewrite(self, session):
        editor = fresh(session)
        count = len(editor.items)
        item_id, question_id = editor.add_question(
            "single", "live test question", options=["a", "b"], correct=["a"], points=1)
        try:
            items = fresh(session).items
            assert len(items) == count + 1
            assert items[-1][0] == item_id and items[-1][4][0][0] == question_id

            if count:
                editor.move_item(count, 0)
                items = fresh(session).items
                assert items[0][0] == item_id and items[0][4][0][0] == question_id
                editor.move_item(0, count)

            editor.replace_question(count, item_id, question_id, "single", "live test question (edited)",
                                    options=["a", "b", "c"], correct=["a"], points=1)
            last = fresh(session).items[-1]
            assert (last[0], last[1], last[4][0][0]) == (item_id, "live test question (edited)", question_id)
            assert [option[0] for option in last[4][0][1]] == ["a", "b", "c"]
        finally:
            current = fresh(session)
            index = [item[0] for item in current.items].index(item_id)
            current.delete_item(index)
        assert len(fresh(session).items) == count

    def test_responses_can_be_listed_and_read(self, session):
        editor = fresh(session)
        for entry in editor.list_responses()[:3]:
            response = editor.get_response(entry["response_id"])
            assert response["email"] == entry["email"]
            assert isinstance(response["answers"], dict)


@pytest.mark.skipif(not COURSE_ID, reason="set GCA_LIVE_COURSE_ID")
class TestClassroom:
    def test_topic_is_created_once_and_listed(self, session):
        web = ClassroomWeb(session)
        name = "cfb live topic"
        first = web.ensure_topic(int(COURSE_ID), name)
        second = web.ensure_topic(int(COURSE_ID), name)
        assert first["topic_id"] == second["topic_id"]
        assert name in [topic["name"] for topic in web.list_topics(int(COURSE_ID))]

    def test_missing_work_grade_round_trip(self, session):
        web = ClassroomWeb(session)
        course = int(COURSE_ID)
        before = web.get_missing_work_grade(course)
        target = 35.0 if before["percent"] != 35.0 else 40.0
        try:
            assert web.set_missing_work_grade(course, enabled=True, percent=target) == {
                "enabled": True, "percent": target}
            assert web.set_missing_work_grade(course, enabled=False) == {"enabled": False, "percent": target}
        finally:
            web.set_missing_work_grade(course, enabled=before["enabled"], percent=before["percent"])
        assert web.get_missing_work_grade(course) == before

    def test_roster_lists_the_students_with_ids_and_emails(self, session):
        students = ClassroomWeb(session).list_students(int(COURSE_ID))
        assert students, "the live test class should have at least one student"
        assert all(s["user_id"].isdigit() and "@" in (s["email"] or "") for s in students)

    def test_assignment_is_listed_under_its_topic_and_deleted(self, session):
        from google_classroom_api import Classroom
        cls = Classroom(session)
        course = int(COURSE_ID)
        topic = cls.ensure_topic(course, "cfb live topic")
        draft = cls.create_assignment(course, f"cfb live delete {int(time.time())}", topic="cfb live topic",
                                      publish=False)
        listed = [a for a in cls.assignments(course) if a["item_id"] == str(draft["item_id"])]
        assert listed and listed[0]["topic_id"] == topic["topic_id"] and listed[0]["state"] == cw.STATE_DRAFT
        cls.delete_assignment(course, int(draft["item_id"]))
        assert all(a["item_id"] != str(draft["item_id"]) for a in cls.assignments(course))

    def test_named_students_are_refused_rather_than_sent_to_everyone(self, session):
        from google_classroom_api import Classroom
        before = {a["item_id"] for a in Classroom(session).assignments(int(COURSE_ID))}
        with pytest.raises(NotImplementedError):
            Classroom(session).create_assignment(int(COURSE_ID), "must not exist", student_ids=["1"])
        assert {a["item_id"] for a in Classroom(session).assignments(int(COURSE_ID))} == before

    def test_draft_assignment_can_be_created_and_rewritten(self, session):
        web = ClassroomWeb(session)
        title = f"cfb live draft {int(time.time())}"
        draft = web.create_assignment(int(COURSE_ID), title, instructions="Created by a live test.")
        try:
            assert draft["state"] == cw.STATE_DRAFT and draft["title"] == title
            renamed = web.update_assignment(int(COURSE_ID), int(draft["item_id"]), title + " (edited)",
                                            instructions="Edited by a live test.")
            assert renamed["state"] == cw.STATE_DRAFT and renamed["title"] == title + " (edited)"
        finally:
            web.delete_assignment(int(COURSE_ID), int(draft["item_id"]))
