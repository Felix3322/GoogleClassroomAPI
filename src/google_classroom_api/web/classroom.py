"""Google Classroom through the web app's private RPCs.

Calls are built from requests captured from the real web app; the docstrings
and ``docs/private-classroom-protocol.md`` say which were also replayed outside
a browser and which parts are inferred. Assigning to individual students is
missing: the web app only expresses it in a class with two or more students.
"""

from __future__ import annotations

import json
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Sequence

from .session import WebProtocolError, WebSession, strip_xssi

HOST = "https://classroom.google.com"
FORM_MIME = "application/vnd.google-apps.freebird"
DRIVE_KIND_FORM = 7

STATE_PUBLISHED = 2
STATE_DRAFT = 3

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

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
# Field mask the gradebook sends with Zj93ge (student submissions of one assignment). It does
# not ask for what the student attached: replayed, records read with it have an empty slot 4.
# The library reads with _GRADE_READ_MASK instead (this mask plus index 3), see below.
_SUBMISSION_MASK = [None, 1, None, None, 1, None, 1, 1, 1, None, None, None, 1, None, [1], None, None,
                    None, 1, 1, 1, None, None, None, [[1, 1]], [[1, 1]], None, None, None, None, None, 1]

_WIZ_KEYS = {"at": "SNlM0e", "sid": "FdrFJe", "build": "cfb2h"}
#: WIZ_global_data key that holds the signed-in account's e-mail address (read from real pages).
_WIZ_EMAIL_KEY = "oPEP7c"
# The signed-in user's own Classroom user id sits in the page's client configuration, right
# after the favicon address (read from real teacher and student pages; quotes may be escaped).
_OWN_ID_PATTERN = re.compile(r'classroom/favicon\.png\\?",\\?"(\d+)\\?"')


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
    attachments = (
        [[None, [[item_id, [course_id]], attachment]] for attachment in attachment_ids]
        if attachment_ids else None
    )
    if instructions:
        text = ["edu.rt", instructions]
        body += [None, text, attachments, None, ["edu.rt", instructions, None, None, [None, instructions]]]
    elif attachments:
        # No text: the web app stops after the attachment list (captured from a material).
        body += [None, None, attachments]
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
    # whole milliseconds by integer arithmetic: a float timestamp can come out 1 ms short
    return (due - _EPOCH) // timedelta(milliseconds=1), 1 if has_time else 0


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


def delete_arguments(course_id: int, item_id: int, kind: str = "assignment", *,
                     multiple_choice: bool = False) -> list:
    """F7Tqub arguments that delete one stream item.

    Same envelope as :func:`update_arguments`, but the masks write only the
    item's lifecycle field, which is set to :data:`ITEM_DELETED`. The masks and
    slot 8 match the request the web app's "delete" menu sent; the other fields
    of the item are ignored by the server (replayed: a minimal item deletes).

    A question is the exception: its question block has to be of the question's
    own type or the server refuses the delete (replayed: status 13 and the
    question stays). ``multiple_choice`` picks the block; the choices themselves
    are not needed.
    """
    item = stream_item(int(course_id), "", item_id=int(item_id))
    item[8] = ITEM_DELETED
    if kind == KIND_ASSIGNMENT:
        wrapper = assignment_wrapper(item)
    elif kind == KIND_QUESTION:
        wrapper = question_wrapper(item)
        if multiple_choice:
            wrapper[0][3][2] = [QUESTION_MULTIPLE_CHOICE, None, [[]]]
    else:
        wrapper = item_wrapper(kind, item)
    return [wrapper, DELETE_MASKS, READ_MASKS, None, [1, 1, 1]]


# --- stream item kinds ----------------------------------------------------------------
#
# n5NjMc (create) and F7Tqub (update) carry one stream item inside a tagged
# envelope; listings return the same envelope. Captured from the web app:
#   assignment    [2, [item, coursework, null, null, [2, 2]]]
#   announcement  [3, null, [item]]
#   question      [5, null, null, [item, coursework, questionBlock]]
#   material      [7, null, null, null, null, [item]]

KIND_ASSIGNMENT = "assignment"
KIND_ANNOUNCEMENT = "announcement"
KIND_QUESTION = "question"
KIND_MATERIAL = "material"
_KIND_SLOTS = {KIND_ASSIGNMENT: (2, 1), KIND_ANNOUNCEMENT: (3, 2), KIND_QUESTION: (5, 3), KIND_MATERIAL: (7, 5)}
_KIND_OF_TAG = {tag: kind for kind, (tag, _slot) in _KIND_SLOTS.items()}

QUESTION_SHORT_ANSWER = 1
QUESTION_MULTIPLE_CHOICE = 2


def item_wrapper(kind: str, item: list) -> list:
    """The tagged envelope of an announcement or a material (see above)."""
    if kind in (KIND_ASSIGNMENT, KIND_QUESTION):
        raise ValueError(f"a {kind} carries a coursework block; use {kind}_wrapper")
    tag, slot = _KIND_SLOTS[kind]
    entry: list = [tag] + [None] * slot
    entry[slot] = [item]
    return [entry]


def question_block(choices: Sequence[str] = ()) -> list:
    """The question settings as the composer sends them with its default checkboxes.

    ``[1, []]`` is a short-answer question; ``[2, null, [[choice, …]]]`` a
    multiple-choice one (one answer may be picked). The checkboxes ("students
    can reply to each other", "students can edit their answer", "students can
    see the class summary") were left at their defaults in the capture; where
    they are encoded is not known.
    """
    if choices:
        return [QUESTION_MULTIPLE_CHOICE, None, [list(choices)]]
    return [QUESTION_SHORT_ANSWER, []]


def question_wrapper(item: list, points: int = 100, choices: Sequence[str] = (), *,
                     due_ms: Optional[int] = None, due_has_time: int = 0) -> list:
    """The envelope of a question: like an assignment's, with work type 2 and a question block."""
    coursework = [due_ms, None, due_has_time if due_ms is not None else 0, points, 0, 1, [2],
                  None, None, [None, 0], None, 0]
    return [[5, None, None, [item, coursework, question_block(choices)]]]


