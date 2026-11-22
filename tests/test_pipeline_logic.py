"""Pipeline orchestration logic, exercised against in-memory fake backends.

FakeForms resolves slot ids the way a real forms backend does (reuse the stored
id, assign a new one otherwise) using the real ``formbuilder.slots_for``, so the
pipeline's behaviour can be checked: idempotent re-publish, answer
de-duplication (resubmissions must not double-count), and resolving a named
audience to Google user ids. The fakes are NOT evidence that the real Google
APIs behave this way — that is what tests/live is for.
"""

from __future__ import annotations

from google_classroom_api import formbuilder
from google_classroom_api.backends import (AssignmentResult, FormSync, ResolvedSlot, ResponseData,
                                             ResponseMeta, StudentInfo, SubmissionInfo)
from google_classroom_api.models import Lesson, Question, Section
from google_classroom_api.pipeline import ALL_STUDENTS, Pipeline
from google_classroom_api.store import Store


class FakeForms:
    def __init__(self):
        self._n = 0
        self.responses: dict = {}
        self.answers: dict = {}
        self.accepting: dict = {}

    def prepare_form(self, form_id, lesson, known) -> FormSync:
        created = form_id is None
        if created:
            self._n += 1
            form_id = f"F{self._n}"
            self.accepting[form_id] = True
        resolved, ops = [], 0
        for slot in formbuilder.slots_for(lesson):
            row = known.get(slot.key)
            if row and row.get("item_id"):
                item_id = row["item_id"]
                question_id = row.get("question_id") or None
                changed = row.get("content_hash") != slot.content_hash
                is_new = False
                ops += 1 if changed else 0
            else:
                self._n += 1
                item_id = f"I{self._n}"
                question_id = f"Q{self._n}" if slot.is_question else None
                is_new, changed = True, False
                ops += 1
            resolved.append(ResolvedSlot(slot.key, slot.kind, slot.site_question_id, item_id,
                                         question_id, slot.content_hash, slot.snapshot, is_new, changed))
        live = {slot.key for slot in formbuilder.slots_for(lesson)}
        deleted = [k for k in known if k not in live]
        ops += len(deleted)
        return FormSync(form_id, f"https://forms/{form_id}/view", resolved, deleted, ops, created, [])

    def set_accepting_responses(self, form_id, accepting):
        self.accepting[form_id] = accepting

    def seed_response(self, form_id, response_id, email, submitted_ms, answers, last_ms=None):
        self.responses.setdefault(form_id, []).append(
            ResponseMeta(response_id, email, submitted_ms, last_ms or submitted_ms))
        self.answers[(form_id, response_id)] = answers

    def list_responses(self, form_id):
        return list(self.responses.get(form_id, []))

    def get_response(self, form_id, response_id):
        meta = next(m for m in self.responses[form_id] if m.response_id == response_id)
        return ResponseData(response_id, meta.email, meta.submitted_ms,
                            [meta.submitted_ms, meta.last_submitted_ms],
                            self.answers[(form_id, response_id)], {}, None)


class FakeClassroom:
    supports_missing_work_grade = False
    supports_individual_students = True

    def __init__(self, roster):
        self._roster = roster
        self.created, self.updated = [], []
        self._n = 0

    def list_students(self, course_id):
        return [StudentInfo(uid, email, name) for uid, email, name in self._roster]

    def ensure_topic(self, course_id, name):
        return f"topic:{name}"

    def create_assignment(self, course_id, *, title, instructions="", points=100, topic_id=None,
                          due=None, due_has_time=True, form_url=None, student_ids=None, publish=True):
        self._n += 1
        self.created.append({"student_ids": list(student_ids) if student_ids else None,
                             "form_url": form_url, "publish": publish})
        return AssignmentResult(f"CW{self._n}", "PUBLISHED" if publish else "DRAFT", "link")

    def update_assignment(self, course_id, coursework_id, *, title, instructions="", points=100,
                          topic_id=None, due=None, due_has_time=True, form_url=None,
                          student_ids=None, publish=True):
        self.updated.append({"coursework_id": coursework_id,
                             "student_ids": list(student_ids) if student_ids else None})
        return AssignmentResult(coursework_id, "PUBLISHED" if publish else "DRAFT", "link")

    def list_submissions(self, course_id, coursework_id):
        return [SubmissionInfo(uid, "TURNED_IN") for uid, _, _ in self._roster]

    def set_missing_work_grade(self, course_id, *, enabled, percent=None):
        raise AssertionError("not supported")


