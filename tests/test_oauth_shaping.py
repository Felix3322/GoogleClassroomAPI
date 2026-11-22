"""Request-shaping tests for the official-API backends.

They check that the library sends Google exactly the JSON the docs call for —
URLs, bodies, update masks, the individual-students fields, the write-control
revision — by capturing requests with an httpx MockTransport. They do NOT prove
Google accepts the requests (that is the live tests' job); they guard the
request shapes and the create→reply id plumbing against regressions.
"""

from __future__ import annotations

import json

import httpx
import pytest

from google_classroom_api.classroom import ClassroomApi
from google_classroom_api.forms import FormsApi
from google_classroom_api.models import Lesson, Question, Section
from google_classroom_api.oauth_backends import OAuthClassroomBackend, OAuthFormsBackend
from google_classroom_api.transport import GoogleSession


class Recorder:
    def __init__(self):
        self.calls = []  # (method, path, body)

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, body))
        path, method = request.url.path, request.method

        if method == "POST" and path == "/v1/forms":
            return httpx.Response(200, json={"formId": "F1", "info": body["info"], "items": [],
                                             "revisionId": "r1", "responderUri": "https://f/view"})
        if path.endswith(":batchUpdate"):
            replies, n = [], 0
            for req in (body or {}).get("requests", []):
                if "createItem" in req:
                    n += 1
                    replies.append({"createItem": {"itemId": f"I{n}", "questionId": [f"Q{n}"]}})
                else:
                    replies.append({})
            return httpx.Response(200, json={"replies": replies,
                                             "form": {"formId": "F1", "responderUri": "https://f/view"}})
        if path.endswith(":setPublishSettings"):
            return httpx.Response(200, json={})
        if method == "GET" and path == "/v1/forms/F1":
            return httpx.Response(200, json={"formId": "F1", "info": {"title": "T"}, "settings": {},
                                             "items": [], "responderUri": "https://f/view", "revisionId": "r1"})
        if method == "GET" and path.endswith("/responses/R1"):
            return httpx.Response(200, json={
                "responseId": "R1", "respondentEmail": "Stu@x.com",
                "createTime": "2026-11-22T10:00:00Z", "lastSubmittedTime": "2026-11-22T10:05:00Z",
                "totalScore": 3,
                "answers": {"Q1": {"questionId": "Q1", "textAnswers": {"answers": [{"value": "2/4"}]},
                                   "grade": {"score": 2, "correct": True}},
                            "Q2": {"questionId": "Q2", "textAnswers": {"answers": [{"value": "x"}]}}}})

        # Classroom
        if method == "GET" and path.endswith("/topics"):
            return httpx.Response(200, json={"topic": [{"topicId": "T9", "name": "Week 1"}]})
        if method == "POST" and path.endswith("/topics"):
            return httpx.Response(200, json={"topicId": "Tnew", "name": body["name"]})
        if method == "POST" and path.endswith("/courseWork"):
            return httpx.Response(200, json={**body, "id": "CW1", "alternateLink": "https://c/w/1"})
        if method == "PATCH" and "/courseWork/" in path:
            return httpx.Response(200, json={**(body or {}), "id": "CW1", "state": body.get("state", "DRAFT")})
        if method == "GET" and path.endswith("/students"):
            return httpx.Response(200, json={"students": [
                {"userId": "123", "profile": {"emailAddress": "A@x.com", "name": {"fullName": "Ann"}}}]})
        if method == "GET" and path.endswith("/studentSubmissions"):
            return httpx.Response(200, json={"studentSubmissions": [
                {"userId": "123", "state": "TURNED_IN", "assignedGrade": 8, "late": False}]})
        if method == "GET" and "/courseWork/" in path:
            return httpx.Response(200, json={"id": "CW1", "assigneeMode": "ALL_STUDENTS"})
        if path.endswith(":modifyAssignees"):
            return httpx.Response(200, json={"id": "CW1"})
        return httpx.Response(404, json={"error": {"message": f"no route for {method} {path}", "status": "NOT_FOUND"}})


def session(rec: Recorder) -> GoogleSession:
    client = httpx.Client(transport=httpx.MockTransport(rec.handler))
    return GoogleSession("tok", client=client)


# --- Forms ----------------------------------------------------------------------

def test_forms_api_create_and_batch_update_shapes():
    rec = Recorder()
    api = FormsApi(session(rec))
    api.create("Lesson 12")
    assert rec.calls[0] == ("POST", "/v1/forms", {"info": {"title": "Lesson 12"}})
    api.batch_update("F1", [{"deleteItem": {"location": {"index": 0}}}], required_revision_id="r1")
    body = rec.calls[-1][2]
    assert body["includeFormInResponse"] is True
    assert body["writeControl"] == {"requiredRevisionId": "r1"}