def create_question_arguments(item: list, points: int = 100, choices: Sequence[str] = (), **due) -> list:
    return [question_wrapper(item, points, choices, **due), READ_MASKS, None, [1, 1, 1],
            None, None, None, None, None, [1, 1, None, 1]]


def update_question_arguments(item: list, points: int = 100, choices: Sequence[str] = (), **due) -> list:
    return [question_wrapper(item, points, choices, **due), UPDATE_MASKS, READ_MASKS, None, [1, 1, 1],
            None, None, None, None, None, None, [1, 1, None, 1]]


def entry_choices(entry: list) -> Optional[list]:
    """The answer choices of a multiple-choice question entry, or None."""
    if entry[0] != _KIND_SLOTS[KIND_QUESTION][0]:
        return None
    payload = entry[_KIND_SLOTS[KIND_QUESTION][1]]
    block = payload[2] if len(payload) > 2 else None
    if block and block[0] == QUESTION_MULTIPLE_CHOICE and len(block) > 2 and block[2]:
        return list(block[2][0])
    return None


def create_item_arguments(kind: str, item: list) -> list:
    """n5NjMc arguments for a new announcement or material draft."""
    return [item_wrapper(kind, item), READ_MASKS, None, [1, 1, 1],
            None, None, None, None, None, [1, 1, None, 1]]


def update_item_arguments(kind: str, item: list) -> list:
    """F7Tqub arguments that rewrite an announcement or a material."""
    return [item_wrapper(kind, item), UPDATE_MASKS, READ_MASKS, None, [1, 1, 1],
            None, None, None, None, None, None, [1, 1, None, 1]]


def _item_text(stream: list) -> str:
    """The plain text of a stream item's body (instructions / announcement text), if any."""
    try:
        body = stream[27][0]
    except (IndexError, TypeError):
        return ""
    for slot in (6, 3):
        if len(body) > slot and body[slot] and len(body[slot]) > 1 and isinstance(body[slot][1], str):
            return body[slot][1]
    return ""


def entry_details(entry: list) -> dict:
    """Points, due date, attachments and topic of one envelope from a listing.

    Layout read from the real Classwork listing: the coursework block of an
    assignment or a question starts ``[dueMs, <a timestamp>, dueHasTime,
    points, …]``; the item's body lists its attachments as
    ``[null, [[itemId, [courseId]], attachmentId]]``; the item's slot 14 holds
    ``[[topicId, [courseId]]]``. ``due`` comes back as an aware UTC datetime.
    Envelopes that were cut short (or kinds without a coursework block) give
    ``None`` / an empty list.
    """
    kind, stream = entry_kind_and_item(entry)
    details: dict = {"points": None, "due": None, "due_has_time": False, "attachment_ids": [], "topic_id": None}
    if kind in (KIND_ASSIGNMENT, KIND_QUESTION):
        payload = entry[_KIND_SLOTS[kind][1]]
        block = payload[1] if len(payload) > 1 and isinstance(payload[1], list) else []
        if len(block) > 3:
            details["points"] = block[3]
            if block[0] is not None:
                details["due"] = _EPOCH + timedelta(milliseconds=int(block[0]))
                details["due_has_time"] = bool(block[2])
    body = stream[27][0] if len(stream) > 27 and stream[27] else None
    if body and len(body) > 4 and body[4]:
        details["attachment_ids"] = [str(attachment[1][1]) for attachment in body[4]]
    if len(stream) > 14 and stream[14]:
        details["topic_id"] = str(stream[14][0][0])
    return details


def entry_kind_and_item(entry: list) -> tuple[str, list]:
    """``(kind, streamItem)`` of one tagged envelope from a listing."""
    tag = entry[0]
    kind = _KIND_OF_TAG.get(tag)
    if kind is not None:
        return kind, entry[_KIND_SLOTS[kind][1]][0]
    for part in entry[1:]:  # a kind not named above (e.g. a question): first payload found
        if isinstance(part, list) and part and isinstance(part[0], list):
            return f"type-{tag}", part[0]
    raise WebProtocolError(f"stream entry of type {tag!r} has no item")


# --- attachments other than Drive files -------------------------------------------------

LINK_KIND = 4


def link_descriptor(url: str) -> list:
    """The 21-slot attachment descriptor the web app sends for a plain link."""
    return [None, None, url, LINK_KIND] + [None] * 16 + [1]


# --- grading ----------------------------------------------------------------------------

# Masks of CVt8yf as the gradebook sent them when a draft grade was typed into a cell.
# The read mask is the gradebook's Zj93ge mask plus index 3, the attachments of the submission
# (the student-work page reads those on their own with [null, 1, null, 1]). Replayed: Zj93ge
# accepts it from teachers and students, by item and by reference, and returns the attachments.
_GRADE_WRITE_MASK = [None, None, None, 1]
_GRADE_READ_MASK = [None, 1, None, 1, 1, None, 1, 1, 1, None, None, None, 1, None, [1], None, None, None,
                    1, 1, 1, None, None, None, [[1, 1]], [[1, 1]], None, None, None, None, None, 1]
#: Slot of the draft grade in a submission record.
SUBMISSION_DRAFT_GRADE_SLOT = 9


def submission_ref(course_id: int, item_id: int, student_id: int) -> list:
    """How a student's submission of one assignment is addressed."""
    return [int(student_id), [int(item_id), [int(course_id)]]]


def draft_grade_arguments(course_id: int, item_id: int, student_id: int, grade: Optional[float]) -> list:
    """CVt8yf arguments that set, or with ``None`` clear, one student's draft grade.

    Both forms were captured from a gradebook cell: typing a number sends the
    record with the grade in slot 9; emptying the cell sends the record with
    nothing but its reference, under the same write mask.
    """
    ref = submission_ref(course_id, item_id, student_id)
    record: list = [ref]
    if grade is not None:
        record += [None] * (SUBMISSION_DRAFT_GRADE_SLOT - 1) + [grade]
    return [[3], [[ref, record, _GRADE_WRITE_MASK]], _GRADE_READ_MASK]


