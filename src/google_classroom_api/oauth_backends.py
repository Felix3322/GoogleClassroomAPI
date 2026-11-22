"""Backend adapters over the official Google REST API clients.

These wrap the low-level :mod:`google_classroom_api.forms`,
:mod:`google_classroom_api.classroom` and :mod:`google_classroom_api.drive`
clients (all on :class:`~google_classroom_api.transport.GoogleSession`) so the
pipeline can treat them through the protocols in
:mod:`google_classroom_api.backends`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Sequence

from . import formbuilder
from .backends import (AssignmentResult, FormSync, ResolvedSlot, ResponseData, ResponseMeta,
                       StudentInfo, SubmissionInfo)
from .classroom import ClassroomApi
from .drive import DriveApi
from .errors import ClassroomAPIError
from .forms import FormsApi
from .timeutil import parse_ts

# Settings the official Forms API has no field for (the web editor had them).
DROPPED_FORM_SETTINGS = [
    "release_grades_immediately", "show_missed_questions", "show_correct_answers",
    "show_point_values", "allow_response_edits", "limit_one_response",
    "shuffle_questions", "progress_bar",
]


def _ms(value: Optional[str]) -> int:
    return int(parse_ts(value).timestamp() * 1000) if value else 0


def _due(due: Optional[datetime], has_time: bool) -> dict:
    if due is None:
        return {}
    if due.tzinfo is None:
        raise ValueError("due must be a timezone-aware datetime")
    u = due.astimezone(timezone.utc)
    return {"dueDate": {"year": u.year, "month": u.month, "day": u.day},
            "dueTime": ({"hours": u.hour, "minutes": u.minute} if has_time
                        else {"hours": 23, "minutes": 59})}


class OAuthFormsBackend:
    """Forms backend on the official API. Satisfies the FormsBackend protocol."""

    def __init__(self, forms: FormsApi, drive: Optional[DriveApi] = None) -> None:
        self._forms = forms
        self._drive = drive  # optional: to grant named responders access to the form

    def prepare_form(self, form_id: Optional[str], lesson, known: dict) -> FormSync:
        created = form_id is None
        form = self._forms.create(lesson.title, document_title=lesson.title) if created \
            else self._forms.get(form_id)
        form_id = form["formId"]

        plan = formbuilder.plan_form(form, lesson, known)
        ops_sent = len(plan.requests)
        reply_form = None
        create_replies: list = []
        if plan.requests:
            revision = form.get("revisionId")
            reply = self._forms.batch_update(form_id, plan.requests,
                                             required_revision_id=revision, include_form=True)
            reply_form = reply.get("form")
            create_replies = [r.get("createItem", {}) for r in reply.get("replies", [])
                              if isinstance(r, dict) and "createItem" in r]

        # Resolve the id of every slot: reused ones keep the stored id, new ones
        # take the id the server just assigned (createItem replies, in slot order).
        working = [item.get("itemId") for item in form.get("items", [])]
        resolved: list[ResolvedSlot] = []
        ci = iter(create_replies)
        for slot in plan.slots:
            row = known.get(slot.key)
            reused = (row is not None and row.get("item_id") in working
                      and row.get("kind") == slot.kind)
            if reused:
                item_id = row["item_id"]
                question_id = row.get("question_id")
                changed = row.get("content_hash") != slot.content_hash
                is_new = False
            else:
                rep = next(ci, {})
                item_id = rep.get("itemId")
                qids = rep.get("questionId") or []
                question_id = qids[0] if qids else None
                is_new, changed = True, False
            resolved.append(ResolvedSlot(
                slot_key=slot.key, kind=slot.kind, site_question_id=slot.site_question_id,
                item_id=item_id, question_id=question_id, content_hash=slot.content_hash,
                snapshot=slot.snapshot, is_new=is_new, changed=changed))

        self._forms.set_publish(form_id, published=True, accepting=True)
        final = reply_form or self._forms.get(form_id)
        responder = final.get("responderUri", "")
        return FormSync(form_id=form_id, responder_url=responder, slots=resolved,
                        deleted_keys=plan.deleted_keys, ops_sent=ops_sent, created=created,
                        dropped_settings=list(DROPPED_FORM_SETTINGS))

    def set_accepting_responses(self, form_id: str, accepting: bool) -> None:
        self._forms.set_publish(form_id, published=True, accepting=accepting)

    def read_form(self, form_id: str) -> dict:
        """The raw form resource (handy for verifying item order in tests)."""
        return self._forms.get(form_id)

    def grant_responders(self, form_id: str, emails: Sequence[str]):
        """Optionally give named students reader access to the form (needs a
        DriveApi and the drive.file scope). Returns an AccessReport or None."""
        if self._drive is None:
            return None
        return self._drive.reconcile_users(form_id, emails, published_view=True,
                                           remove_unexpected=False)

    def list_responses(self, form_id: str) -> list[ResponseMeta]:
        out = []
        for r in self._forms.list_responses(form_id):
            out.append(ResponseMeta(
                response_id=r["responseId"],
                email=(r.get("respondentEmail") or "").strip().lower(),
                submitted_ms=_ms(r.get("createTime")),
                last_submitted_ms=_ms(r.get("lastSubmittedTime") or r.get("createTime"))))
        return out

    def get_response(self, form_id: str, response_id: str) -> ResponseData:
        r = self._forms.get_response(form_id, response_id)
        answers: dict[str, list[str]] = {}
        scores: dict[str, dict] = {}
        for qid, answer in (r.get("answers") or {}).items():
            answers[qid] = [a.get("value", "") for a in (answer.get("textAnswers") or {}).get("answers", [])]
            grade = answer.get("grade")
            if grade is not None:
                scores[qid] = {"max_points": None, "earned_points": grade.get("score"),
                               "correct": grade.get("correct")}
        total = {"max_points": None, "earned_points": r.get("totalScore")} if "totalScore" in r else None
        submitted = _ms(r.get("createTime"))
        last = _ms(r.get("lastSubmittedTime") or r.get("createTime"))
        return ResponseData(response_id=response_id,
                            email=(r.get("respondentEmail") or "").strip().lower(),
                            submitted_ms=submitted, timestamps_ms=[submitted, last],
                            answers=answers, scores=scores, total=total)


class OAuthClassroomBackend:
    """Classroom backend on the official API. Satisfies the ClassroomBackend protocol."""

    supports_missing_work_grade = False
    supports_individual_students = True

    def __init__(self, classroom: ClassroomApi) -> None:
        self._c = classroom

    def list_courses(self) -> list[dict]:
        return [{"id": str(c["id"]), "name": c.get("name"), "section": c.get("section")}
                for c in self._c.list_courses()]

    def list_students(self, course_id: str) -> list[StudentInfo]:
        out = []
        for row in self._c.list_students(course_id):
            profile = row.get("profile") or {}
            out.append(StudentInfo(user_id=str(row["userId"]),
                                   email=(profile.get("emailAddress") or None),
                                   full_name=(profile.get("name") or {}).get("fullName")))
        return out

    def ensure_topic(self, course_id: str, name: str) -> str:
        return str(self._c.ensure_topic(course_id, name)["topicId"])

    def _body(self, *, title, instructions, points, topic_id, due, due_has_time,
              form_url, student_ids, publish) -> dict:
        body: dict = {"title": title, "workType": "ASSIGNMENT",
                      "state": "PUBLISHED" if publish else "DRAFT"}
        if instructions:
            body["description"] = instructions
        if form_url:
            body["materials"] = [{"link": {"url": form_url}}]
        if topic_id:
            body["topicId"] = topic_id
        body.update(_due(due, due_has_time))
        if points:
            body["maxPoints"] = points
        if student_ids:
            body["assigneeMode"] = "INDIVIDUAL_STUDENTS"
            body["individualStudentsOptions"] = {"studentIds": list(dict.fromkeys(student_ids))}
        else:
            body["assigneeMode"] = "ALL_STUDENTS"
        return body

    @staticmethod
    def _result(cw: dict) -> AssignmentResult:
        return AssignmentResult(coursework_id=str(cw["id"]), state=cw.get("state", "DRAFT"),
                                alternate_link=cw.get("alternateLink"))

    def create_assignment(self, course_id, *, title, instructions="", points=100, topic_id=None,
                          due=None, due_has_time=True, form_url=None, student_ids=None,
                          publish=True) -> AssignmentResult:
        body = self._body(title=title, instructions=instructions, points=points, topic_id=topic_id,
                          due=due, due_has_time=due_has_time, form_url=form_url,
                          student_ids=student_ids, publish=publish)
        return self._result(self._c.create_coursework(course_id, body))

    def update_assignment(self, course_id, coursework_id, *, title, instructions="", points=100,
                          topic_id=None, due=None, due_has_time=True, form_url=None,
                          student_ids=None, publish=True) -> AssignmentResult:
        fields: dict = {"title": title, "description": instructions,
                        "state": "PUBLISHED" if publish else "DRAFT"}
        if points:
            fields["maxPoints"] = points
        if topic_id:
            fields["topicId"] = topic_id
        if form_url:
            fields["materials"] = [{"link": {"url": form_url}}]
        fields.update(_due(due, due_has_time))
        cw = self._c.patch_coursework(course_id, coursework_id, fields)
        if student_ids is not None:
            self._set_students(course_id, coursework_id, student_ids)
            cw = self._c.get_coursework(course_id, coursework_id)
        return self._result(cw)

    def _set_students(self, course_id, coursework_id, student_ids) -> None:
        current = self._c.get_coursework(course_id, coursework_id)
        existing = set((current.get("individualStudentsOptions") or {}).get("studentIds") or [])
        wanted = set(map(str, student_ids))
        add, remove = sorted(wanted - existing), sorted(existing - wanted)
        if add or remove or current.get("assigneeMode") != "INDIVIDUAL_STUDENTS":
            self._c.modify_assignees(course_id, coursework_id, add=add, remove=remove)

    def list_submissions(self, course_id: str, coursework_id: str) -> list[SubmissionInfo]:
        out = []
        for row in self._c.list_submissions(course_id, coursework_id):
            out.append(SubmissionInfo(
                user_id=str(row.get("userId")), state=row.get("state", "NEW"),
                late=bool(row.get("late")), assigned_grade=row.get("assignedGrade"),
                draft_grade=row.get("draftGrade"), update_time=row.get("updateTime"),
                history=row.get("submissionHistory", [])))
        return out

    def set_missing_work_grade(self, course_id: str, *, enabled: bool,
                               percent: Optional[float] = None) -> dict:
        raise ClassroomAPIError(
            "the official Classroom API has no field for a default grade for missing work; "
            "use the web (cookie) classroom backend for this, or set it once in the Classroom web UI")


__all__ = ["OAuthFormsBackend", "OAuthClassroomBackend", "DROPPED_FORM_SETTINGS"]
