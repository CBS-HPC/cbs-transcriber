"""Release profile: which unfinished features a build offers for new work (ADR 0145).

One central switch decides the effective profile when the app starts: the ``--profile`` command
line option, then ``CBS_TRANSCRIBER_PROFILE``, then the packaged ``catalogs/release.json``, then
``full``. An unknown name refuses startup. A hidden feature never offers new work: the browser
hides its entry points and the server refuses to start it with a plain 403. Existing results,
history, exports and recovery controls (Stop, Retry, Reconnect, cleanup) stay available.
"""

import argparse
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

ENVIRONMENT = "CBS_TRANSCRIBER_PROFILE"
PACKAGED = Path(__file__).parent / "catalogs" / "release.json"
CODE = "feature_disabled"

# Plain names for refusals; keys are the browser's data-feature values.
FEATURES = {
    "retranscribe": "Re-transcribing passages",
    "topics": "Topics & Sentiments",
    "research_report": "The research report",
    "cbs_runtime": "CBS Runtime transcription",
    "ucloud_enhancement": "UCloud processing for word timing and speakers",
    "routing_rules": "Changing the Automatic processing rules",
    "runtime_python": "Choosing another Python for local processing",
    "editor_realign": "Realigning a passage after saving",
    "wording_adoption": "Using wording from a comparison",
    "cloud_backup": "UCloud backup upload and retention",
    "restore_ui": "Restoring a backup",
    "custom_providers": "Setting up a custom or UCloud vLLM provider",
    "activity_diagnostics": "Inline Activity routing details",
}
PROFILES = {"full": frozenset(), "mvp": frozenset(FEATURES)}
LABEL = re.compile(r"[A-Za-z0-9 ._()+-]{1,40}")
HIDDEN_PROVIDER_TYPES = {"custom": "custom_providers", "ucloud-vllm": "custom_providers"}

# Routes that only start new work for one feature. Reads, exports and recovery of existing
# work (Stop, Retry, Reconnect, cleanup, abandon, reconcile) are deliberately absent.
# Body-dependent refusals (provider types, UCloud placement, CBS Runtime models) live in
# their handlers.
_ID = r"[^/]+"
GUARDED_ROUTES = tuple(
    (method, re.compile(pattern), feature)
    for method, pattern, feature in (
        ("POST", rf"/api/recordings/{_ID}/retranscriptions", "retranscribe"),
        ("POST", rf"/api/projects/{_ID}/codebook/versions", "topics"),
        ("POST", rf"/api/recordings/{_ID}/annotation-runs(?:/import|/{_ID}/items)?", "topics"),
        ("PATCH", rf"/api/recordings/{_ID}/annotation-runs/{_ID}/items/{_ID}", "topics"),
        ("POST", rf"/api/projects/{_ID}/research-report", "research_report"),
        ("POST", r"/api/enhancement-api/(?:check|prepare)", "cbs_runtime"),
        ("POST", r"/api/enhancement-cloud/check", "cbs_runtime"),
        ("PUT", rf"/api/providers/{_ID}/runtime-language", "cbs_runtime"),
        ("PUT", r"/api/runtime-batches/settings", "cbs_runtime"),
        ("PUT", r"/api/enhancement-routing", "routing_rules"),
        (
            "POST",
            rf"/api/recordings/{_ID}/transcripts/{_ID}/wording-(?:preview|adopt)",
            "wording_adoption",
        ),
        ("POST", r"/api/library/portable-simple/(?:prepare|folder|admit)", "cloud_backup"),
        ("PUT", r"/api/library/portable-storage", "cloud_backup"),
        ("POST", r"/api/library/portable-upload", "cloud_backup"),
        ("PUT", r"/api/library/portable-retention", "cloud_backup"),
        ("POST", r"/api/library/portable-retention/(?:preview|apply)", "cloud_backup"),
        ("POST", r"/api/library/portable-restore", "restore_ui"),
    )
)


def guarded(method, path):
    """The feature a request would start new work for, if it is one of the fixed routes."""
    return next(
        (
            feature
            for verb, pattern, feature in GUARDED_ROUTES
            if verb == method and pattern.fullmatch(path)
        ),
        None,
    )


class FeatureDisabled(Exception):
    """New work for a feature this release profile hides."""

    def __init__(self, feature):
        self.feature = feature
        super().__init__(
            f"{FEATURES[feature]} is not available in this version of CBS Transcriber."
        )


@dataclass(frozen=True)
class Profile:
    name: str
    disabled: frozenset
    label: str | None = None

    def enabled(self, feature):
        if feature not in FEATURES:
            raise KeyError(feature)
        return feature not in self.disabled

    def require(self, feature):
        if not self.enabled(feature):
            raise FeatureDisabled(feature)

    def place(self, execution):
        """Without UCloud processing, Automatic runs here and an explicit UCloud is refused."""
        if self.enabled("ucloud_enhancement") or execution == "local":
            return execution
        if execution == "ucloud":
            raise FeatureDisabled("ucloud_enhancement")
        return "local"

    def require_provider(self, provider_type, *, runtime=False):
        """New work or setup for a hidden provider kind (custom/vLLM, CBS Runtime model)."""
        if provider_type in HIDDEN_PROVIDER_TYPES:
            self.require(HIDDEN_PROVIDER_TYPES[provider_type])
        if runtime:
            self.require("cbs_runtime")

    def public(self):
        return {
            "profile": self.name,
            "label": self.label,
            "disabled": sorted(self.disabled),
            "features": sorted(FEATURES),
        }


def named(name, label=None):
    if name not in PROFILES:
        raise ValueError(f"Unknown release profile {name!r}. Choose one of: {', '.join(PROFILES)}.")
    if label is not None and (not isinstance(label, str) or not LABEL.fullmatch(label)):
        raise ValueError("A release label uses up to 40 letters, digits, spaces or ._()+-.")
    return Profile(name, PROFILES[name], label)


def packaged(path=PACKAGED):
    """The build's own choice; an absent file means the full profile."""
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return named("full")
    except (OSError, ValueError) as exc:
        raise ValueError(f"The packaged release profile {path} is unreadable: {exc}") from None
    if not isinstance(value, dict) or set(value) - {"profile", "label"}:
        raise ValueError(f"The packaged release profile {path} is invalid.")
    return named(value.get("profile", "full"), value.get("label"))


def resolve(choice=None, *, environ=None, path=PACKAGED):
    """Command line, then environment, then the packaged file, then full."""
    if isinstance(choice, Profile):
        return choice
    environ = os.environ if environ is None else environ
    build = packaged(path)
    override = (choice or environ.get(ENVIRONMENT, "")).strip().lower()
    # The build's label (for bug reports) describes only the build's own profile.
    return build if not override or override == build.name else named(override)


def write(name, label=None, path=PACKAGED):
    """Write a packaged profile; the release branch commits the result (ADR 0145)."""
    profile = named(name, label)
    value = {"profile": profile.name, **({"label": label} if label else {})}
    Path(path).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8", newline="\n")
    return profile


def main(argv=None):
    parser = argparse.ArgumentParser(description="Set the packaged CBS Transcriber release profile")
    parser.add_argument("profile", choices=sorted(PROFILES))
    parser.add_argument("--label", help="Shown in Settings for bug reports, e.g. 'MVP rc1'")
    parser.add_argument("--file", type=Path, default=PACKAGED, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        profile = write(args.profile, args.label, args.file)
    except ValueError as exc:
        parser.error(str(exc))
    print(f"Release profile {profile.name} written to {args.file}")


if __name__ == "__main__":
    main()
