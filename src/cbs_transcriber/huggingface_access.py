"""Explicit, metadata-only HF credential checks; no models or recordings are transferred."""

import hashlib
from datetime import UTC, datetime

import httpx

SPEAKER_MODEL = "pyannote/speaker-diarization-community-1"


def token_fingerprint(token):
    """Bind transient check receipts to a credential without retaining its plaintext."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_state(token):
    return {
        "has_token": bool(token),
        "token_status": "unchecked" if token else "missing",
        "speaker_model": SPEAKER_MODEL,
        "speaker_model_access": "unknown",
        "checked_at": None,
    }


def check_token(token, *, client=None):
    result = token_state(token)
    if not token:
        return result
    if len(token) > 4096 or any(c.isspace() or ord(c) < 32 for c in token):
        raise ValueError("Use a Hugging Face access token without spaces or line breaks.")
    owned = client is None
    client = client or httpx.Client(timeout=15, follow_redirects=False)
    try:
        result["checked_at"] = datetime.now(UTC).isoformat()
        headers = {"Authorization": "Bearer " + token}
        response = client.get(
            "https://huggingface.co/api/whoami-v2", headers=headers, follow_redirects=False
        )
        if response.status_code in {401, 403}:
            result["token_status"] = "invalid"
            return result
        if response.status_code != 200:
            result["token_status"] = "unavailable"
            return result
        # A successful identity request establishes authentication. Deliberately do not
        # return its body, which contains personal account and token permission details.
        result["token_status"] = "valid"
        response = client.get(
            "https://huggingface.co/api/models/" + SPEAKER_MODEL + "/auth-check",
            headers=headers,
            follow_redirects=False,
        )
        result["speaker_model_access"] = (
            "allowed"
            if response.status_code == 200
            else "denied"
            if response.status_code in {401, 403, 404}
            else "unavailable"
        )
        return result
    except httpx.HTTPError:
        if result["token_status"] != "valid":
            result["token_status"] = "unavailable"
        result["speaker_model_access"] = "unavailable"
        return result
    finally:
        if owned:
            client.close()