def test_prepare_form_creates_items_and_fills_server_ids():
    rec = Recorder()
    backend = OAuthFormsBackend(FormsApi(session(rec)))
    lesson = Lesson(site_id="L", title="L", sections=[Section(questions=[
        Question("q1", "single", "Which?", choices=["2/4", "2/3"], correct=["2/4"], points=2,
                 feedback_right="Yes.", feedback_wrong="No."),
        Question("q2", "short", "2+2?", correct=["4", "four"], points=1),
    ])])
    sync = backend.prepare_form(None, lesson, {})
    assert sync.created and sync.form_id == "F1" and sync.responder_url == "https://f/view"
    # ids came back from the createItem replies, in slot order
    by_key = {s.slot_key: s for s in sync.slots}
    assert (by_key["q:q1"].item_id, by_key["q:q1"].question_id) == ("I1", "Q1")
    assert (by_key["q:q2"].item_id, by_key["q:q2"].question_id) == ("I2", "Q2")
    assert all(s.is_new for s in sync.slots)
    assert "shuffle_questions" in sync.dropped_settings
    # a setPublishSettings call was made
    assert any(p.endswith(":setPublishSettings") for _, p, _ in rec.calls)


def test_prepare_form_request_bodies_have_quiz_grading():
    rec = Recorder()
    OAuthFormsBackend(FormsApi(session(rec))).prepare_form(
        None, Lesson(site_id="L", title="L", sections=[Section(questions=[
            Question("q1", "single", "Which?", choices=["a", "b"], correct=["a"], points=2,
                     feedback_right="Y", feedback_wrong="N")])]), {})
    batch = next(b for _, p, b in rec.calls if p.endswith(":batchUpdate"))
    q = next(r for r in batch["requests"] if "createItem" in r)["createItem"]["item"]["questionItem"]["question"]
    assert q["choiceQuestion"]["type"] == "RADIO"
    assert q["grading"]["pointValue"] == 2
    assert q["grading"]["correctAnswers"]["answers"] == [{"value": "a"}]
    assert q["grading"]["whenRight"] == {"text": "Y"}


def test_get_response_maps_answers_scores_total():
    rec = Recorder()
    data = OAuthFormsBackend(FormsApi(session(rec))).get_response("F1", "R1")
    assert data.email == "stu@x.com"
    assert data.answers == {"Q1": ["2/4"], "Q2": ["x"]}
    assert data.scores["Q1"] == {"max_points": None, "earned_points": 2, "correct": True}
    assert data.total == {"max_points": None, "earned_points": 3}


# --- Classroom ------------------------------------------------------------------

def test_create_assignment_to_named_students_sets_individual_mode():
    rec = Recorder()
    cls = OAuthClassroomBackend(ClassroomApi(session(rec)))
    res = cls.create_assignment("C1", title="Lesson 12", instructions="do it", points=10,
                                topic_id="T9", form_url="https://f/view",
                                student_ids=["123", "456"], publish=True)
    body = next(b for m, p, b in rec.calls if m == "POST" and p.endswith("/courseWork"))
    assert body["workType"] == "ASSIGNMENT" and body["state"] == "PUBLISHED"
    assert body["assigneeMode"] == "INDIVIDUAL_STUDENTS"
    assert body["individualStudentsOptions"] == {"studentIds": ["123", "456"]}
    assert body["materials"] == [{"link": {"url": "https://f/view"}}]
    assert body["maxPoints"] == 10 and body["topicId"] == "T9"
    assert res.coursework_id == "CW1" and res.state == "PUBLISHED"


def test_create_assignment_without_students_is_whole_class():
    rec = Recorder()
    OAuthClassroomBackend(ClassroomApi(session(rec))).create_assignment(
        "C1", title="L", form_url="https://f/view", student_ids=None)
    body = next(b for m, p, b in rec.calls if m == "POST" and p.endswith("/courseWork"))
    assert body["assigneeMode"] == "ALL_STUDENTS" and "individualStudentsOptions" not in body


def test_ensure_topic_reuses_then_creates():
    rec = Recorder()
    cls = OAuthClassroomBackend(ClassroomApi(session(rec)))
    assert cls.ensure_topic("C1", "Week 1") == "T9"
    assert all(not (m == "POST" and p.endswith("/topics")) for m, p, _ in rec.calls)
    assert cls.ensure_topic("C1", "Week 2") == "Tnew"


def test_list_students_and_submissions_normalize():
    rec = Recorder()
    cls = OAuthClassroomBackend(ClassroomApi(session(rec)))
    students = cls.list_students("C1")
    assert students[0].user_id == "123" and students[0].email == "A@x.com" and students[0].full_name == "Ann"
    subs = cls.list_submissions("C1", "CW1")
    assert subs[0].user_id == "123" and subs[0].state == "TURNED_IN" and subs[0].assigned_grade == 8


def test_missing_work_grade_is_unsupported_on_oauth():
    from google_classroom_api.errors import ClassroomAPIError
    cls = OAuthClassroomBackend(ClassroomApi(session(Recorder())))
    with pytest.raises(ClassroomAPIError):
        cls.set_missing_work_grade("C1", enabled=True, percent=50)
