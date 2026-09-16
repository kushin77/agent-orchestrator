"""The OpenAPI document is generated from ERP-02, and drift is refused by name (#651).

Acceptance criterion 1 is *"``openapi.json`` is generated from ERP-02 schemas —
never hand-maintained"*. A promise in a docstring cannot hold that, so these tests
hold it three ways: the components are compared to a fresh derivation from the
schema files, the committed artifact is compared byte for byte to a fresh
emission, and a document that has drifted is shown to be refused **naming the file
it drifted from** — each refusal provoked by mutating the real document, never a
synthetic one.
"""

from __future__ import annotations

import copy
import json

import pytest

from integrations.erp.api import errors as err
from integrations.erp.api import openapi
from integrations.erp.core import validators as core_validators


def test_every_component_comes_from_a_schema_file(root):
    """Each component is its ERP-02 source, modulo the $ref rewrite and the $id drop."""
    model = core_validators.load_model(root / openapi.MODEL_ROOT)
    components = openapi.schema_components(model)
    for filename, schema in model.schema_files.items():
        name = openapi.component_name(filename)
        assert name in components
        assert components[name]["title"] == schema["title"]
        assert components[name]["description"] == schema["description"]
        # The provenance record travels with the contract — restating nothing,
        # omitting nothing.
        assert components[name]["x-erp-provenance"] == schema["x-erp-provenance"]
        assert "$id" not in components[name], "the $id is replaced by x-erp-schema-sources"


def test_refs_are_rewritten_into_single_pointers(root):
    """A cross-file ref and a file-local ref both become one pointer into this document."""
    document = openapi.build_document(root)
    sales_order = document["components"]["schemas"]["sales-order"]
    assert sales_order["properties"]["id"]["$ref"] == (
        "#/components/schemas/document/$defs/identifier"
    )
    # A ref that carries a sibling description keeps it (JSON Schema 2020-12 allows it).
    assert sales_order["properties"]["party"]["description"] == "Link to the customer party."


def test_every_ref_resolves(root):
    """The classic generated-document failure: a reference nothing can resolve."""
    document = openapi.build_document(root)
    refs = openapi._walk_refs(document)
    assert len(refs) > 100, "the document should be built from real references"
    for where, ref in refs:
        assert ref.startswith("#"), f"{where}: {ref} leaves the document"
        assert openapi._resolve_pointer(document, ref) is not None, f"{where}: {ref} does not resolve"


def test_the_document_validates(root):
    document = openapi.build_document(root)
    assert openapi.validate_document(document, root) == []


def test_the_committed_artifact_is_a_fresh_emission(root):
    """A stale or hand-edited artifact fails here, which is what 'never hand-maintained' means."""
    artifact = root / openapi.EMITTED_ARTIFACT
    assert artifact.is_file(), f"{openapi.EMITTED_ARTIFACT} must be committed"
    assert artifact.read_text(encoding="utf-8") == openapi.serialize(openapi.build_document(root))


def test_the_emission_is_deterministic(root):
    first = openapi.serialize(openapi.build_document(root))
    second = openapi.serialize(openapi.build_document(root))
    assert first == second
    assert first.endswith("\n")
    assert json.loads(first)["openapi"] == "3.1.0"


def test_the_kind_enum_is_the_models_own(root):
    model = core_validators.load_model(root / openapi.MODEL_ROOT)
    document = openapi.build_document(root)
    parameter = document["components"]["parameters"]["kind"]
    assert parameter["schema"]["enum"] == list(model.document_kinds())


def test_the_document_union_is_the_models_families(root):
    model = core_validators.load_model(root / openapi.MODEL_ROOT)
    union = openapi.build_document(root)["components"]["schemas"]["ErpDocument"]
    mapping = union["discriminator"]["mapping"]
    assert union["discriminator"]["propertyName"] == "doctype"
    assert sorted(mapping) == sorted(model.document_kinds())
    assert union["oneOf"] == [{"$ref": mapping[kind]} for kind in sorted(mapping)]


def test_no_parameter_could_select_a_tenant(root):
    """The structural half of 'no back door': there is no tenant parameter to send."""
    document = openapi.build_document(root)
    names = {str(param.get("name", "")) for _where, param in openapi._walk_parameters(document)}
    assert names & set(openapi.FORBIDDEN_PARAMETERS) == set()
    assert names == {"kind", "documentId", "action"}


def test_the_contract_declares_no_credential_surface(root):
    """GR-6: the surface has no credential, so it declares no security scheme."""
    document = openapi.build_document(root)
    assert "securitySchemes" not in document["components"]
    for path, item in document["paths"].items():
        for method, operation in item.items():
            assert operation["security"] == [], f"{method} {path} claims a security requirement"
    assert document["x-erp-identity"]["scheme"] == "none-in-this-contract"


def test_every_operation_carries_the_whole_refusal_vocabulary(root):
    document = openapi.build_document(root)
    for path, item in document["paths"].items():
        for method, operation in item.items():
            for status in err.STATUSES:
                assert str(status) in operation["responses"], f"{method} {path} omits {status}"


