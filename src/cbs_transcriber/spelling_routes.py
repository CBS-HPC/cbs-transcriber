"""HTTP boundary for local draft spelling and explicit public dictionary retrieval.

Hidden by the ``spelling`` release-profile feature (ADR 0145/0185): its fixed new-work routes
(settings writes, check, suggest, dictionary retrieval) are refused before these handlers.
Reads, dictionary removal and vocabulary removals stay available for existing private data.
"""

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from . import release_profile, spelling


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Preferences(StrictBody):
    revision: int = Field(strict=True, ge=0, lt=2**63)
    enabled: bool = Field(strict=True)
    auto_download: bool = Field(strict=True)


class Vocabulary(StrictBody):
    revision: int = Field(strict=True, ge=0, lt=2**63)
    words: list[str] = Field(max_length=spelling.MAX_WORDS)


class Check(StrictBody):
    text: str = Field(max_length=200_000)
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")


class Suggest(StrictBody):
    word: str = Field(min_length=1, max_length=spelling.MAX_WORD)
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")


class Install(StrictBody):
    settings_revision: int = Field(strict=True, ge=0, lt=2**63)
    consent: bool = Field(default=False, strict=True)


class Remove(StrictBody):
    settings_revision: int = Field(strict=True, ge=0, lt=2**63)


def install(app, service, profile):
    def call(function, *args, **kwargs):
        try:
            return function(*args, **kwargs)
        except spelling.AdditionsDisabled:
            raise release_profile.FeatureDisabled("spelling") from None
        except spelling.Conflict as error:
            raise HTTPException(409, str(error)) from None
        except (ValueError, UnicodeError, OSError):
            # Invalid values and filesystem errors can contain private terms/paths.
            raise HTTPException(
                400, "Spelling assistance is unavailable for this request."
            ) from None

    @app.get("/api/spelling/settings")
    def settings():
        return call(service.settings)

    @app.put("/api/spelling/settings")
    def save_settings(body: Preferences):
        return call(service.save_settings, **body.model_dump())

    @app.get("/api/spelling/dictionaries")
    def dictionaries():
        return call(service.dictionaries)

    @app.delete("/api/spelling/dictionaries/{language}")
    def remove(language: str, body: Remove):
        return call(service.remove, language, body.settings_revision)

    @app.post("/api/recordings/{identifier}/spelling/check")
    def check(identifier: str, body: Check):
        return call(service.check, identifier, **body.model_dump())

    @app.post("/api/recordings/{identifier}/spelling/suggest")
    def suggest(identifier: str, body: Suggest):
        return call(service.suggest, identifier, **body.model_dump())

    @app.post("/api/recordings/{identifier}/spelling/dictionary")
    def retrieve(identifier: str, body: Install):
        return call(service.install, identifier, **body.model_dump())

    @app.get("/api/spelling/vocabulary")
    def vocabulary():
        return call(service.vocabulary)

    # Body-dependent: with the feature hidden, removing private words stays possible.
    @app.put("/api/spelling/vocabulary")
    def save_vocabulary(body: Vocabulary):
        return call(
            service.save_vocabulary,
            additions=profile.enabled("spelling"),
            **body.model_dump(),
        )

    @app.get("/api/projects/{identifier}/spelling/vocabulary")
    def project_vocabulary(identifier: str):
        return call(service.vocabulary, identifier)

    @app.put("/api/projects/{identifier}/spelling/vocabulary")
    def save_project_vocabulary(identifier: str, body: Vocabulary):
        return call(
            service.save_vocabulary,
            project_id=identifier,
            additions=profile.enabled("spelling"),
            **body.model_dump(),
        )
