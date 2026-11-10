"""Account discovery uses synthetic metadata and never allocates or transfers files."""

import copy
import json

import httpx
import pytest

from cbs_transcriber.providers import Settings
from cbs_transcriber.ucloud_discovery import discover, native

REAL_CLIENT = httpx.Client
SECRET = "synthetic-discovery-secret"
REFERENCE = {"name": "transcriber-batch", "version": "1.12"}
PRODUCT = {"id": "gpu-nvidia-b200-1-gpu", "category": "gpu", "provider": "ucloud"}
APPLICATION = {
    "metadata": {**REFERENCE, "title": "Transcriber Batch"},
    "invocation": {
        "applicationType": "BATCH",
        "tool": {
            "name": "transcriber-tool",
            "version": "1.12",
            "tool": {"description": {"backend": "DOCKER", "supportedProviders": ["ucloud"]}},
        },
        "parameters": [
            {"name": "input_file", "type": "input_file", "optional": True},
            {"name": "output_dir", "type": "input_directory", "optional": True},
            {
                "name": "model",
                "type": "enumeration",
                "options": [{"name": "large-v3", "value": "large-v3"}],
            },
            {"name": "language", "type": "text", "optional": True},
            {
                "name": "output_format",
                "type": "enumeration",
                "options": [{"name": "JSON", "value": "json"}],
            },
        ],
    },
}


def settings(**setup):
    return Settings(
        provider_type="ucloud",
        base_url="https://cloud.invalid",
        model="large-v3",
        ucloud_transcriber=setup,
    )


@pytest.fixture
def account(monkeypatch):
    class Account:
        def __init__(self):
            self.calls = []
            self.error = None
            self.repeat_cursor = False
            self.extra_versions = []
            self.groups = {}
            self.key = SECRET
            self.tokens = []
            self.token_error = None
            self.template_product = copy.deepcopy(PRODUCT)
            self.projects = [
                {
                    "id": "project-one",
                    "specification": {"title": "Research", "canConsumeResources": True},
                    "status": {"archived": False},
                }
            ]
            self.drives = [
                {
                    "id": "drive-one",
                    "specification": {"title": "Project files", "product": {"provider": "ucloud"}},
                    "permissions": {"myself": ["EDIT"]},
                    "status": {"resolvedSupport": {"support": {"files": {"isReadOnly": False}}}},
                }
            ]
            self.applications = [copy.deepcopy(APPLICATION)]
            self.products = [
                {
                    "product": {
                        "name": PRODUCT["id"],
                        "category": {"name": PRODUCT["category"], "provider": "ucloud"},
                        "gpu": 1,
                        "memoryInGigs": 96,
                    },
                    "support": {"product": copy.deepcopy(PRODUCT), "docker": {"enabled": True}},
                }
            ]
            self.wallets = [
                {
                    "owner": {"projectId": "project-one"},
                    "paysFor": {"name": "gpu", "provider": "ucloud", "productType": "COMPUTE"},
                    "maxUsable": 1000,
                }
            ]

        def respond(self, request):
            self.calls.append(request)
            assert request.url.host == "cloud.invalid"
            assert request.headers["authorization"] == "Bearer " + self.key
            if self.error:
                return httpx.Response(self.error, text=SECRET + " upstream private detail")
            path = request.url.path
            if path == "/api/projects/v2/browse":
                assert "project" not in request.headers
                return httpx.Response(200, json={"items": self.projects})
            if path == "/api/tokens/browse":
                assert "project" not in request.headers
                if self.token_error:
                    return httpx.Response(self.token_error, text=SECRET + " private body")
                return httpx.Response(200, json={"items": self.tokens})
            assert request.headers["project"] == "project-one"
            if path == "/api/hpc/apps/search":
                assert request.method == "POST"
                payload = json.loads(request.content)
                assert payload["discovery"] == "AVAILABLE"
                return httpx.Response(
                    200,
                    json={
                        "items": self.applications,
                        **({"next": "repeated"} if self.repeat_cursor else {}),
                    },
                )
            assert request.method == "GET", "Discovery must not create or delete remote data."
            # UCloud apiRetrieve(request, base, "groups") builds retrieveGroups.
            if path == "/api/hpc/apps/groups":
                return httpx.Response(404)
            if path == "/api/hpc/apps/retrieveGroups":
                assert request.url.params["discovery"] == "AVAILABLE"
                group = self.groups.get(int(request.url.params["id"]))
                return (
                    httpx.Response(200, json=group)
                    if group is not None
                    else httpx.Response(404, text=SECRET + " private application response")
                )
            if path == "/api/hpc/apps/byNameAndVersion":
                matches = [
                    app
                    for app in self.applications + self.extra_versions
                    if app["metadata"]["name"] == request.url.params["appName"]
                    and app["metadata"]["version"] == request.url.params["appVersion"]
                ]
                return httpx.Response(200, json=matches[0])
            if path == "/api/hpc/tools/byNameAndVersion":
                return httpx.Response(200, json=APPLICATION["invocation"]["tool"]["tool"])
            if path == "/api/jobs/retrieveProducts":
                return httpx.Response(200, json={"productsByProvider": {"ucloud": self.products}})
            if path == "/api/accounting/v2/browseWallets":
                return httpx.Response(200, json={"items": self.wallets})
            if path == "/api/files/collections/browse":
                return httpx.Response(200, json={"items": self.drives})
            if path == "/api/files/retrieve":
                target = request.url.params["id"]
                if target != "/drive-one":
                    return httpx.Response(404)
                return httpx.Response(
                    200,
                    json={
                        "id": target,
                        "status": {"type": "DIRECTORY"},
                        "permissions": {"myself": ["EDIT"]},
                    },
                )
            if path == "/api/jobs/retrieve":
                assert request.url.params["id"] == "chosen-template"
                return httpx.Response(
                    200,
                    json={
                        "specification": {
                            "application": REFERENCE,
                            "product": self.template_product,
                            "replicas": 1,
                            "parameters": {},
                            "resources": [],
                        },
                        "status": {"resolvedApplication": APPLICATION},
                    },
                )
            raise AssertionError("Unexpected discovery request: " + path)

    account = Account()
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda *a, **kw: REAL_CLIENT(*a, **kw, transport=httpx.MockTransport(account.respond)),
    )
    return account


