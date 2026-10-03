"""调用覆盖（Call Coverage）引擎与 HTTP API 测试。"""
import io
import json
import os
import zipfile

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from app.main import app
    with TestClient(app) as c:
        yield c


def _zip_bytes(folder: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(folder):
            for fn in files:
                full = os.path.join(root, fn)
                z.write(full, os.path.relpath(full, folder))
    return buf.getvalue()


def _ndjson(rows) -> bytes:
    return ("\n".join(
        r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)
        for r in rows)).encode()


@pytest.fixture(scope="module")
def analysis_id(client):
    r = client.post(
        "/api/analyses",
        files={
            "baseline": ("b.zip", _zip_bytes("examples/baseline"),
                         "application/zip"),
            "candidate": ("c.zip", _zip_bytes("examples/candidate"),
                          "application/zip"),
        })
    assert r.status_code == 201, r.text
    return r.json()["analysis_id"]


def _upload(client, aid, side, rows):
    return client.post(
        f"/api/analyses/{aid}/coverage/{side}",
        files={"file": ("calls.ndjson", _ndjson(rows),
                        "application/x-ndjson")})


# ============================================================== engine cases
def _analysis_from_docs(client, baseline: dict, candidate=None):
    candidate = candidate or baseline
    r = client.post(
        "/api/analyses",
        files={
            "baseline": ("b.json", json.dumps(baseline).encode(),
                         "application/json"),
            "candidate": ("c.json", json.dumps(candidate).encode(),
                          "application/json"),
        })
    assert r.status_code == 201, r.text
    return r.json()["analysis_id"]


PET_LIST_OP = {
    "parameters": [
        {"name": "status", "in": "query",
         "schema": {"type": "string", "enum": ["a", "b"]}},
        {"name": "limit", "in": "query", "required": False,
         "schema": {"type": "integer", "minimum": 1, "maximum": 100}},
    ],
    "responses": {
        "200": {"description": "ok", "content": {"application/json": {
            "schema": {"type": "array", "items": {
                "type": "object", "required": ["id"],
                "properties": {"id": {"type": "integer"}}}}}}},
        "400": {"description": "bad"},
        "default": {"description": "err", "content": {"application/json": {
            "schema": {"type": "object"}}}},
    },
}


def _spec(ops):
    return {"openapi": "3.0.3", "info": {"title": "b", "version": "1"},
            "paths": ops}


def simple_spec():
    return _spec({"/p": {"get": {"responses": {"200": {"description": "d"}}}}})


def test_covered_grid_and_gaps(client):
    aid = _analysis_from_docs(client, _spec({"/pets": {"get": PET_LIST_OP}}))
    rows = [
        {"method": "GET", "path": "/pets", "query": {"limit": "10"},
         "request": {},
         "response": {"status": 200, "media_type": "application/json",
                      "body": [{"id": 1}]}},
        {"method": "GET", "path": "/pets", "request": {},
         "response": {"status": 400}},
    ]
    r = _upload(client, aid, "baseline", rows)
    assert r.status_code == 201, r.text
    doc = r.json()
    s = doc["summary"]
    assert s["cells_total"] == 3
    assert s["cells_covered"] == 2
    assert s["cells_gap"] == 1
    op = doc["operations"][0]
    all_keys = {c["key"] for c in op["cells"]} | \
        {c["key"] for c in op["gaps"]}
    assert all_keys == {
        "200|application/json", "400|(no content)",
        "default|application/json"}
    assert op["covered"] == 2 and op["total"] == 3
    gap_keys = {c["key"] for c in op["gaps"]}
    assert gap_keys == {"default|application/json"}


def test_status_resolution_exact_range_default(client):
    spec = _spec({"/p": {"get": {"responses": {
        "200": {"description": "ok", "content": {"application/json": {
            "schema": {"type": "object"}}}},
        "2XX": {"description": "range", "content": {"application/json": {
            "schema": {"type": "object"}}}},
        "default": {"description": "def", "content": {"application/json": {
            "schema": {"type": "object"}}}}}}}})
    aid = _analysis_from_docs(client, spec)
    rows = [
        {"method": "GET", "path": "/p",
         "response": {"status": 200, "media_type": "application/json",
                      "body": {}}},
        {"method": "GET", "path": "/p",
         "response": {"status": 204, "media_type": "application/json",
                      "body": {}}},
        {"method": "GET", "path": "/p",
         "response": {"status": 500, "media_type": "application/json",
                      "body": {}}},
    ]
    doc = _upload(client, aid, "baseline", rows).json()
    by_status = {r["resolved_status"]: r["outcome"]
                 for r in doc["rows"] if r["outcome"] == "covered"}
    assert by_status == {"200": "covered", "2XX": "covered",
                         "default": "covered"}