def test_the_reported_size_is_real_bytes(root):
    """The number the gate prints as "bytes" is the artifact's size on disk.

    `len()` on the serialization counts characters, and the ERP-02 descriptions
    carry non-ASCII, so a character count reported as bytes disagreed with the
    `wc -c` on the very next line of the gate.
    """
    document = openapi.build_document(root)
    artifact = (root / openapi.EMITTED_ARTIFACT).read_bytes()
    assert openapi.byte_size(document) == len(artifact)
    assert openapi.byte_size(document) >= len(openapi.serialize(document))


def test_the_transition_response_is_declared_and_carries_no_document(root):
    """The advance reply is a declared shape, and it deliberately carries no document."""
    assert openapi._SUCCESS["advance"] == (200, "TransitionResponse")
    assert "TransitionResponse" in openapi.PAYLOAD_COMPONENTS
    document = openapi.build_document(root)
    component = document["components"]["schemas"]["TransitionResponse"]
    assert "document" not in component["properties"], (
        "a transition carries no fields, so its reply must not describe a field set"
    )
    assert set(component["properties"]) == {"transition", "action"}


def test_the_error_enum_is_the_closed_vocabulary(root):
    document = openapi.build_document(root)
    envelope = document["components"]["schemas"]["Envelope"]
    code_enum = envelope["properties"]["error"]["oneOf"][1]["properties"]["code"]["enum"]
    assert code_enum == list(openapi.error_codes())
    assert set(err.BOUNDARY_CODES) <= set(code_enum)
    assert set(err.MODEL_STATUS) <= set(code_enum)


def test_the_transition_derivation_is_the_workflows(root):
    model = core_validators.load_model(root / openapi.MODEL_ROOT)
    document = openapi.build_document(root)
    derived = document["x-erp-transitions"]
    for kind, states in derived.items():
        workflow = model.workflow_for(kind)
        assert sorted(states) == sorted(workflow.state_names())
        for state, actions in states.items():
            assert actions == list(workflow.actions_from(state))


# --- provoked drift: each refusal must name the offender ----------------------


def test_a_drifted_component_is_refused_naming_its_file(root):
    document = openapi.build_document(root)
    drifted = copy.deepcopy(document)
    drifted["components"]["schemas"]["sales-order"]["properties"]["total"]["minimum"] = 1
    findings = openapi.validate_document(drifted, root)
    assert any(
        "sales-order" in finding and "integrations/erp/core/schemas/sales-order.schema.json" in finding
        for finding in findings
    ), findings


def test_a_dropped_route_is_refused(root):
    document = openapi.build_document(root)
    dropped = copy.deepcopy(document)
    del dropped["paths"]["/v1/erp/documents/{kind}"]["post"]
    findings = openapi.validate_document(dropped, root)
    assert any("missing from the document" in finding for finding in findings), findings


def test_an_invented_route_is_refused(root):
    document = openapi.build_document(root)
    invented = copy.deepcopy(document)
    invented["paths"]["/v1/erp/sprockets"] = {"get": {"operationId": "x", "responses": {}}}
    findings = openapi.validate_document(invented, root)
    assert any("the route table does not declare" in finding for finding in findings), findings


def test_a_tenant_parameter_is_refused(root):
    document = openapi.build_document(root)
    smuggled = copy.deepcopy(document)
    smuggled["components"]["parameters"]["tenant"] = {
        "name": "tenant",
        "in": "path",
        "required": True,
        "schema": {"type": "string"},
    }
    findings = openapi.validate_document(smuggled, root)
    assert any("would let a client select the scope" in finding for finding in findings), findings


def test_a_security_scheme_is_refused(root):
    document = openapi.build_document(root)
    credential = copy.deepcopy(document)
    credential["components"]["securitySchemes"] = {"bearer": {"type": "http", "scheme": "bearer"}}
    findings = openapi.validate_document(credential, root)
    assert any("carries no credential" in finding for finding in findings), findings


def test_a_missing_status_is_refused(root):
    document = openapi.build_document(root)
    thinned = copy.deepcopy(document)
    del thinned["components"]["responses"]["503"]
    findings = openapi.validate_document(thinned, root)
    assert any("503" in finding for finding in findings), findings


def test_a_drifted_transition_is_refused(root):
    document = openapi.build_document(root)
    drifted = copy.deepcopy(document)
    drifted["x-erp-transitions"]["sales-order"]["draft"] = ["sprocket"]
    findings = openapi.validate_document(drifted, root)
    assert any("workflow derivation" in finding for finding in findings), findings


def test_an_unresolvable_ref_is_refused(root):
    document = openapi.build_document(root)
    broken = copy.deepcopy(document)
    broken["paths"]["/v1/erp/health"]["get"]["responses"]["200"]["content"]["application/json"][
        "schema"
    ] = {"$ref": "#/components/schemas/Nowhere"}
    findings = openapi.validate_document(broken, root)
    assert any("does not resolve in this document" in finding for finding in findings), findings


def test_a_restated_identity_is_refused(root):
    document = openapi.build_document(root)
    restated = copy.deepcopy(document)
    restated["x-erp-identity"]["tenantSource"] = "the request"
    findings = openapi.validate_document(restated, root)
    assert any("tenant comes from the principal" in finding for finding in findings), findings