# --- a student's own work ----------------------------------------------------------------
#
# What the student's pages sent (captured 2026-11-23). All of it is CVt8yf, the same call a
# gradebook cell uses, on the student's own submission record:
#   "mark as done"            slot 5 = 2,           write mask [1, null×11, 1]
#   answer a question         slot 5 = 2 and the answer in slot 16, mask [1, null×6, [1], null×4, 1]
#       short answer   [null, "text"]         multiple choice   [null, null, "the choice"]
#   "unsubmit"                the record cut after slot 5 = 5, mask [1]
# Slot 32 of the record is 1 in every turn-in, as it is in every record read back.

SUBMISSION_TURNED_IN = 2
SUBMISSION_RETURNED = 3
SUBMISSION_ASSIGNED = 4
SUBMISSION_UNSUBMITTED = 5
#: Names for the state code in slot 5 of a submission record. All four were observed.
SUBMISSION_STATES = {SUBMISSION_TURNED_IN: "turned_in", SUBMISSION_RETURNED: "returned",
                     SUBMISSION_ASSIGNED: "assigned", SUBMISSION_UNSUBMITTED: "unsubmitted"}
#: Slot of the grade the student can see (set when work is returned); slot 9 is the teacher's draft.
SUBMISSION_GRADE_SLOT = 8
SUBMISSION_ANSWER_SLOT = 16
SUBMISSION_TURNED_IN_AT_SLOT = 21

_TURN_IN_MASK = [1] + [None] * 11 + [1]
_ANSWER_MASK = [1] + [None] * 6 + [[1]] + [None] * 4 + [1]
_UNSUBMIT_MASK = [1]
# The teacher's "return": slot 5 = 3 and the grade in both slot 8 and slot 9.
_RETURN_MASK = [1, None, 1, 1] + [None] * 8 + [1]


def _turned_in_record(ref: list, answer: Optional[list] = None) -> list:
    record: list = [ref] + [None] * 32
    record[5] = SUBMISSION_TURNED_IN
    record[SUBMISSION_ANSWER_SLOT] = answer
    record[32] = 1
    return record


def turn_in_arguments(course_id: int, item_id: int, student_id: int) -> list:
    """CVt8yf arguments of the student's "mark as done"."""
    ref = submission_ref(course_id, item_id, student_id)
    return [[3], [[ref, _turned_in_record(ref), _TURN_IN_MASK]], _GRADE_READ_MASK]


def answer_arguments(course_id: int, item_id: int, student_id: int, *, text: Optional[str] = None,
                     choice: Optional[str] = None) -> list:
    """CVt8yf arguments that answer a question and turn the answer in."""
    if (text is None) == (choice is None):
        raise ValueError("give either text (short answer) or choice (multiple choice)")
    ref = submission_ref(course_id, item_id, student_id)
    answer = [None, text] if choice is None else [None, None, choice]
    return [[3], [[ref, _turned_in_record(ref, answer), _ANSWER_MASK]], _GRADE_READ_MASK]


def unsubmit_arguments(course_id: int, item_id: int, student_id: int) -> list:
    """CVt8yf arguments of the student's "unsubmit"."""
    ref = submission_ref(course_id, item_id, student_id)
    return [[3], [[ref, [ref, None, None, None, None, SUBMISSION_UNSUBMITTED], _UNSUBMIT_MASK]],
            _GRADE_READ_MASK]


def return_arguments(course_id: int, item_id: int, student_id: int, grade: Optional[float]) -> list:
    """CVt8yf arguments of the teacher's "return", as the student-work page sent them.

    The grade goes out twice: as the grade the student gets (slot 8) and as
    the draft (slot 9). ``None`` returns the work ungraded.
    """
    ref = submission_ref(course_id, item_id, student_id)
    record: list = [ref] + [None] * 32
    record[5] = SUBMISSION_RETURNED
    record[SUBMISSION_GRADE_SLOT] = grade
    record[SUBMISSION_DRAFT_GRADE_SLOT] = grade
    record[32] = 1
    return [[3], [[ref, record, _RETURN_MASK]], _GRADE_READ_MASK]


# --- what a student attaches to their work ---------------------------------------------------
#
# Captured from the student's "Add or create" menu (2026-11-23). Attachments live in slot 4
# of the submission record and are written as a whole list (mask [null, 1]): adding one sends
# the old ones again plus the new one, removing one sends what is left. Each entry is a
# 21-slot descriptor:
#   a link                 [null, null, url, 4, null×13, id, null, null, 1]
#   a new Google Doc       [null, null, null, 2, "application/vnd.google-apps.document",
#                           null×9, [], null, null, id, null, null, 1]     (the server creates it)
#   one already attached   the same with slot 2 = its url / Drive id, slot 4 = its type,
#                          and its attachment id in slot 17
# For a new entry the web app puts a random number of its own in slot 17 (seen: 32206126917,
# -13956082662). A link keeps it as its id; a created file gets a server id instead.
# Turning in afterwards is the plain "mark as done" request: the attachments are not sent again.

SUBMISSION_ATTACHMENTS_SLOT = 4
DRIVE_KIND_FILE = 2
NEW_DOCUMENT_MIME = "application/vnd.google-apps.document"
_ATTACHMENTS_MASK = [None, 1]
ATTACHMENT_KINDS = {LINK_KIND: "link", DRIVE_KIND_FILE: "drive_file", DRIVE_KIND_FORM: "form"}


def new_attachment_id() -> int:
    """A client-side id for a new attachment, in the range of the web app's own."""
    return secrets.randbelow(2 ** 36) - 2 ** 35


def submission_link(url: str, attachment_id: Optional[int] = None) -> list:
    """Descriptor of a link in a student's submission."""
    return ([None, None, url, LINK_KIND] + [None] * 13
            + [new_attachment_id() if attachment_id is None else int(attachment_id), None, None, 1])