def test_no_content_cell_and_entity_mismatch(client):
    spec = _spec({"/p": {"get": {"responses": {"204": {"description": "x"}}}}})
    aid = _analysis_from_docs(client, spec)
    doc = _upload(client, aid, "baseline", [
        {"method": "GET", "path": "/p", "response": {"status": 204}},
        {"method": "GET", "path": "/p",
         "response": {"status": 204, "media_type": "application/json",
                      "body": {}}},
    ]).json()
    assert doc["rows"][0]["outcome"] == "covered"
    assert doc["rows"][1]["outcome"] == "validation_failed"
    assert any("无 content" in x for x in doc["rows"][1]["reasons"])


def test_exact_path_precedence(client):
    spec = _spec({
        "/pets/search": {"get": {"responses": {"200": {"description": "d"}}}},
        "/pets/{id}": {"get": {"responses": {"200": {"description": "d"}}}},
    })
    aid = _analysis_from_docs(client, spec)
    doc = _upload(client, aid, "baseline", [
        {"method": "GET", "path": "/pets/search",
         "response": {"status": 200}},
    ]).json()
    row = doc["rows"][0]
    assert row["outcome"] == "covered"
    assert row["operation"] == "GET /pets/search"


def test_ambiguous_template(client):
    spec = _spec({
        "/a/{x}": {"get": {"responses": {"200": {"description": "d"}}}},
        "/a/{y}": {"get": {"responses": {"200": {"description": "d"}}}},
    })
    aid = _analysis_from_docs(client, spec)
    doc = _upload(client, aid, "baseline", [
        {"method": "GET", "path": "/a/v", "response": {"status": 200}},
    ]).json()
    row = doc["rows"][0]
    assert row["outcome"] == "ambiguous"
    assert "同时命中" in row["reasons"][0]
    assert doc["summary"]["calls_ambiguous"] == 1


def test_unmatched_wrong_method_then_no_template(client):
    spec = _spec({"/p": {"get": {"responses": {"200": {"description": "d"}}}}})
    aid = _analysis_from_docs(client, spec)
    doc = _upload(client, aid, "baseline", [
        {"method": "POST", "path": "/p", "response": {"status": 200}},
        {"method": "GET", "path": "/q", "response": {"status": 200}},
    ]).json()
    assert doc["rows"][0]["outcome"] == "unmatched"
    assert "方法" in doc["rows"][0]["reasons"][0]
    assert doc["rows"][1]["outcome"] == "unmatched"


def test_parameter_validation_coercion_and_required(client):
    spec = _spec({"/p": {"get": {"parameters": [
        {"name": "n", "in": "query", "required": True,
         "schema": {"type": "integer", "minimum": 1, "maximum": 100}}],
        "responses": {"200": {"description": "d"}}}}})
    aid = _analysis_from_docs(client, spec)
    doc = _upload(client, aid, "baseline", [
        {"method": "GET", "path": "/p", "query": {"n": "7"},
         "response": {"status": 200}},                       # coerce ok
        {"method": "GET", "path": "/p",
         "response": {"status": 200}},                       # missing req
        {"method": "GET", "path": "/p", "query": {"n": "x"},
         "response": {"status": 200}},                       # bad value
    ]).json()
    assert [r["outcome"] for r in doc["rows"]] == [
        "covered", "validation_failed", "validation_failed"]


def test_path_param_validation(client):
    spec = _spec({"/p/{id}": {"get": {"parameters": [
        {"name": "id", "in": "path", "required": True,
         "schema": {"type": "integer"}}],
        "responses": {"200": {"description": "d"}}}}})
    aid = _analysis_from_docs(client, spec)
    doc = _upload(client, aid, "baseline", [
        {"method": "GET", "path": "/p/42", "response": {"status": 200}},
        {"method": "GET", "path": "/p/abc", "response": {"status": 200}},
    ]).json()
    assert doc["rows"][0]["outcome"] == "covered"
    assert doc["rows"][1]["outcome"] == "validation_failed"


