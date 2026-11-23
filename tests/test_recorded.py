"""Offline tests pinned to traffic recorded from the real Google web apps.

There is no simulated server here. Each test does one of two things:

* build a request with this library and compare it, character for character,
  with the request the real web app sent (or that was replayed by hand and
  accepted by the real server);
* feed the parser a reply the real server actually returned.

The ``Transcript`` transport only plays back recorded replies in order and
records what the library sent. It contains no logic of its own, so it cannot
make a wrong request look right. What these tests cannot show is whether
Google still behaves this way today: that is what ``tests/live`` is for.

Identifiers and the account email were replaced with placeholders of the same
length. The edit page and the Classroom boot page are rebuilt around the
recorded values, because the full HTML was not kept.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs

import httpx
import pytest

from google_classroom_api.web import ClassroomWeb, FormsWeb, WebProtocolError, WebSession
from google_classroom_api.web import classroom as cw
from google_classroom_api.web import forms as fw

COURSE = 111111111111
ITEM = 222222222222
SID = "7ad449cc88fbf28b"

# --- recorded: Forms ----------------------------------------------------------------

# The form model as the real page embedded it (public copy, revision 43), with three
# settings put back to the values they had at the moment the commands below were recorded.
MODEL = json.loads(
    '["stale revision write",[[823638774,"1. Which fraction equals one half?",null,2,[[2136159205,'
    '[["2/4",null,null,null,0],["2/3",null,null,null,0],["3/4",null,null,null,0]],1,null,null,null,null,null,0]],'
    'null,null,null,null,null,null,[null,"1. Which fraction equals one half?"]],[1227686324,'
    '"2. Explain why in one sentence.",null,1,[[1211596529,null,0]],null,null,null,null,null,null,'
    '[null,"2. Explain why in one sentence."]],[2010201394,"无标题小节",null,8,null,null,null,null,null,null,null,'
    '[null,"无标题小节"]]],["我们已收到您的回答",1,0,0,0],null,null,null,null,null,"CFB test form",76,'
    '[0,0,0,2,null,null,1],null,null,null,null,[2],[[1,1,0,0,0],1,1,null,0],null,null,null,null,null,null,null,'
    '[null,"stale revision write"],[null,"CFB test form"]]'
)

SAVE_OK = (
    ")]}'\n{\"revisionRanges\":[[44,44]],\"metadata\":{\"needsTransformOnClient\":false,\"serverRevision\":43},"
    "\"ackMessages\":[{\"c\":[[[1],1795368754091,\"08180897125784649421\",44,\"7ad449cc88fbf28b\",0,null,null,false]],"
    "\"mv\":0,\"fv\":0,\"t\":\"redacted\"}]}"
)
SAVE_UNKNOWN_SESSION = ")]}'\n\n[[\"er\",null,null,null,null,550,null,null,null,13],[\"di\",46]]"
PUBLISH_OK = ")]}'\n{\"status\":0}"
RESPONSE_LIST = ")]}'\n{\"status\":0,\"prsj\":\"[[[\\\"RESPONSE-ID-1\\\",\\\"avery@example.com\\\",1795369160660]]]\"}"
SINGLE_RESPONSE = (
    ")]}'\n{\"status\":0,\"prsj\":\"[[1795369160660,null,\\\"avery@example.com\\\",null,"
    "[[null,2136159205,[\\\"2/3\\\"],0],[null,1211596529,[\\\"Because it looks like half.\\\"],0]],1,"
    "\\\"-5538906228985907027\\\",null,[[2136159205,2,0,0],[1211596529,0]],[null,2,0],null,null,null,null,null,"
    "null,null,null,0,null,1795369160660,1795369160660,null,null,null,null,null,null,[1795369160,660999000]]]\"}"
)


# Question 1 as it appears in the editor's full copy of the model, with its answer key.
FULL_QUESTION_1 = json.loads(
    '[2136159205,[["2/4",null,null,null,0],["2/3",null,null,null,0],["3/4",null,null,null,0]],1,'
    'null,null,null,null,null,0,[2,[["2/4"]],["Not quite.Correct."],["Not quite."]]]'
)


def edit_page(model=MODEL, revision=43) -> str:
    # The real page carries the model twice: the respondent's copy at [0][1] (no answer
    # keys) and the editor's full copy at [14]. Index 13 is the form id.
    full = json.loads(json.dumps(model))
    full[1][0][4][0] = FULL_QUESTION_1
    data = [[None, model, "/forms", "未命名的表单", None, None, None, "0", None, 0, 0, None, "", 0,
             "e/1FAIpQLSexampleResponderId", 1, "[]", 1, 0, 1],
            revision, 0, SID, "teacher@example.com", 0, "[]", None, "", None, None, "", None, "FORM", full]
    flags = {"docs-ails": "docs_cold", "info_params": {"token": "TOKEN", "ouid": "OUID"}, "docs-eytpgcv": 0}
    return (
        "<script>_docs_flag_initialData=" + json.dumps(flags, separators=(",", ":")) + ";</script>"
        "<script>var FB_LOAD_DATA_ = " + json.dumps(data, ensure_ascii=False) + ";var x='a\\x3d\\x3d';</script>"
    )


class Transcript:
    """Plays recorded replies back in order and records the requests. No logic."""

    def __init__(self, *replies) -> None:
        self.replies = list(replies)
        self.requests: list[httpx.Request] = []

    def session(self) -> WebSession:
        return WebSession(client=httpx.Client(transport=httpx.MockTransport(self._play), follow_redirects=True))

    def _play(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        reply = self.replies.pop(0)
        status, text = reply if isinstance(reply, tuple) else (200, reply)
        return httpx.Response(status, text=text)

    def form(self, index: int) -> dict:
        return {k: v[0] for k, v in parse_qs(self.requests[index].content.decode(), keep_blank_values=True).items()}


def sent_command(transcript: Transcript, index: int = 1) -> tuple[int, str]:
    """The command id and payload text of the save request at ``index``."""
    request = transcript.requests[index]
    assert request.method == "POST" and request.url.path == "/forms/u/0/d/FORM/save"
    assert request.headers["X-Same-Domain"] == "1"
    query = dict(request.url.params)
    assert query == {"id": "FORM", "sid": SID, "vc": "0", "c": "0", "w": "1", "flr": "0",
                     "token": "TOKEN", "ouid": "OUID"}
    form = transcript.form(index)
    assert form["rev"] == "43"
    (bundle,) = json.loads(form["bundles"])
    assert bundle["sid"] == SID and bundle["reqId"] == 0
    ((command_id, repeated, payload),) = bundle["commands"]
    assert command_id == repeated
    return command_id, payload


def open_editor(*replies):
    transcript = Transcript(edit_page(), *replies)
    return transcript, FormsWeb(transcript.session()).open("FORM")


def test_editor_reads_the_recorded_page():
    _, editor = open_editor()
    assert (editor.sid, editor.revision) == (SID, 43)
    assert editor.title == "CFB test form"
    assert [item[0] for item in editor.items] == [823638774, 1227686324, 2010201394]
    assert editor.responder_url == "https://docs.google.com/forms/d/e/1FAIpQLSexampleResponderId/viewform"
    assert editor.settings()["email_collection"] == fw.EMAIL_DO_NOT_COLLECT


def test_questions_come_from_the_full_copy_and_include_the_answer_key():
    _, editor = open_editor()
    first, second = editor.questions()
    assert first == {
        "index": 0, "item_id": 823638774, "question_id": 2136159205, "kind": "single",
        "title": "1. Which fraction equals one half?", "description": "", "options": ["2/4", "2/3", "3/4"],
        "required": True, "points": 2, "correct": ["2/4"], "general_feedback": "",
    }
    assert (second["kind"], second["question_id"], second["correct"]) == ("long", 1211596529, [])


# Each row: what the library is asked to do, and the command the real editor sent for the same action.
SETTING_CASES = [
    (lambda e: e.set_quiz(release_grades_immediately=False),
     112282607, "[[[true,true,false,false,false],false,true,null,false]]"),
    (lambda e: e.set_after_submit(allow_response_edits=True),
     28132780, '[["我们已收到您的回答",true,false,true,false]]'),
    (lambda e: e.set_after_submit(show_link_to_respond_again=False),
     28132780, '[["我们已收到您的回答",false,false,false,false]]'),
    (lambda e: e.set_responder(email_collection=fw.EMAIL_VERIFIED),
     47603585, "[[false,false,false,2,null,null,2]]"),
    (lambda e: e.set_responder(progress_bar=True, shuffle_questions=True),
     47603585, "[[true,false,true,2,null,null,1]]"),
    (lambda e: e.set_title("CFB test form"), 32427935, '["",null,"CFB test form"]'),
    (lambda e: e.set_description("Test description"), 25101318, '["",null,"Test description"]'),
    (lambda e: e.delete_item(2), 28511809, "[2]"),
    (lambda e: e.move_item(1, 0), 28252204, "[1,0]"),
    (lambda e: e.replace_question(
        0, 823638774, 2136159205, "single", "1. Which fraction equals one half?",
        options=["2/4", "2/3", "3/4"], required=True, correct=["2/4"], points=2,
        feedback_right="Not quite.Correct.", feedback_wrong="Not quite."),
     24479122,
     '[0,[823638774,null,null,2,[[2136159205,[["2/4",null,null,null,false],["2/3",null,null,null,false],'
     '["3/4",null,null,null,false]],true,null,null,null,null,null,false,[2,[["2/4"]],["Not quite.Correct."],'
     '["Not quite."]]]],null,null,null,null,null,null,null,null,"1. Which fraction equals one half?"]]'),
]


@pytest.mark.parametrize("action, command_id, payload", SETTING_CASES)
def test_commands_equal_the_ones_the_real_editor_sent(action, command_id, payload):
    transcript, editor = open_editor(SAVE_OK)
    action(editor)
    assert sent_command(transcript) == (command_id, payload)
    assert editor.revision == 44, "revision comes from the server's recorded reply"


def test_added_items_equal_the_recorded_add_commands(monkeypatch):
    ids = iter([1042924315, 437544451, 2010201394])
    monkeypatch.setattr(fw, "new_id", lambda: next(ids))
    transcript, editor = open_editor(SAVE_OK)
    editor.add_question("long", "2. Explain why in one sentence.", index=2)
    assert sent_command(transcript) == (
        25290104,
        '[2,[1042924315,null,null,1,[[437544451,null,false]],null,null,null,null,null,null,null,null,'
        '"2. Explain why in one sentence."]]',
    )
    transcript, editor = open_editor(SAVE_OK)
    editor.add_section("无标题小节", index=2)
    assert sent_command(transcript) == (
        25290104, '[2,[2010201394,null,null,8,null,null,null,null,null,null,null,null,null,"无标题小节"]]')


def test_publish_request_and_recorded_reply():
    transcript, editor = open_editor(PUBLISH_OK)
    editor.publish()
    request = transcript.requests[1]
    assert request.url.path == "/forms/u/0/d/FORM/publish"
    assert transcript.form(1) == {"request": "[0]"}


def test_recorded_responses_parse_into_answers_by_question_id():
    transcript, editor = open_editor(RESPONSE_LIST, SINGLE_RESPONSE)
    assert editor.list_responses() == [
        {"response_id": "RESPONSE-ID-1", "email": "avery@example.com", "submitted_ms": 1795369160660}]
    response = editor.get_response("RESPONSE-ID-1")
    assert transcript.form(2) == {"viewresponse": "RESPONSE-ID-1"}
    assert response["answers"] == {"2136159205": ["2/3"], "1211596529": ["Because it looks like half."]}
    assert response["scores"]["2136159205"] == {"max_points": 2, "earned_points": 0}
    assert response["scores"]["1211596529"] == {"max_points": 0, "earned_points": None}
    assert response["total"] == {"max_points": 2, "earned_points": 0}
    assert response["email"] == "avery@example.com"


def test_the_recorded_rejection_of_an_unknown_session_is_an_error():
    transcript, editor = open_editor((550, SAVE_UNKNOWN_SESSION))
    with pytest.raises(WebProtocolError, match="550"):
        editor.set_title("x")


# The next payloads were sent to the real server by hand and read back unchanged.


def test_each_correct_answer_is_its_own_list():
    # Sending [["4","four"]] was accepted too, but the server kept only "4".
    assert fw.question_payload(1500000032, "short", correct=["4", "four"], points=2) == [
        1500000032, None, False, None, None, None, None, None, False, [2, [["4"], ["four"]]]]
    checkbox = fw.question_payload(1500000022, "multiple", options=["a", "b", "c"], correct=["a", "b"], points=1)
    assert checkbox[9] == [1, [["a"], ["b"]]]


def test_text_questions_carry_points_and_general_feedback():
    assert fw.question_payload(1211596529, "long", points=3, general_feedback="Sample answer.") == [
        1211596529, None, False, None, None, None, None, None, False, [3, None, None, None, ["Sample answer."]]]
    # The server answered 400 when the feedback of a paragraph question sat in the right/wrong slots.
    with pytest.raises(ValueError):
        fw.question_payload(1211596529, "long", feedback_right="x")


def test_item_description_travels_in_slot_two():
    question = fw.question_payload(1211596529, "long")
    assert fw.question_item(1227686324, "long", "2. Explain why in one sentence.", question,
                            "Write a full sentence.") == [
        1227686324, None, "Write a full sentence.", 1, [[1211596529, None, False]],
        None, None, None, None, None, None, None, None, "2. Explain why in one sentence."]


def test_closing_and_reopening_the_form_to_responses():
    transcript, editor = open_editor(PUBLISH_OK, PUBLISH_OK)
    editor.set_accepting_responses(False)
    editor.set_accepting_responses(True)
    assert [r.url.path for r in transcript.requests[1:]] == ["/forms/u/0/d/FORM/setacceptingresponses"] * 2
    assert (transcript.form(1), transcript.form(2)) == ({"request": "[0]"}, {"request": "[1]"})


# A brand-new form as the real create page embedded it: one placeholder question, no settings yet.
NEW_FORM_MODEL = json.loads(
    '[null,[[823638774,"未命名的问题",null,2,[[2136159205,[["选项 1"]],0,null,null,null,null,null,0]],null,null,'
    'null,null,null,null,[null,"未命名的问题"]]],null,null,null,null,null,null,null,76,[null,null,null,2,null,null,1],'
    'null,null,null,null,[2]]'
)
NEW_FORM_ID = "1NEWformId0000000000000000000000000000000000"


def test_create_makes_a_form_titles_it_and_removes_the_placeholder_question():
    data = [[None, NEW_FORM_MODEL, "/forms", "未命名的表单", None, None, None, "0", None, 0, 0, None, "", 0,
             "e/1FAIpQLSnewResponderId", 1, "[]", 1, 0, 1],
            1, 0, SID, "teacher@example.com", 0, "[]", None, "", None, None, "", None, NEW_FORM_ID, NEW_FORM_MODEL]
    page = ('<script>_docs_flag_initialData={"info_params":{"token":"TOKEN","ouid":"OUID"}};</script>'
            "<script>var FB_LOAD_DATA_ = " + json.dumps(data, ensure_ascii=False) + ";</script>")
    transcript = Transcript(page, SAVE_OK, SAVE_OK)
    editor = FormsWeb(transcript.session()).create("Lesson 12")
    assert editor.form_id == NEW_FORM_ID
    assert [(r.method, r.url.path) for r in transcript.requests] == [
        ("GET", "/forms/u/0/create"),
        ("POST", f"/forms/u/0/d/{NEW_FORM_ID}/save"),
        ("POST", f"/forms/u/0/d/{NEW_FORM_ID}/save"),
    ]
    commands = [json.loads(transcript.form(i)["bundles"])[0]["commands"][0] for i in (1, 2)]
    assert commands == [[32427935, 32427935, '["",null,"Lesson 12"]'], [28511809, 28511809, "[0]"]]
    assert transcript.form(1)["rev"] == "1"


# --- recorded: Classroom ----------------------------------------------------------------

BOOT = ('<script>window.WIZ_global_data = {"FdrFJe":"-1234567890123456789","SNlM0e":"AT","cfb2h":"BUILD",'
        '"eptZe":"/_/ClassroomUi/"};</script>')


def reply(rpc: str, result_json: str | None) -> str:
    inner = json.dumps(result_json) if result_json is not None else "null"
    tail = "null,null,null" if result_json is not None else "null,null,[3]"
    line = f'[["wrb.fr","{rpc}",{inner},{tail},"generic"],["di",45],["af.httprm",45,"3455658636775921217",52]]'
    return f")]}}'\n\n{len(line)}\n{line}\n25\n[[\"e\",4,null,null,255]]\n"


TOPICS = '["hrt.qr",null,[[["900816347160",["111111111111"]],"G6 Maths Fractions",1795368958080,"gA\\u003d\\u003d"]]]'
TOPIC_CREATED = '["hrt.ur",[["900816347160",["111111111111"]],"G6 Maths Fractions",1795368958080,"gA\\u003d\\u003d"]]'
# First ten slots of the recorded stream item; the rest of the reply was cut.
DRAFT_CREATED = ('["hrsi.ur",[[[0],[2,[[["900816021533",["111111111111"]],1795368994903,1795368994903,1795368994902,'
                 '["333333333333"],"CFB test assignment",null,null,2,[3]]]]]]]')
LINK_IS_A_FORM = ('["hrq.pau",["CFB test form",null,"DRIVE-ID",7,null,"https://lh6.googleusercontent.com/thumb",'
                  '"https://docs.google.com/forms/d/e/1FAIpQLSexample/viewform",null,null,null,null,null,null,'
                  '[["CFB test form","https://lh6.googleusercontent.com/thumb",'
                  '"https://docs.google.com/forms/d/DRIVE-ID/edit"],null,3,1,[null,false]]]]')
ATTACHED = ('[["CFB test form",null,"DRIVE-ID",7,null,"thumb","url",null,null,null,2,null,null,'
            '[["CFB test form","thumb","edit"],null,3,1,[null,false]],null,1,null,"900815764904",null,null,1],'
            'null,[["222222222222",["111111111111"]],"900815764904"]]')
ATTACH_REQUEST = (
    '[[[null,null,"DRIVE-ID",7,"application/vnd.google-apps.freebird",null,null,null,null,null,null,null,null,'
    '[[],null,null,null,[null,0]],null,null,null,null,null,null,1],null,[[222222222222,[111111111111]]]],'
    '[[null,1,1,1,1,1,null,1,1,1,1,1,1,null,[[1,1,1,1,1],1,1,1,1,1,1],[[1,1,1,1,1],1,1,1,[1,1],1],1,null,1,'
    '[[1,1,1,1,1],1,1],1,[[1,1,1,1,1],1]]]]'
)


def rpc_arguments(transcript: Transcript, index: int, rpc: str) -> str:
    request = transcript.requests[index]
    assert request.url.path == "/_/ClassroomUi/data/batchexecute"
    assert request.headers["X-Same-Domain"] == "1"
    query = dict(request.url.params)
    assert query["rpcids"] == rpc and query["f.sid"] == "-1234567890123456789" and query["bl"] == "BUILD"
    assert (query["soc-app"], query["soc-platform"], query["soc-device"], query["rt"]) == ("1", "1", "1", "c")
    form = transcript.form(index)
    assert form["at"] == "AT"
    (((name, arguments, nothing, kind),),) = json.loads(form["f.req"])
    assert (name, nothing, kind) == (rpc, None, "generic")
    return arguments


def classroom(*replies):
    transcript = Transcript(BOOT, *replies)
    return transcript, ClassroomWeb(transcript.session())


def test_list_topics_request_and_recorded_reply():
    transcript, web = classroom(reply("ZZFlc", TOPICS))
    assert web.list_topics(COURSE) == [
        {"topic_id": "900816347160", "name": "G6 Maths Fractions", "updated_ms": 1795368958080}]
    assert rpc_arguments(transcript, 1, "ZZFlc") == "[[100,null,1,0],[1,1,1],[[[[111111111111]]]]]"


def test_create_topic_request_and_recorded_reply():
    transcript, web = classroom(reply("lkA1wb", TOPIC_CREATED))
    assert web.create_topic(COURSE, "G6 Maths Fractions")["topic_id"] == "900816347160"
    assert rpc_arguments(transcript, 1, "lkA1wb") == '[[[null,[111111111111]],"G6 Maths Fractions"],[1,1,1]]'


def test_create_assignment_request_has_the_recorded_length_and_shape():
    item = cw.stream_item(COURSE, "CFB draft two", client_uuid="A3EFFF89-A3EF-430C-B9CF-5998A43C2F47")
    text = json.dumps(cw.create_arguments(item), ensure_ascii=False, separators=(",", ":"))
    assert len(text) == 1038, "the real request was 1038 characters long"
    assert text.startswith('[[[2,[[[null,[111111111111]],null,null,null,null,"CFB draft two",null,null,null,[3],'
                           'null,null,null,null,null,1,null,null,null,null,null,null,null,null,null,[0],null,'
                           '[[null,"A3EFFF89-A3EF-430C-B9CF-5998A43C2F47"]]],[null,null,0,100,0,1,[1],null,null,'
                           '[null,0],null,0],null,null,[2,2]]]],[[[1,1,1,1,1,null,null,[1,1,1,null,1,1,1],')
    assert text.endswith("1,[1],1,[null,null,1,1,1,null,1]]]],null,[1,1,1],null,null,null,null,null,[1,1,null,1]]")


def test_publish_assignment_request_has_the_recorded_length_and_shape():
    item = cw.stream_item(COURSE, "CFB capture three", item_id=ITEM, instructions="Hello.",
                          state=cw.STATE_PUBLISHED, client_uuid="8D65FE35-E049-4B2B-B9FE-6FCD5B5DC541")
    text = json.dumps(cw.update_arguments(item), ensure_ascii=False, separators=(",", ":"))
    assert len(text) == 1587, "the real request was 1587 characters long"
    assert text.startswith('[[[2,[[[222222222222,[111111111111]],null,null,null,null,"CFB capture three",null,null,'
                           'null,[2],null,null,null,null,null,1,null,null,null,null,null,null,null,null,null,[0],'
                           'null,[[null,"8D65FE35-E049-4B2B-B9FE-6FCD5B5DC541",null,["edu.rt","Hello."],null,null,'
                           '["edu.rt","Hello.",null,null,[null,"Hello."]]]]],[null,null,0,100,0,1,[1],null,null,'
                           '[null,0],null,0],null,null,[2,2]]]],[[[1,null,null,[1,null,1,1],null,1,4,1,null,null,'
                           'null,null,null,1,4,[null,1,1,1,1]],[1,1,1,1,1,[1],[1],1,null,null,null,null,1],null,'
                           '[1,1]],')
    assert text.endswith("]]]],null,[1,1,1],null,null,null,null,null,null,[1,1,null,1]]")


def test_create_assignment_parses_the_recorded_reply():
    transcript, web = classroom(reply("n5NjMc", DRAFT_CREATED))
    draft = web.create_assignment(COURSE, "CFB test assignment", instructions="Complete the form.")
    assert draft == {"item_id": "900816021533", "course_id": "111111111111",
                     "title": "CFB test assignment", "state": cw.STATE_DRAFT}
    sent = json.loads(rpc_arguments(transcript, 1, "n5NjMc"))
    assert len(sent) == 10 and sent[1] == cw.READ_MASKS
    body = sent[0][0][1][0][27][0]
    assert body[3] == ["edu.rt", "Complete the form."]
    assert body[6] == ["edu.rt", "Complete the form.", None, None, [None, "Complete the form."]]


def test_attaching_a_form_sends_the_recorded_request():
    transcript, web = classroom(reply("u1xGoc", LINK_IS_A_FORM), reply("n5QGnd", ATTACHED))
    attachment = web.attach_form(COURSE, ITEM, "https://docs.google.com/forms/d/DRIVE-ID/viewform")
    assert attachment == "900815764904"
    assert rpc_arguments(transcript, 1, "u1xGoc") == '["https://docs.google.com/forms/d/DRIVE-ID/viewform"]'
    assert rpc_arguments(transcript, 2, "n5QGnd") == ATTACH_REQUEST


# --- recorded: topic, due date, class settings ---------------------------------------


def test_topic_and_due_date_sit_where_the_real_editor_put_them():
    item = cw.stream_item(COURSE, "CFB due and topic", item_id=ITEM, topic_id="900816347160")
    assert json.dumps(item[14], separators=(",", ":")) == '[["900816347160",[111111111111]]]'
    # Recorded coursework blocks: a due date with no time, then the same day at 09:30 (UTC-5).
    date_only = cw.assignment_wrapper(item, due_ms=1798779540000, due_has_time=0)[0][1][1]
    with_time = cw.assignment_wrapper(item, due_ms=1798727400000, due_has_time=1)[0][1][1]
    assert json.dumps(date_only, separators=(",", ":")) == "[1798779540000,null,0,100,0,1,[1],null,null,[null,0],null,0]"
    assert json.dumps(with_time, separators=(",", ":")) == "[1798727400000,null,1,100,0,1,[1],null,null,[null,0],null,0]"


def test_due_fields_reproduce_the_recorded_timestamps():
    eastern = timezone(timedelta(hours=-5))
    assert cw.due_fields(datetime(2026, 12, 31, 23, 59, tzinfo=eastern), has_time=False) == (1798779540000, 0)
    assert cw.due_fields(datetime(2026, 12, 31, 9, 30, tzinfo=eastern)) == (1798727400000, 1)
    assert cw.due_fields(None) == (None, 0)


# The course record as the real settings page sent it (class code and account id replaced).
COURSE_REQUEST_OBJECT = json.loads(
    '[[111111111111],null,null,null,null,"CFB test class",null,null,"G6-G8",[null,null,null,null,2,null,'
    '"//ssl.gstatic.com/classroom/favicon_blue.png",[null,["#129eaf","#e4f7fB","#007b83"],0,null,null,'
    '"https://gstatic.com/classroom/themes/img_backtoschool.jpg",null,null,'
    '"https://gstatic.com/classroom/themes/gm3/img_backtoschool.png",null,1],null,[2],null,null,1,1,[0],[null,0]],'
    '"CLASSCODE",null,null,null,null,null,null,null,null,[0,null,null,null,null,0,null,1,1,0],1,null,null,null,null,'
    '[null,null,"GAIA"],null,null,null,null,null,0,null,null,null,null,null,null,1,[null,null,null,null,0,null,null,'
    'null,0,null,null,null,null,null,null,null,null,null,0,0,0,null,0,0],null,[5,0,0],null,null,null,1,'
    '[[null,0],[[]],[1,0.6]],null,null,null,[1,2],null,null,1]'
)


def test_class_settings_save_has_the_recorded_length_and_masks():
    arguments = [[3], [[[COURSE], COURSE_REQUEST_OBJECT, cw.COURSE_UPDATE_MASK]], cw.COURSE_READ_MASK]
    text = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
    assert len(text) == 1353, "the real request, with the same placeholders, was 1353 characters long"
    assert len(COURSE_REQUEST_OBJECT) == 54 and len(cw.COURSE_READ_MASK) == 57
    assert COURSE_REQUEST_OBJECT[cw.COURSE_GRADING_SLOT] == [[None, 0], [[]], [1, 0.6]]


def course_reply(missing_work: list) -> str:
    # Reduced course record: the real one has 54 slots; only the ones the library reads are filled.
    course: list = [None] * 54
    course[0], course[5], course[8] = ["111111111111"], "CFB test class", "G6-G8"
    course[cw.COURSE_GRADING_SLOT] = [[None, 0], None, missing_work]
    return json.dumps(["hrq.crs", None, [course]])


def test_missing_work_grade_is_read_changed_and_written_back():
    transcript, web = classroom(
        reply("gXtzob", course_reply([True, 0.6])),
        reply("lNyLS", '["hrw.crs",[]]'),
        reply("gXtzob", course_reply([True, 0.45])),
    )
    assert web.set_missing_work_grade(COURSE, enabled=True, percent=45) == {"enabled": True, "percent": 45.0}
    assert json.loads(rpc_arguments(transcript, 1, "gXtzob")) == [
        [None, None, 2, 0], cw.COURSE_READ_MASK, [None, [[COURSE]], None, None, None, None, [1, 2]]]
    head, ((course_ref, sent, mask),), read_mask = json.loads(rpc_arguments(transcript, 2, "lNyLS"))
    assert (head, course_ref, mask, read_mask) == ([3], [COURSE], cw.COURSE_UPDATE_MASK, cw.COURSE_READ_MASK)
    assert sent[cw.COURSE_GRADING_SLOT] == [[None, 0], None, [1, 0.45]]
    assert (sent[5], sent[8]) == ("CFB test class", "G6-G8"), "the rest of the record goes back untouched"


def test_missing_work_grade_can_be_switched_off_keeping_the_percentage():
    transcript, web = classroom(
        reply("gXtzob", course_reply([True, 0.45])),
        reply("lNyLS", '["hrw.crs",[]]'),
        reply("gXtzob", course_reply([False, 0.45])),
    )
    assert web.set_missing_work_grade(COURSE, enabled=False) == {"enabled": False, "percent": 45.0}
    sent = json.loads(rpc_arguments(transcript, 2, "lNyLS"))[1][0][1]
    assert sent[cw.COURSE_GRADING_SLOT][2] == [0, 0.45]


def test_the_recorded_rejection_without_field_masks_is_an_error():
    transcript, web = classroom(reply("n5NjMc", None))
    with pytest.raises(WebProtocolError, match="n5NjMc"):
        web.call("n5NjMc", [cw.assignment_wrapper(cw.stream_item(COURSE, "No masks"))])


# --- recorded 2026-11-22 (second capture): roster, listing, deleting, submissions -------
#
# Requests were captured from the Classroom web app (People page, gradebook,
# Classwork "delete" menu) and replayed with exported cookies; replies are the
# ones the real server returned to those replays. The student's Google id was
# replaced with a placeholder of the same length, and the student's name and
# email with fictional ones.

STUDENT = 444444444444
USERS = ('["hrq.usr",[],[[["444444444444"],"Avery Example","avery@example.com",null,'
         '"//lh3.googleusercontent.com/a/PHOTO=mo",null,null,null,null,"Avery Example avery@example.com",1,'
         '"Avery Example avery@example.com","Example Avery avery@example.com",null,null,0]]]')
SUBMISSIONS_EMPTY = '["hrq.sub",[false]]'
SECTION_EMPTY = '["hrcw.qr",[false]]'
# The envelope, item id, title and state are from the real "G6 Maths Fractions" listing; the
# timestamps and creator id are the placeholders used above. The library reads only slots 0, 5, 9.
SECTION_ONE_DRAFT = ('["hrcw.qr",[false],[[2,[[["900816770865",["111111111111"]],1795368994903,1795368994903,'
                     '1795368994902,["333333333333"],"Library draft with topic and due",null,null,2,[3]]]]]]')
RECORDED_DELETE_MASKS = ('[[[null,null,null,null,null,1],[]],[[null,null,null,null,null,1]],'
                         '[[null,null,null,null,null,1],[]],null,null,[[null,null,null,null,null,1]]]')


def course_with_students(*ids: int) -> str:
    course: list = [None] * 54
    course[0], course[5], course[8] = ["111111111111"], "CFB test class", "G6-G8"
    course[cw.COURSE_STUDENTS_SLOT] = [[str(i)] for i in ids]
    return json.dumps(["hrq.crs", None, [course]])


def test_roster_is_the_course_record_student_slot_plus_user_profiles():
    transcript, web = classroom(reply("gXtzob", course_with_students(STUDENT)), reply("UG41I", USERS))
    assert web.list_students(COURSE) == [{"user_id": "444444444444", "name": "Avery Example",
                                          "email": "avery@example.com",
                                          "photo_url": "//lh3.googleusercontent.com/a/PHOTO=mo"}]
    assert rpc_arguments(transcript, 2, "UG41I") == (
        "[[null,null,1,0],[1,1,null,1,null,1,null,null,1,1,1,1,null,null,1],[[null,[[null,[444444444444]]]]]]")


def test_a_class_without_students_needs_no_profile_lookup():
    transcript, web = classroom(reply("gXtzob", course_with_students()))
    assert web.list_students(COURSE) == []
    assert len(transcript.requests) == 2, "only the boot page and the course read"


def test_course_info_reads_name_and_section():
    transcript, web = classroom(reply("gXtzob", course_with_students()))
    assert web.course_info(COURSE) == {"course_id": "111111111111", "name": "CFB test class", "section": "G6-G8"}


def test_submissions_request_and_the_recorded_empty_reply():
    transcript, web = classroom(reply("Zj93ge", SUBMISSIONS_EMPTY))
    assert web.list_submissions_raw(COURSE, ITEM) == []
    assert rpc_arguments(transcript, 1, "Zj93ge") == (
        "[[100,null,1,0],[null,1,null,null,1,null,1,1,1,null,null,null,1,null,[1],null,null,null,1,1,1,"
        "null,null,null,[[1,1]],[[1,1]],null,null,null,null,null,1],[null,[[222222222222,[111111111111]]]],0]")


def test_delete_sends_the_recorded_masks_and_lifecycle_value():
    arguments = cw.delete_arguments(COURSE, ITEM)
    assert json.dumps(arguments[1], separators=(",", ":")) == RECORDED_DELETE_MASKS
    item = arguments[0][0][1][0]
    assert item[0] == [ITEM, [COURSE]] and item[8] == cw.ITEM_DELETED == 3
    assert arguments[2] == cw.READ_MASKS


def test_assignments_are_listed_one_classwork_section_at_a_time():
    transcript, web = classroom(reply("ZZFlc", TOPICS), reply("dpT4Vd", SECTION_EMPTY),
                                reply("dpT4Vd", SECTION_ONE_DRAFT))
    # The recorded item was cut after slot 9, so points / due / attachments are not in it.
    assert web.list_assignments(COURSE) == [{"item_id": "900816770865", "kind": "assignment", "state": 3,
                                             "topic_id": "900816347160", "text": "",
                                             "title": "Library draft with topic and due",
                                             "points": None, "due": None, "due_has_time": False,
                                             "attachment_ids": []}]
    no_topic = json.loads(rpc_arguments(transcript, 2, "dpT4Vd"))
    in_topic = json.loads(rpc_arguments(transcript, 3, "dpT4Vd"))
    # The web app sent [10,null,2,0]; 100 was replayed and accepted, and avoids paging.
    assert no_topic[0] == in_topic[0] == [100, None, 2, 0]
    assert no_topic[1:] == [[COURSE], None, 1, 2, cw.READ_MASKS]
    assert in_topic[1:] == [[COURSE], ["900816347160", [COURSE]], 0, 2, cw.READ_MASKS]


def test_course_id_comes_from_the_base64_segment_of_a_classroom_link():
    # The three URL shapes were seen in the real app; the course token is a placeholder.
    for link in ("https://classroom.google.com/u/0/c/MTIzNDU2Nzg5MDEy",
                 "https://classroom.google.com/u/0/w/MTIzNDU2Nzg5MDEy/t/all",
                 "https://classroom.google.com/u/0/r/MTIzNDU2Nzg5MDEy/sort-last-name"):
        assert cw.course_id_from_url(link) == 123456789012
    with pytest.raises(ValueError):
        cw.course_id_from_url("https://classroom.google.com/u/0/h")


# --- recorded 2026-11-22 (third capture): materials, announcements, links, grades, course list ---
#
# Each request below was captured from the Classroom web app (composer, gradebook
# cell, home page start-up). The library's builders are compared with the captured
# text character for character; ids are placeholders of the same length.

ATTACHMENT = 555555555555


def envelope(rpc: str, arguments: list) -> str:
    return cw._dump([[[rpc, cw._dump(arguments), None, "generic"]]])


def test_draft_grade_equals_the_request_the_gradebook_cell_sent():
    assert envelope("CVt8yf", cw.draft_grade_arguments(COURSE, ITEM, STUDENT, 7)) == (
        '[[["CVt8yf","[[3],[[[444444444444,[222222222222,[111111111111]]],[[444444444444,[222222222222,[111111111111]]],'
        'null,null,null,null,null,null,null,null,7],[null,null,null,1]]],[null,1,null,1,1,null,1,1,1,null,null,null,1,null,'
        '[1],null,null,null,1,1,1,null,null,null,[[1,1]],[[1,1]],null,null,null,null,null,1]]",null,"generic"]]]')


def test_link_attachment_equals_the_request_the_composer_sent():
    arguments = [[cw.link_descriptor("https://example.com/"), None, [[ITEM, [COURSE]]]], [cw._ATTACHMENT_MASK]]
    assert envelope("n5QGnd", arguments) == (
        '[[["n5QGnd","[[[null,null,\\"https://example.com/\\",4,null,null,null,null,null,null,null,null,null,null,null,'
        'null,null,null,null,null,1],null,[[222222222222,[111111111111]]]],[[null,1,1,1,1,1,null,1,1,1,1,1,1,null,'
        '[[1,1,1,1,1],1,1,1,1,1,1],[[1,1,1,1,1],1,1,1,[1,1],1],1,null,1,[[1,1,1,1,1],1,1],1,[[1,1,1,1,1],1]]]]",'
        'null,"generic"]]]')


def test_course_list_equals_the_request_the_home_page_sent_at_start_up():
    text = envelope("gXtzob", cw.course_list_arguments())
    assert len(text) == 512, "the real request was 512 characters long"
    assert text.startswith('[[["gXtzob","[[100,null,1,0],[1,1,null,null,1,1,null,1,[null,1,null,1,null,1,')
    assert text.endswith(',[[null,[[1]]],null,null,[1,2,3],null,null,[1,2]]]",null,"generic"]]]')


def test_material_draft_has_the_recorded_envelope_and_length():
    item = cw.stream_item(COURSE, "GCA material capture", client_uuid="0262B980-EFEA-4C30-B08B-C9ABCB171136")
    text = envelope("n5NjMc", cw.create_item_arguments(cw.KIND_MATERIAL, item))
    assert len(text) == 1033, "the real request was 1033 characters long"
    assert text.startswith('[[["n5NjMc","[[[7,null,null,null,null,[[[null,[111111111111]],null,null,null,null,'
                           '\\"GCA material capture\\",null,null,null,[3],')
    assert text.endswith(']]]],null,[1,1,1],null,null,null,null,null,[1,1,null,1]]",null,"generic"]]]')


def test_announcement_draft_has_the_recorded_envelope_and_length():
    item = cw.stream_item(COURSE, "", instructions="GCA announcement capture",
                          client_uuid="FBC8339C-D4D2-4C39-956A-998CFCB5277A")
    text = envelope("n5NjMc", cw.create_item_arguments(cw.KIND_ANNOUNCEMENT, item))
    assert len(text) == 1143, "the real request was 1143 characters long"
    assert text.startswith(
        '[[["n5NjMc","[[[3,null,[[[null,[111111111111]],null,null,null,null,\\"\\",null,null,null,[3],null,null,null,'
        'null,null,1,null,null,null,null,null,null,null,null,null,[0],null,[[null,'
        '\\"FBC8339C-D4D2-4C39-956A-998CFCB5277A\\",null,[\\"edu.rt\\",\\"GCA announcement capture\\"],null,null,'
        '[\\"edu.rt\\",\\"GCA announcement capture\\",null,null,[null,\\"GCA announcement capture\\"]]]]]]]],')


def test_material_update_with_a_link_has_the_recorded_envelope_and_length():
    item = cw.stream_item(COURSE, "GCA material capture", item_id=ITEM, attachment_ids=[ATTACHMENT],
                          client_uuid="0262B980-EFEA-4C30-B08B-C9ABCB171136")
    text = envelope("F7Tqub", cw.update_item_arguments(cw.KIND_MATERIAL, item))
    assert len(text) == 1561, "the real request was 1561 characters long"
    assert text.startswith(
        '[[["F7Tqub","[[[7,null,null,null,null,[[[222222222222,[111111111111]],null,null,null,null,'
        '\\"GCA material capture\\",null,null,null,[3],null,null,null,null,null,1,null,null,null,null,null,null,null,'
        'null,null,[0],null,[[null,\\"0262B980-EFEA-4C30-B08B-C9ABCB171136\\",null,null,'
        '[[null,[[222222222222,[111111111111]],555555555555]]]]]]]]],[[[1,null,null,[1,null,1,1],null,1,4,1,')


@pytest.mark.parametrize("scope, kinds, states, page, length", [
    ([2], [2, 5], [2], 10, 784),      # the gradebook's columns
    ([2, 3], None, [2], 10, 785),     # the Stream page's feed
    ([2], [3], [3], 100, 783),        # the announcement drafts drawer
])
def test_stream_queries_have_the_recorded_lengths(scope, kinds, states, page, length):
    text = envelope("pONvgf", cw.stream_query_arguments(COURSE, scope, kinds, states, page_size=page))
    assert len(text) == length


def test_listing_tells_assignments_materials_and_announcements_apart():
    # Envelope shapes as captured; items reduced to the slots the library reads.
    def item(item_id, title):
        stream = [None] * 28
        stream[0], stream[5], stream[9] = [str(item_id), [str(COURSE)]], title, [3]
        return stream
    assert cw.entry_kind_and_item([2, [item(1, "a"), [None]]])[0] == "assignment"
    assert cw.entry_kind_and_item([3, None, [item(2, "")]])[0] == "announcement"
    assert cw.entry_kind_and_item([7, None, None, None, None, [item(3, "m")]])[0] == "material"


# Replies the real server gave in the gradebook (student, item, course and teacher ids replaced).
SUBMISSIONS_ONE = (
    '["hrq.sub",[false],[[["444444444444",["222222222222",["111111111111"]]],null,1795391573092,null,null,4,null,'
    '["444444444444"],null,8,null,null,null,0,null,null,null,null,null,[[[4,1795390975171,["333333333333"],1,null,5],'
    'null,1],[null,[7,100,1795390975171,["333333333333"],null,1,5],2],[null,[8,100,1795391573086,["333333333333"],'
    'null,1,5],2]],1,null,null,null,null,null,null,null,null,null,null,null,1]]]')
GRADE_CLEARED = (
    '["hrw.sub",[[["444444444444",["222222222222",["111111111111"]]],null,1795392124035,null,null,4,null,'
    '["444444444444"],null,null,null,null,null,0,null,null,null,null,null,[[[4,1795390975171,["333333333333"],1,null,5],'
    'null,1],[null,[7,100,1795390975171,["333333333333"],null,1,5],2],[null,[8,100,1795391573086,["333333333333"],'
    'null,1,5],2],[null,[null,100,1795392124025,["333333333333"],null,1,1],2]],1,null,null,null,null,null,null,null,'
    'null,null,null,null,1]]]')


def test_a_recorded_submission_with_a_draft_grade_is_parsed():
    transcript, web = classroom(reply("Zj93ge", SUBMISSIONS_ONE))
    (record,) = web.list_submissions(COURSE, ITEM)
    assert (record["student_id"], record["item_id"]) == ("444444444444", "222222222222")
    assert record["draft_grade"] == 8 and record["state_code"] == 4 and record["updated_ms"] == 1795391573092
    assert [(g["grade"], g["max_points"]) for g in record["grade_history"]] == [(7, 100), (8, 100)]
    assert record["grade_history"][0]["by_user_id"] == "333333333333"


def test_clearing_a_draft_grade_equals_the_request_an_emptied_cell_sent():
    assert envelope("CVt8yf", cw.draft_grade_arguments(COURSE, ITEM, STUDENT, None)) == (
        '[[["CVt8yf","[[3],[[[444444444444,[222222222222,[111111111111]]],[[444444444444,[222222222222,[111111111111]]]],'
        '[null,null,null,1]]],[null,1,null,1,1,null,1,1,1,null,null,null,1,null,[1],null,null,null,1,1,1,null,null,null,'
        '[[1,1]],[[1,1]],null,null,null,null,null,1]]",null,"generic"]]]')


def test_the_recorded_reply_to_clearing_a_grade_is_parsed():
    transcript, web = classroom(reply("CVt8yf", GRADE_CLEARED))
    record = web.set_draft_grade(COURSE, ITEM, STUDENT, None)
    assert record["draft_grade"] is None
    assert [g["grade"] for g in record["grade_history"]] == [7, 8, None]


# Listing replies. The envelopes (tag, "more" flag, entry type, payload slot, where the text and
# the state sit) are as the real server returned them on the Stream page and the home page; the
# items are cut down to the slots the library reads, with placeholder ids.

def listed_item(item_id: int, title: str, state: list, text: str = "") -> list:
    stream: list = [None] * 28
    stream[0], stream[5], stream[9] = [str(item_id), [str(COURSE)]], title, state
    stream[27] = [[None, "UUID", None, None, None, None, ["edu.rt", text, None, None, [None, text]]]] if text else [[None, "UUID"]]
    return stream


def test_announcement_drafts_are_listed_from_the_recorded_reply_shape():
    draft = [3, None, [listed_item(ITEM, "", [3], "GCA announcement capture")]]
    transcript, web = classroom(reply("pONvgf", json.dumps(["hrsi.qr", [False], [draft]])))
    assert web.list_announcements(COURSE, drafts=True) == [
        {"item_id": str(ITEM), "kind": "announcement", "title": "", "state": 3, "topic_id": None,
         "text": "GCA announcement capture", "points": None, "due": None, "due_has_time": False,
         "attachment_ids": []}]
    sent = json.loads(rpc_arguments(transcript, 1, "pONvgf"))
    assert sent[0] == [100, None, 1, 0] and sent[1] == cw.READ_MASKS and sent[2] == [[[2], [[COURSE]], [3], [3]]]


def test_the_feed_lists_published_items_and_keeps_only_announcements_when_asked():
    posted = [2, [listed_item(ITEM, "CFB test assignment", [2, ["333333333333"], 1795369052717],
                              "Complete the form."), [None], None, None, [2, 2]]]
    note = [3, None, [listed_item(ATTACHMENT, "", [2, ["333333333333"], 1795369052717], "Hello class")]]
    body = json.dumps(["hrsi.qr", [False], [posted, note]])
    transcript, web = classroom(reply("pONvgf", body), reply("pONvgf", body))
    assert [(r["kind"], r["title"], r["state"]) for r in web.list_stream(COURSE)] == [
        ("assignment", "CFB test assignment", 2), ("announcement", "", 2)]
    assert [r["text"] for r in web.list_announcements(COURSE)] == ["Hello class"]
    # The Stream page sent [2, 3] here (live and deleted); replayed: [2] returns the live ones only.
    assert json.loads(rpc_arguments(transcript, 1, "pONvgf"))[2] == [[[2], [[COURSE]], None, [2]]]


def test_courses_are_listed_from_the_recorded_reply_shape():
    course: list = [None] * 54
    course[0], course[5], course[8] = ["111111111111"], "CFB test class", "G6-G8"
    transcript, web = classroom(reply("gXtzob", json.dumps(["hrq.crs", [False], [course]])))
    assert web.list_courses() == [{"course_id": "111111111111", "name": "CFB test class", "section": "G6-G8"}]
    assert json.loads(rpc_arguments(transcript, 1, "gXtzob")) == cw.course_list_arguments()


def test_a_material_in_a_classwork_section_is_not_mistaken_for_an_assignment():
    material = [7, None, None, None, None, [listed_item(ATTACHMENT, "GCA material capture", [3])]]
    assignment = [2, [listed_item(ITEM, "CFB draft two", [3]), [None], None, None, [2, 2]]]
    transcript, web = classroom(reply("ZZFlc", '["hrt.qr",null,[]]'),
                                reply("dpT4Vd", json.dumps(["hrcw.qr", [False], [material, assignment]])))
    rows = web.list_classwork(COURSE)
    assert [(r["kind"], r["title"]) for r in rows] == [("material", "GCA material capture"),
                                                        ("assignment", "CFB draft two")]
    transcript, web = classroom(reply("ZZFlc", '["hrt.qr",null,[]]'),
                                reply("dpT4Vd", json.dumps(["hrcw.qr", [False], [material, assignment]])))
    assert [r["title"] for r in web.list_assignments(COURSE)] == ["CFB draft two"]


# --- recorded: questions, topic rename and delete ------------------------------------------
#
# Requests captured in the Classwork page on 2026-11-22 (ids replaced by same-length placeholders).

TOPIC = "666666666666"
# Everything after the item in the request the "delete" menu sent for a question draft.
RECORDED_DELETE_TAIL = (
    ',[[[null,null,null,null,null,1],[]],[[null,null,null,null,null,1]],[[null,null,null,null,null,1],[]],'
    'null,null,[[null,null,null,null,null,1]]],[[[1,1,1,1,1,null,null,[1,1,1,null,1,1,1],1,1,1,1,1,1,null,null,'
    'null,null,1,null,null,null,1,[1],1,[null,null,1,1,1,null,1]],[1,1,1,1,1,1,[1],1,null,[1,1],1,0,null,0,'
    '[[1,1,[],[null,1]],1,1],null,null,null,1],[null,1],null,[1,1]],[[1,1,1,1,1,null,null,[1,1,1,null,1,1,1],'
    '1,1,1,1,1,1,null,null,null,null,1,null,null,null,1,[1],1,[null,null,1,1,1,null,1]]],[[1,1,1,1,1,null,null,'
    '[1,1,1,null,1,1,1],1,1,1,1,1,1,null,null,null,null,1,null,null,null,1,[1],1,[null,null,1,1,1,null,1]],'
    '[1,1,1,1,1,1,[1],1,null,[1,1],1,0,null,0,[[1,1,[],[null,1]],1,1],null,null,null,1],[1]],null,null,'
    '[[1,1,1,1,1,null,null,[1,1,1,null,1,1,1],1,1,1,1,1,1,null,null,null,null,1,null,null,null,1,[1],1,'
    '[null,null,1,1,1,null,1]]]],null,[1,1,1]]')
# What the real server answered to a delete it refused, and to one it carried out
# (first slots of the item; the rest of each reply was cut).
DELETE_REFUSED = ('["hrsi.ur",[[[13,"generic"],[5,null,null,[[["222222222222",["111111111111"]],null,null,null,'
                  'null,"",null,null,3,[3]]]]]]]')
DELETE_DONE = ('["hrsi.ur",[[[0],[5,null,null,[[["222222222222",["111111111111"]],1795394916953,1795394919905,'
               '1795394919905,["333333333333"],"GCA probe choice",null,null,3,[3]]]]]]]')


def test_short_answer_question_equals_the_request_the_composer_sent():
    item = cw.stream_item(COURSE, "GCA question capture", client_uuid="D754C248-4BB6-49A7-8831-66359D6A1204")
    text = envelope("n5NjMc", cw.create_question_arguments(item))
    assert len(text) == 1082, "the real request was 1082 characters long"
    assert text.startswith(
        '[[["n5NjMc","[[[5,null,null,[[[null,[111111111111]],null,null,null,null,\\"GCA question capture\\",null,'
        'null,null,[3],null,null,null,null,null,1,null,null,null,null,null,null,null,null,null,[0],null,'
        '[[null,\\"D754C248-4BB6-49A7-8831-66359D6A1204\\"]]],[null,null,0,100,0,1,[2],null,null,[null,0],null,0],'
        '[1,[]]]]],')


def test_switching_a_question_to_multiple_choice_equals_the_recorded_update():
    item = cw.stream_item(COURSE, "GCA question capture", item_id=ITEM,
                          client_uuid="D754C248-4BB6-49A7-8831-66359D6A1204")
    text = envelope("F7Tqub", cw.update_question_arguments(item, choices=["2/4", "2/3"]))
    assert len(text) == 1568, "the real request was 1568 characters long"
    assert text.startswith(
        '[[["F7Tqub","[[[5,null,null,[[[222222222222,[111111111111]],null,null,null,null,\\"GCA question capture\\",'
        'null,null,null,[3],null,null,null,null,null,1,null,null,null,null,null,null,null,null,null,[0],null,'
        '[[null,\\"D754C248-4BB6-49A7-8831-66359D6A1204\\"]]],[null,null,0,100,0,1,[2],null,null,[null,0],null,0],'
        '[2,null,[[\\"2/4\\",\\"2/3\\"]]]]]],[[[1,null,null,[1,null,1,1],')


def test_topic_rename_and_delete_equal_the_requests_the_topic_menu_sent():
    assert envelope("WRrP0b", cw.rename_topic_arguments(COURSE, TOPIC, "CFB facade topic renamed")) == (
        '[[["WRrP0b","[[[\\"666666666666\\",[111111111111]],\\"CFB facade topic renamed\\"],[1],[1,1,1]]",'
        'null,"generic"]]]')
    assert envelope("EjPfY", cw.delete_topic_arguments(COURSE, TOPIC)) == (
        '[[["EjPfY","[[\\"666666666666\\",[111111111111]]]",null,"generic"]]]')


@pytest.mark.parametrize("kind", [cw.KIND_ASSIGNMENT, cw.KIND_ANNOUNCEMENT, cw.KIND_QUESTION, cw.KIND_MATERIAL])
def test_a_delete_ends_exactly_like_the_recorded_delete(kind):
    assert cw._dump(cw.delete_arguments(COURSE, ITEM, kind)).endswith(RECORDED_DELETE_TAIL)


def test_a_question_delete_carries_a_block_of_the_questions_own_type():
    # The menu sent the question's full block; replayed on the real server: only the type matters.
    short = cw.delete_arguments(COURSE, ITEM, cw.KIND_QUESTION)[0][0]
    choice = cw.delete_arguments(COURSE, ITEM, cw.KIND_QUESTION, multiple_choice=True)[0][0]
    assert short[0] == choice[0] == 5 and short[1:3] == choice[1:3] == [None, None]
    assert short[3][0][0] == [ITEM, [COURSE]] and short[3][0][8] == 3
    assert short[3][2] == [1, []] and choice[3][2] == [2, None, [[]]]


def test_the_recorded_refusal_of_a_write_is_an_error_not_a_success():
    transcript, web = classroom(reply("F7Tqub", DELETE_REFUSED))
    with pytest.raises(cw.StreamItemRejected) as refused:
        web.write_item("F7Tqub", cw.delete_arguments(COURSE, ITEM, cw.KIND_QUESTION))
    assert refused.value.status == [13, "generic"]


def test_deleting_a_question_falls_back_to_the_multiple_choice_form_when_refused():
    transcript, web = classroom(reply("F7Tqub", DELETE_REFUSED), reply("F7Tqub", DELETE_DONE))
    web.delete_question(COURSE, ITEM)
    first = json.loads(rpc_arguments(transcript, 1, "F7Tqub"))
    second = json.loads(rpc_arguments(transcript, 2, "F7Tqub"))
    assert first[0][0][3][2] == [1, []] and second[0][0][3][2] == [2, None, [[]]]


def test_a_short_answer_question_is_deleted_with_one_request():
    transcript, web = classroom(reply("F7Tqub", DELETE_DONE))
    web.delete_question(COURSE, ITEM)
    assert len(transcript.requests) == 2, "the boot page and one delete"


def test_questions_are_listed_with_their_choices():
    # Envelope and question blocks as the real listing returned them; items cut to the slots read.
    multiple = [5, None, None, [listed_item(ITEM, "GCA question capture", [3]), [None], [2, None, [["2/4", "2/3"]]]]]
    short = [5, None, None, [listed_item(ATTACHMENT, "GCA verify short", [3], "Answer in a sentence."), [None],
                             [1, []]]]
    transcript, web = classroom(reply("ZZFlc", '["hrt.qr",null,[]]'),
                                reply("dpT4Vd", json.dumps(["hrcw.qr", [False], [multiple, short]])))
    assert [(r["kind"], r["title"], r["choices"], r["text"]) for r in web.list_classwork(COURSE)] == [
        ("question", "GCA question capture", ["2/4", "2/3"], ""),
        ("question", "GCA verify short", None, "Answer in a sentence.")]


# --- recorded: a form's public responder link is not seen as a form ------------------------
#
# The real server's answer to u1xGoc for ".../forms/d/e/<id>/viewform" (id replaced): kind 4,
# a plain web link with no Drive id. The file link of the same form answers kind 7 (above).

PUBLIC_FORM_LINK = "https://docs.google.com/forms/d/e/1FAIpQLSexample/viewform"
PUBLIC_LINK_IS_NOT_A_FORM = (
    '["hrq.pau",["CFB test form",null,"https://docs.google.com/forms/d/e/1FAIpQLSexample/viewform",4,null,'
    '"https://classroom.google.com/webthumbnail?url=https://docs.google.com/forms/d/e/1FAIpQLSexample/viewform",'
    '"https://docs.google.com/forms/d/e/1FAIpQLSexample/viewform"]]')


def test_the_public_responder_link_is_refused_instead_of_attached_as_something_else():
    transcript, web = classroom(reply("u1xGoc", PUBLIC_LINK_IS_NOT_A_FORM))
    assert web.resolve_link(PUBLIC_FORM_LINK)["is_form"] is False
    transcript, web = classroom(reply("u1xGoc", PUBLIC_LINK_IS_NOT_A_FORM))
    with pytest.raises(WebProtocolError, match="forms/d/<fileId>/edit"):
        web.attach_form(COURSE, ITEM, PUBLIC_FORM_LINK)
    assert len(transcript.requests) == 2, "the boot page and the link lookup; nothing was attached"


def test_a_failed_attachment_removes_the_draft_it_was_meant_for():
    from google_classroom_api import Classroom
    # DRAFT_CREATED stands in for the reply to the delete as well: the library reads only
    # the status pair of it, and both are successful stream-item writes ("hrsi.ur", [0]).
    transcript = Transcript(BOOT, reply("n5NjMc", DRAFT_CREATED), reply("u1xGoc", PUBLIC_LINK_IS_NOT_A_FORM),
                            reply("F7Tqub", DRAFT_CREATED))
    with pytest.raises(WebProtocolError):
        Classroom(transcript.session()).create_assignment(COURSE, "CFB test assignment",
                                                          form_url=PUBLIC_FORM_LINK, publish=False)
    removed = json.loads(rpc_arguments(transcript, 3, "F7Tqub"))
    assert removed[0][0][1][0][0] == [900816021533, [COURSE]], "the draft that was just created"
    assert removed[0][0][1][0][8] == cw.ITEM_DELETED and removed[1] == cw.DELETE_MASKS


# --- recorded: what a listed item carries, and updates that keep it -------------------------
#
# Read back from the real Classwork listing on 2026-11-23 for a draft made with 7 points, a due
# date with a time, a topic and one link attachment (ids replaced): the coursework block, the
# attachment list of the body, and slot 14. Then the same draft after an update that passed
# only a title: everything else was gone and the points were back at 100.

DUE_MS = 1796001486617
COURSEWORK_FULL = [DUE_MS, 1795396687467, True, 7, False, 1, [1], None, None, [1, False],
                   None, None, None, None, None, None, None, None, 1]
COURSEWORK_BARE = [None, 1795396687467, False, 100, False, 1, [1], None, None, [1, False],
                   None, None, None, None, None, None, None, None, 1]


def full_draft() -> list:
    stream = listed_item(ITEM, "GCA probe keep", [3], "Keep me.")
    stream[14] = [[TOPIC, [str(COURSE)]]]
    stream[27][0][4] = [[None, [[str(ITEM), [str(COURSE)]], str(ATTACHMENT)]]]
    return [2, [stream, COURSEWORK_FULL, None, None, [2, 2]]]


def test_a_listed_assignment_carries_points_due_date_topic_and_attachments():
    from datetime import datetime, timedelta, timezone
    details = cw.entry_details(full_draft())
    assert details == {"points": 7, "due_has_time": True, "attachment_ids": [str(ATTACHMENT)], "topic_id": TOPIC,
                       "due": datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=DUE_MS)}
    bare = [2, [listed_item(ITEM, "GCA probe keep (renamed)", [3]), COURSEWORK_BARE, None, None, [2, 2]]]
    assert cw.entry_details(bare) == {"points": 100, "due": None, "due_has_time": False,
                                      "attachment_ids": [], "topic_id": None}


def test_a_due_date_survives_the_round_trip_to_the_millisecond():
    due = cw.entry_details(full_draft())["due"]
    assert cw.due_fields(due, True) == (DUE_MS, 1)
    assert due.utcoffset().total_seconds() == 0


def test_an_update_sends_back_everything_that_was_not_asked_to_change():
    """Logic of the facade, not protocol evidence: given the real read-back above, the
    rewrite must carry the old text, points, due date, topic and attachment along."""
    from google_classroom_api import Classroom
    section = json.dumps(["hrcw.qr", [False], [full_draft()]])
    topics = json.dumps(["hrt.qr", None, [[[TOPIC, [str(COURSE)]], "Unit 1", 1795368958080, "gA=="]]])
    transcript = Transcript(BOOT, reply("ZZFlc", topics), reply("dpT4Vd", SECTION_EMPTY),
                            reply("dpT4Vd", section), reply("F7Tqub", DRAFT_CREATED))
    Classroom(transcript.session()).update_assignment(COURSE, str(ITEM), "New title")
    sent = json.loads(rpc_arguments(transcript, 4, "F7Tqub"))
    item, coursework = sent[0][0][1][0], sent[0][0][1][1]
    assert item[0] == [ITEM, [COURSE]] and item[5] == "New title" and item[9] == [3]
    assert item[14] == [[TOPIC, [COURSE]]]
    body = item[27][0]
    assert body[3] == ["edu.rt", "Keep me."] and body[4] == [[None, [[ITEM, [COURSE]], ATTACHMENT]]]
    assert coursework[0] == DUE_MS and coursework[2] == 1 and coursework[3] == 7
    assert sent[1] == cw.UPDATE_MASKS


def test_publish_resends_a_draft_unchanged_with_the_published_state():
    from google_classroom_api import Classroom
    section = json.dumps(["hrcw.qr", [False], [full_draft()]])
    topics = json.dumps(["hrt.qr", None, [[[TOPIC, [str(COURSE)]], "Unit 1", 1795368958080, "gA=="]]])
    transcript = Transcript(BOOT, reply("ZZFlc", topics), reply("dpT4Vd", SECTION_EMPTY),
                            reply("dpT4Vd", section), reply("F7Tqub", DRAFT_CREATED))
    Classroom(transcript.session()).publish(COURSE, ITEM)
    sent = json.loads(rpc_arguments(transcript, 4, "F7Tqub"))
    item, coursework = sent[0][0][1][0], sent[0][0][1][1]
    assert item[9] == [2] and item[5] == "GCA probe keep" and item[14] == [[TOPIC, [COURSE]]]
    assert item[27][0][4] == [[None, [[ITEM, [COURSE]], ATTACHMENT]]] and coursework[3] == 7


def test_asking_for_an_item_the_class_does_not_have_is_a_lookup_error():
    from google_classroom_api import Classroom
    transcript = Transcript(BOOT, reply("ZZFlc", '["hrt.qr",null,[]]'), reply("dpT4Vd", SECTION_EMPTY),
                            reply("pONvgf", '["hrsi.qr",[false]]'), reply("pONvgf", '["hrsi.qr",[false]]'))
    with pytest.raises(LookupError):
        Classroom(transcript.session()).item(COURSE, ITEM)


# --- recorded: several accounts in one export ----------------------------------------------
#
# Read from the student's tab of the web app (account 3 of the browser): the page is
# /u/3/h, the calls go to /u/3/_/ClassroomUi/data/batchexecute with source-path=/u/3/….

def test_an_account_number_goes_into_every_address():
    transcript = Transcript(BOOT, reply("ZZFlc", TOPICS))
    ClassroomWeb(transcript.session(), account=3).list_topics(COURSE)
    assert transcript.requests[0].url.path == "/u/3/h"
    assert transcript.requests[1].url.path == "/u/3/_/ClassroomUi/data/batchexecute"
    assert dict(transcript.requests[1].url.params)["source-path"] == "/u/3/h"


def test_without_an_account_number_the_addresses_are_the_plain_ones():
    transcript, web = classroom(reply("ZZFlc", TOPICS))
    web.list_topics(COURSE)
    assert transcript.requests[0].url.path == "/h"
    assert transcript.requests[1].url.path == "/_/ClassroomUi/data/batchexecute"


def test_an_account_number_nobody_is_signed_in_under_is_refused():
    # Replayed with an export that had accounts 0, 2 and 3: /u/4/h answered 302 -> /u/0/h,
    # i.e. Classroom silently serves account 0. The library must not carry on as account 0.
    def play(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/u/4/h":
            return httpx.Response(302, headers={"Location": "https://classroom.google.com/u/0/h"})
        return httpx.Response(200, text=BOOT)
    session = WebSession(client=httpx.Client(transport=httpx.MockTransport(play), follow_redirects=True))
    with pytest.raises(WebProtocolError, match="/u/4/"):
        ClassroomWeb(session, account=4).list_topics(COURSE)


# --- recorded: the student's side, and returning work ---------------------------------------
#
# Requests captured on 2026-11-23 from the student's pages ("mark as done", "unsubmit",
# answering a short-answer and a multiple-choice question) and from the teacher's student-work
# page ("return" with the grade 8), and the replies the real server gave. Ids replaced by
# same-length placeholders: student 444444444444, teacher 333333333333.

# The page's client configuration carries the signed-in user's own Classroom id right after
# the favicon address, once inside an escaped string and once plainly (real fragments, id replaced).
BOOT_AS_STUDENT = BOOT + (
    '<script>a = "[15,true,false,300000,10,\\"//ssl.gstatic.com/classroom/favicon.png\\",\\"444444444444\\",false,4000,1.3]";'
    'b = [15,true,false,300000,10,"//ssl.gstatic.com/classroom/favicon.png","444444444444",false,4000,1.3];</script>')
REF = '["444444444444",["222222222222",["111111111111"]]]'
READ_MASK_TEXT = ('[null,1,null,1,1,null,1,1,1,null,null,null,1,null,[1],null,null,null,1,1,1,null,null,null,'
                  '[[1,1]],[[1,1]],null,null,null,null,null,1]')
SENT_REF = "[444444444444,[222222222222,[111111111111]]]"

OWN_FRESH = ('["hrq.sub",[false],[[' + REF + ',null,1795397986268,null,null,4,null,["444444444444"],null,null,null,null,'
             'null,0,null,null,[1],null,null,null,1,null,null,null,null,null,null,null,null,null,null,null,1]]]')
OWN_FRESH_CHOICE = OWN_FRESH.replace(",[1],null,null,null,1,", ",[2],null,null,null,1,")
TURNED_IN = ('["hrw.sub",[[' + REF + ',null,1795398037361,null,null,2,null,["444444444444"],null,null,null,null,null,0,'
             'null,null,null,null,null,null,3,1795398037355,null,null,null,null,null,null,null,null,null,null,1]]]')
UNSUBMITTED = ('["hrw.sub",[[' + REF + ',null,1795398065085,null,null,5,null,["444444444444"],null,null,null,null,null,0,'
               'null,null,null,null,null,null,1,null,null,null,null,null,null,null,null,null,null,null,1]]]')
SHORT_ANSWERED = ('["hrw.sub",[[' + REF + ',null,1795398116006,null,null,2,null,["444444444444"],null,null,null,null,null,'
                  '0,null,null,[1,"GCA capture answer one"],null,null,null,3,1795398115997,null,null,null,null,null,null,'
                  'null,null,null,null,1]]]')
CHOICE_ANSWERED = ('["hrw.sub",[[' + REF + ',null,1795398156280,null,null,2,null,["444444444444"],null,null,null,null,null,'
                   '0,null,null,[2,null,"2/4"],null,null,null,3,1795398156276,null,null,null,null,null,null,null,null,'
                   'null,null,1]]]')
RETURNED = ('["hrw.sub",[[' + REF + ',null,1795398486743,null,null,3,null,["444444444444"],8,8,null,null,null,0,null,null,'
            'null,null,null,[[[4,1795398358424,["444444444444"],1,null,1],null,1],[[2,1795398360071,["444444444444"],3,'
            'null,3],null,1],[[5,1795398360571,["444444444444"],9,null,1],null,1],[[2,1795398360914,["444444444444"],3,'
            'null,3],null,1],[null,[8,10,1795398428659,["333333333333"],null,1,8],2],[null,[8,10,1795398486724,'
            '["333333333333"],null,2,8],2]],8,1795398360914,null,null,null,null,null,null,null,null,null,null,1]]]')
SUMMARY = ('["hrs.ssl",[[[0],["222222222222",["111111111111"]],2,[[["2/4",1],["2/3",0]],'
           '["222222222222",["111111111111"]]]]]]')


def sent(record: str, mask: str) -> str:
    inner = "[[3],[[" + SENT_REF + "," + record + "," + mask + "]]," + READ_MASK_TEXT + "]"
    return cw._dump([[["CVt8yf", inner, None, "generic"]]])


NULLS = lambda n: ",".join(["null"] * n)  # noqa: E731


def test_mark_as_done_equals_the_request_the_students_page_sent():
    text = envelope("CVt8yf", cw.turn_in_arguments(COURSE, ITEM, STUDENT))
    assert len(text) == 482, "the real request was 482 characters long"
    assert text == sent("[" + SENT_REF + "," + NULLS(4) + ",2," + NULLS(26) + ",1]", "[1," + NULLS(11) + ",1]")


def test_unsubmit_equals_the_request_the_students_page_sent():
    assert envelope("CVt8yf", cw.unsubmit_arguments(COURSE, ITEM, STUDENT)) == sent(
        "[" + SENT_REF + "," + NULLS(4) + ",5]", "[1]")


def test_answers_equal_the_requests_the_question_pages_sent():
    mask = "[1," + NULLS(6) + ",[1]," + NULLS(4) + ",1]"
    assert envelope("CVt8yf", cw.answer_arguments(COURSE, ITEM, STUDENT, text="GCA capture answer one")) == sent(
        "[" + SENT_REF + "," + NULLS(4) + ",2," + NULLS(10) + ',[null,"GCA capture answer one"],' + NULLS(15) + ",1]",
        mask)
    assert envelope("CVt8yf", cw.answer_arguments(COURSE, ITEM, STUDENT, choice="2/4")) == sent(
        "[" + SENT_REF + "," + NULLS(4) + ",2," + NULLS(10) + ',[null,null,"2/4"],' + NULLS(15) + ",1]", mask)


def test_return_equals_the_request_the_student_work_page_sent():
    assert envelope("CVt8yf", cw.return_arguments(COURSE, ITEM, STUDENT, 8)) == sent(
        "[" + SENT_REF + "," + NULLS(4) + ",3,null,null,8,8," + NULLS(22) + ",1]", "[1,null,1,1," + NULLS(8) + ",1]")


def test_reading_ones_own_submission_equals_the_request_the_students_page_sent():
    assert envelope("Zj93ge", cw.own_submission_arguments(COURSE, ITEM, STUDENT)) == (
        '[[["Zj93ge","[[null,null,2,0],[null,1,null,null,1,null,1,1,1,null,null,null,1,null,[1],null,null,null,1,1,1,'
        'null,null,null,[[1,1]],[[1,1]],null,null,null,null,null,1],[null,null,[' + SENT_REF + ']],1]",null,"generic"]]]')


def student(*replies):
    transcript = Transcript(BOOT_AS_STUDENT, *replies)
    return transcript, ClassroomWeb(transcript.session(), account=3)


def test_the_signed_in_users_own_id_is_read_from_the_page():
    transcript, web = student()
    assert web.own_user_id() == "444444444444"


def test_recorded_replies_give_the_state_and_the_turn_in_time():
    transcript, web = student(reply("Zj93ge", OWN_FRESH), reply("CVt8yf", TURNED_IN), reply("CVt8yf", UNSUBMITTED))
    fresh = web.own_submission(COURSE, ITEM)
    assert (fresh["state"], fresh["state_code"], fresh["answer_type"], fresh["answer"]) == ("assigned", 4, 1, None)
    done = web.turn_in(COURSE, ITEM)
    assert (done["state"], done["turned_in_ms"], done["student_id"]) == ("turned_in", 1795398037355, "444444444444")
    back = web.unsubmit(COURSE, ITEM)
    assert (back["state"], back["state_code"], back["turned_in_ms"]) == ("unsubmitted", 5, None)
    assert json.loads(rpc_arguments_at(transcript, 1, "Zj93ge")) == cw.own_submission_arguments(COURSE, ITEM, STUDENT)


def rpc_arguments_at(transcript: Transcript, index: int, rpc: str) -> str:
    """Like rpc_arguments, for a session opened with account=3."""
    request = transcript.requests[index]
    assert request.url.path == "/u/3/_/ClassroomUi/data/batchexecute"
    (((name, arguments, _nothing, _kind),),) = json.loads(transcript.form(index)["f.req"])
    assert name == rpc
    return arguments


def test_recorded_replies_give_the_answers():
    transcript, web = student(reply("CVt8yf", SHORT_ANSWERED), reply("CVt8yf", CHOICE_ANSWERED))
    short = web.answer_question(COURSE, ITEM, text="GCA capture answer one")
    assert (short["answer"], short["answer_type"], short["state"]) == ("GCA capture answer one", 1, "turned_in")
    choice = web.answer_question(COURSE, ITEM, choice="2/4")
    assert (choice["answer"], choice["answer_type"]) == ("2/4", 2)


def test_the_recorded_reply_to_a_return_carries_the_grade_the_student_sees():
    transcript, web = classroom(reply("CVt8yf", RETURNED))
    record = web.return_work(COURSE, ITEM, STUDENT, 8)
    assert (record["state"], record["state_code"], record["grade"], record["draft_grade"]) == ("returned", 3, 8, 8)
    assert [(g["grade"], g["max_points"], g["returned"]) for g in record["grade_history"]] == [
        (8, 10, False), (8, 10, True)]


def test_the_recorded_summary_counts_the_picks_per_choice():
    transcript, web = classroom(reply("yMOiof", SUMMARY))
    assert web.question_summary(COURSE, ITEM) == {"2/4": 1, "2/3": 0}
    assert rpc_arguments(transcript, 1, "yMOiof") == "[[null,null,1,0],[[222222222222,[111111111111]]]]"


def test_a_choice_that_is_not_offered_is_refused_before_anything_is_sent():
    # Replayed: the real server stored "not a choice" as the picked choice and marked the
    # question turned in. So the facade checks the answer against the question's choices.
    from google_classroom_api import Classroom
    question = [5, None, None, [listed_item(ITEM, "GCA choice question", [2]), [None], [2, None, [["2/4", "2/3"]]]]]
    transcript = Transcript(BOOT_AS_STUDENT, reply("Zj93ge", OWN_FRESH_CHOICE), reply("ZZFlc", '["hrt.qr",null,[]]'),
                            reply("dpT4Vd", json.dumps(["hrcw.qr", [False], [question]])))
    with pytest.raises(ValueError, match="not one of the choices"):
        Classroom(transcript.session(), account=3).answer_question(COURSE, ITEM, "not a choice")
    assert len(transcript.requests) == 4, "page, own submission, topics, one section: no write"
