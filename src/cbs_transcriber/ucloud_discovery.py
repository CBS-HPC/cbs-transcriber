"""Read-only native Transcriber setup using the released UCloud client helpers."""

import re

import httpx
from ucloud_workflow import Settings as CloudSettings
from ucloud_workflow import UCloudAPIError, UCloudClient
from ucloud_workflow.resources import ManagementError, browse_all, product_reference
from ucloud_workflow.setup import (
    list_projects,
    list_writable_drives,
    prepare_storage_directory,
    storage_directory_path,
    validate_server,
)

from . import ucloud_native as native


def _object(value):
    if not isinstance(value, dict):
        raise ValueError("Malformed UCloud discovery metadata.")
    return value


def _response(client, method, path, **kwargs):
    return _object(client.request(method, path, **kwargs).json())


def _client(settings, key, project=""):
    # Do not use from_env(): this workflow must never inspect another project's .env.
    return UCloudClient(CloudSettings(server=settings.base_url, token=key, project=project))


def _failure(stage, error):
    # Released management helpers suppress display of their context, but retain it.
    # Inspect types/status only; upstream exception text can contain tokens or bodies.
    for _ in range(8):
        if isinstance(error, UCloudAPIError):
            status = error.status_code
            hint = {
                401: "The token was rejected; check that it is valid and has not expired.",
                403: "Access was denied; check the token's permissions.",
                404: "The requested UCloud endpoint or resource was not found.",
                429: "UCloud is rate limiting requests; wait before trying again.",
            }.get(status, "Check UCloud service availability and try again.")
            return ValueError(
                f"UCloud discovery failed during {stage} (HTTP {status}). "
                f"{hint} No job was started."
            )
        if isinstance(error, httpx.HTTPError):
            reason = (
                "request timed out"
                if isinstance(error, httpx.TimeoutException)
                else ("connection failed")
            )
            return ValueError(
                f"UCloud {reason} during {stage}. "
                "Check connectivity to the UCloud server and try again. No job was started."
            )
        if not isinstance(error, ManagementError):
            break
        error = error.__cause__ or error.__context__
    return ValueError(
        f"UCloud setup could not be verified during {stage}. "
        "Unexpected or incomplete setup metadata; check account access and retry. "
        "No job was started."
    )


def _result(setup, note, *, ready=False, choices=None):
    return {"ready": ready, "setup": setup, "choices": choices or {}, "note": note}


def _token_project(client, key):
    # The public uc<hex-id>- prefix identifies a metadata record, not its rights.
    # Only an exact record returned by UCloud can supply a project association.
    reference = re.fullmatch(r"uc([0-9a-fA-F]{1,16})-[^\s]+", key)
    if reference is None:
        return ""
    identifier = str(int(reference[1], 16))
    try:
        first_page = client.browse_api_tokens()
        tokens = browse_all(
            lambda next_page=None: (
                _response(
                    client,
                    "GET",
                    "/api/tokens/browse",
                    params={"itemsPerPage": 100, "filterHidden": "false", "next": next_page},
                )
                if next_page
                else first_page
            )
        )
    except UCloudAPIError as exc:
        if exc.status_code in {403, 404}:
            return ""  # Optional metadata unavailable: retain explicit project choice.
        raise
    matches = [item for item in tokens if item.get("id") == identifier]
    if not matches:
        return ""
    if len(matches) != 1:
        raise ValueError("Ambiguous UCloud token metadata.")
    project = _object(matches[0].get("owner")).get("project")
    if project is None:
        return ""
    if not isinstance(project, str) or not project:
        raise ValueError("Malformed UCloud token project metadata.")
    return project