LESSON = Lesson(site_id="L12", title="Lesson 12", sections=[Section(questions=[
    Question("q1", "single", "Which?", choices=["2/4", "2/3"], correct=["2/4"], points=1),
    Question("q2", "short", "2+2?", correct=["4", "four"], points=1),
])])


def make(roster):
    return Pipeline(FakeForms(), FakeClassroom(roster), Store(":memory:"))


def test_publish_then_republish_is_idempotent():
    p = make([("u1", "a@x.com", "Ann")])
    first = p.publish_lesson(LESSON, course_id=1, group="7A", audience=ALL_STUDENTS)
    assert first.created_form and first.created_assignment and first.form_ops_sent == 2
    again = p.publish_lesson(LESSON, course_id=1, group="7A", audience=ALL_STUDENTS)
    assert not again.created_form and not again.created_assignment
    assert again.form_ops_sent == 0
    assert again.form_id == first.form_id
    assert len(p.classroom.created) == 1 and len(p.classroom.updated) == 1


def test_named_audience_resolves_to_google_user_ids():
    p = make([("u1", "a@x.com", "Ann"), ("u2", "b@x.com", "Bob"), ("u3", "c@x.com", "Cat")])
    p.sync_roster(1)
    p.bind_student(1, "s-1", email="a@x.com")
    p.bind_student(1, "s-2", email="b@x.com")
    res = p.publish_lesson(LESSON, course_id=1, group="7A", audience=["s-1", "s-2"])
    assert res.recipients == ["s-1", "s-2"]
    assert p.classroom.created[0]["student_ids"] == ["u1", "u2"]


def test_resubmission_updates_without_double_counting():
    p = make([("u1", "a@x.com", "Ann")])
    p.sync_roster(1)
    p.bind_student(1, "s-1", email="a@x.com")
    res = p.publish_lesson(LESSON, course_id=1, group="7A", audience=ALL_STUDENTS)
    form_id = res.form_id
    qids = {q["site_question_id"]: q["question_id"] for q in res.questions}

    p.forms.seed_response(form_id, "R1", "a@x.com", 1000, {qids["q1"]: ["2/3"], qids["q2"]: ["4"]})
    out1 = p.sync_responses("L12", "7A")
    assert len(out1["created"]) == 2 and out1["updated"] == []
    assert p.sync_responses("L12", "7A") == {"created": [], "updated": [], "unknown_respondents": []}

    p.forms.responses[form_id] = []
    p.forms.seed_response(form_id, "R1", "a@x.com", 2000, {qids["q1"]: ["2/4"], qids["q2"]: ["4"]})
    out2 = p.sync_responses("L12", "7A")
    assert out2["created"] == []
    statuses = {r["site_question_id"]: r["status"] for r in out2["updated"]}
    assert statuses["q1"] == "correct" and "q2" not in statuses


def test_named_audience_with_unknown_recipient_raises_before_sending():
    from google_classroom_api.errors import RecipientError
    p = make([("u1", "a@x.com", "Ann")])
    p.sync_roster(1)
    try:
        p.publish_lesson(LESSON, course_id=1, group="7A", audience=["nobody"])
        assert False, "expected RecipientError"
    except RecipientError as exc:
        assert exc.unmatched == ["nobody"]
    assert p.classroom.created == []


def test_web_classroom_backend_refuses_named_students():
    from google_classroom_api.errors import ClassroomAPIError
    from google_classroom_api.pipeline import Pipeline as P

    class WebLike:
        supports_missing_work_grade = True
        supports_individual_students = False
    pipe = P(FakeForms(), WebLike(), Store(":memory:"))
    try:
        pipe.publish_lesson(LESSON, course_id=1, group="7A", audience=["s-1"])
        assert False, "expected ClassroomAPIError"
    except ClassroomAPIError:
        pass