def test_token_only_resolves_unique_account_without_creating_anything(account):
    configured = settings()
    before = configured.model_dump()
    result = discover(configured, SECRET)
    assert result["ready"]
    assert result["setup"]["project_id"] == "project-one"
    assert result["setup"]["application"] == REFERENCE
    assert result["setup"]["product"] == PRODUCT
    assert result["setup"]["template_job_id"] == ""
    assert result["setup"]["storage_path"] == "/drive-one/CBS-Transcriber"
    assert "created when transcription starts" in result["note"]
    assert all(text in result["note"] for text in ("Research", PRODUCT["id"], "/drive-one/"))
    assert SECRET not in json.dumps(result)
    assert configured.model_dump() == before
    assert not any(r.url.path in {"/api/jobs", "/api/files/folder"} for r in account.calls)


def test_observed_web_batch_flavor_resolves_camel_case_fields(
    account, current_transcriber_application, monkeypatch
):
    account.applications = [current_transcriber_application]
    built = []
    build = native.build_specification

    def capture(*args):
        value = build(*args)
        built.append(value)
        return value

    monkeypatch.setattr(native, "build_specification", capture)
    configured = settings()
    before = configured.model_dump()
    result = discover(configured, SECRET)
    assert result["ready"]
    assert result["setup"]["application"] == {"name": "transcriber", "version": "1.12"}
    for field, value in [
        ("input_parameter", "inputFile"),
        ("output_parameter", "outputDir"),
        ("format_parameter", "outputFormat"),
    ]:
        assert result["setup"][field] == value
    values = built[0]["parameters"]
    assert values["jupyterLab"] == {"type": "boolean", "value": False}
    assert values["inputFile"]["path"].endswith("/check/recording.wav")
    assert values["outputDir"]["path"].endswith("/check/out")
    assert values["outputFormat"] == {"type": "text", "value": "json"}
    assert "initScript" not in values and "inputDir" not in values
    assert configured.model_dump() == before
    assert not any(r.url.path in {"/api/jobs", "/api/files/upload"} for r in account.calls)


