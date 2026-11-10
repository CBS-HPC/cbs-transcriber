"""Thin HTTP adapter for a revision-bound, proven deleted-passage restoration."""

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from . import passage_undo


class RestoreWorkingChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version_id: str = Field(strict=True, min_length=1, max_length=100)
    role_revision: int = Field(strict=True, ge=0, lt=2**63)
    make_working: bool = Field(strict=True)


class RestorePassage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(strict=True, ge=0, lt=2**63)
    result_id: str = Field(strict=True, min_length=1, max_length=100)
    delete_version_id: str = Field(strict=True, min_length=1, max_length=100)
    reason: str | None = Field(default=None, strict=True, max_length=2000)
    working_choice: RestoreWorkingChoice | None = None


def install(app, store):
    @app.post("/api/recordings/{identifier}/segments/{segment_id}/restore")
    def restore(identifier: str, segment_id: str, body: RestorePassage):
        try:
            return passage_undo.apply_restore(
                store,
                identifier,
                body.result_id,
                segment_id,
                body.delete_version_id,
                body.revision,
                body.reason,
                working_choice=body.working_choice.model_dump() if body.working_choice else None,
            )
        except ValueError as error:
            raise HTTPException(409, str(error)) from None
