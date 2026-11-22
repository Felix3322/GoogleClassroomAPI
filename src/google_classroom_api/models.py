"""The site's own description of a lesson, independent of Google's formats."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Optional

CHOICE_KINDS = ("single", "multiple")
TEXT_KINDS = ("short", "long")
KINDS = CHOICE_KINDS + TEXT_KINDS


@dataclass
class Choice:
    text: str
    image_url: Optional[str] = None

    @classmethod
    def from_value(cls, value) -> "Choice":
        if isinstance(value, Choice):
            return value
        if isinstance(value, str):
            return cls(text=value)
        return cls(text=value["text"], image_url=value.get("image_url"))


@dataclass
class Question:
    """One sub-question.

    ``title`` is shown as-is, so the textbook wording and the question number
    belong in it. Google Forms shows plain text only: anything that needs
    layout, such as a fraction with a horizontal bar, goes into ``image_url``.
    """

    site_id: str
    kind: str
    title: str
    description: str = ""
    choices: list[Choice] = field(default_factory=list)
    correct: list[str] = field(default_factory=list)
    points: int = 1
    feedback_right: str = ""
    feedback_wrong: str = ""
    reference_answer: str = ""
    image_url: Optional[str] = None
    image_alt: str = ""
    required: bool = False

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown question kind {self.kind!r}; use one of {KINDS}")
        self.choices = [Choice.from_value(c) for c in self.choices]
        if self.kind in CHOICE_KINDS:
            if not self.choices:
                raise ValueError(f"question {self.site_id}: a choice question needs choices")
            texts = [c.text for c in self.choices]
            stray = [value for value in self.correct if value not in texts]
            if stray:
                raise ValueError(f"question {self.site_id}: correct answers not among choices: {stray}")
            if self.kind == "single" and len(self.correct) > 1:
                raise ValueError(f"question {self.site_id}: a single-choice question has one answer")
        elif self.choices:
            raise ValueError(f"question {self.site_id}: a text question has no choices")

    @property
    def is_objective(self) -> bool:
        return bool(self.correct)

    def snapshot(self) -> dict:
        """What the site needs later to judge an answer given to this version."""
        return {
            "site_question_id": self.site_id,
            "kind": self.kind,
            "title": self.title,
            "description": self.description,
            "choices": [c.text for c in self.choices],
            "correct": list(self.correct),
            "points": self.points,
            "reference_answer": self.reference_answer,
        }

    def content_hash(self) -> str:
        payload = {
            **self.snapshot(),
            "choice_images": [c.image_url for c in self.choices],
            "feedback_right": self.feedback_right,
            "feedback_wrong": self.feedback_wrong,
            "image_url": self.image_url,
            "image_alt": self.image_alt,
            "required": self.required,
        }
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def from_dict(cls, data: dict) -> "Question":
        return cls(**data)


@dataclass
class Section:
    title: str = ""
    description: str = ""
    questions: list[Question] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "Section":
        return cls(
            title=data.get("title", ""),
            description=data.get("description", ""),
            questions=[
                q if isinstance(q, Question) else Question.from_dict(q)
                for q in data.get("questions", [])
            ],
        )


@dataclass
class Lesson:
    site_id: str
    title: str
    description: str = ""
    sections: list[Section] = field(default_factory=list)
    is_quiz: bool = True

    def __post_init__(self) -> None:
        seen: set[str] = set()
        for question in self.questions():
            if question.site_id in seen:
                raise ValueError(f"duplicate question id {question.site_id!r} in lesson {self.site_id}")
            seen.add(question.site_id)

    def questions(self) -> list[Question]:
        return [q for section in self.sections for q in section.questions]

    @classmethod
    def from_dict(cls, data: dict) -> "Lesson":
        return cls(
            site_id=data["site_id"],
            title=data["title"],
            description=data.get("description", ""),
            sections=[
                s if isinstance(s, Section) else Section.from_dict(s)
                for s in data.get("sections", [])
            ],
            is_quiz=data.get("is_quiz", True),
        )