def _applications(client, setup):
    applications = {}
    # A saved version need not appear in a search returning only the latest flavor.
    summaries = (
        [{"metadata": setup["application"]}]
        if setup.get("application")
        else browse_all(
            lambda next_page=None: _response(
                client,
                "POST",
                "/api/hpc/apps/search",
                json={
                    "query": "Transcriber",
                    "itemsPerPage": 100,
                    "discovery": "AVAILABLE",
                    **({"next": next_page} if next_page else {}),
                },
            )
        )
    )
    # Search returns a group's default flavor, which can be the interactive app.
    # Expand available siblings before checking the Batch contract; pinned saves
    # still resolve only their exact application/version.
    if not setup.get("application"):
        groups = set()
        for item in tuple(summaries):
            metadata = _object(item.get("metadata"))
            group_id = metadata.get("groupId")
            if group_id is None and metadata.get("group") is not None:
                group_id = _object(_object(metadata["group"]).get("metadata")).get("id")
            if group_id is None or group_id == -1:
                continue
            if type(group_id) is not int or group_id < 0:
                raise ValueError("Malformed UCloud application group identity.")
            if group_id in groups:
                continue
            groups.add(group_id)
            group = _response(
                client,
                "GET",
                "/api/hpc/apps/retrieveGroups",
                params={"id": group_id, "discovery": "AVAILABLE"},
            )
            if _object(group.get("metadata")).get("id") != group_id:
                raise ValueError("UCloud application group identity changed during discovery.")
            flavors = _object(group.get("status")).get("applications")
            if not isinstance(flavors, list) or any(not isinstance(p, dict) for p in flavors):
                raise ValueError("Malformed UCloud application flavors.")
            summaries.extend(flavors)
    seen = set()
    for item in summaries:
        metadata = _object(item.get("metadata"))
        name, version = metadata.get("name"), metadata.get("version")
        if not isinstance(name, str) or "transcrib" not in name.lower():
            continue
        if not isinstance(version, str) or not version:
            raise ValueError("Transcriber application has no pinned version.")
        identity = (name, version)
        if identity in seen:
            continue
        seen.add(identity)
        application = _response(
            client,
            "GET",
            "/api/hpc/apps/byNameAndVersion",
            params={"appName": name, "appVersion": version},
        )
        resolved = _object(application.get("metadata"))
        if (resolved.get("name"), resolved.get("version")) != identity:
            raise ValueError("UCloud application identity changed during discovery.")
        names = native.application_parameters(application)
        try:
            overrides = native.batch_overrides(application, names)
        except ValueError:
            continue
        resolved = dict(setup)
        if overrides:
            for field, legacy, current in [
                ("input_parameter", "input_file", "inputFile"),
                ("output_parameter", "output_dir", "outputDir"),
                ("format_parameter", "output_format", "outputFormat"),
            ]:
                if resolved[field] == legacy and legacy not in names and current in names:
                    resolved[field] = current
        if names.get(resolved["input_parameter"], {}).get("type") != "input_file":
            continue
        if names.get(resolved["output_parameter"], {}).get("type") != "input_directory":
            continue
        if not names.get(resolved["model_parameter"]):
            continue
        applications[identity] = (application, resolved)
    return applications


def _tool_description(client, application):
    tool = _object(_object(application.get("invocation")).get("tool"))
    resolved = tool.get("tool")
    if resolved is None:
        if not all(isinstance(tool.get(k), str) and tool[k] for k in ("name", "version")):
            raise ValueError("Transcriber tool identity is missing.")
        resolved = _response(
            client,
            "GET",
            "/api/hpc/tools/byNameAndVersion",
            params={"name": tool["name"], "version": tool["version"]},
        )
    return _object(_object(resolved).get("description"))


def _wallets(client):
    return browse_all(
        lambda next_page=None: (
            _response(
                client,
                "GET",
                "/api/accounting/v2/browseWallets",
                params={"includeChildren": "false", "itemsPerPage": 100, "next": next_page},
            )
            if next_page
            else client.browse_wallets(include_children=False)
        )
    )