def test_request_body_required_and_schema(client):
    body_schema = {"type": "object", "required": ["name"],
                   "properties": {"name": {"type": "string", "minLength": 1}}}
    op = {
        "requestBody": {"required": True, "content": {
            "application/json": {"schema": body_schema}}},
        "responses": {"201": {"description": "c", "content": {
            "application/json": {"schema": {"type": "object"}}}}},
    }
    spec = _spec({"/p": {"post": op}})
    aid = _analysis_from_docs(client, spec)
    doc = _upload(client, aid, "baseline", [
        {"method": "POST", "path": "/p",
         "request": {"media_type": "application/json",
                     "body": {"name": "x"}},
         "response": {"status": 201, "media_type": "application/json",
                      "body": {}}},
        {"method": "POST", "path": "/p", "request": {},
         "response": {"status": 201, "media_type": "application/json",
                      "body": {}}},
        {"method": "POST", "path": "/p",
         "request": {"media_type": "application/json", "body": {}},
         "response": {"status": 201, "media_type": "application/json",
                      "body": {}}},
    ]).json()
    assert [r["outcome"] for r in doc["rows"]] == [
        "covered", "validation_failed", "validation_failed"]


def test_non_json_media_excluded_and_zero_denominator(client):
    spec = _spec({
        "/xml": {"get": {"responses": {"200": {"description": "d",
            "content": {"application/xml": {"schema": {
                "type": "string"}}}}}}},
        "/json": {"get": {"responses": {"200": {"description": "d",
            "content": {"application/json": {"schema": {
                "type": "object"}}}}}}},
    })
    aid = _analysis_from_docs(client, spec)
    doc = _upload(client, aid, "baseline", [
        {"method": "GET", "path": "/xml",
         "response": {"status": 200, "media_type": "application/xml",
                      "body": "<x/>"}},
        {"method": "GET", "path": "/json",
         "response": {"status": 200, "media_type": "application/json",
                      "body": {}}},
    ]).json()
    assert doc["rows"][0]["outcome"] == "excluded_media"
    assert doc["rows"][1]["outcome"] == "covered"
    ops = {o["path"]: o for o in doc["operations"]}
    assert ops["/xml"]["applicable"] is False
    assert ops["/xml"]["note"]
    assert ops["/json"]["applicable"] is True
    # the xml operation's excluded media is reported
    assert ops["/xml"]["excluded_media"][0]["media_type"] == "application/xml"


def test_undetermined_for_unresolvable_schema(client):
    spec = _spec({"/p": {"get": {"parameters": [
        {"name": "t", "in": "query",
         "schema": {"$ref": "https://e.example.com/s#/T"}}],
        "responses": {"200": {"description": "d"}}}}})
    aid = _analysis_from_docs(client, spec)
    doc = _upload(client, aid, "baseline", [
        {"method": "GET", "path": "/p", "query": {"t": "v"},
         "response": {"status": 200}},
    ]).json()
    row = doc["rows"][0]
    assert row["outcome"] == "undetermined"
    assert any("无法判定" in x for x in row["reasons"])


def test_invalid_lines_are_listed(client):
    simple = _spec({"/p": {"get": {"responses": {"200": {"description": "d"}}}}})
    aid = _analysis_from_docs(client, simple)
    doc = _upload(client, aid, "baseline", [
        "not-a-json-line",
        json.dumps({"method": "GET", "path": "/p",
                    "response": {"status": 200}}),
        json.dumps({"method": "WAT", "path": "/p",
                    "response": {"status": 200}}),
        json.dumps({"method": "GET", "path": "p",
                    "response": {"status": 200}}),
        json.dumps({"method": "GET", "path": "/p",
                    "response": {"status": 999}}),
    ]).json()
    outcomes = {r["line"]: r["outcome"] for r in doc["rows"]}
    assert outcomes[1] == "invalid"
    assert outcomes[2] == "covered"
    assert outcomes[3] == "invalid"
    assert outcomes[4] == "invalid"
    assert outcomes[5] == "invalid"
    assert doc["summary"]["calls_invalid"] == 4


def test_empty_file_rejected(client):
    aid = _analysis_from_docs(client, simple_spec())
    r = client.post(
        f"/api/analyses/{aid}/coverage/baseline",
        files={"file": ("empty.ndjson", b"", "application/x-ndjson")})
    assert r.status_code == 400


