"""Google Forms through the editor's private web protocol.

This covers what the official Forms API has no field for: when grades are
released, what respondents may see afterwards, one response per person,
response editing, question shuffling and the progress bar. It also edits
items and reads responses, so a form can be run with a web session alone.

Protocol notes and how each piece was verified: ``docs/private-forms-protocol.md``.
"""

from __future__ import annotations

import json
import secrets
from typing import Any, Iterable, Optional, Sequence

from .session import WebProtocolError, WebSession, js_value, strip_xssi

HOST = "https://docs.google.com"

CMD_ADD_ITEM = 25290104
CMD_REPLACE_ITEM = 24479122
CMD_MOVE_ITEM = 28252204
CMD_DELETE_ITEM = 28511809
CMD_TITLE = 32427935
CMD_DESCRIPTION = 25101318
CMD_QUIZ = 112282607
CMD_AFTER_SUBMIT = 28132780
CMD_RESPONDER = 47603585

ITEM_TYPES = {"short": 0, "long": 1, "single": 2, "multiple": 4, "section": 8}
CHOICE_KINDS = ("single", "multiple")

EMAIL_DO_NOT_COLLECT = 1
EMAIL_VERIFIED = 2
EMAIL_RESPONDER_INPUT = 3


def new_id() -> int:
    """Item and question IDs are 31-bit integers picked by the client."""
    return secrets.randbelow(2**31 - 2) + 1


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def question_payload(
    question_id: int,
    kind: str,
    *,
    options: Sequence[str] = (),
    required: bool = False,
    correct: Sequence[str] = (),
    points: Optional[int] = None,
    feedback_right: str = "",
    feedback_wrong: str = "",
    general_feedback: str = "",
) -> list:
    """One question as the save commands carry it.

    The grading block is ``[points, correctAnswers, whenRight, whenWrong, general]``.
    Each correct answer is its own one-element list: ``[["4"], ["four"]]``. The
    server accepts ``[["4", "four"]]`` too, but silently keeps only the first.
    Choice questions take right/wrong feedback; text questions take general
    feedback (the server rejects it in the right/wrong slots).
    """
    if kind not in ITEM_TYPES or kind == "section":
        raise ValueError(f"unknown question kind {kind!r}")
    is_choice = kind in CHOICE_KINDS
    if is_choice:
        if not options:
            raise ValueError("a choice question needs options")
        stray = [value for value in correct if value not in options]
        if stray:
            raise ValueError(f"correct answers not among the options: {stray}")
        if general_feedback:
            raise NotImplementedError("general feedback on a choice question was not verified")
        question: list = [
            question_id,
            [[text, None, None, None, False] for text in options],
            bool(required),
            None, None, None, None, None,
            False,
        ]
    else:
        if options:
            raise ValueError("a text question has no options")
        if feedback_right or feedback_wrong:
            raise ValueError("a text question takes general_feedback, not right/wrong feedback")
        question = [question_id, None, bool(required)]

    graded = bool(correct) or points is not None or feedback_right or feedback_wrong or general_feedback
    if not graded:
        return question
    grading: list = [points, [[value] for value in correct] if correct else None]
    if feedback_right or feedback_wrong:
        grading += [[feedback_right], [feedback_wrong]]
    elif general_feedback:
        grading += [None, None, [general_feedback]]
    if not is_choice:
        question += [None, None, None, None, None, False]
    question.append(grading)
    return question


def question_item(
    item_id: int, kind: str, title: str, question: list, description: Optional[str] = None
) -> list:
    return [item_id, None, description or None, ITEM_TYPES[kind], [question],
            None, None, None, None, None, None, None, None, title]


def section_item(item_id: int, title: str) -> list:
    return [item_id, None, None, ITEM_TYPES["section"], None, None, None, None, None, None, None, None, None, title]


