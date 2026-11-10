"""Atomic Working-role decisions for manual edits of an exact saved head."""

from . import result_roles
from .history import validate_revision


class Conflict(ValueError):
    """The viewed head or Working decision changed before this edit committed."""


def prepare(db, store, recording_id, head, choice, *, automatic=True, first=False):
    """Capture the role inside the edit transaction, refusing stale user choices."""
    state = result_roles.read(db, store, recording_id, verify_tail=True)
    if choice is not None:
        if type(choice) is not dict or set(choice) != {
            "version_id",
            "role_revision",
            "make_working",
        }:
            raise ValueError("Choose whether the edited version should be Working.")
        validate_revision(choice["role_revision"])
        if type(choice["make_working"]) is not bool or not isinstance(choice["version_id"], str):
            raise ValueError("Choose a valid Working decision.")
        if (
            head is None
            or choice["version_id"] != head["id"]
            or choice["role_revision"] != state["revision"]
        ):
            raise Conflict("The saved version or Working designation changed. Review again.")
    was_working = bool(head and state["working"] and state["working"]["version_id"] == head["id"])
    if was_working and automatic and choice is not None and not choice["make_working"]:
        raise Conflict("Edits to the current Working version must remain Working. Review again.")
    if first and not state["working"] and state["revision"] == 0:
        decision = "first"
    elif was_working and automatic:
        decision = "automatic"
    elif choice is None:
        decision = None
    elif choice["make_working"]:
        decision = "consented"
    else:
        decision = "keep"
    return state, decision


def apply(db, store, recording_id, version, prepared):
    state, decision = prepared
    if decision in {"automatic", "first"}:
        if not result_roles.move_automatic(
            db,
            store,
            recording_id,
            version["id"],
            state,
            "working_auto_first" if decision == "first" else "working_auto_edit",
        ):
            raise Conflict("The Working designation changed. Review the edit again.")
    elif decision == "consented":
        result_roles.move_consented(db, store, recording_id, version["id"], state)
    elif decision == "keep":
        result_roles.keep_consented(db, store, recording_id, state)