def _products(client, application, project):
    description = _tool_description(client, application)
    backend = {"DOCKER": "docker", "SINGULARITY": "singularity", "NATIVE": "native"}.get(
        description.get("backend")
    )
    if not backend:
        raise ValueError("Transcriber application has no supported batch backend.")
    supported_providers = description.get("supportedProviders") or []
    if not isinstance(supported_providers, list) or any(
        not isinstance(value, str) for value in supported_providers
    ):
        raise ValueError("Malformed Transcriber provider metadata.")
    funded = set()
    for wallet in _wallets(client):
        category = _object(wallet.get("paysFor"))
        owner = _object(wallet.get("owner"))
        balance = wallet.get("maxUsable")
        if (
            owner.get("projectId") == project
            and category.get("productType") == "COMPUTE"
            and type(balance) is int
            and balance > 0
        ):
            funded.add((category.get("name"), category.get("provider")))
    providers = _object(_object(client.retrieve_compute_products()).get("productsByProvider"))
    products = {}
    for provider, entries in providers.items():
        if not isinstance(entries, list) or any(not isinstance(item, dict) for item in entries):
            raise ValueError("Malformed UCloud compute products.")
        if supported_providers and provider not in supported_providers:
            continue
        for entry in entries:
            product, support = _object(entry.get("product")), _object(entry.get("support"))
            category = _object(product.get("category"))
            reference = product_reference(
                support.get("product")
                or {
                    "id": product.get("name"),
                    "category": category.get("name"),
                    "provider": provider,
                }
            )
            if (
                reference["provider"] != provider
                or category.get("provider", provider) != provider
                or product.get("name", reference["id"]) != reference["id"]
                or category.get("name", reference["category"]) != reference["category"]
            ):
                raise ValueError("UCloud compute product identity mismatch.")
            if (
                type(product.get("gpu")) is not int
                or product["gpu"] < 1
                or _object(support.get(backend, {})).get("enabled") is not True
                or (
                    category.get("freeToUse") is not True
                    and (reference["category"], provider) not in funded
                )
            ):
                continue
            identity = tuple(reference[k] for k in ("id", "category", "provider"))
            if identity in products:
                raise ValueError("Duplicate UCloud compute product identity.")
            name = f"{reference['id']} · {provider}"
            if product.get("memoryInGigs"):
                name += f" · {product['memoryInGigs']} GB host RAM"
            products[identity] = {"id": "@".join(identity), "name": name, "product": reference}
    # Host RAM is not GPU memory. Never infer that the smallest MIG slice fits this model.
    return list(products.values())


