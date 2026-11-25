"""HTTP boundary for local draft spelling and public dictionary retrieval.

Hidden by the ``spelling`` release-profile feature (ADR 0145/0185): its fixed new-work routes
(settings writes, check, suggest, dictionary retrieval and its Activity tasks) are refused
before these handlers. Reads, dictionary removal, dismissing a finished download card and
vocabulary removals stay available for existing private data. Recording vocabulary follows
ADR 0186; dictionary downloads run as Activity tasks (ADR 0187).
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


class DictionaryRequest(StrictBody):
    settings_revision: int = Field(strict=True, ge=0, lt=2**63)
    consent: bool = Field(default=False, strict=True)
    language: str | None = Field(default=None, pattern=r"^[a-z]{2,8}$")
    recording_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,100}$")


def install(app, service, profile, tasks):
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
        saved = call(service.save_settings, **body.model_dump())
        # Turning checking or automatic downloads on queues each selected language's
        # dictionary; turning them off stops automatic downloads (ADR 0187).
        tasks.preferences_changed()
        return saved

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

    # Activity downloads (ADR 0187): the editor's first use, an explicit consent and Retry.
    @app.get("/api/spelling/dictionary-tasks")
    def dictionary_tasks():
        return {"tasks": tasks.activity()}

    @app.post("/api/spelling/dictionary-tasks", status_code=202)
    def request_dictionaries(body: DictionaryRequest):
        return call(tasks.request, **body.model_dump())

    @app.post("/api/spelling/dictionary-tasks/{identifier}/retry", status_code=202)
    def retry_dictionary(identifier: str):
        return call(tasks.retry, identifier)

    @app.delete("/api/spelling/dictionary-tasks/{identifier}")
    def dismiss_dictionary(identifier: str):
        return call(tasks.dismiss, identifier)

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

    # A recording's own words; its GET also returns the inherited project words (ADR 0186).
    @app.get("/api/recordings/{identifier}/spelling/vocabulary")
    def recording_vocabulary(identifier: str):
        return call(service.vocabulary, recording_id=identifier)

    @app.put("/api/recordings/{identifier}/spelling/vocabulary")
    def save_recording_vocabulary(identifier: str, body: Vocabulary):
        return call(
            service.save_vocabulary,
            recording_id=identifier,
            additions=profile.enabled("spelling"),
            **body.model_dump(),
        )
