"""Frozen native allocation, independent of saved connection/approval settings."""

from copy import deepcopy

from .native_capture import validate_allocation as validate


def effective(base_settings, params):
    """Copy the base settings, changing only the explicitly frozen submission product."""
    allocation = validate(params)
    settings = deepcopy(base_settings)
    if allocation is not None:
        setup = settings.get("ucloud_transcriber")
        if settings.get("provider_type") != "ucloud" or not isinstance(setup, dict):
            raise ValueError("A frozen native allocation requires its native UCloud connection.")
        setup["product"] = allocation["product"]
    return settings
