"""Google Classroom through the web app's private RPCs.

Only calls that were captured from the real web app are implemented, and the
docstrings say which ones were also replayed by hand. Assigning to individual
students and reading submissions are missing: the capture class had no
students. See ``docs/private-classroom-protocol.md``.
"""

from __future__ import annotations

import json
import re
import secrets
import uuid
from datetime import datetime
from typing import Any, Optional, Sequence

from .session import WebProtocolError, WebSession, strip_xssi

HOST = "https://classroom.google.com"
FORM_MIME = "application/vnd.google-apps.freebird"
DRIVE_KIND_FORM = 7

STATE_PUBLISHED = 2
STATE_DRAFT = 3

# Field masks exactly as the web app sends them. The server rejects the
# stream-item calls when they are left out.
_STREAM = [1, 1, 1, 1, 1, None, None, [1, 1, 1, None, 1, 1, 1], 1, 1, 1, 1, 1, 1, None, None, None, None,
           1, None, None, None, 1, [1], 1, [None, None, 1, 1, 1, None, 1]]
_COURSEWORK = [1, 1, 1, 1, 1, 1, [1], 1, None, [1, 1], 1, 0, None, 0, [[1, 1, [], [None, 1]], 1, 1],
               None, None, None, 1]
READ_MASKS = [[_STREAM, _COURSEWORK, [None, 1], None, [1, 1]], [_STREAM], [_STREAM, _COURSEWORK, [1]],
              None, None, [_STREAM]]

_UPDATE_STREAM = [1, None, None, [1, None, 1, 1], None, 1, 4, 1, None, None, None, None, None, 1, 4,
                  [None, 1, 1, 1, 1]]
_UPDATE_COURSEWORK = [1, 1, 1, 1, 1, [1], [1], 1, None, None, None, None, 1]
UPDATE_MASKS = [[_UPDATE_STREAM, _UPDATE_COURSEWORK, None, [1, 1]], [_UPDATE_STREAM],
                [_UPDATE_STREAM, _UPDATE_COURSEWORK, [1]], None, None, [_UPDATE_STREAM]]

# Deleting a stream item is an F7Tqub update that writes only the item's
# lifecycle field (slot 8: 2 = live, 3 = deleted). Captured from the web app's
# "delete" menu and replayed.
ITEM_LIVE = 2
ITEM_DELETED = 3
_DELETE_ONE = [None, None, None, None, None, 1]
DELETE_MASKS = [[_DELETE_ONE, []], [_DELETE_ONE], [_DELETE_ONE, []], None, None, [_DELETE_ONE]]

_ATTACHMENT_MASK = [None, 1, 1, 1, 1, 1, None, 1, 1, 1, 1, 1, 1, None, [[1, 1, 1, 1, 1], 1, 1, 1, 1, 1, 1],
                    [[1, 1, 1, 1, 1], 1, 1, 1, [1, 1], 1], 1, None, 1, [[1, 1, 1, 1, 1], 1, 1], 1,
                    [[1, 1, 1, 1, 1], 1]]

# Masks of the course record: which fields a settings save may write, and which to return.
COURSE_UPDATE_MASK = [1, None, None, 1, [None, None, None, 1, None, None, None, None, 1, None, [None, 1]],
                      None, 1, None, None, 1, None, None, None, None, [1], None, None, None, 4, None, None,
                      None, None, None, [1], None, None, 0, 1, 1]
COURSE_READ_MASK = [1, 1, None, None, 1, 1, 1, 1,
                    [None, 1, None, 1, None, 1, [1, 1, 1, 1, 1, 1], None, [1], 1, 1, 1, 1, [1, 1], [1, 1]],
                    1, 1, None, None, 1, None, None, None, 1, None, None, [1, 1, 1, 1, 1, 1, 1], 1, 1, None,
                    1, None, None, None, [1, 1], 1, 1, 1, 1, 1, 1, 1, 1, None, 1, 1,
                    [None, None, None, None, 1, 1, None, None, 1, None, None, None, None, None, None, None,
                     None, 1, 1, 1, 1, 1, 1],
                    None, [1, 1, 1], [1, 1, None, 1], 1, 1, 1, [[[1, 1, 1], 1], [[[1, 1]]], 1], None, 1, [1, 1],
                    None, None, 1, None, [None, None, 1], 1]

#: Slot of the grading block in the course record: ``[overall, categories, missingWork]``.
COURSE_GRADING_SLOT = 46

#: Slot of the student id list in the course record: ``[["<userId>"], ...]``.
#: Replayed 2026-11-22: a class with one student returned exactly that student's id here.
COURSE_STUDENTS_SLOT = 7