def submission_file(drive_id: Optional[str], mime_type: Optional[str],
                    attachment_id: Optional[int] = None) -> list:
    """Descriptor of a Drive file in a student's submission.

    ``drive_id=None`` with a Google Docs type asks Classroom to create a new,
    empty file of that type in the student's Drive (captured for a Doc).
    """
    return ([None, None, drive_id, DRIVE_KIND_FILE, mime_type] + [None] * 9 + [[]]
            + [None, None, new_attachment_id() if attachment_id is None else int(attachment_id),
               None, None, 1])


def attachment_descriptor(attachment: list) -> list:
    """The descriptor to send again for an attachment as it was read back."""
    kind, attachment_id = attachment[3], attachment[17]
    if kind == LINK_KIND:
        return submission_link(attachment[2], attachment_id)
    return submission_file(attachment[2], attachment[4], attachment_id)


def attachments_arguments(course_id: int, item_id: int, student_id: int, descriptors: list) -> list:
    """CVt8yf arguments that replace the attachments of the student's own submission."""
    ref = submission_ref(course_id, item_id, student_id)
    return [[3], [[ref, [ref, None, None, None, descriptors], _ATTACHMENTS_MASK]], _GRADE_READ_MASK]


def parse_attachment(attachment: list) -> dict:
    """One attachment of a submission as plain fields (layout read from real replies)."""
    kind = attachment[3]
    is_link = kind == LINK_KIND
    return {
        "attachment_id": str(attachment[17]) if len(attachment) > 17 and attachment[17] is not None else None,
        "kind": ATTACHMENT_KINDS.get(kind, f"type-{kind}"),
        "title": attachment[0],
        "url": (attachment[6] if len(attachment) > 6 and attachment[6] else None) or (attachment[2] if is_link else None),
        "drive_id": None if is_link else attachment[2],
        "mime_type": attachment[4] if len(attachment) > 4 else None,
    }


def own_submission_arguments(course_id: int, item_id: int, student_id: int) -> list:
    """Zj93ge arguments that read one submission by its reference, as the student's page does.

    The page sends this with the gradebook's mask; the library adds the
    attachments field to it (see :data:`_GRADE_READ_MASK`).
    """
    return [[None, None, 2, 0], _GRADE_READ_MASK,
            [None, None, [submission_ref(course_id, item_id, student_id)]], 1]


def parse_submission(record: list) -> dict:
    """One submission record as plain fields.

    Layout read from records the real server returned: slot 0 the reference,
    2 the last update, 5 the state, 8 the grade the student got when the work
    was returned, 9 the teacher's draft grade, 16 the answer to a question,
    19 the history (teachers only), 21 when it was turned in.

    ``state`` is the name of the state code (:data:`SUBMISSION_STATES`:
    assigned, turned in, returned, unsubmitted), ``None`` for a code never
    seen; the code itself is in ``state_code``. A record appears a second or
    two after the work is published to a student.

    ``answer`` is the text of a short answer or the picked choice;
    ``answer_type`` is 1 for a short-answer question, 2 for multiple choice,
    ``None`` for an assignment. ``attachments`` is what the student attached
    (slot 4, see :func:`parse_attachment`). The whole record is kept under
    ``raw``.
    """
    try:
        ref = record[0]
        history = record[19] if len(record) > 19 and record[19] else []
        grades = []
        for entry in history:
            change = entry[1] if len(entry) > 1 else None
            if change:
                grades.append({"grade": change[0], "max_points": change[1], "time_ms": change[2],
                               "by_user_id": str(change[3][0]) if change[3] else None,
                               # 1 where a draft grade was typed, 2 where work was returned with it
                               "returned": len(change) > 5 and change[5] == 2})
        block = record[SUBMISSION_ANSWER_SLOT] if len(record) > SUBMISSION_ANSWER_SLOT else None
        answer_type = block[0] if block else None
        answer = None
        if block and answer_type == QUESTION_SHORT_ANSWER and len(block) > 1:
            answer = block[1]
        elif block and answer_type == QUESTION_MULTIPLE_CHOICE and len(block) > 2:
            answer = block[2]
        return {
            "student_id": str(ref[0]),
            "item_id": str(ref[1][0]),
            "updated_ms": record[2],
            "state": SUBMISSION_STATES.get(record[5]),
            "state_code": record[5],
            "turned_in_ms": record[SUBMISSION_TURNED_IN_AT_SLOT] if len(record) > SUBMISSION_TURNED_IN_AT_SLOT else None,
            "answer": answer,
            "answer_type": answer_type,
            "attachments": [parse_attachment(a) for a in (record[SUBMISSION_ATTACHMENTS_SLOT] or [])]
            if len(record) > SUBMISSION_ATTACHMENTS_SLOT else [],
            "grade": record[SUBMISSION_GRADE_SLOT] if len(record) > SUBMISSION_GRADE_SLOT else None,
            "draft_grade": record[SUBMISSION_DRAFT_GRADE_SLOT] if len(record) > SUBMISSION_DRAFT_GRADE_SLOT else None,
            "grade_history": grades,
            "raw": record,
        }
    except (IndexError, TypeError) as exc:
        raise WebProtocolError(f"unexpected submission record: {exc!r}") from exc


# --- topics -----------------------------------------------------------------------------


def rename_topic_arguments(course_id: int, topic_id: str, name: str) -> list:
    """WRrP0b arguments, as the topic menu's "rename" sent them."""
    return [[[str(topic_id), [int(course_id)]], name], [1], [1, 1, 1]]


def delete_topic_arguments(course_id: int, topic_id: str) -> list:
    """EjPfY arguments, as the topic menu's "delete" sent them."""
    return [[str(topic_id), [int(course_id)]]]


# --- course list ------------------------------------------------------------------------

