"""The form planner (formbuilder) and the answer judge.

Pure functions with explicit inputs. ``formbuilder.plan_form`` turns "the form
as the Forms API reports it" into the batchUpdate requests that make it match a
lesson, keeping question ids across reorder and re-edit. ``judge`` classifies a
raw answer. Nothing here talks to Google.
"""

from __future__ import annotations

import pytest

from google_classroom_api import formbuilder
from google_classroom_api.models import Lesson, Question, Section
from google_classroom_api.pipeline import judge


def lesson(*questions: Question, second_section: tuple[Question, ...] = ()) -> Lesson:
    sections = [Section(questions=list(questions))]
    if second_section:
        sections.append(Section(title="Part 2", questions=list(second_section)))
    return Lesson(site_id="lesson-12", title="Lesson 12", sections=sections)


Q1 = Question("q1", "single", "1. Which?", choices=["2/4", "2/3"], correct=["2/4"], points=1,
              feedback_right="Yes.", feedback_wrong="No.")
Q2 = Question("q2", "long", "2. Explain.", reference_answer="Two of four is one of two.", points=0)
Q3 = Question("q3", "short", "3. 2 + 2?", correct=["4", "four"], points=1)


def applied(the_lesson):
    """A (form, known) pair representing ``the_lesson`` already fully applied."""
    slots = formbuilder.slots_for(the_lesson)
    known, items = {}, []
    for i, slot in enumerate(slots):
        item_id, question_id = f"I{i}", (f"Q{i}" if slot.is_question else None)
        items.append({"itemId": item_id})
        known[slot.key] = {"item_id": item_id, "question_id": question_id or "",
                           "kind": slot.kind, "content_hash": slot.content_hash}
    form = {"info": {"title": the_lesson.title, "description": the_lesson.description},
            "settings": {"quizSettings": {"isQuiz": the_lesson.is_quiz},
                         "emailCollectionType": formbuilder.EMAIL_COLLECTION},
            "items": items}
    return form, known


EMPTY = {"info": {}, "settings": {}, "items": []}


def kinds(plan):
    return [next(iter(r)) for r in plan.requests]


def test_a_new_form_sets_info_settings_then_creates_each_question():
    plan = formbuilder.plan_form(EMPTY, lesson(Q1, Q2, Q3), {})
    assert kinds(plan)[:2] == ["updateFormInfo", "updateSettings"]
    creates = [r for r in plan.requests if "createItem" in r]
    assert len(creates) == 3 and [c["createItem"]["location"]["index"] for c in creates] == [0, 1, 2]
    first = creates[0]["createItem"]["item"]["questionItem"]["question"]
    assert first["choiceQuestion"]["type"] == "RADIO"
    assert first["grading"]["pointValue"] == 1
    assert first["grading"]["correctAnswers"]["answers"] == [{"value": "2/4"}]
    assert first["grading"]["whenRight"] == {"text": "Yes."}
    third = creates[2]["createItem"]["item"]["questionItem"]["question"]
    assert third["grading"]["correctAnswers"]["answers"] == [{"value": "4"}, {"value": "four"}]


def test_publishing_the_same_lesson_again_sends_nothing():
    form, known = applied(lesson(Q1, Q2, Q3))
    again = formbuilder.plan_form(form, lesson(Q1, Q2, Q3), known)
    assert again.requests == []


def test_swapping_two_questions_only_moves_and_keeps_ids():
    form, known = applied(lesson(Q1, Q2))
    plan = formbuilder.plan_form(form, lesson(Q2, Q1), known)
    assert kinds(plan) == ["moveItem"]
    move = plan.requests[0]["moveItem"]
    assert move["originalLocation"]["index"] == 1 and move["newLocation"]["index"] == 0


def test_rewording_updates_in_place_and_keeps_item_and_question_ids():
    form, known = applied(lesson(Q1, Q2))
    reworded = Question("q1", "single", "1. Which fraction?", choices=["2/4", "2/3", "3/4"], correct=["2/4"])
    plan = formbuilder.plan_form(form, lesson(reworded, Q2), known)
    assert kinds(plan) == ["updateItem"]
    update = plan.requests[0]["updateItem"]
    assert update["item"]["itemId"] == "I0"
    assert update["item"]["questionItem"]["question"]["questionId"] == "Q0"
    assert update["updateMask"] == "title,description,questionItem"


def test_removing_a_question_deletes_it_and_leaves_the_rest_alone():
    form, known = applied(lesson(Q1, Q2, Q3))
    plan = formbuilder.plan_form(form, lesson(Q1, Q3), known)
    assert kinds(plan) == ["deleteItem"]
    assert plan.requests[0]["deleteItem"]["location"]["index"] == 1
    assert plan.deleted_keys == ["q:q2"]


def test_changing_the_question_type_deletes_then_creates():
    form, known = applied(lesson(Q1, Q2))
    as_short = Question("q2", "short", "2. Explain.")
    plan = formbuilder.plan_form(form, lesson(Q1, as_short), known)
    assert kinds(plan) == ["deleteItem", "createItem"]
    assert plan.deleted_keys == ["q:q2"]


def test_items_added_by_hand_are_left_after_the_managed_ones():
    form, known = applied(lesson(Q1, Q2))
    form["items"] = [{"itemId": "FOREIGN"}] + form["items"]  # a question the teacher added
    plan = formbuilder.plan_form(form, lesson(Q1, Q2), known)
    assert kinds(plan) == ["moveItem", "moveItem"]
    assert all("deleteItem" not in r for r in plan.requests)


def test_a_second_section_becomes_a_page_break_before_its_questions():
    plan = formbuilder.plan_form(EMPTY, lesson(Q1, second_section=(Q2,)), {})
    creates = [r["createItem"]["item"] for r in plan.requests if "createItem" in r]
    assert "choiceQuestion" in creates[0]["questionItem"]["question"]
    assert "pageBreakItem" in creates[1] and creates[1]["title"] == "Part 2"
    assert "textQuestion" in creates[2]["questionItem"]["question"]


@pytest.mark.parametrize(
    "snapshot, raw, expected",
    [
        ({"kind": "single", "correct": ["2/4"]}, [], "blank"),
        ({"kind": "single", "correct": ["2/4"]}, ["2/3"], "wrong"),
        ({"kind": "single", "correct": ["2/4"]}, ["2/4"], "correct"),
        ({"kind": "multiple", "correct": ["a", "b"]}, ["b", "a"], "correct"),
        ({"kind": "multiple", "correct": ["a", "b"]}, ["a"], "wrong"),
        ({"kind": "short", "correct": ["4", "four"]}, [" Four "], "correct"),
        ({"kind": "long", "correct": []}, ["Because it looks like half."], "completed_ungraded"),
        ({"kind": "long", "correct": []}, ["   "], "blank"),
    ],
)
def test_blank_wrong_correct_and_ungraded_are_told_apart(snapshot, raw, expected):
    assert judge(snapshot, raw) == expected
