"""Live tests against the real Google servers. These are the ones that count.

They are skipped unless you point them at a signed-in session and at
throwaway objects you own:

    GCA_COOKIE_FILE     Netscape cookies.txt exported from a browser where the
                        test account is signed in
    GCA_LIVE_FORM_ID    file id of a scratch Google Form owned by that account
                        (the part after /forms/d/ in its edit link); optional,
                        needed by the form-attachment test and by the opt-in
                        web Forms tests (GCA_WEB_FORMS=1, expected to fail with
                        exported cookies: Forms editing is bound to the device)
    GCA_LIVE_COURSE_ID  numeric id of a scratch Classroom class it teaches

    pytest tests/live -k TestClassroom -v

Every check reads the result back from Google, so a pass means the server
really stored the change. The Classroom tests only ever create drafts, which
students cannot see, and delete what they create; they leave one topic
("cfb live topic") in the class and put the missing-work setting back.
"""

from __future__ import annotations

import os
import time

import pytest

from google_classroom_api.web import ClassroomWeb, FormsWeb, WebProtocolError, WebSession
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
# Publishing notifies the students of the class, so those tests are opt-in.
PUBLISH = os.environ.get("GCA_LIVE_PUBLISH") == "1"

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

    def test_all_courses_are_listed_and_include_the_test_class(self, session):
        from google_classroom_api import Classroom
        courses = Classroom(session).courses()
        assert COURSE_ID in [c["course_id"] for c in courses]
        assert all(c["name"] for c in courses)

    def test_material_draft_with_a_link_is_created_listed_and_deleted(self, session):
        from google_classroom_api import Classroom
        cls, course = Classroom(session), int(COURSE_ID)
        made = cls.create_material(course, f"gca live material {int(time.time())}", description="Live test.",
                                   link_urls=["https://example.com/"], publish=False)
        item_id = str(made["item_id"])
        try:
            listed = [m for m in cls.materials(course) if m["item_id"] == item_id]
            assert listed and listed[0]["state"] == cw.STATE_DRAFT and listed[0]["text"] == "Live test."
            assert all(a["item_id"] != item_id for a in cls.assignments(course)), "a material is not an assignment"
        finally:
            cls.delete_material(course, int(item_id))
        assert all(m["item_id"] != item_id for m in cls.materials(course))

    def test_announcement_draft_is_created_listed_and_deleted(self, session):
        from google_classroom_api import Classroom
        cls, course = Classroom(session), int(COURSE_ID)
        text = f"gca live announcement {int(time.time())}"
        made = cls.create_announcement(course, text, publish=False)
        item_id = str(made["item_id"])
        try:
            assert any(d["item_id"] == item_id and d["text"] == text
                       for d in cls.announcements(course, drafts=True))
            assert all(a["item_id"] != item_id for a in cls.announcements(course)), "a draft is not in the feed"
        finally:
            cls.delete_announcement(course, int(item_id))
        assert all(d["item_id"] != item_id for d in cls.announcements(course, drafts=True))

    def test_draft_grade_is_set_read_back_and_cleared(self, session):
        """Needs a published assignment and a student in the test class. Draft grades
        are visible to teachers only; the cell is left empty afterwards."""
        from google_classroom_api import Classroom
        cls, course = Classroom(session), int(COURSE_ID)
        published = [a for a in cls.assignments(course) if a["state"] == cw.STATE_PUBLISHED]
        students = cls.students(course)
        if not (published and students):
            pytest.skip("the test class needs a published assignment and a student")
        item, student = int(published[0]["item_id"]), students[0]["user_id"]
        try:
            assert cls.set_draft_grade(course, item, student, 6)["draft_grade"] == 6
            mine = [s for s in cls.submissions(course, item) if s["student_id"] == student]
            assert mine and mine[0]["draft_grade"] == 6
        finally:
            assert cls.clear_draft_grade(course, item, student)["draft_grade"] is None
        mine = [s for s in cls.submissions(course, item) if s["student_id"] == student]
        assert mine and mine[0]["draft_grade"] is None and mine[0]["grade_history"]

    def test_question_drafts_of_both_types_are_created_changed_listed_and_deleted(self, session):
        from google_classroom_api import Classroom
        cls, course = Classroom(session), int(COURSE_ID)
        stamp = int(time.time())
        short = str(cls.create_question(course, f"gca live short {stamp}", instructions="One sentence.",
                                        publish=False)["item_id"])
        choice = str(cls.create_question(course, f"gca live choice {stamp}", choices=["A", "B"],
                                         publish=False)["item_id"])
        try:
            rows = {q["item_id"]: q for q in cls.questions(course)}
            assert rows[short]["choices"] is None and rows[short]["text"] == "One sentence."
            assert rows[choice]["choices"] == ["A", "B"] and rows[choice]["state"] == cw.STATE_DRAFT
            cls.update_question(course, int(short), f"gca live short {stamp}", choices=["yes", "no"])
            assert {q["item_id"]: q for q in cls.questions(course)}[short]["choices"] == ["yes", "no"]
        finally:
            cls.delete_question(course, int(short))
            cls.delete_question(course, int(choice))
        assert not {short, choice} & {q["item_id"] for q in cls.questions(course)}

    def test_a_refused_write_raises_and_changes_nothing(self, session):
        """A delete whose question block has the wrong type is answered with HTTP 200
        and an error status; the library must not report that as success."""
        web, course = ClassroomWeb(session), int(COURSE_ID)
        made = web.create_question(course, f"gca live refused {int(time.time())}", choices=["A", "B"])
        item = int(made["item_id"])
        try:
            with pytest.raises(cw.StreamItemRejected):
                web.write_item("F7Tqub", cw.delete_arguments(course, item, cw.KIND_QUESTION))
            assert any(r["item_id"] == str(item) for r in web.list_classwork(course)), "refused, so still there"
        finally:
            web.delete_question(course, item)
        assert all(r["item_id"] != str(item) for r in web.list_classwork(course))

    def test_topic_is_renamed_and_deleted(self, session):
        from google_classroom_api import Classroom
        cls, course = Classroom(session), int(COURSE_ID)
        name = f"gca live topic {int(time.time())}"
        topic_id = str(cls.ensure_topic(course, name)["topic_id"])
        try:
            cls.rename_topic(course, topic_id, name + " renamed")
            assert {str(t["topic_id"]): t["name"] for t in cls.topics(course)}[topic_id] == name + " renamed"
        finally:
            cls.delete_topic(course, topic_id)
        assert topic_id not in {str(t["topic_id"]) for t in cls.topics(course)}

    @pytest.mark.skipif(not FORM_ID, reason="set GCA_LIVE_FORM_ID to the file id of a scratch form")
    def test_a_form_is_attached_by_its_file_link_and_its_public_link_is_refused(self, session):
        from google_classroom_api import Classroom
        cls, course = Classroom(session), int(COURSE_ID)
        web = ClassroomWeb(session)
        file_link = f"https://docs.google.com/forms/d/{FORM_ID}/edit"
        made = cls.create_assignment(course, f"gca live form {int(time.time())}", form_url=file_link,
                                     publish=False)
        item_id = str(made["item_id"])
        try:
            section = web.call("dpT4Vd", [[100, None, 2, 0], [course], None, 1, 2, cw.READ_MASKS])
            entry = next(e for e in section[2] if str(e[1][0][0][0]) == item_id)
            assert entry[1][0][9] == [cw.STATE_DRAFT] and len(entry[1][0][27][0][4]) == 1, "one attachment kept"
        finally:
            cls.delete_assignment(course, int(item_id))

        public_link = web.resolve_link(file_link)["url"]
        assert "/forms/d/e/" in public_link
        before = {a["item_id"] for a in cls.assignments(course)}
        with pytest.raises(WebProtocolError):
            cls.create_assignment(course, "must not stay", form_url=public_link, publish=False)
        assert {a["item_id"] for a in cls.assignments(course)} == before, "the half-made draft was removed"

    def test_an_update_keeps_what_is_not_passed(self, session):
        from datetime import datetime, timedelta, timezone
        from google_classroom_api import Classroom
        cls, course = Classroom(session), int(COURSE_ID)
        due = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=7)
        made = cls.create_assignment(course, f"gca live keep {int(time.time())}", instructions="Keep me.",
                                     points=7, topic="cfb live topic", due=due,
                                     link_urls=["https://example.com/"], publish=False)
        item_id = made["item_id"]
        topic_id = str(cls.ensure_topic(course, "cfb live topic")["topic_id"])
        try:
            cls.update_assignment(course, item_id, made["title"] + " (renamed)")
            row = cls.item(course, item_id)
            assert row["title"].endswith("(renamed)") and row["text"] == "Keep me." and row["points"] == 7
            assert row["due"] == due and row["due_has_time"] is True and row["topic_id"] == topic_id
            assert len(row["attachment_ids"]) == 1 and row["state"] == cw.STATE_DRAFT

            cls.update_assignment(course, item_id, points=9, topic=None, due=None)
            row = cls.item(course, item_id)
            assert row["points"] == 9 and row["topic_id"] is None and row["due"] is None
            assert row["title"].endswith("(renamed)") and row["text"] == "Keep me."
            assert len(row["attachment_ids"]) == 1
        finally:
            cls.delete_assignment(course, item_id)
        with pytest.raises(LookupError):
            cls.item(course, item_id)

    def test_deleting_a_topic_keeps_the_posts_under_it(self, session):
        from google_classroom_api import Classroom
        cls, course = Classroom(session), int(COURSE_ID)
        name = f"gca live topic with a post {int(time.time())}"
        made = cls.create_assignment(course, name, topic=name, publish=False)
        topic_id = cls.item(course, made["item_id"])["topic_id"]
        try:
            assert topic_id
            cls.delete_topic(course, topic_id)
            assert cls.item(course, made["item_id"])["topic_id"] is None
        finally:
            cls.delete_assignment(course, made["item_id"])

    @pytest.mark.skipif(not PUBLISH, reason="publishing notifies the class's students; set GCA_LIVE_PUBLISH=1 "
                                            "only for a class that has no real students")
    def test_drafts_of_every_kind_are_published_as_they_are_and_deleted(self, session):
        from google_classroom_api import Classroom
        cls, course = Classroom(session), int(COURSE_ID)
        stamp = int(time.time())
        drafts = {
            "assignment": cls.create_assignment(course, f"gca live publish a {stamp}", points=5,
                                                link_urls=["https://example.com/"], publish=False),
            "material": cls.create_material(course, f"gca live publish m {stamp}", description="Read this.",
                                            link_urls=["https://example.com/"], publish=False),
            "question": cls.create_question(course, f"gca live publish q {stamp}", choices=["A", "B"],
                                            publish=False),
            "announcement": cls.create_announcement(course, f"gca live publish n {stamp}", publish=False),
        }
        delete = {"assignment": cls.delete_assignment, "material": cls.delete_material,
                  "question": cls.delete_question, "announcement": cls.delete_announcement}
        try:
            for kind, draft in drafts.items():
                before = cls.item(course, draft["item_id"])
                assert before["state"] == cw.STATE_DRAFT and before["kind"] == kind
                assert cls.publish(course, draft["item_id"])["state"] == cw.STATE_PUBLISHED
                after = cls.item(course, draft["item_id"])
                assert after["state"] == cw.STATE_PUBLISHED
                for field in ("title", "text", "points", "attachment_ids"):
                    assert after[field] == before[field], (kind, field)
                assert after.get("choices") == before.get("choices")
            posted = {a["item_id"] for a in cls.announcements(course)}
            assert drafts["announcement"]["item_id"] in posted
        finally:
            for kind, draft in drafts.items():
                delete[kind](course, draft["item_id"])
        for draft in drafts.values():
            with pytest.raises(LookupError):
                cls.item(course, draft["item_id"])