def discover(settings, key, *, compute_id=None):
    """Resolve unique account choices without uploading, creating folders, or starting jobs."""
    if not key:
        raise ValueError("Add your UCloud API token to discover Transcriber setup.")
    if settings.provider_type != "ucloud" or settings.ucloud_transcriber is None:
        raise ValueError("Choose the UCloud Transcriber provider before connecting a token.")
    validate_server(settings.base_url)
    setup = settings.ucloud_transcriber.model_dump()
    compute_id = compute_id or (
        native.MIG_PRODUCT["id"]
        if (setup.get("product") or {}).get("id") == native.MIG_PRODUCT["id"]
        else native.COMPUTE_ID
    )
    if compute_id not in {native.COMPUTE_ID, native.MIG_PRODUCT["id"]}:
        raise ValueError("Choose full B200 or the verified native 1g MIG allocation.")
    stage = "project listing"
    try:
        with _client(settings, key) as client:
            projects = list_projects(client)
            if not setup["project_id"] and len(projects) > 1:
                stage = "token project lookup"
                owner_project = _token_project(client, key)
                if any(p["id"] == owner_project for p in projects):
                    setup["project_id"] = owner_project
        if setup["project_id"]:
            if not any(p["id"] == setup["project_id"] for p in projects):
                raise ValueError("Configured project is not an eligible account project.")
        elif len(projects) == 1:
            setup["project_id"] = projects[0]["id"]
        else:
            return _result(
                setup,
                "Choose the UCloud project to use."
                if projects
                else "No resource-enabled UCloud project is available to this token.",
                choices={
                    "project_id": [
                        {"id": p["id"], "name": p["specification"].get("title", p["id"])}
                        for p in projects
                    ]
                },
            )
        project_title = next(
            p["specification"].get("title", p["id"])
            for p in projects
            if p["id"] == setup["project_id"]
        )
        with _client(settings, key, setup["project_id"]) as client:
            stage = "Transcriber application discovery"
            if setup["template_job_id"]:
                configured = settings.model_copy(
                    update={"ucloud_transcriber": native.NativeSetup.model_validate(setup)}
                )
                specification, _ = native.template_info(client, configured)
                application = client.retrieve_job(
                    setup["template_job_id"], include_updates=False, include_application=True
                )["status"]["resolvedApplication"]
                setup["application"] = specification["application"]
                setup["product"] = specification["product"]
            else:
                applications = _applications(client, setup)
                selected = setup.get("application")
                if selected:
                    match = applications.get((selected.get("name"), selected.get("version")))
                    if match is None:
                        raise ValueError("Selected Transcriber Batch application is unavailable.")
                    application, setup = match
                elif len(applications) == 1:
                    application, setup = next(iter(applications.values()))
                    metadata = application["metadata"]
                    setup["application"] = {k: metadata[k] for k in ("name", "version")}
                else:
                    return _result(
                        setup,
                        "Choose a Transcriber Batch application."
                        if applications
                        else "No compatible Transcriber Batch application is available.",
                        choices={
                            "application": [
                                {
                                    "id": name + "@" + version,
                                    "name": a["metadata"].get("title", name) + " · " + version,
                                    "application": {"name": name, "version": version},
                                }
                                for (name, version), (a, _) in applications.items()
                            ]
                        },
                    )
            stage = "GPU allocation lookup"
            products = _products(client, application, setup["project_id"])
            matches = [
                p
                for p in products
                if p["product"]["id"] == compute_id
                and (compute_id != native.MIG_PRODUCT["id"] or p["product"] == native.MIG_PRODUCT)
            ]
            if setup["template_job_id"] and (setup.get("product") or {}).get("id") != compute_id:
                setup["product"] = None
                return _result(
                    setup,
                    f"The template must use {compute_id}. "
                    "Clear the advanced template field to configure it automatically.",
                )
            if any(p["product"] == setup.get("product") for p in matches):
                pass  # An existing full B200 identity can disambiguate categories/providers.
            elif len(matches) == 1:
                setup["product"] = matches[0]["product"]
            else:
                setup["product"] = None
                return _result(
                    setup,
                    f"More than one compatible {compute_id} allocation was returned. "
                    "Check the project's allocation configuration. No compute was selected."
                    if matches
                    else f"Required compute {compute_id} is not available as a compatible "
                    "funded allocation in this project. Check the project and its allocation.",
                )
            stage = "writable storage lookup"
            drives = [
                drive
                for drive in list_writable_drives(client)
                if _object(_object(drive.get("specification")).get("product")).get("provider")
                == setup["product"]["provider"]
            ]
            if setup["storage_path"]:
                drive_id = setup["storage_path"].split("/")[1]
                if not any(d["id"] == drive_id for d in drives):
                    raise ValueError("Storage is not a writable drive on the selected provider.")
                storage_directory_path(drive_id, setup["storage_path"])
            elif len(drives) == 1:
                setup["storage_path"] = f"/{drives[0]['id']}/CBS-Transcriber"
            else:
                return _result(
                    setup,
                    "Choose a writable UCloud drive."
                    if drives
                    else "No writable drive is available on the selected compute provider.",
                    choices={
                        "storage_path": [
                            {
                                "id": f"/{drive['id']}/CBS-Transcriber",
                                "name": drive["specification"].get("title", drive["id"]),
                            }
                            for drive in drives
                        ]
                    },
                )
            stage = "storage folder validation"
            missing = prepare_storage_directory(client, setup["storage_path"], create=False)
            stage = "Transcriber parameter validation"
            configured = settings.model_copy(
                update={"ucloud_transcriber": native.NativeSetup.model_validate(setup)}
            )
            specification, parameters = native.template_info(client, configured)
            native.build_specification(
                configured,
                specification,
                parameters,
                setup["storage_path"] + "/check",
                ".wav",
                "check",
            )
        return _result(
            setup,
            f"Ready: {project_title} · {setup['product']['id']} · {setup['storage_path']}. "
            "No files uploaded or job started. "
            + (
                "The app's storage folder will be created when transcription starts. "
                if missing
                else ""
            )
            + "GPU memory and execution still require a live run.",
            ready=True,
        )
    except (
        UCloudAPIError,
        httpx.HTTPError,
        ManagementError,
        ValueError,
        TypeError,
        KeyError,
    ) as exc:
        raise _failure(stage, exc) from None