# gXtzob as the home page sends it at start-up to list the user's courses.
COURSE_LIST_MASK = [1, 1, None, None, 1, 1, None, 1,
                    [None, 1, None, 1, None, 1, [1, 1, 1, 1, 1, 1], None, [1], 1, 1, 1, 1, [1, 1], [1, 1]],
                    1, 1, None, None, 1, None, None, None, None, None, None, [1, 1, 1, 1, 1, 1, 1], 1, None,
                    None, 1, None, None, None, [1, 1], 1, 1, None, None, 1, 1, 1, 1, None, 1, 1,
                    [None, None, None, None, 1, 1, None, None, 1, None, None, None, None, None, None, None,
                     None, 1, 1, 1, 1, 1, 1],
                    None, [1, 1, 1], [1, 1, None, 1], 1, 1, 1, [[[1, 1, 1], 1], [[[1, 1]]], 1], None, 1, [1, 1],
                    None, None, 1, None, [None, None, 1], 1]
COURSE_LIST_QUERY = [[None, [[1]]], None, None, [1, 2, 3], None, None, [1, 2]]


def course_list_arguments() -> list:
    return [[100, None, 1, 0], COURSE_LIST_MASK, COURSE_LIST_QUERY]


# --- stream queries (pONvgf) ------------------------------------------------------------
#
# Filters captured from the web app, as [lifecycle, [[courseId]], kinds, states]:
#   the Stream page's feed          [[2, 3], course, null,   [2]]   published items of every kind
#   its "drafts" drawer             [[2],    course, [3],    [3]]   announcement drafts
#   the gradebook's columns         [[2],    course, [2, 5], [2]]   published graded work
#
# ``lifecycle`` is the item's slot 8: 2 = live, 3 = deleted. Replayed on the real
# server: [2] returns live items only, [3] only deleted ones, and the feed's
# [2, 3] both -- a deleted post stays in that answer for good, marked 3 (the web
# app evidently drops those itself).


def stream_query_arguments(course_id: int, lifecycle: list, kinds: Optional[list], states: list,
                           page_size: int = 100) -> list:
    return [[page_size, None, 1, 0], READ_MASKS, [[lifecycle, [[int(course_id)]], kinds, states]]]


class StreamItemRejected(WebProtocolError):
    """Classroom answered a create / update / delete of a stream item with an error status.

    The reply to n5NjMc and F7Tqub carries one ``[status, item]`` pair per item
    sent; ``[0]`` is success. Seen on the real server: ``[13, "generic"]`` for a
    question delete whose question block had the wrong type — the call itself
    returned HTTP 200 and the item was left as it was.
    """

    def __init__(self, rpc_id: str, status: list) -> None:
        super().__init__(f"Classroom refused {rpc_id} (status {status!r}); nothing was changed")
        self.status = status