@pytest.mark.parametrize(
    "change", ["gui", "gui-no-parameters", "unknown-web", "no-switch", "wrong-switch-type"]
)
def test_only_verified_web_batch_contract_is_accepted(
    account, current_transcriber_application, change
):
    application = current_transcriber_application
    if change in {"gui", "gui-no-parameters"}:
        application["metadata"]["name"] = "transcriber-gui"
        if change == "gui-no-parameters":
            application["invocation"]["parameters"] = None
    elif change == "unknown-web":
        application["metadata"]["flavorName"] = "Default"
    else:
        switch = next(
            p for p in application["invocation"]["parameters"] if p["name"] == "jupyterLab"
        )
        if change == "no-switch":
            application["invocation"]["parameters"].remove(switch)
        else:
            switch["type"] = "text"
    account.applications = [application]
    result = discover(settings(), SECRET)
    assert not result["ready"] and not result["choices"]["application"]


def test_current_batch_does_not_replace_explicit_advanced_parameter_names(
    account, current_transcriber_application
):
    account.applications = [current_transcriber_application]
    result = discover(settings(input_parameter="my_explicit_input"), SECRET)
    assert not result["ready"]
    assert result["setup"]["input_parameter"] == "my_explicit_input"


@pytest.mark.parametrize("metadata", [{"groupId": 42}, {"group": {"metadata": {"id": 42}}}])
def test_discovery_expands_default_flavor_to_find_batch_application(account, metadata):
    gui = copy.deepcopy(APPLICATION)
    gui["metadata"].update(name="transcriber-default", **metadata)
    gui["invocation"]["applicationType"] = "WEB"
    account.applications = [gui]
    account.extra_versions = [copy.deepcopy(APPLICATION)]
    account.groups[42] = {
        "metadata": {"id": 42},
        "status": {"applications": [gui, APPLICATION]},
    }
    result = discover(settings(), SECRET)
    assert result["ready"]
    assert result["setup"]["application"] == REFERENCE
    assert sum(r.url.path == "/api/hpc/apps/retrieveGroups" for r in account.calls) == 1


def test_token_project_is_selected_from_exact_server_owned_metadata(account):
    account.key = "uc2a-" + SECRET
    account.projects.append({**account.projects[0], "id": "project-two"})
    account.tokens = [
        {"id": "41", "owner": {"project": "project-two"}},
        {"id": "42", "owner": {"project": "project-one"}, "specification": {"provider": None}},
    ]
    result = discover(settings(), account.key)
    assert result["ready"]
    assert result["setup"]["project_id"] == "project-one"
    assert account.key not in json.dumps(result)
    assert any(r.url.path == "/api/tokens/browse" for r in account.calls)


@pytest.mark.parametrize("problem", ["not-listed", "personal", "no-permission", "unsupported"])
def test_token_without_verified_project_ownership_retains_named_choices(account, problem):
    account.key = "uc2a-" + SECRET
    account.projects.append({**account.projects[0], "id": "project-two"})
    if problem == "personal":
        account.tokens = [{"id": "42", "owner": {"project": None}}]
    elif problem in {"no-permission", "unsupported"}:
        account.token_error = 403 if problem == "no-permission" else 404
    result = discover(settings(), account.key)
    assert not result["ready"]
    assert len(result["choices"]["project_id"]) == 2
    assert not result["setup"]["project_id"]
    assert all(r.method == "GET" for r in account.calls)


@pytest.mark.parametrize("owner", ["unknown-project", None])
def test_unusable_or_duplicate_token_metadata_cannot_choose_a_cost_destination(account, owner):
    account.key = "uc2a-" + SECRET
    account.projects.append({**account.projects[0], "id": "project-two"})
    account.tokens = [{"id": "42", "owner": {"project": owner}}]
    if owner is None:
        account.tokens *= 2
        with pytest.raises(ValueError, match="token project lookup"):
            discover(settings(), account.key)
    else:
        result = discover(settings(), account.key)
        assert len(result["choices"]["project_id"]) == 2
        assert not result["setup"]["project_id"]
    assert all(r.method == "GET" for r in account.calls)


def test_saved_explicit_project_is_preserved_without_token_metadata_lookup(account):
    account.key = "uc2a-" + SECRET
    account.projects.append({**account.projects[0], "id": "project-two"})
    assert discover(settings(project_id="project-one"), account.key)["ready"]
    assert not any(r.url.path == "/api/tokens/browse" for r in account.calls)


