"""Turn a :class:`Lesson` into Forms ``batchUpdate`` requests.

The same planner creates a new form and edits an existing one in place. It
works as a diff, so question IDs survive reordering and rewording, and so that
running it again after a half-finished attempt only sends what is still missing.

Layout rule: after the plan is applied, ``form["items"][i]`` is slot ``i`` for
every managed slot. Items the teacher added by hand in the Forms editor are
left alone and end up after the managed ones.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Mapping, Optional

from .models import CHOICE_KINDS, Lesson, Question

EMAIL_COLLECTION = "VERIFIED"


@dataclass
class Slot:
    key: str
    kind: str
    title: str
    content_hash: str
    item: dict
    site_question_id: Optional[str] = None
    snapshot: Optional[dict] = None

    @property
    def is_question(self) -> bool:
        return self.site_question_id is not None

    @property
    def update_mask(self) -> str:
        return "title,description,questionItem" if self.is_question else "title,description"

    def build(self, *, item_id: Optional[str] = None, question_id: Optional[str] = None) -> dict:
        item = json.loads(json.dumps(self.item))
        if item_id:
            item["itemId"] = item_id
        if question_id and "questionItem" in item:
            item["questionItem"]["question"]["questionId"] = question_id
        return item


@dataclass
class Plan:
    requests: list[dict]
    slots: list[Slot]
    deleted_keys: list[str] = field(default_factory=list)


def question_item(question: Question, *, is_quiz: bool = True) -> dict:
    """Forms ``Item`` for one question, without IDs."""
    body: dict = {"required": question.required}
    if question.kind in CHOICE_KINDS:
        options = []
        for choice in question.choices:
            option: dict = {"value": choice.text}
            if choice.image_url:
                option["image"] = {"sourceUri": choice.image_url}
            options.append(option)
        body["choiceQuestion"] = {
            "type": "RADIO" if question.kind == "single" else "CHECKBOX",
            "options": options,
        }
    else:
        body["textQuestion"] = {"paragraph": question.kind == "long"}

    if is_quiz:
        grading: dict = {"pointValue": question.points}
        if question.correct:
            grading["correctAnswers"] = {"answers": [{"value": v} for v in question.correct]}
        auto_graded_choice = question.kind in CHOICE_KINDS and bool(question.correct)
        if auto_graded_choice:
            # Google only accepts whenRight/whenWrong on choice questions with an answer key.
            if question.feedback_right:
                grading["whenRight"] = {"text": question.feedback_right}
            if question.feedback_wrong:
                grading["whenWrong"] = {"text": question.feedback_wrong}
        elif question.reference_answer:
            # ...and generalFeedback everywhere else. This is where a reference answer goes.
            grading["generalFeedback"] = {"text": question.reference_answer}
        body["grading"] = grading

    item: dict = {"title": question.title, "questionItem": {"question": body}}
    if question.description:
        item["description"] = question.description
    if question.image_url:
        item["questionItem"]["image"] = {
            "sourceUri": question.image_url,
            "altText": question.image_alt,
        }
    return item


def _hash(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def slots_for(lesson: Lesson) -> list[Slot]:
    """The managed items of the form, in order."""
    slots: list[Slot] = []
    for index, section in enumerate(lesson.sections):
        if index == 0:
            if section.title or section.description:
                item = {"title": section.title, "textItem": {}}
                if section.description:
                    item["description"] = section.description
                slots.append(Slot(f"s:{index}", "text", section.title, _hash(item), item))
        else:
            item = {"title": section.title, "pageBreakItem": {}}
            if section.description:
                item["description"] = section.description
            slots.append(Slot(f"s:{index}", "page", section.title, _hash(item), item))
        for question in section.questions:
            slots.append(
                Slot(
                    key=f"q:{question.site_id}",
                    kind=question.kind,
                    title=question.title,
                    content_hash=question.content_hash(),
                    item=question_item(question, is_quiz=lesson.is_quiz),
                    site_question_id=question.site_id,
                    snapshot=question.snapshot(),
                )
            )
    return slots


def plan_form(form: dict, lesson: Lesson, known: Mapping[str, Mapping]) -> Plan:
    """Requests that turn ``form`` into ``lesson``.

    ``known`` maps a slot key to the stored row for it: ``item_id``,
    ``question_id``, ``kind`` and ``content_hash``.
    """
    slots = slots_for(lesson)
    requests: list[dict] = []

    info = form.get("info", {})
    if info.get("title") != lesson.title or (info.get("description") or "") != lesson.description:
        requests.append(
            {
                "updateFormInfo": {
                    "info": {"title": lesson.title, "description": lesson.description},
                    "updateMask": "title,description",
                }
            }
        )

    settings = form.get("settings", {})
    is_quiz = bool(settings.get("quizSettings", {}).get("isQuiz"))
    if is_quiz != lesson.is_quiz or settings.get("emailCollectionType") != EMAIL_COLLECTION:
        # Must come before any item that carries grading.
        requests.append(
            {
                "updateSettings": {
                    "settings": {
                        "quizSettings": {"isQuiz": lesson.is_quiz},
                        "emailCollectionType": EMAIL_COLLECTION,
                    },
                    "updateMask": "quizSettings.isQuiz,emailCollectionType",
                }
            }
        )

    working = [item.get("itemId") for item in form.get("items", [])]
    wanted = {slot.key: slot for slot in slots}
    key_of_item = {row["item_id"]: key for key, row in known.items() if row.get("item_id")}
    deleted: list[str] = []

    # 1. Remove managed items that are gone, or whose question type changed
    #    (Forms cannot change the type of an existing question).
    for index in range(len(working) - 1, -1, -1):
        key = key_of_item.get(working[index])
        if key is None:
            continue
        slot = wanted.get(key)
        if slot is None or slot.kind != known[key].get("kind"):
            requests.append({"deleteItem": {"location": {"index": index}}})
            working.pop(index)
            deleted.append(key)

    # 2. Put every slot at its position, creating or updating as needed.
    for position, slot in enumerate(slots):
        row = known.get(slot.key)
        reusable = (
            row is not None
            and row.get("item_id") in working
            and row.get("kind") == slot.kind
        )
        if reusable:
            current = working.index(row["item_id"])
            if current != position:
                requests.append(
                    {
                        "moveItem": {
                            "originalLocation": {"index": current},
                            "newLocation": {"index": position},
                        }
                    }
                )
                working.insert(position, working.pop(current))
            if row.get("content_hash") != slot.content_hash:
                requests.append(
                    {
                        "updateItem": {
                            "item": slot.build(
                                item_id=row["item_id"], question_id=row.get("question_id")
                            ),
                            "location": {"index": position},
                            "updateMask": slot.update_mask,
                        }
                    }
                )
        else:
            requests.append(
                {"createItem": {"item": slot.build(), "location": {"index": position}}}
            )
            working.insert(position, None)

    return Plan(requests=requests, slots=slots, deleted_keys=deleted)