def check_item_statuses(rpc_id: str, result: Any) -> None:
    """Raise :class:`StreamItemRejected` when a stream-item write was refused."""
    pairs = result[1] if isinstance(result, list) and len(result) > 1 and isinstance(result[1], list) else []
    for pair in pairs:
        status = pair[0] if isinstance(pair, list) and pair else None
        if isinstance(status, list) and status and status[0] not in (0, None):
            raise StreamItemRejected(rpc_id, status)


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
    """The private Classroom calls, as one of the accounts the cookies are signed in to.

    A browser profile can be signed in to several Google accounts at once, and
    an export from it carries all of them; the web app tells them apart by the
    ``/u/<n>/`` part of its URLs. ``account`` is that number. Without it the
    URLs have no such part and Google uses the default account, number 0
    (replayed: an export with accounts 0, 2 and 3 acted as 0).
    """

    def __init__(self, session: WebSession, *, language: str = "en", account: Optional[int] = None) -> None:
        self._s = session
        self._language = language
        self._prefix = "" if account is None else f"/u/{int(account)}"
        self._wiz: Optional[dict] = None

    def _boot(self) -> dict:
        if self._wiz is None:
            response = self._s.get(f"{HOST}{self._prefix}/h")
            if response.status_code != 200:
                raise WebProtocolError(f"cannot open Classroom: HTTP {response.status_code}")
            # Replayed: an account number nobody is signed in under is redirected to
            # account 0 instead of being refused. Acting as the wrong account must not happen.
            if self._prefix and not response.url.path.startswith(self._prefix + "/"):
                raise WebProtocolError(
                    f"the cookies have no account under {self._prefix}/ (Classroom answered from "
                    f"{response.url.path}); check the number in the web app's address bar")
            wiz = {}
            for name, key in _WIZ_KEYS.items():
                match = re.search(r'"%s":"([^"]+)"' % key, response.text)
                if not match:
                    raise WebProtocolError(f"Classroom page has no {key}; is the session signed in?")
                wiz[name] = match.group(1)
            email = re.search(r'"%s":"([^"]+)"' % _WIZ_EMAIL_KEY, response.text)
            wiz["email"] = email.group(1) if email else None
            own_id = _OWN_ID_PATTERN.search(response.text)
            wiz["user_id"] = own_id.group(1) if own_id else None
            self._wiz = wiz
        return self._wiz

    def signed_in_email(self) -> Optional[str]:
        """The e-mail address of the account this session acts as (from the page's own data)."""
        return self._boot()["email"]

    def own_user_id(self) -> str:
        """The Classroom user id of the account this session acts as (from the page's own data)."""
        user_id = self._boot()["user_id"]
        if not user_id:
            raise WebProtocolError("the Classroom page no longer shows the signed-in user's id where it did")
        return user_id

    def call(self, rpc_id: str, arguments: Any, *, source_path: str = "/h") -> Any:
        """One ``batchexecute`` call. Replayed by hand for reads and writes."""
        wiz = self._boot()
        params = {
            "rpcids": rpc_id,
            "source-path": self._prefix + source_path,
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
            f"{HOST}{self._prefix}/_/ClassroomUi/data/batchexecute",
            params=params,
            data={"f.req": _dump(envelope), "at": wiz["at"]},
            headers={"X-Same-Domain": "1"},
        )
        if response.status_code != 200:
            raise WebProtocolError(f"{rpc_id} failed: HTTP {response.status_code}")
        return parse_batchexecute(response.text, rpc_id)

    def write_item(self, rpc_id: str, arguments: Any) -> Any:
        """A create (n5NjMc) or update (F7Tqub) of a stream item, with its status checked."""
        result = self.call(rpc_id, arguments)
        check_item_statuses(rpc_id, result)
        return result

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

    def rename_topic(self, course_id: int, topic_id: str, name: str) -> None:
        """Rename a topic (WRrP0b, captured from the topic menu's "rename"). Replayed."""
        self.call("WRrP0b", rename_topic_arguments(course_id, topic_id, name))

    def delete_topic(self, course_id: int, topic_id: str) -> None:
        """Delete a topic (EjPfY, captured from the topic menu's "delete"). Replayed.

        The posts filed under it are kept and fall back to "no topic"
        (replayed with a draft under the deleted topic).
        """
        self.call("EjPfY", delete_topic_arguments(course_id, topic_id))

    # --- assignments --------------------------------------------------------------

    @staticmethod
    def _item_summary(result: Any) -> dict:
        try:
            _kind, stream = entry_kind_and_item(result[1][0][1])
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
        return self._item_summary(self.write_item("n5NjMc", arguments))

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

        The call sends the whole assignment: what is not passed again is
        cleared (replayed: an update with only a title removed the topic, the
        attachment, the instructions and the due date, and reset the points to
        100). :meth:`Classroom.update_assignment` reads the item first and
        keeps what you do not change.
        """
        item = stream_item(
            int(course_id), title, item_id=int(item_id), instructions=instructions,
            state=STATE_PUBLISHED if publish else STATE_DRAFT,
            attachment_ids=[int(a) for a in attachment_ids], topic_id=topic_id,
        )
        due_ms, flag = due_fields(due, due_has_time)
        arguments = update_arguments(item, points, due_ms=due_ms, due_has_time=flag)
        return self._item_summary(self.write_item("F7Tqub", arguments))

    def delete_assignment(self, course_id: int, item_id: int) -> None:
        """Delete an assignment (draft or published), as the web app's menu does. Replayed.

        The web app sends an F7Tqub update whose masks write only the item's
        lifecycle field, set to "deleted"; the other fields it sends are ignored.
        """
        self.write_item("F7Tqub", delete_arguments(course_id, item_id))

    def delete_item(self, course_id: int, item_id: int, kind: str) -> None:
        """Delete a stream item of any kind (assignment, material, announcement, question). Replayed.

        A question can only be deleted with a question block of its own type
        (see :func:`delete_arguments`). The type is not known from the id alone,
        so the short-answer form is sent first and, when the server refuses it,
        the multiple-choice form. A refused delete changes nothing (replayed).
        """
        if kind != KIND_QUESTION:
            self.write_item("F7Tqub", delete_arguments(course_id, item_id, kind))
            return
        try:
            self.write_item("F7Tqub", delete_arguments(course_id, item_id, kind))
        except StreamItemRejected:
            self.write_item("F7Tqub", delete_arguments(course_id, item_id, kind, multiple_choice=True))

    def delete_question(self, course_id: int, item_id: int) -> None:
        """Delete a question of either type. Replayed."""
        self.delete_item(course_id, item_id, KIND_QUESTION)

    @staticmethod
    def _rows(result: Any, topic_id: Optional[str] = None) -> list[dict]:
        rows = []
        for entry in (result[2] if len(result) > 2 and result[2] else []):
            try:
                kind, stream = entry_kind_and_item(entry)
                details = entry_details(entry)
                row = {"item_id": str(stream[0][0]), "kind": kind, "title": stream[5],
                       "state": stream[9][0] if stream[9] else None,
                       "topic_id": topic_id or details["topic_id"], "text": _item_text(stream),
                       "points": details["points"], "due": details["due"],
                       "due_has_time": details["due_has_time"],
                       "attachment_ids": details["attachment_ids"]}
                if kind == KIND_QUESTION:
                    row["choices"] = entry_choices(entry)
                rows.append(row)
            except (IndexError, TypeError) as exc:
                raise WebProtocolError(f"unexpected stream item: {exc!r}") from exc
        return rows

    @staticmethod
    def _refuse_more(result: Any, what: str) -> None:
        more = result[1] if len(result) > 1 and isinstance(result[1], list) else [False]
        if more and more[0] is True:
            raise WebProtocolError(f"{what} has more than 100 items; "
                                   "paging past the first 100 has not been reverse-engineered")

    def list_classwork(self, course_id: int) -> list[dict]:
        """Everything on the Classwork page (assignments, materials, drafts), by topic. Replayed.

        The web app lists Classwork one section at a time with dpT4Vd: the
        "no topic" section is ``(null, 1)`` and each topic is
        ``([topicId, [courseId]], 0)``. Items come back newest first within a
        section. Rows are described in :func:`entry_details`.
        """
        cid = int(course_id)
        sections = [(None, 1, None)] + [([str(t["topic_id"]), [cid]], 0, str(t["topic_id"]))
                                        for t in self.list_topics(cid)]
        rows = []
        for ref, flag, topic_id in sections:
            result = self.call("dpT4Vd", [[100, None, 2, 0], [cid], ref, flag, 2, READ_MASKS])
            self._refuse_more(result, "a Classwork section")
            rows += self._rows(result, topic_id)
        return rows

    def get_item(self, course_id: int, item_id: int) -> Optional[dict]:
        """One item of the class as a listing row, or None. Replayed.

        There is no captured call that reads a single item, so this looks it up
        in the Classwork listing and, failing that, among the announcements
        (drafts first, then posted ones).
        """
        wanted = str(item_id)
        for row in self.list_classwork(course_id):
            if row["item_id"] == wanted:
                return row
        for drafts in (True, False):
            for row in self.list_announcements(course_id, drafts=drafts):
                if row["item_id"] == wanted:
                    return row
        return None

    def list_assignments(self, course_id: int) -> list[dict]:
        """Every assignment and assignment draft of the class. Replayed."""
        return [row for row in self.list_classwork(course_id) if row["kind"] == KIND_ASSIGNMENT]

    # --- questions ------------------------------------------------------------------

    def create_question(self, course_id: int, question: str, *, choices: Sequence[str] = (),
                        instructions: str = "", points: int = 100, topic_id: Optional[str] = None,
                        due: Optional[datetime] = None, due_has_time: bool = True) -> dict:
        """Create a draft question: short answer, or multiple choice when ``choices`` is given.

        The composer creates a short-answer draft and switches it with an
        update; sending the final question block in the create was replayed.
        """
        item = stream_item(int(course_id), question, instructions=instructions, state=STATE_DRAFT,
                           topic_id=topic_id)
        due_ms, flag = due_fields(due, due_has_time)
        arguments = create_question_arguments(item, points, choices, due_ms=due_ms, due_has_time=flag)
        return self._item_summary(self.write_item("n5NjMc", arguments))

    def update_question(self, course_id: int, item_id: int, question: str, *,
                        choices: Sequence[str] = (), instructions: str = "", points: int = 100,
                        attachment_ids: Sequence[int] = (),
                        topic_id: Optional[str] = None, due: Optional[datetime] = None,
                        due_has_time: bool = True, publish: bool = False) -> dict:
        """Rewrite a question; with ``publish`` it is asked to the class. Replayed.

        Like every update, this sends the whole item: what is not passed is cleared.
        """
        item = stream_item(int(course_id), question, item_id=int(item_id), instructions=instructions,
                           state=STATE_PUBLISHED if publish else STATE_DRAFT,
                           attachment_ids=[int(a) for a in attachment_ids], topic_id=topic_id)
        due_ms, flag = due_fields(due, due_has_time)
        arguments = update_question_arguments(item, points, choices, due_ms=due_ms, due_has_time=flag)
        return self._item_summary(self.write_item("F7Tqub", arguments))

    # --- materials and announcements ------------------------------------------------

    def create_item(self, kind: str, course_id: int, title: str, *, text: str = "",
                    topic_id: Optional[str] = None) -> dict:
        """Create a draft material or announcement (n5NjMc with that kind's envelope).

        An announcement has no title: its words go in ``text``.
        """
        item = stream_item(int(course_id), title, instructions=text, state=STATE_DRAFT, topic_id=topic_id)
        return self._item_summary(self.write_item("n5NjMc", create_item_arguments(kind, item)))

    def update_item(self, kind: str, course_id: int, item_id: int, title: str, *, text: str = "",
                    attachment_ids: Sequence[int] = (), topic_id: Optional[str] = None,
                    publish: bool = False) -> dict:
        """Rewrite a material or announcement; the whole item is sent, as for assignments."""
        item = stream_item(int(course_id), title, item_id=int(item_id), instructions=text,
                           state=STATE_PUBLISHED if publish else STATE_DRAFT,
                           attachment_ids=[int(a) for a in attachment_ids], topic_id=topic_id)
        return self._item_summary(self.write_item("F7Tqub", update_item_arguments(kind, item)))

    def query_stream(self, course_id: int, lifecycle: list, kinds: Optional[list], states: list) -> list[dict]:
        """Stream items matching a pONvgf filter (see :func:`stream_query_arguments`)."""
        result = self.call("pONvgf", stream_query_arguments(course_id, lifecycle, kinds, states))
        self._refuse_more(result, "the stream")
        return self._rows(result)

    def list_stream(self, course_id: int) -> list[dict]:
        """Published items of every kind, newest first: what is on the Stream page. Replayed.

        The Stream page itself asks for live *and* deleted items; this asks for
        live ones only, so that deleted posts are not listed.
        """
        return self.query_stream(course_id, [ITEM_LIVE], None, [STATE_PUBLISHED])

    def list_announcements(self, course_id: int, *, drafts: bool = False) -> list[dict]:
        """Announcements: published ones from the feed, or the saved drafts."""
        if drafts:
            return self.query_stream(course_id, [ITEM_LIVE], [3], [STATE_DRAFT])
        return [row for row in self.list_stream(course_id) if row["kind"] == KIND_ANNOUNCEMENT]

    # --- courses --------------------------------------------------------------------

    def list_courses(self) -> list[dict]:
        """The signed-in user's courses, as the home page lists them at start-up."""
        result = self.call("gXtzob", course_list_arguments())
        rows = []
        for course in (result[2] if len(result) > 2 and result[2] else []):
            try:
                rows.append({"course_id": str(course[0][0]), "name": course[5],
                             "section": course[8] if len(course) > 8 else None})
            except (IndexError, TypeError) as exc:
                raise WebProtocolError(f"unexpected course record: {exc!r}") from exc
        return rows

    # --- grading --------------------------------------------------------------------

    def set_draft_grade(self, course_id: int, item_id: int, student_id: int,
                        grade: Optional[float]) -> dict:
        """Set (or with ``None`` clear) one student's draft grade, as a gradebook cell does.

        A draft grade is visible to teachers only until the work is returned.
        Returns the submission record the server answers with (``hrw.sub``).
        """
        result = self.call("CVt8yf", draft_grade_arguments(course_id, item_id, student_id, grade))
        try:
            return parse_submission(result[1][0])
        except (IndexError, TypeError) as exc:
            raise WebProtocolError(f"unexpected grade reply: {exc!r}") from exc

    def return_work(self, course_id: int, item_id: int, student_id: int, grade: Optional[float]) -> dict:
        """As the teacher: return one student's work, with a grade or (``None``) without. Replayed.

        Returning is what makes a grade visible to the student. Returns the
        submission record the server answers with.
        """
        return self._write_submission(return_arguments(course_id, item_id, int(student_id), grade))

    # --- the student's side ---------------------------------------------------------

    def _write_submission(self, arguments: list) -> dict:
        result = self.call("CVt8yf", arguments)
        try:
            return parse_submission(result[1][0])
        except (IndexError, TypeError) as exc:
            raise WebProtocolError(f"unexpected submission reply: {exc!r}") from exc

    def own_submission(self, course_id: int, item_id: int) -> dict:
        """As a student: your own submission record of one item. Replayed."""
        result = self.call("Zj93ge", own_submission_arguments(course_id, item_id, int(self.own_user_id())))
        records = result[2] if len(result) > 2 and result[2] else []
        if not records:
            raise WebProtocolError(f"item {item_id} has no submission for the signed-in user (not a student "
                                   "of it, not published, or published less than a couple of seconds ago)")
        return parse_submission(records[0])

    def turn_in(self, course_id: int, item_id: int) -> dict:
        """As a student: mark an assignment as done. Replayed."""
        return self._write_submission(turn_in_arguments(course_id, item_id, int(self.own_user_id())))

    def unsubmit(self, course_id: int, item_id: int) -> dict:
        """As a student: take turned-in work back. Replayed."""
        return self._write_submission(unsubmit_arguments(course_id, item_id, int(self.own_user_id())))

    def answer_question(self, course_id: int, item_id: int, *, text: Optional[str] = None,
                        choice: Optional[str] = None) -> dict:
        """As a student: answer a question and turn the answer in. Replayed."""
        return self._write_submission(
            answer_arguments(course_id, item_id, int(self.own_user_id()), text=text, choice=choice))

    def _own_attachments(self, course_id: int, item_id: int) -> list:
        """Descriptors of what is attached to the signed-in student's submission right now."""
        raw = self.own_submission(course_id, item_id)["raw"]
        current = raw[SUBMISSION_ATTACHMENTS_SLOT] if len(raw) > SUBMISSION_ATTACHMENTS_SLOT else None
        return [attachment_descriptor(a) for a in (current or [])]

    def set_own_attachments(self, course_id: int, item_id: int, descriptors: list) -> dict:
        """As a student: replace the attachments of your own submission. Replayed."""
        return self._write_submission(
            attachments_arguments(course_id, item_id, int(self.own_user_id()), descriptors))

    def add_own_attachment(self, course_id: int, item_id: int, descriptor: list) -> dict:
        """As a student: attach one more thing, keeping what is already attached. Replayed."""
        return self.set_own_attachments(course_id, item_id,
                                        self._own_attachments(course_id, item_id) + [descriptor])

    def remove_own_attachment(self, course_id: int, item_id: int, attachment_id: str) -> dict:
        """As a student: detach one attachment by its id. Replayed."""
        current = self._own_attachments(course_id, item_id)
        kept = [d for d in current if str(d[17]) != str(attachment_id)]
        if len(kept) == len(current):
            raise LookupError(f"the submission has no attachment {attachment_id}")
        return self.set_own_attachments(course_id, item_id, kept)

    def question_summary(self, course_id: int, item_id: int) -> dict:
        """How many students picked each choice of a multiple-choice question (yMOiof).

        Captured from the student's page after answering: the reply is
        ``["hrs.ssl", [[[0], itemRef, 2, [[[choice, count], …], itemRef]]]]``.
        """
        result = self.call("yMOiof", [[None, None, 1, 0], [[int(item_id), [int(course_id)]]]])
        try:
            rows = result[1][0][3][0] if result[1] and len(result[1][0]) > 3 and result[1][0][3] else []
            return {choice: count for choice, count in rows}
        except (IndexError, TypeError, ValueError) as exc:
            raise WebProtocolError(f"unexpected question summary: {exc!r}") from exc

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
        """Raw student submission records of one assignment (Zj93ge, as the gradebook calls it).

        The server answers ``["hrq.sub", [hasMore], [record, …]]``; the third
        element is missing when no student has a record yet. A student only gets
        a record once something happened to their work (here: a draft grade), so
        students who were merely assigned the work are absent.
        """
        result = self.call("Zj93ge", [[100, None, 1, 0], _GRADE_READ_MASK,
                                      [None, [[int(item_id), [int(course_id)]]]], 0])
        self._refuse_more(result, "the submission list")
        return list(result[2]) if len(result) > 2 and result[2] else []

    def list_submissions(self, course_id: int, item_id: int) -> list[dict]:
        """Submission records of one assignment as plain fields (see :func:`parse_submission`)."""
        return [parse_submission(record) for record in self.list_submissions_raw(course_id, item_id)]

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

        Replayed for a Google Form; other Drive files have not been tried.
        """
        descriptor = [None, None, drive_id, kind, mime_type] + [None] * 8 \
            + [[[], None, None, None, [None, 0]]] + [None] * 6 + [1]
        arguments = [[descriptor, None, [[int(item_id), [int(course_id)]]]], [_ATTACHMENT_MASK]]
        result = self.call("n5QGnd", arguments)
        try:
            return result[2][1]
        except (IndexError, TypeError) as exc:
            raise WebProtocolError(f"unexpected attachment reply: {exc!r}") from exc

    def attach_link(self, course_id: int, item_id: int, url: str) -> str:
        """Attach a plain web link to a stream item and return the attachment id.

        Captured from the composer's "link" button: n5QGnd with the link
        descriptor (kind 4). Pass the returned id in ``attachment_ids`` of the
        next update, as the web app does, or the attachment is not kept.
        """
        arguments = [[link_descriptor(url), None, [[int(item_id), [int(course_id)]]]], [_ATTACHMENT_MASK]]
        result = self.call("n5QGnd", arguments)
        try:
            return result[2][1]
        except (IndexError, TypeError) as exc:
            raise WebProtocolError(f"unexpected attachment reply: {exc!r}") from exc

    def attach_form(self, course_id: int, item_id: int, form_url: str) -> str:
        """Attach a Google Form the way the web app does: as a Form, not as a link. Replayed.

        Classroom recognises a form only by a link that contains its file id —
        ``…/forms/d/<fileId>/edit`` or ``…/forms/d/<fileId>/viewform``. The
        public responder link (``…/forms/d/e/<long id>/viewform``) resolves as a
        plain web link (replayed), so it is refused here rather than attached as
        something else.
        """
        link = self.resolve_link(form_url)
        if not link["is_form"]:
            raise WebProtocolError(
                "Classroom did not recognise the link as a Google Form. Use the form's own link "
                "(https://docs.google.com/forms/d/<fileId>/edit); the public responder link "
                "(…/forms/d/e/…/viewform) is only seen as a web link.")
        return self.attach_drive_item(course_id, item_id, link["drive_id"])
