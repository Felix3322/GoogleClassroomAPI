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
    assert web.list_assignments(COURSE) == [{"item_id": "900816770865", "state": 3, "topic_id": "900816347160",
                                             "title": "Library draft with topic and due"}]
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