# Field mask the People page sends with UG41I (user profiles by id).
_USER_MASK = [1, 1, None, 1, None, 1, None, None, 1, 1, 1, 1, None, None, 1]
# Field mask the gradebook sends with Zj93ge (student submissions of one assignment).
_SUBMISSION_MASK = [None, 1, None, None, 1, None, 1, 1, 1, None, None, None, 1, None, [1], None, None,
                    None, 1, 1, 1, None, None, None, [[1, 1]], [[1, 1]], None, None, None, None, None, 1]

_WIZ_KEYS = {"at": "SNlM0e", "sid": "FdrFJe", "build": "cfb2h"}


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def course_id_from_url(url: str) -> int:
    """The numeric course id inside a Classroom link.

    Classroom puts the course id in its URLs base64-encoded, e.g.
    ``https://classroom.google.com/u/0/c/MTIzNDU2Nzg5MDEy`` is course
    ``123456789012``. Works for the stream (``/c/``), classwork (``/w/``),
    people (``/r/``) and gradebook links.
    """
    import base64
    match = re.search(r"/(?:c|w|r)/([A-Za-z0-9_-]+)", url)
    if not match:
        raise ValueError(f"no course segment in {url!r}")
    token = match.group(1)
    try:
        decoded = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode("ascii")
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError(f"{token!r} is not a Classroom course token") from exc
    if not decoded.isdigit():
        raise ValueError(f"{token!r} does not decode to a course id")
    return int(decoded)


def stream_item(
    course_id: int,
    title: str,
    *,
    item_id: Optional[int] = None,
    instructions: str = "",
    state: int = STATE_DRAFT,
    attachment_ids: Sequence[int] = (),
    topic_id: Optional[str] = None,
    client_uuid: Optional[str] = None,
) -> list:
    """The 28-slot stream item the web app sends for an assignment."""
    item: list = [None] * 28
    item[0] = [item_id, [course_id]]
    item[5] = title
    item[9] = [state]
    if topic_id:
        item[14] = [[str(topic_id), [course_id]]]
    item[15] = 1
    item[25] = [0]
    body: list = [None, client_uuid or str(uuid.uuid4()).upper()]
    if instructions or attachment_ids:
        text = ["edu.rt", instructions]
        attachments = (
            [[None, [[item_id, [course_id]], attachment]] for attachment in attachment_ids]
            if attachment_ids else None
        )
        body += [None, text, attachments, None, ["edu.rt", instructions, None, None, [None, instructions]]]
    item[27] = [body]
    return item


def due_fields(due: Optional[datetime], has_time: bool = True) -> tuple[Optional[int], int]:
    """``(dueMs, hasTimeFlag)`` as the web app sends them.

    A due date without a time is sent as 23:59 in the teacher's own time zone
    with the flag at 0; pass exactly that moment and ``has_time=False`` for it.
    """
    if due is None:
        return None, 0
    if due.tzinfo is None:
        raise ValueError("due must be a timezone-aware datetime")
    return int(due.timestamp() * 1000), 1 if has_time else 0


def assignment_wrapper(
    item: list, points: int = 100, *, due_ms: Optional[int] = None, due_has_time: int = 0
) -> list:
    coursework = [due_ms, None, due_has_time if due_ms is not None else 0, points, 0, 1, [1],
                  None, None, [None, 0], None, 0]
    return [[2, [item, coursework, None, None, [2, 2]]]]


def create_arguments(item: list, points: int = 100, **due) -> list:
    return [assignment_wrapper(item, points, **due), READ_MASKS, None, [1, 1, 1],
            None, None, None, None, None, [1, 1, None, 1]]


def update_arguments(item: list, points: int = 100, **due) -> list:
    return [assignment_wrapper(item, points, **due), UPDATE_MASKS, READ_MASKS, None, [1, 1, 1],
            None, None, None, None, None, None, [1, 1, None, 1]]


def delete_arguments(course_id: int, item_id: int) -> list:
    """F7Tqub arguments that delete one stream item.

    Same envelope as :func:`update_arguments`, but the masks write only the
    item's lifecycle field, which is set to :data:`ITEM_DELETED`. The masks and
    slot 8 match the request the web app's "delete" menu sent; the other fields
    of the item are ignored by the server (replayed: a minimal item deletes).
    """
    item = stream_item(int(course_id), "", item_id=int(item_id))
    item[8] = ITEM_DELETED
    return [assignment_wrapper(item), DELETE_MASKS, READ_MASKS, None, [1, 1, 1],
            None, None, None, None, None, None, [1, 1, None, 1]]