class FormEditor:
    """One editing session on one form.

    Opening the editor fetches the edit page once to obtain the session id,
    the anti-forgery token and the current model. Nothing in the browser needs
    to be running.
    """

    def __init__(
        self, session: WebSession, form_id: str, *, user_index: int = 0, html: Optional[str] = None
    ) -> None:
        self._s = session
        self.form_id = form_id
        self._base = f"{HOST}/forms/u/{user_index}/d/{form_id}/"
        self._request_id = 0
        self._stale = False
        if html is None:
            self.reload()
        else:
            self._load(html)

    # --- state ----------------------------------------------------------------

    def reload(self) -> None:
        response = self._s.get(f"{HOST}/forms/d/{self.form_id}/edit")
        if response.status_code != 200:
            raise WebProtocolError(f"cannot open the form editor: HTTP {response.status_code}")
        self._load(response.text)

    def _load(self, html: str) -> None:
        data = js_value(html, "FB_LOAD_DATA_ = ", "[")
        flags = js_value(html, "_docs_flag_initialData=", "{")
        try:
            self.revision: int = int(data[1])
            self.sid: str = data[3]
            self.user_email: str = data[4]
            self.document_title: str = data[0][3]
            self.responder_path: str = data[0][14]
            # The page carries the model twice: the respondent's copy at [0][1],
            # without answer keys, and the editor's full copy at [14].
            full = data[14] if len(data) > 14 and isinstance(data[14], list) and len(data[14]) >= 2 else None
            self._model: list = full if full is not None else data[0][1]
            info = flags["info_params"]
            self._token: str = info["token"]
            self._ouid: str = info["ouid"]
        except (IndexError, KeyError, TypeError) as exc:
            raise WebProtocolError(f"the edit page no longer has the expected layout: {exc!r}") from exc
        self._request_id = 0
        self._stale = False

    @property
    def model(self) -> list:
        if self._stale:
            self.reload()
        return self._model

    def _slot(self, index: int) -> Any:
        model = self.model
        return model[index] if len(model) > index else None

    @property
    def title(self) -> str:
        return self._slot(8) or ""

    @property
    def description(self) -> str:
        return self._slot(0) or ""

    @property
    def items(self) -> list:
        """Items in order. Each is ``[itemId, title, description, type, [question, …], …]``."""
        return self._slot(1) or []

    def questions(self) -> list[dict]:
        """The questions in plain terms, including the answer key when one is set."""
        kinds = {code: name for name, code in ITEM_TYPES.items()}
        result = []
        for index, item in enumerate(self.items):
            if not item[4]:
                continue
            question = item[4][0]
            grading = question[9] if len(question) > 9 and question[9] else None
            result.append(
                {
                    "index": index,
                    "item_id": item[0],
                    "question_id": question[0],
                    "kind": kinds.get(item[3], f"type-{item[3]}"),
                    "title": item[1],
                    "description": item[2] or "",
                    "options": [option[0] for option in (question[1] or [])],
                    "required": bool(question[2]),
                    "points": grading[0] if grading else None,
                    "correct": [answer[0] for answer in grading[1]]
                    if grading and len(grading) > 1 and grading[1] else [],
                    "general_feedback": grading[4][0]
                    if grading and len(grading) > 4 and grading[4] else "",
                }
            )
        return result

    @property
    def responder_url(self) -> str:
        return f"{HOST}/forms/d/{self.responder_path}/viewform"

    def settings(self) -> dict:
        """Every setting this module can change, as currently stored."""
        quiz = self._quiz_tuple()
        after = self._after_tuple()
        responder = self._responder_tuple()
        return {
            "is_quiz": bool(quiz[2]),
            "release_grades_immediately": bool(quiz[1]),
            "show_missed_questions": bool(quiz[0][2]),
            "show_correct_answers": bool(quiz[0][3]),
            "show_point_values": bool(quiz[0][4]),
            "confirmation_message": after[0],
            "show_link_to_respond_again": bool(after[1]),
            "publish_summary": bool(after[2]),
            "allow_response_edits": bool(after[3]),
            "progress_bar": bool(responder[0]),
            "limit_one_response": bool(responder[1]),
            "shuffle_questions": bool(responder[2]),
            "email_collection": responder[6],
        }

    # --- transport ------------------------------------------------------------

    def _params(self) -> dict:
        return {"sid": self.sid, "vc": "0", "c": "0", "w": "1", "flr": "0",
                "token": self._token, "ouid": self._ouid}

    def _save(self, commands: Iterable[tuple[int, Any]]) -> dict:
        batch = [[command_id, command_id, _dump(payload)] for command_id, payload in commands]
        bundles = [{"commands": batch, "sid": self.sid, "reqId": self._request_id}]
        response = self._s.post(
            self._base + "save",
            params={"id": self.form_id, **self._params()},
            data={"rev": str(self.revision), "bundles": _dump(bundles)},
            headers={"X-Same-Domain": "1"},
        )
        if response.status_code != 200:
            raise WebProtocolError(
                f"the form editor rejected the save: HTTP {response.status_code} "
                f"{strip_xssi(response.text)[:120]}"
            )
        self._request_id += 1
        try:
            answer = json.loads(strip_xssi(response.text))
            self.revision = int(answer["revisionRanges"][-1][-1])
        except (ValueError, KeyError, IndexError, TypeError):
            self.revision += len(batch)
            answer = {}
        return answer

    def _call(self, method: str, name: str, data: Optional[dict] = None) -> Any:
        response = self._s.request(
            method, self._base + name, params=self._params(), data=data,
            headers={"X-Same-Domain": "1"},
        )
        if response.status_code != 200:
            raise WebProtocolError(f"{name} failed: HTTP {response.status_code}")
        try:
            answer = json.loads(strip_xssi(response.text))
        except ValueError as exc:
            raise WebProtocolError(f"{name} did not return JSON") from exc
        if answer.get("status") != 0:
            raise WebProtocolError(f"{name} failed with status {answer.get('status')}")
        return json.loads(answer["prsj"]) if "prsj" in answer else answer

    def apply(self, commands: Iterable[tuple[int, Any]]) -> None:
        """Send several commands in one save, the way the editor batches quick edits."""
        batch = list(commands)
        if batch:
            self._save(batch)
            self._stale = True

    # --- title and description --------------------------------------------------

    def set_title(self, title: str) -> None:
        self._save([(CMD_TITLE, ["", None, title])])
        self._stale = True

    def set_description(self, description: str) -> None:
        self._save([(CMD_DESCRIPTION, ["", None, description])])
        self._stale = True

    # --- items ----------------------------------------------------------------

    def add_question(
        self,
        kind: str,
        title: str,
        *,
        description: Optional[str] = None,
        index: Optional[int] = None,
        **question_options: Any,
    ) -> tuple[int, int]:
        """Add a question and return ``(item_id, question_id)``.

        ``question_id`` is the key under which answers come back later.
        ``question_options`` are those of :func:`question_payload`: ``options``,
        ``required``, ``correct``, ``points``, ``feedback_right``,
        ``feedback_wrong`` and, for text questions, ``general_feedback`` (the
        place for a reference answer shown after submitting).
        """
        item_id, question_id = new_id(), new_id()
        question = question_payload(question_id, kind, **question_options)
        position = len(self.items) if index is None else index
        item = question_item(item_id, kind, title, question, description)
        self._save([(CMD_ADD_ITEM, [position, item])])
        self._stale = True
        return item_id, question_id

    def add_section(self, title: str, *, index: Optional[int] = None) -> int:
        item_id = new_id()
        position = len(self.items) if index is None else index
        self._save([(CMD_ADD_ITEM, [position, section_item(item_id, title)])])
        self._stale = True
        return item_id

    def replace_question(
        self,
        index: int,
        item_id: int,
        question_id: int,
        kind: str,
        title: str,
        *,
        description: Optional[str] = None,
        **question_options: Any,
    ) -> None:
        """Rewrite the question at ``index`` in place, keeping its IDs.

        The whole question is replaced, so pass everything it should have.
        """
        question = question_payload(question_id, kind, **question_options)
        item = question_item(item_id, kind, title, question, description)
        self._save([(CMD_REPLACE_ITEM, [index, item])])
        self._stale = True

    def move_item(self, from_index: int, to_index: int) -> None:
        """Reorder items. IDs do not change, so collected answers stay attached."""
        self._save([(CMD_MOVE_ITEM, [from_index, to_index])])
        self._stale = True

    def delete_item(self, index: int) -> None:
        self._save([(CMD_DELETE_ITEM, [index])])
        self._stale = True

    # --- settings ---------------------------------------------------------------

    def _quiz_tuple(self) -> list:
        stored = self._slot(16) or [[1, 1, 1, 1, 1], 1, 0, None, 0]
        inner = list(stored[0]) + [1] * (5 - len(stored[0]))
        rest = list(stored[1:]) + [None] * (4 - len(stored[1:]))
        return [inner, rest[0], rest[1], rest[2], rest[3]]

    def _after_tuple(self) -> list:
        stored = list(self._slot(2) or ["", 1, 0, 0, 0])
        return stored + [0] * (5 - len(stored))

    def _responder_tuple(self) -> list:
        stored = list(self._slot(10) or [None, 0, None, 2, None, None, EMAIL_DO_NOT_COLLECT])
        return stored + [None] * (7 - len(stored))

    def _store(self, index: int, value: list) -> None:
        if not self._stale and len(self._model) > index:
            self._model[index] = value

    def set_quiz(
        self,
        *,
        is_quiz: Optional[bool] = None,
        release_grades_immediately: Optional[bool] = None,
        show_missed_questions: Optional[bool] = None,
        show_correct_answers: Optional[bool] = None,
        show_point_values: Optional[bool] = None,
    ) -> dict:
        """Quiz mode, when grades are released, and what respondents may see."""
        inner, release, quiz, extra, last = self._quiz_tuple()
        inner = [bool(v) for v in inner]
        if show_missed_questions is not None:
            inner[2] = bool(show_missed_questions)
        if show_correct_answers is not None:
            inner[3] = bool(show_correct_answers)
        if show_point_values is not None:
            inner[4] = bool(show_point_values)
        release = bool(release) if release_grades_immediately is None else bool(release_grades_immediately)
        quiz = bool(quiz) if is_quiz is None else bool(is_quiz)
        value = [inner, release, quiz, extra, bool(last)]
        try:
            self._save([(CMD_QUIZ, [value])])
        except WebProtocolError as exc:
            if is_quiz is False:
                raise WebProtocolError(
                    f"{exc} (the server was seen to refuse turning quiz mode off while "
                    "questions still carry answer keys; the editor's own sequence for that "
                    "was not captured)"
                ) from exc
            raise
        self._store(16, value)
        return self.settings()

    def set_after_submit(
        self,
        *,
        confirmation_message: Optional[str] = None,
        show_link_to_respond_again: Optional[bool] = None,
        publish_summary: Optional[bool] = None,
        allow_response_edits: Optional[bool] = None,
    ) -> dict:
        message, again, summary, edits, last = self._after_tuple()
        value = [
            message if confirmation_message is None else confirmation_message,
            bool(again) if show_link_to_respond_again is None else bool(show_link_to_respond_again),
            bool(summary) if publish_summary is None else bool(publish_summary),
            bool(edits) if allow_response_edits is None else bool(allow_response_edits),
            bool(last),
        ]
        self._save([(CMD_AFTER_SUBMIT, [value])])
        self._store(2, value)
        return self.settings()

    def set_responder(
        self,
        *,
        progress_bar: Optional[bool] = None,
        limit_one_response: Optional[bool] = None,
        shuffle_questions: Optional[bool] = None,
        email_collection: Optional[int] = None,
    ) -> dict:
        stored = self._responder_tuple()
        value = [
            bool(stored[0]) if progress_bar is None else bool(progress_bar),
            bool(stored[1]) if limit_one_response is None else bool(limit_one_response),
            bool(stored[2]) if shuffle_questions is None else bool(shuffle_questions),
            stored[3] if stored[3] is not None else 2,
            stored[4],
            stored[5],
            stored[6] if email_collection is None else email_collection,
        ]
        self._save([(CMD_RESPONDER, [value])])
        self._store(10, value)
        return self.settings()

    # --- publishing -------------------------------------------------------------

    def publish(self) -> None:
        self._call("POST", "publish", {"request": "[0]"})

    def set_accepting_responses(self, accepting: bool) -> None:
        """Open or close the form to new responses. Replayed in both directions.

        Closing it is the way to take a quiz down: the form stays published,
        but nobody can submit any more.
        """
        self._call("POST", "setacceptingresponses", {"request": "[1]" if accepting else "[0]"})

    def is_accepting_responses(self) -> bool:
        settings = self.publishing_settings()
        return bool(settings.get("published")) and bool(
            (settings.get("specialPublishedMetadata") or {}).get("acceptingResponses"))

    def publishing_settings(self) -> dict:
        response = self._s.get(self._base + "publishingsettings")
        if response.status_code != 200:
            raise WebProtocolError(f"publishingsettings failed: HTTP {response.status_code}")
        return json.loads(strip_xssi(response.text))

    # --- responses --------------------------------------------------------------

    def list_responses(self) -> list[dict]:
        """One entry per stored response: id, respondent email and submit time."""
        rows = self._call("GET", "responsemetadata")
        entries = rows[0] if rows else []
        return [
            {"response_id": entry[0], "email": entry[1], "submitted_ms": entry[2]}
            for entry in entries or []
        ]

    def get_response(self, response_id: str) -> dict:
        """Raw answers and scores of one response, keyed by question id (as text).

        A question the respondent left blank is simply absent from ``answers``.
        """
        rows = self._call("POST", "getsingleresponse", {"viewresponse": response_id})
        if not rows:
            raise WebProtocolError("getsingleresponse returned nothing")
        row = rows[0]
        answers = {str(entry[1]): list(entry[2] or []) for entry in (row[4] or [])}
        scores = {}
        for entry in (row[8] if len(row) > 8 and row[8] else []):
            scores[str(entry[0])] = {
                "max_points": entry[1],
                "earned_points": entry[2] if len(entry) > 2 else None,
            }
        total = row[9] if len(row) > 9 and row[9] else None
        return {
            "response_id": response_id,
            "email": row[2],
            "submitted_ms": row[0],
            # Two more timestamps follow in the record. They were identical in the
            # capture, so which is "first" and which is "last" is not confirmed.
            "timestamps_ms": [row[20], row[21]] if len(row) > 21 else [row[0], row[0]],
            "answers": answers,
            "scores": scores,
            "total": {"max_points": total[1], "earned_points": total[2]} if total else None,
        }


