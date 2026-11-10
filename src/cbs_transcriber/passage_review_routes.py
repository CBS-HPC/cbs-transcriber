"""HTTP adapter for manual passage review metadata."""

from typing import Literal

from fastapi import HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from . import passage_review


class ReviewSave(BaseModel):
    model_config = ConfigDict(extra="forbid")
    result_id: str = Field(min_length=1, max_length=200)
    revision: int = Field(ge=0, lt=2**63, strict=True)
    status: Literal["not_checked", "checked", "needs_review"]
    review_revision: int = Field(ge=0, lt=2**63, strict=True)
    passage_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


def install(app, store):
    @app.get("/api/recordings/{identifier}/passage-reviews")
    def read(identifier: str, result_id: str = Query(min_length=1, max_length=200)):
        try:
            return passage_review.read(store, identifier, result_id)
        except passage_review.Conflict as error:
            raise HTTPException(409, str(error)) from None

    @app.patch("/api/recordings/{identifier}/segments/{segment_id}/review")
    def save(identifier: str, segment_id: str, body: ReviewSave):
        try:
            return passage_review.save(store, identifier, segment_id, **body.model_dump())
        except passage_review.Conflict as error:
            raise HTTPException(409, str(error)) from None