@pytest.mark.parametrize("later_failure", [False, True])
def test_token_project_lookup_reads_all_pages_before_accepting_ownership(account, later_failure):
    account.key = "uc2a-" + SECRET
    account.projects.append({**account.projects[0], "id": "project-two"})
    owned = {"id": "42", "owner": {"project": "project-one"}}
    respond = account.respond

    def paginated(request):
        response = respond(request)
        if request.url.path != "/api/tokens/browse":
            return response
        if request.url.params.get("next"):
            return (
                httpx.Response(503, text=SECRET + " private body")
                if later_failure
                else httpx.Response(200, json={"items": [owned]})
            )
        return httpx.Response(200, json={"items": [owned] if later_failure else [], "next": "p2"})

    account.respond = paginated
    if later_failure:
        with pytest.raises(ValueError, match="token project lookup") as caught:
            discover(settings(), account.key)
        assert SECRET not in str(caught.value)
        assert not any(r.url.path.startswith("/api/hpc/apps/") for r in account.calls)
    else:
        assert discover(settings(), account.key)["ready"]
    assert sum(r.url.path == "/api/tokens/browse" for r in account.calls) == 2


@pytest.mark.parametrize("status", [401, 503])
def test_token_metadata_authentication_and_outage_errors_are_not_empty_choices(account, status):
    account.key = "uc2a-" + SECRET
    account.projects.append({**account.projects[0], "id": "project-two"})
    account.token_error = status
    with pytest.raises(ValueError, match=rf"token project lookup \(HTTP {status}\)") as caught:
        discover(settings(), account.key)
    assert SECRET not in str(caught.value)
    assert "private body" not in str(caught.value)


def test_application_404_identifies_stage_without_upstream_details(account):
    account.applications[0]["metadata"]["groupId"] = 42
    with pytest.raises(
        ValueError, match=r"Transcriber application discovery \(HTTP 404\)"
    ) as caught:
        discover(settings(), SECRET)
    assert SECRET not in str(caught.value)
    assert "private application response" not in str(caught.value)


@pytest.mark.parametrize(
    "group",
    [
        {"metadata": {"id": 99}, "status": {"applications": []}},
        {"metadata": {"id": 42}, "status": {}},
    ],
)
def test_malformed_application_group_is_an_error_not_an_empty_catalog(account, group):
    account.applications[0]["metadata"]["groupId"] = 42
    account.groups[42] = group
    with pytest.raises(ValueError, match="setup could not be verified"):
        discover(settings(), SECRET)
    assert not any(r.url.path == "/api/files/collections/browse" for r in account.calls)