class FormsWeb:
    def __init__(self, session: WebSession, *, user_index: int = 0) -> None:
        self._s = session
        self._user_index = user_index

    def open(self, form_id: str) -> FormEditor:
        return FormEditor(self._s, form_id, user_index=self._user_index)

    def create(self, title: Optional[str] = None, *, empty: bool = True) -> FormEditor:
        """Create a new form in the account's Drive and open it. No official API needed.

        The web app does this with a plain ``GET …/create``, which answers with
        the editor page of a brand-new form; the session in that page can save
        straight away (replayed). Every call makes a new form, so do not retry
        it blindly. A new form comes with one placeholder question, which is
        removed unless ``empty`` is false.
        """
        response = self._s.get(f"{HOST}/forms/u/{self._user_index}/create")
        if response.status_code != 200:
            raise WebProtocolError(f"cannot create a form: HTTP {response.status_code}")
        data = js_value(response.text, "FB_LOAD_DATA_ = ", "[")
        try:
            form_id = data[13]
        except (IndexError, TypeError) as exc:
            raise WebProtocolError("the new form's page has no form id") from exc
        if not isinstance(form_id, str) or len(form_id) < 20:
            raise WebProtocolError("the new form's page has no form id")
        editor = FormEditor(self._s, form_id, user_index=self._user_index, html=response.text)
        placeholders = len(editor.items) if empty else 0
        if title:
            editor.set_title(title)
        for _ in range(placeholders):
            editor.delete_item(0)
        return editor
