"""Shared contracts: all model output is validated before it is saved."""
import re
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class ContentModel(BaseModel):
    model_config = {"str_strip_whitespace": True, "extra": "forbid"}


# List entries need their own limits: a nonempty list may still contain blank text.
ContentLine = Annotated[str, Field(min_length=1, max_length=4000)]
SourceIdentifier = Annotated[str, Field(min_length=1, max_length=100)]


class Source(BaseModel):
    id: str
    label: str
    text: str


class KnowledgePoint(ContentModel):
    title: str = Field(min_length=1, max_length=200)
    explanation: str = Field(min_length=1, max_length=8000)
    key_takeaway: str = Field(min_length=1, max_length=1000)
    pitfalls: list[ContentLine] = Field(default_factory=list, max_length=8)
    source_ids: list[SourceIdentifier] = Field(default_factory=list, max_length=30)


class WorkedExample(ContentModel):
    title: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=1, max_length=4000)
    steps: list[ContentLine] = Field(min_length=1, max_length=12)
    answer: str = Field(min_length=1, max_length=4000)
    mistake: str = Field(default="", max_length=2000)


class Analogy(ContentModel):
    concept: str = Field(min_length=1, max_length=200)
    scene: str = Field(min_length=1, max_length=3000)
    mapping: list[ContentLine] = Field(min_length=1, max_length=10)
    limitation: str = Field(min_length=1, max_length=2000)


class QuizQuestion(ContentModel):
    question: str = Field(min_length=1, max_length=3000)
    options: list[ContentLine] = Field(min_length=4, max_length=4)
    answer: int = Field(ge=0, le=3, strict=True)
    explanation: str = Field(min_length=1, max_length=3000)

    @field_validator("options")
    @classmethod
    def distinct_options(cls, values):
        if any(not value.strip() for value in values) or len(set(values)) != 4:
            raise ValueError("选项必须为四个不同的非空选项")
        return values


class StudyGuide(ContentModel):
    overview: str = Field(min_length=1, max_length=5000)
    objectives: list[ContentLine] = Field(min_length=1, max_length=12)
    prerequisites: list[ContentLine] = Field(default_factory=list, max_length=12)
    knowledge_points: list[KnowledgePoint] = Field(min_length=1, max_length=30)
    worked_examples: list[WorkedExample] = Field(min_length=1, max_length=10)
    analogies: list[Analogy] = Field(min_length=1, max_length=10)
    quiz: list[QuizQuestion] = Field(min_length=1, max_length=12)


class GenerateRequest(BaseModel):
    course: str = Field(min_length=1, max_length=100)
    textbook: str = Field(default="", max_length=300)
    chapter: str = Field(min_length=1, max_length=200)
    mode: Literal["material", "topic"]
    goal: Literal["understand", "exam", "practice"] = "understand"
    level: Literal["beginner", "intermediate"] = "beginner"
    text: str = Field(default="", max_length=90000)
    material_id: str | None = None
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)

    @field_validator("course", "chapter")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("不能为空")
        return value.strip()

    @model_validator(mode="after")
    def valid_pages(self):
        if self.mode == "topic" and re.fullmatch(r"(?:第?[一二三四五六七八九十百零〇\d]+[章节]|chapter\s*\d+|\d+)", self.chapter, flags=re.IGNORECASE):
            raise ValueError("按标题讲解时，请补充章节名称，例如「第三章 数据链路层」，而不只是章节编号")
        if self.page_start and self.page_end and self.page_start > self.page_end:
            raise ValueError("起始页不能大于结束页")
        if self.mode == "material" and not (self.material_id or self.text.strip()):
            raise ValueError("请上传资料或粘贴章节正文")
        return self


class SettingsInput(BaseModel):
    base_url: str = Field(min_length=1, max_length=1000)
    model: str = Field(min_length=1, max_length=200)
    api_key: str | None = Field(default=None, max_length=2000)


class QuestionInput(BaseModel):
    question: str = Field(min_length=1, max_length=3000)

    @field_validator("question")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("请输入问题")
        return value.strip()


class AttemptInput(BaseModel):
    question_index: int = Field(ge=0)
    choice: int = Field(ge=0, le=3)


class ProgressInput(BaseModel):
    completed: bool


class NotesInput(BaseModel):
    notes: str = Field(max_length=30000)
    expected_revision: int = Field(default=0, ge=0)