def parse_batchexecute(text: str, rpc_id: str) -> Any:
    """Pull one RPC's result out of a ``batchexecute`` reply."""
    for line in strip_xssi(text).splitlines():
        line = line.strip()
        if not line.startswith("[["):
            continue
        try:
            entries = json.loads(line)
        except ValueError:
            continue
        for entry in entries:
            if isinstance(entry, list) and len(entry) > 2 and entry[0] == "wrb.fr" and entry[1] == rpc_id:
                if entry[2] is None:
                    code = entry[5] if len(entry) > 5 else None
                    raise WebProtocolError(f"Classroom rejected {rpc_id} (error {code})")
                return json.loads(entry[2])
    raise WebProtocolError(f"no result for {rpc_id} in the reply")


class ClassroomWeb:
    def __init__(self, session: WebSession, *, language: str = "en") -> None:
        self._s = session
        self._language = language
        self._wiz: Optional[dict] = None

    def _boot(self) -> dict:
        if self._wiz is None:
            response = self._s.get(f"{HOST}/h")
            if response.status_code != 200:
                raise WebProtocolError(f"cannot open Classroom: HTTP {response.status_code}")
            wiz = {}
            for name, key in _WIZ_KEYS.items():
                match = re.search(r'"%s":"([^"]+)"' % key, response.text)
                if not match:
                    raise WebProtocolError(f"Classroom page has no {key}; is the session signed in?")
                wiz[name] = match.group(1)
            self._wiz = wiz
        return self._wiz

    def call(self, rpc_id: str, arguments: Any, *, source_path: str = "/h") -> Any:
        """One ``batchexecute`` call. Replayed by hand for reads and writes."""
        wiz = self._boot()
        params = {
            "rpcids": rpc_id,
            "source-path": source_path,
            "f.sid": wiz["sid"],
            "bl": wiz["build"],
            "hl": self._language,
            "soc-app": "1",
            "soc-platform": "1",
            "soc-device": "1",
            "_reqid": str(100000 + secrets.randbelow(900000)),
            "rt": "c",
        }
        envelope = [[[rpc_id, _dump(arguments), None, "generic"]]]
        response = self._s.post(
            f"{HOST}/_/ClassroomUi/data/batchexecute",
            params=params,
            data={"f.req": _dump(envelope), "at": wiz["at"]},
            headers={"X-Same-Domain": "1"},
        )
        if response.status_code != 200:
            raise WebProtocolError(f"{rpc_id} failed: HTTP {response.status_code}")
        return parse_batchexecute(response.text, rpc_id)

    # --- topics -----------------------------------------------------------------

    def list_topics(self, course_id: int) -> list[dict]:
        """Replayed."""
        result = self.call("ZZFlc", [[100, None, 1, 0], [1, 1, 1], [[[[int(course_id)]]]]])
        rows = result[2] if len(result) > 2 and result[2] else []
        return [{"topic_id": row[0][0], "name": row[1], "updated_ms": row[2]} for row in rows]

    def create_topic(self, course_id: int, name: str) -> dict:
        """Replayed."""
        result = self.call("lkA1wb", [[[None, [int(course_id)]], name], [1, 1, 1]])
        row = result[1]
        return {"topic_id": row[0][0], "name": row[1], "updated_ms": row[2]}

    def ensure_topic(self, course_id: int, name: str) -> dict:
        for topic in self.list_topics(course_id):
            if topic["name"] == name:
                return topic
        return self.create_topic(course_id, name)

    # --- assignments --------------------------------------------------------------

    @staticmethod
    def _item_summary(result: Any) -> dict:
        try:
            stream = result[1][0][1][1][0]
            return {"item_id": stream[0][0], "course_id": stream[0][1][0], "title": stream[5],
                    "state": stream[9][0]}
        except (IndexError, TypeError) as exc:
            raise WebProtocolError(f"unexpected stream item reply: {exc!r}") from exc

    def create_assignment(
        self,
        course_id: int,
        title: str,
        *,
        instructions: str = "",
        points: int = 100,
        topic_id: Optional[str] = None,
        due: Optional[datetime] = None,
        due_has_time: bool = True,
    ) -> dict:
        """Create a draft assignment. Students cannot see it yet. Replayed."""
        item = stream_item(int(course_id), title, instructions=instructions, state=STATE_DRAFT,
                           topic_id=topic_id)
        due_ms, flag = due_fields(due, due_has_time)
        arguments = create_arguments(item, points, due_ms=due_ms, due_has_time=flag)
        return self._item_summary(self.call("n5NjMc", arguments))

    def update_assignment(
        self,
        course_id: int,
        item_id: int,
        title: str,
        *,
        instructions: str = "",
        points: int = 100,
        attachment_ids: Sequence[int] = (),
        topic_id: Optional[str] = None,
        due: Optional[datetime] = None,
        due_has_time: bool = True,
        publish: bool = False,
    ) -> dict:
        """Rewrite an assignment; with ``publish`` it is assigned to the class. Replayed.

        The call sends the whole assignment, so pass the topic, due date and
        attachments again or they are cleared.
        """
        item = stream_item(
            int(course_id), title, item_id=int(item_id), instructions=instructions,
            state=STATE_PUBLISHED if publish else STATE_DRAFT,
            attachment_ids=[int(a) for a in attachment_ids], topic_id=topic_id,
        )
        due_ms, flag = due_fields(due, due_has_time)
        arguments = update_arguments(item, points, due_ms=due_ms, due_has_time=flag)
        return self._item_summary(self.call("F7Tqub", arguments))

    def delete_assignment(self, course_id: int, item_id: int) -> None:
        """Delete an assignment (draft or published), as the web app's menu does. Replayed.

        The web app sends an F7Tqub update whose masks write only the item's
        lifecycle field, set to "deleted"; the other fields it sends are ignored.
        """
        self.call("F7Tqub", delete_arguments(course_id, item_id))

    def list_assignments(self, course_id: int) -> list[dict]:
        """Every assignment and draft of the class, grouped by topic. Replayed.

        The web app lists Classwork one section at a time with dpT4Vd: the
        "no topic" section is ``(null, 1)`` and each topic is
        ``([topicId, [courseId]], 0)``. Items come back newest first within a
        section; a read-back item does not carry its topic, so ``topic_id`` is
        taken from the section it was listed in.
        """
        cid = int(course_id)
        sections = [(None, 1, None)] + [([str(t["topic_id"]), [cid]], 0, str(t["topic_id"]))
                                        for t in self.list_topics(cid)]
        rows = []
        for ref, flag, topic_id in sections:
            result = self.call("dpT4Vd", [[100, None, 2, 0], [cid], ref, flag, 2, READ_MASKS])
            more = result[1] if len(result) > 1 and isinstance(result[1], list) else [False]
            if more and more[0] is True:
                raise WebProtocolError("a Classwork section has more than 100 items; "
                                       "paging past the first 100 has not been reverse-engineered")
            for entry in (result[2] if len(result) > 2 and result[2] else []):
                try:
                    stream = entry[1][0]
                    rows.append({"item_id": str(stream[0][0]), "title": stream[5],
                                 "state": stream[9][0] if stream[9] else None, "topic_id": topic_id})
                except (IndexError, TypeError) as exc:
                    raise WebProtocolError(f"unexpected stream item: {exc!r}") from exc
        return rows

    # --- class settings -------------------------------------------------------------

    def get_course(self, course_id: int) -> list:
        """The raw course record (54 slots; title at 5, section at 8). Replayed."""
        arguments = [[None, None, 2, 0], COURSE_READ_MASK,
                     [None, [[int(course_id)]], None, None, None, None, [1, 2]]]
        result = self.call("gXtzob", arguments)
        try:
            return result[2][0]
        except (IndexError, TypeError) as exc:
            raise WebProtocolError(f"unexpected course reply: {exc!r}") from exc

    def course_info(self, course_id: int) -> dict:
        """``{course_id, name, section}`` read from the course record. Replayed."""
        course = self.get_course(course_id)
        return {"course_id": str(course_id),
                "name": course[5] if len(course) > 5 else None,
                "section": course[8] if len(course) > 8 else None}

    # --- roster -------------------------------------------------------------------

    def list_student_ids(self, course_id: int) -> list[str]:
        """Google user ids of the class's students, read from the course record. Replayed."""
        course = self.get_course(course_id)
        slot = course[COURSE_STUDENTS_SLOT] if len(course) > COURSE_STUDENTS_SLOT else None
        return [str(entry[0]) for entry in (slot or []) if entry]

    def get_user_profiles(self, user_ids: Sequence[str]) -> list[dict]:
        """Name, email and photo for Google user ids (UG41I, as the People page calls it). Replayed."""
        ids = [str(u) for u in user_ids]
        if not ids:
            return []
        refs = [[None, [int(u)]] for u in ids]
        result = self.call("UG41I", [[None, None, 1, 0], _USER_MASK, [[None, refs]]])
        rows = result[2] if len(result) > 2 and result[2] else []
        profiles = []
        for row in rows:
            try:
                profiles.append({"user_id": str(row[0][0]), "name": row[1], "email": row[2],
                                 "photo_url": row[4] if len(row) > 4 else None})
            except (IndexError, TypeError) as exc:
                raise WebProtocolError(f"unexpected user profile record: {exc!r}") from exc
        return profiles

    def list_students(self, course_id: int) -> list[dict]:
        """The class's students: ``[{user_id, name, email, photo_url}]``. Replayed."""
        return self.get_user_profiles(self.list_student_ids(course_id))

    # --- submissions ----------------------------------------------------------------

    def list_submissions_raw(self, course_id: int, item_id: int) -> list:
        """Student submission records of one assignment (Zj93ge, as the gradebook calls it).

        The RPC id and request were captured from the gradebook and replayed; the
        server answers ``["hrq.sub", [...records, hasMore]]``. In the test class no
        student had opened or turned in anything, so only the empty answer
        (``[false]``) has been seen: the layout of a populated record is NOT yet
        verified, which is why this returns the raw records unparsed.
        """
        result = self.call("Zj93ge", [[100, None, 1, 0], _SUBMISSION_MASK,
                                      [None, [[int(item_id), [int(course_id)]]]], 0])
        body = result[1] if len(result) > 1 and isinstance(result[1], list) else []
        return [entry for entry in body if not isinstance(entry, bool)]

    def get_missing_work_grade(self, course_id: int) -> dict:
        """Whether missing work gets a default grade automatically, and which one."""
        course = self.get_course(course_id)
        block = course[COURSE_GRADING_SLOT] if len(course) > COURSE_GRADING_SLOT else None
        setting = block[2] if block and len(block) > 2 and block[2] else [False, 0]
        return {"enabled": bool(setting[0]), "percent": round(float(setting[1] or 0) * 100, 4)}

    def set_missing_work_grade(
        self, course_id: int, *, enabled: bool, percent: Optional[float] = None
    ) -> dict:
        """Turn the default grade for missing work on or off and set its percentage.

        Reads the class settings, changes only this one and writes them back,
        then returns what Google reports. Replayed, with the title and section
        checked to be untouched.
        """
        course = self.get_course(course_id)
        while len(course) <= COURSE_GRADING_SLOT:
            course.append(None)
        block = list(course[COURSE_GRADING_SLOT] or [[None, 0], None, None])
        while len(block) < 3:
            block.append(None)
        current = block[2] or [False, 0]
        if percent is None:
            fraction = current[1] or 0
        else:
            if not 0 <= percent <= 100:
                raise ValueError("percent must be between 0 and 100")
            fraction = round(percent / 100, 4)
        block[2] = [1 if enabled else 0, fraction]
        course[COURSE_GRADING_SLOT] = block
        self.call("lNyLS", [[3], [[[int(course_id)], course, COURSE_UPDATE_MASK]], COURSE_READ_MASK])
        return self.get_missing_work_grade(course_id)

    # --- attachments ----------------------------------------------------------------

    def resolve_link(self, url: str) -> dict:
        """Ask Classroom what a pasted link is. A Forms link comes back as a Form."""
        row = self.call("u1xGoc", [url])[1]
        return {"title": row[0], "drive_id": row[2], "kind": row[3],
                "url": row[6] if len(row) > 6 else None, "is_form": row[3] == DRIVE_KIND_FORM}

    def attach_drive_item(
        self, course_id: int, item_id: int, drive_id: str, *,
        kind: int = DRIVE_KIND_FORM, mime_type: str = FORM_MIME,
    ) -> str:
        """Attach a Drive item to an assignment and return the attachment id.

        Observed in the web app, not replayed by hand yet.
        """
        descriptor = [None, None, drive_id, kind, mime_type] + [None] * 8 \
            + [[[], None, None, None, [None, 0]]] + [None] * 6 + [1]
        arguments = [[descriptor, None, [[int(item_id), [int(course_id)]]]], [_ATTACHMENT_MASK]]
        result = self.call("n5QGnd", arguments)
        try:
            return result[2][1]
        except (IndexError, TypeError) as exc:
            raise WebProtocolError(f"unexpected attachment reply: {exc!r}") from exc

    def attach_form(self, course_id: int, item_id: int, form_url: str) -> str:
        """Attach a Google Form the way the web app does: as a Form, not as a link."""
        link = self.resolve_link(form_url)
        if not link["is_form"]:
            raise WebProtocolError("Classroom did not recognise the link as a Google Form")
        return self.attach_drive_item(course_id, item_id, link["drive_id"])