def test_response_status_undeclared_fails(client):
    aid = _analysis_from_docs(client, simple_spec())
    doc = _upload(client, aid, "baseline", [
        {"method": "GET", "path": "/p", "response": {"status": 404}},
    ]).json()
    assert doc["rows"][0]["outcome"] == "validation_failed"
    assert any("未在操作响应中声明" in x for x in doc["rows"][0]["reasons"])


# ============================================================== HTTP surface
def test_coverage_404_unknown_analysis(client):
    r = _upload(client, "nope", "baseline", [
        json.dumps({"method": "GET", "path": "/p",
                    "response": {"status": 200}})])
    assert r.status_code == 404


def test_coverage_bad_side(client, analysis_id):
    r = client.post(
        f"/api/analyses/{analysis_id}/coverage/sideways",
        files={"file": ("c.ndjson", b"{}", "application/x-ndjson")})
    assert r.status_code == 400


def test_coverage_upload_get_export_and_embed(client, analysis_id):
    with open("examples/calls-baseline.ndjson", "rb") as fh:
        raw = fh.read()
    r = client.post(
        f"/api/analyses/{analysis_id}/coverage/baseline",
        files={"file": ("calls-baseline.ndjson", raw,
                        "application/x-ndjson")})
    assert r.status_code == 201, r.text
    doc = r.json()
    assert doc["analysis_side"] == "baseline"
    assert doc["summary"]["calls_covered"] >= 4

    # list endpoint reports availability
    lst = client.get(
        f"/api/analyses/{analysis_id}/coverage").json()
    sides = {s["side"]: s for s in lst["sides"]}
    assert sides["baseline"]["available"] is True
    assert sides["candidate"]["available"] is False

    # GET single side
    g = client.get(
        f"/api/analyses/{analysis_id}/coverage/baseline")
    assert g.status_code == 200
    assert g.json()["summary"]["cells_total"] == doc["summary"]["cells_total"]

    # GET missing side -> 404
    assert client.get(
        f"/api/analyses/{analysis_id}/coverage/candidate").status_code == 404

    # deterministic export
    e1 = client.get(
        f"/api/analyses/{analysis_id}/coverage/baseline/export")
    e2 = client.get(
        f"/api/analyses/{analysis_id}/coverage/baseline/export")
    assert e1.content == e2.content
    import hashlib
    assert hashlib.sha256(e1.content).hexdigest() == \
        e1.headers["x-coverage-fingerprint"]
    parsed = json.loads(e1.content)
    assert parsed["rows"] == doc["rows"]

    # coverage embedded in main export and stays deterministic
    m1 = client.get(f"/api/analyses/{analysis_id}/export").content
    m2 = client.get(f"/api/analyses/{analysis_id}/export").content
    assert m1 == m2
    embedded = json.loads(m1)
    assert "call_coverage" in embedded
    assert "baseline" in embedded["call_coverage"]
    assert hashlib.sha256(m1).hexdigest() == \
        client.get(f"/api/analyses/{analysis_id}/export")\
        .headers["x-report-fingerprint"]


def test_coverage_overwrite_and_delete(client, analysis_id):
    good = [{"method": "GET", "path": "/pets",
             "request": {},
             "response": {"status": 200, "media_type": "application/json",
                          "body": []}}]
    assert _upload(client, analysis_id, "candidate", good).status_code == 201
    doc = client.get(
        f"/api/analyses/{analysis_id}/coverage/candidate").json()
    assert doc["summary"]["calls_total"] == 1

    # re-upload replaces
    assert _upload(client, analysis_id, "candidate",
                   good + good).status_code == 201
    doc2 = client.get(
        f"/api/analyses/{analysis_id}/coverage/candidate").json()
    assert doc2["summary"]["calls_total"] == 2

    d = client.delete(
        f"/api/analyses/{analysis_id}/coverage/candidate")
    assert d.status_code == 200
    assert client.get(
        f"/api/analyses/{analysis_id}/coverage/candidate").status_code == 404
    # baseline still present
    assert client.get(
        f"/api/analyses/{analysis_id}/coverage/baseline").status_code == 200


def test_coverage_survives_analysis_reload(client, analysis_id):
    g = client.get(
        f"/api/analyses/{analysis_id}/coverage/baseline")
    assert g.status_code == 200
    cells = g.json()["summary"]["cells_total"]
    assert cells > 0