def test_discovery_endpoint_does_not_save_or_echo_draft_key(account, client, credentials):
    before_keys = credentials.keys.copy()
    before_providers = client.get("/api/providers").json()
    response = client.post(
        "/api/providers/discover-ucloud",
        json={
            **settings().model_dump(),
            "api_key": SECRET,
            "remember": True,
            "native_allocation_choice": "full",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["ready"]
    assert SECRET not in response.text
    assert credentials.keys == before_keys
    assert client.get("/api/providers").json() == before_providers
    assert all(
        request.method == "GET" or request.url.path == "/api/hpc/apps/search"
        for request in account.calls
    )


def test_multiple_projects_require_choice_before_cost_destination_is_selected(account):
    account.projects.append({**account.projects[0], "id": "project-two"})
    result = discover(settings(), SECRET)
    assert not result["ready"]
    assert [p["id"] for p in result["choices"]["project_id"]] == ["project-one", "project-two"]
    assert len(account.calls) == 1


@pytest.mark.parametrize("problem", ["unfunded", "wrong-owner", "cpu", "backend-disabled"])
def test_unavailable_or_unallocated_products_are_not_offered(account, problem):
    if problem == "unfunded":
        account.wallets[0]["maxUsable"] = 0
    elif problem == "wrong-owner":
        account.wallets[0]["owner"]["projectId"] = "other-project"
    elif problem == "cpu":
        account.products[0]["product"]["gpu"] = 0
    else:
        account.products[0]["support"]["docker"]["enabled"] = False
    result = discover(settings(), SECRET)
    assert not result["ready"]
    assert "product" not in result["choices"]
    assert not any(r.url.path == "/api/files/collections/browse" for r in account.calls)


def test_gpu_choices_do_not_treat_host_ram_as_gpu_memory_or_pick_mig(account):
    account.products[0]["product"]["name"] = "gpu-small"
    account.products[0]["support"]["product"]["id"] = "gpu-small"
    another = copy.deepcopy(account.products[0])
    another["product"].update(name="gpu-mig", memoryInGigs=32)
    another["support"]["product"] = {**PRODUCT, "id": "gpu-mig"}
    account.products.append(another)
    result = discover(settings(), SECRET)
    assert not result["ready"]
    assert "product" not in result["choices"]
    assert PRODUCT["id"] in result["note"]
    assert "product" not in result["setup"] or result["setup"]["product"] is None


def test_v76_preferred_b200_resolves_compute_without_user_choice(account):
    other = copy.deepcopy(account.products[0])
    other["product"].update(name="gpu-mig", memoryInGigs=512)
    other["support"]["product"]["id"] = "gpu-mig"
    account.products.insert(0, other)
    configured = settings()
    before = configured.model_dump()
    result = discover(configured, SECRET)
    assert result["ready"]
    assert result["setup"]["product"] == PRODUCT
    assert "product" not in result["choices"]
    assert configured.model_dump() == before
    explicit = discover(settings(product=other["support"]["product"]), SECRET)
    assert explicit["ready"]
    assert explicit["setup"]["product"] == PRODUCT


def test_v76_unavailable_b200_blocks_setup_without_a_compute_chooser(account):
    account.products[0]["product"]["name"] = "other-gpu"
    account.products[0]["support"]["product"]["id"] = "other-gpu"
    result = discover(settings(), SECRET)
    assert not result["ready"]
    assert result["setup"]["product"] is None
    assert PRODUCT["id"] in result["note"]
    assert "product" not in result["choices"]


def test_v76_b200_identity_ambiguity_blocks_setup_without_a_compute_chooser(account):
    other = copy.deepcopy(account.products[0])
    other["product"]["category"]["name"] = "other-category"
    other["support"]["product"]["category"] = "other-category"
    account.products.append(other)
    wallet = copy.deepcopy(account.wallets[0])
    wallet["paysFor"]["name"] = "other-category"
    account.wallets.append(wallet)
    result = discover(settings(), SECRET)
    assert not result["ready"]
    assert "product" not in result["choices"]
    assert result["setup"]["product"] is None
    assert PRODUCT["id"] in result["note"]


def test_selected_product_and_writable_drive_can_resolve_ambiguity(account):
    another = copy.deepcopy(account.drives[0])
    another["id"] = "drive-two"
    account.drives.append(another)
    result = discover(settings(), SECRET)
    assert not result["ready"]
    assert [d["id"] for d in result["choices"]["storage_path"]] == [
        "/drive-one/CBS-Transcriber",
        "/drive-two/CBS-Transcriber",
    ]
    ready = discover(settings(product=PRODUCT, storage_path="/drive-one/chosen"), SECRET)
    assert ready["ready"]
    assert ready["setup"]["storage_path"] == "/drive-one/chosen"


def test_gui_app_is_excluded_and_multiple_batch_variants_require_choice(account):
    gui = copy.deepcopy(APPLICATION)
    gui["metadata"]["name"] = "transcriber-gui"
    gui["invocation"]["applicationType"] = "WEB"
    account.applications.append(gui)
    assert discover(settings(), SECRET)["ready"]
    batch = copy.deepcopy(APPLICATION)
    batch["metadata"]["name"] = "transcriber-batch-alt"
    account.applications.append(batch)
    result = discover(settings(), SECRET)
    assert not result["ready"]
    assert len(result["choices"]["application"]) == 2
    assert discover(settings(application=REFERENCE), SECRET)["ready"]


def test_explicit_template_keeps_its_machine_and_uses_advanced_path(account):
    result = discover(settings(template_job_id="chosen-template"), SECRET)
    assert result["ready"]
    assert result["setup"]["template_job_id"] == "chosen-template"
    assert result["setup"]["product"] == PRODUCT
    assert not any(r.url.path == "/api/hpc/apps/search" for r in account.calls)


def test_v76_non_b200_template_is_not_offered_for_new_setup(account):
    account.template_product["id"] = "other-gpu"
    result = discover(settings(template_job_id="chosen-template"), SECRET)
    assert not result["ready"]
    assert result["setup"]["product"] is None
    assert not result["choices"]
    assert PRODUCT["id"] in result["note"]


def test_tool_reference_is_resolved_when_metadata_does_not_embed_it(account):
    del account.applications[0]["invocation"]["tool"]["tool"]
    assert discover(settings(), SECRET)["ready"]
    assert any(r.url.path == "/api/hpc/tools/byNameAndVersion" for r in account.calls)


def test_saved_app_version_remains_usable_when_search_shows_only_latest(account):
    previous = copy.deepcopy(APPLICATION)
    previous["metadata"]["version"] = "1.11"
    account.extra_versions.append(previous)
    chosen = {**REFERENCE, "version": "1.11"}
    result = discover(settings(application=chosen), SECRET)
    assert result["ready"]
    assert result["setup"]["application"] == chosen
    assert not any(r.url.path == "/api/hpc/apps/search" for r in account.calls)


def test_other_provider_cannot_send_its_credentials_to_ucloud(account):
    with pytest.raises(ValueError, match="Choose the UCloud Transcriber"):
        discover(Settings(), SECRET)
    assert not account.calls


@pytest.mark.parametrize("default", [False, {"type": "boolean", "value": False}])
def test_direct_setup_preserves_declared_false_default_and_excludes_file_defaults(
    account, monkeypatch, default
):
    account.applications[0]["invocation"]["parameters"].extend(
        [
            {"name": "jupyterlab", "type": "boolean", "defaultValue": default, "optional": True},
            {
                "name": "initialization",
                "type": "input_file",
                "defaultValue": {"type": "file", "path": "/other/script.sh"},
                "optional": True,
            },
        ]
    )
    build = native.build_specification
    built = []

    def capture(*args):
        result = build(*args)
        built.append(result)
        return result

    monkeypatch.setattr(native, "build_specification", capture)
    assert discover(settings(), SECRET)["ready"]
    assert built[0]["parameters"]["jupyterlab"] == {"type": "boolean", "value": False}
    assert "initialization" not in built[0]["parameters"]


def test_unresolved_required_parameter_fails_before_upload_or_allocation(account):
    account.applications[0]["invocation"]["parameters"].append(
        {"name": "required_new_setting", "type": "text", "optional": False}
    )
    with pytest.raises(ValueError, match="could not be verified"):
        discover(settings(), SECRET)
    assert not any(r.url.path in {"/api/jobs", "/api/files/upload"} for r in account.calls)


def test_model_alias_is_checked_against_application_enumeration_before_upload(account):
    model = account.applications[0]["invocation"]["parameters"][2]
    model["options"][0]["value"] = "whisper/large-v3"
    assert discover(settings(), SECRET)["ready"]
    model["options"][0]["value"] = "base"
    with pytest.raises(ValueError, match="could not be verified"):
        discover(settings(), SECRET)


def test_missing_backend_support_is_not_ready(account):
    account.products[0]["support"].pop("docker")
    result = discover(settings(), SECRET)
    assert not result["ready"]
    assert "product" not in result["choices"]


@pytest.mark.parametrize("status", [401, 403, 404, 429, 503])
def test_upstream_credentials_and_private_bodies_are_redacted(account, status):
    account.error = status
    with pytest.raises(ValueError) as caught:
        discover(settings(), SECRET)
    assert SECRET not in str(caught.value)
    assert "private detail" not in str(caught.value)
    assert f"HTTP {status}" in str(caught.value)
    assert "during project listing" in str(caught.value)


@pytest.mark.parametrize("error", [httpx.ReadTimeout, httpx.ConnectError])
def test_v75_wrapped_project_connection_failure_retains_safe_reason(account, error):
    def fail(request):
        raise error(SECRET + " private endpoint detail", request=request)

    account.respond = fail
    with pytest.raises(ValueError) as caught:
        discover(settings(), SECRET)
    message = str(caught.value)
    assert "during project listing" in message
    assert ("timed out" if error is httpx.ReadTimeout else "connection failed") in message
    assert SECRET not in message and "private endpoint detail" not in message


def test_repeated_pagination_fails_instead_of_hanging(account):
    account.repeat_cursor = True
    with pytest.raises(ValueError, match="could not be verified"):
        discover(settings(), SECRET)
    assert sum(r.url.path == "/api/hpc/apps/search" for r in account.calls) == 2


def test_discovery_does_not_treat_failed_listing_as_empty_account(account):
    account.projects = "malformed"
    with pytest.raises(ValueError, match="could not be verified"):
        discover(settings(), SECRET)


def test_missing_token_never_contacts_account(account):
    with pytest.raises(ValueError, match="API token"):
        discover(settings(), "")
    assert not account.calls
