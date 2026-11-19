"""Thin HTTP adapter for Undo last change and Redo (ADR 0180).

Both bind the viewed result, revision, head version and the exact offered step; any newer
save refuses with 409 so the button relabels to the new newest change. The release profile
guards these routes as new work for the undo_last_change feature (ADR 0145).
"""

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from . import change_undo


class StepWorkingChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version_id: str = Field(strict=True, min_length=1, max_length=100)
    role_revision: int = Field(strict=True, ge=0, lt=2**63)
    make_working: bool = Field(strict=True)


class ChangeStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(strict=True, ge=0, lt=2**63)
    head_version_id: str = Field(strict=True, min_length=1, max_length=100)
    version_id: str = Field(strict=True, min_length=1, max_length=100)
    reason: str | None = Field(default=None, strict=True, max_length=2000)
    working_choice: StepWorkingChoice | None = None


def install(app, store):
    def step(operation, identifier, result_id, body):
        from .app import json_response

        try:
            saved = operation(
                store,
                identifier,
                result_id,
                body.revision,
                body.head_version_id,
                body.version_id,
                body.reason,
                working_choice=body.working_choice.model_dump() if body.working_choice else None,
            )
        except ValueError as error:
            raise HTTPException(409, str(error)) from None
        return json_response(saved)

    @app.post("/api/recordings/{identifier}/transcripts/{result_id}/undo-last")
    def undo_last_change(identifier: str, result_id: str, body: ChangeStep):
        return step(change_undo.undo, identifier, result_id, body)

    @app.post("/api/recordings/{identifier}/transcripts/{result_id}/redo-last")
    def redo_last_change(identifier: str, result_id: str, body: ChangeStep):
        return step(change_undo.redo, identifier, result_id, body)
