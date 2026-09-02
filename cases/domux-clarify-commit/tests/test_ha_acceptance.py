from __future__ import annotations

import copy
import contextlib
import hashlib
import io
import json
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest import mock
from urllib.error import URLError
from urllib.parse import urlparse


CASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CASE_DIR))

import ha_acceptance as ha  # noqa: E402


RUN_ID = "a1b2c3d4e5f6"


def completed(
    command: list[str],
    returncode: int = 0,
    *,
    stdout: bytes = b"",
    stderr: bytes = b"",
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(command, returncode, stdout, stderr)


class FakeDockerRunner:
    """Stateful subprocess.run replacement; it never calls Docker."""

    def __init__(self) -> None:
        self.container_name = f"{ha.CONTAINER_PREFIX}-{RUN_ID}"
        self.volume_name = f"{ha.VOLUME_PREFIX}-{RUN_ID}"
        self.container_exists = False
        self.volume_exists = False
        self.container_label = RUN_ID
        self.volume_label = RUN_ID
        self.commands: list[tuple[str, ...]] = []
        self.cp_archive: bytes | None = None

    def _container_inspect(self) -> dict[str, Any]:
        return {
            "Name": f"/{self.container_name}",
            "Config": {
                "Image": ha.IMAGE_REFERENCE,
                "Labels": {ha.RUN_LABEL: self.container_label},
            },
            "HostConfig": {
                "Memory": ha.MEMORY_LIMIT_BYTES,
                "NanoCpus": ha.NANO_CPUS,
                "NetworkMode": "bridge",
                "PidsLimit": ha.PIDS_LIMIT,
                "PortBindings": {
                    f"{ha.CONTAINER_PORT}/tcp": [
                        {"HostIp": "127.0.0.1", "HostPort": ""}
                    ]
                },
                "Privileged": False,
                "RestartPolicy": {"Name": "no"},
            },
            "Mounts": [
                {
                    "Destination": "/config",
                    "Name": self.volume_name,
                    "RW": True,
                    "Type": "volume",
                }
            ],
        }

    def __call__(
        self,
        command: list[str],
        *,
        input: bytes | None,
        stdout: int,
        stderr: int,
        check: bool,
        timeout: int,
    ) -> subprocess.CompletedProcess[bytes]:
        del stdout, stderr, check, timeout
        self.commands.append(tuple(command))
        self.assert_command_shape(command)
        arguments = command[1:]

        if arguments[:1] == ["pull"]:
            return completed(command, stdout=b"pulled\n")
        if arguments[:2] == ["image", "inspect"]:
            payload = [
                {
                    "Architecture": "amd64",
                    "Config": {
                        "Labels": {"io.hass.version": ha.HOME_ASSISTANT_VERSION}
                    },
                    "Os": "linux",
                    "RepoDigests": [ha.IMAGE_REFERENCE],
                }
            ]
            return completed(command, stdout=json.dumps(payload).encode())
        if arguments[:2] == ["volume", "create"]:
            self.volume_exists = True
            return completed(command, stdout=f"{self.volume_name}\n".encode())
        if arguments[:2] == ["volume", "inspect"]:
            if not self.volume_exists:
                return completed(command, 1, stderr=b"no such volume")
            payload = [
                {
                    "Labels": {ha.RUN_LABEL: self.volume_label},
                    "Name": self.volume_name,
                }
            ]
            return completed(command, stdout=json.dumps(payload).encode())
        if arguments[:1] == ["create"]:
            self.container_exists = True
            return completed(command, stdout=b"synthetic-container-id\n")
        if arguments[:2] == ["container", "inspect"]:
            if not self.container_exists:
                return completed(command, 1, stderr=b"no such container")
            return completed(
                command, stdout=json.dumps([self._container_inspect()]).encode()
            )
        if arguments[:1] == ["cp"]:
            if not self.container_exists or input is None:
                return completed(command, 1, stderr=b"copy failed")
            self.cp_archive = input
            return completed(command)
        if arguments[:1] == ["start"]:
            return completed(command, stdout=f"{self.container_name}\n".encode())
        if arguments[:1] == ["port"]:
            return completed(command, stdout=b"127.0.0.1:34567\n")
        if arguments[:2] == ["rm", "--force"]:
            self.container_exists = False
            return completed(command, stdout=f"{self.container_name}\n".encode())
        if arguments[:2] == ["volume", "rm"]:
            if self.container_exists:
                return completed(command, 1, stderr=b"volume is in use")
            self.volume_exists = False
            return completed(command, stdout=f"{self.volume_name}\n".encode())
        raise AssertionError(f"unexpected synthetic Docker command: {arguments}")

    def assert_command_shape(self, command: list[str]) -> None:
        if not command or command[0] != "docker":
            raise AssertionError("test runner received a non-Docker command")


class FakeRestResponse:
    def __init__(self, payload: object) -> None:
        self.status = 200
        self._raw = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "FakeRestResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._raw


class FakeRestBackend:
    """Mock HA HTTP transport shared by setup and the real REST adapter."""

    def __init__(self) -> None:
        self.states: dict[str, dict[str, Any]] = {
            "light.bed_light": {
                "attributes": {
                    "brightness": 180,
                    "color_temp_kelvin": 2631,
                    "max_color_temp_kelvin": 6500,
                    "min_color_temp_kelvin": 2000,
                    "supported_color_modes": ["color_temp", "hs"],
                },
                "entity_id": "light.bed_light",
                "state": "off",
            },
            "light.ceiling_lights": {
                "attributes": {
                    "brightness": 180,
                    "color_temp_kelvin": 2631,
                    "max_color_temp_kelvin": 6500,
                    "min_color_temp_kelvin": 2000,
                    "supported_color_modes": ["color_temp", "hs"],
                },
                "entity_id": "light.ceiling_lights",
                "state": "on",
            },
            "cover.hall_window": {
                "attributes": {
                    "current_position": 10,
                    "supported_features": 15,
                },
                "entity_id": "cover.hall_window",
                "state": "open",
            },
            "climate.hvac": {
                "attributes": {
                    "fan_mode": "on_high",
                    "fan_modes": ["on_low", "on_high", "auto_low", "auto_high", "off"],
                    "hvac_modes": ["off", "heat", "cool", "dry", "fan_only", "auto"],
                    "max_temp": 35.0,
                    "min_temp": 7.0,
                    "supported_features": 385,
                    "target_temp_step": 1.0,
                    "temperature": 21.0,
                },
                "entity_id": "climate.hvac",
                "state": "cool",
            },
        }
        self.setup_dispatches: list[dict[str, Any]] = []
        self.external_dispatches: list[dict[str, Any]] = []
        self.sut_http_dispatches: list[dict[str, Any]] = []
        self.service_events: list[dict[str, Any]] = []

    def state(self, entity_id: str) -> dict[str, Any]:
        result = json.loads(json.dumps(self.states[entity_id]))
        if entity_id.startswith("light.") and result["state"] == "off":
            # Pinned HA suppresses active light values while an entity is off.
            result["attributes"]["brightness"] = None
            result["attributes"]["color_temp_kelvin"] = None
        return result

    def dispatch(
        self,
        domain: str,
        service: str,
        data: dict[str, Any],
        *,
        phase: str,
    ) -> dict[str, Any]:
        entity_id = str(data["entity_id"])
        state = self.states[entity_id]
        attributes = state["attributes"]
        if domain == "light" and service == "turn_on":
            state["state"] = "on"
            if "brightness_pct" in data:
                attributes["brightness"] = round(float(data["brightness_pct"]) * 255 / 100)
            if "color_temp_kelvin" in data:
                attributes["color_temp_kelvin"] = round(float(data["color_temp_kelvin"]))
        elif domain == "light" and service == "turn_off":
            state["state"] = "off"
        elif domain == "cover" and service == "set_cover_position":
            position = round(float(data["position"]))
            attributes["current_position"] = position
            state["state"] = "closed" if position == 0 else "open"
        elif domain == "climate" and service == "set_hvac_mode":
            state["state"] = str(data["hvac_mode"])
        elif domain == "climate" and service == "set_temperature":
            attributes["temperature"] = float(data["temperature"])
        else:
            raise AssertionError(f"unexpected synthetic service: {domain}.{service}")
        event = {"data": dict(data), "domain": domain, "service": service}
        self.service_events.append({**event, "phase": phase})
        if phase == "setup":
            self.setup_dispatches.append(event)
        elif phase == "external_fault_injection":
            self.external_dispatches.append(event)
        elif phase == "sut":
            self.sut_http_dispatches.append(event)
        else:
            raise AssertionError(f"unexpected synthetic service phase: {phase}")
        return self.state(entity_id)

    def open(self, request: Any, *, timeout: float) -> FakeRestResponse:
        del timeout
        if request.headers.get("Authorization") != "Bearer synthetic-access-secret":
            raise AssertionError("real adapter omitted its Bearer token")
        path = urlparse(request.full_url).path
        if request.method == "GET" and path.startswith("/api/states/"):
            entity_id = path.removeprefix("/api/states/")
            return FakeRestResponse(self.state(entity_id))
        if request.method == "GET" and path == "/api/config":
            return FakeRestResponse({"unit_system": {"temperature": "°C"}})
        if request.method == "POST" and path.startswith("/api/services/"):
            domain, service = path.removeprefix("/api/services/").split("/", 1)
            data = json.loads(request.data)
            self.dispatch(domain, service, data, phase="sut")
            return FakeRestResponse([self.state(str(data["entity_id"]))])
        raise AssertionError(f"unexpected real-adapter request: {request.method} {path}")

    def adapter(
        self, base_url: str, token: str
    ) -> ha.HomeAssistantRESTAdapter:
        adapter = ha.HomeAssistantRESTAdapter(
            base_url,
            token,
            timeout_seconds=1,
            poll_seconds=0.01,
        )
        adapter._opener = self  # type: ignore[attr-defined]
        return adapter


class FakeHomeAssistantApi:
    """In-memory onboarding/setup API; SUT dispatch uses the real adapter."""

    access_token = "synthetic-access-secret"
    refresh_token = "synthetic-refresh-secret"

    def __init__(
        self,
        base_url: str,
        backend: FakeRestBackend | None = None,
    ) -> None:
        self.base_url = base_url
        self.backend = backend or FakeRestBackend()
        self.steps: set[str] = set()
        self.revoked = False
        self.direct_service_calls: list[dict[str, object]] = []

    @staticmethod
    def _onboarding(done: bool) -> list[dict[str, Any]]:
        return [{"step": step, "done": done} for step in ha.ONBOARDING_STEPS]

    def wait_for_readiness(self) -> ha.HttpResult:
        return ha.HttpResult(200, self._onboarding(False))

    def wait_for_entities(self, entity_ids: Any, token: str) -> None:
        self._assert_token(token)
        if set(entity_ids) != set(self.backend.states):
            raise AssertionError("runner requested unexpected entities")

    def wait_for_projection(
        self,
        entity_id: str,
        token: str,
        *,
        state: str,
        attributes: dict[str, object],
    ) -> dict[str, Any]:
        self._assert_token(token)
        actual = self.backend.state(entity_id)
        if actual["state"] != state or any(
            not ha._scalar_matches(actual["attributes"].get(key), value)
            for key, value in attributes.items()
        ):
            raise AssertionError("synthetic setup projection does not match")
        return actual

    def call_service(
        self,
        domain: str,
        service: str,
        data: dict[str, Any],
        token: str,
        *,
        evidence_phase: str,
    ) -> ha.HttpResult:
        self._assert_token(token)
        payload = [
            self.backend.dispatch(
                domain,
                service,
                data,
                phase=evidence_phase,
            )
        ]
        self.direct_service_calls.append(
            ha._direct_service_event(
                evidence_phase,
                domain,
                service,
                f"/api/services/{domain}/{service}",
                200,
            )
        )
        return ha.HttpResult(200, payload)

    def _assert_token(self, token: str | None) -> None:
        if token != self.access_token:
            raise AssertionError("missing synthetic Bearer token")

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        form_body: dict[str, str] | None = None,
        token: str | None = None,
        expected: Any = (200,),
        timeout: float = 30,
    ) -> ha.HttpResult:
        del timeout
        status = 200
        payload: Any = {}
        if method == "GET" and path == "/api/":
            if token is None:
                status, payload = 401, {"message": "Unauthorized"}
            else:
                self._assert_token(token)
                payload = {"message": "API running."}
        elif method == "POST" and path == "/api/onboarding/users":
            if not json_body or set(json_body) != {
                "client_id",
                "language",
                "name",
                "password",
                "username",
            }:
                raise AssertionError("invalid user onboarding body")
            self.steps.add("user")
            payload = {"auth_code": "synthetic-auth-code"}
        elif method == "POST" and path == "/auth/token":
            if not form_body:
                raise AssertionError("missing token form")
            if form_body.get("grant_type") == "authorization_code":
                payload = {
                    "access_token": self.access_token,
                    "expires_in": 1800,
                    "refresh_token": self.refresh_token,
                    "token_type": "Bearer",
                }
            elif form_body.get("grant_type") == "refresh_token":
                if form_body.get("refresh_token") != self.refresh_token or not self.revoked:
                    raise AssertionError("refresh revocation was not tested correctly")
                status, payload = 400, {"error": "invalid_grant"}
            else:
                raise AssertionError("unexpected token grant")
        elif method == "POST" and path == "/api/onboarding/core_config":
            self._assert_token(token)
            self.steps.add("core_config")
        elif method == "POST" and path == "/api/onboarding/integration":
            self._assert_token(token)
            if not json_body or not json_body["redirect_uri"].endswith("/auth-callback"):
                raise AssertionError("invalid integration onboarding body")
            self.steps.add("integration")
            payload = {"auth_code": "synthetic-integration-code"}
        elif method == "POST" and path == "/api/onboarding/analytics":
            self._assert_token(token)
            self.steps.add("analytics")
        elif method == "GET" and path == "/api/onboarding":
            payload = self._onboarding(set(ha.ONBOARDING_STEPS) == self.steps)
        elif method == "GET" and path.startswith("/api/states/"):
            self._assert_token(token)
            entity_id = path.removeprefix("/api/states/")
            payload = self.backend.state(entity_id)
        elif method == "GET" and path == "/api/config":
            self._assert_token(token)
            payload = {"unit_system": {"temperature": "°C"}}
        elif method == "POST" and path.startswith("/api/services/"):
            self._assert_token(token)
            if not json_body:
                raise AssertionError("missing service body")
            domain, service = path.removeprefix("/api/services/").split("/", 1)
            payload = [
                self.backend.dispatch(domain, service, json_body, phase="setup")
            ]
        elif method == "POST" and path == "/auth/revoke":
            if not form_body or form_body.get("token") != self.refresh_token:
                raise AssertionError("wrong refresh token was revoked")
            self.revoked = True
            payload = None
        else:
            raise AssertionError(f"unexpected synthetic HA request: {method} {path}")

        if status not in set(expected):
            raise ha.AcceptanceError(f"{method} {path} returned unexpected HTTP {status}")
        return ha.HttpResult(status, payload)


class FakeDockerRuntime:
    def __init__(self, *, fail: bool = False) -> None:
        self.cleaned = False
        self.fail = fail

    def prepare(self) -> ha.PreparedRuntime:
        if self.fail:
            raise ha.AcceptanceError("synthetic prepare failure")
        return ha.PreparedRuntime(
            base_url="http://127.0.0.1:45678",
            image={
                "architecture": "amd64",
                "docker_healthcheck": False,
                "manifest_digest": ha.IMAGE_DIGEST,
                "operating_system": "linux",
                "repository": ha.IMAGE_REPOSITORY,
                "version": ha.HOME_ASSISTANT_VERSION,
            },
        )

    def cleanup(self) -> None:
        self.cleaned = True


class RecordedDomuxEvidenceTests(unittest.TestCase):
    def test_loader_binds_the_four_exact_v1_command_output_pairs(self) -> None:
        records = ha.load_recorded_domux_evidence()
        expected = {
            ha.DomuxEvidenceKey("eval-duplicate_entity-01", "ambiguous"): (
                "Turn off the light.",
                "turnOff|Light|*|*|*|*|*",
                2,
            ),
            ha.DomuxEvidenceKey("eval-duplicate_entity-02", "clear"): (
                "Set the Curtain in the Hall on the First Floor to 20 percent.",
                "set|Curtain|position|20|Percent|Hall|First Floor",
                3,
            ),
            ha.DomuxEvidenceKey("eval-duplicate_entity-03", "clear"): (
                "Set the AC in the Bedroom on the Second Floor to 22 Celsius.",
                "set|AC|temperature|22|Celsius|Bedroom|Second Floor",
                5,
            ),
            ha.DomuxEvidenceKey("eval-duplicate_entity-04", "clear"): (
                ("Set the Light in the Study on the Ground Floor to 35 percent brightness."),
                "set|Light|brightness|35|Percent|Study|Ground Floor",
                7,
            ),
        }
        self.assertEqual(set(records), set(expected))
        for key, (command, raw_output, line_number) in expected.items():
            record = records[key]
            self.assertEqual(record.command, command)
            self.assertEqual(record.raw_output, raw_output)
            self.assertEqual(record.line_number, line_number)
            self.assertEqual(record.query_sha256, ha._sha256_text(command))
            self.assertEqual(
                record.raw_output_sha256,
                ha._sha256_text(raw_output),
            )
            self.assertEqual(
                record.artifact_sha256,
                ha.V1_DOMUX_EVIDENCE_SHA256,
            )
            self.assertTrue(record.provenance()["pair_verified"])

    def test_loader_rejects_a_modified_v1_artifact(self) -> None:
        payload = ha.V1_DOMUX_EVIDENCE_PATH.read_bytes().replace(
            b"Turn off the light.",
            b"Turn off a light.",
            1,
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "domux_raw.jsonl"
            path.write_bytes(payload)
            with self.assertRaisesRegex(
                ha.AcceptanceError,
                "artifact digest does not match",
            ):
                ha.load_recorded_domux_evidence(path)

    def test_injected_v1_record_with_wrong_line_number_is_rejected(self) -> None:
        records = ha.load_recorded_domux_evidence()
        key = ha.DomuxEvidenceKey("eval-duplicate_entity-01", "ambiguous")
        records[key] = replace(records[key], line_number=999)
        with self.assertRaisesRegex(
            ha.AcceptanceError,
            "not bound to verified v1 Domux evidence",
        ):
            ha._evidence_for_case(records, key)

    def test_loader_checks_field_hashes_after_whole_artifact_hash(self) -> None:
        payload = ha.V1_DOMUX_EVIDENCE_PATH.read_bytes().replace(
            b"Turn off the light.",
            b"Turn off a light.",
            1,
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "domux_raw.jsonl"
            path.write_bytes(payload)
            with mock.patch.object(
                ha,
                "V1_DOMUX_EVIDENCE_SHA256",
                ha._sha256_bytes(payload),
            ):
                with self.assertRaisesRegex(
                    ha.AcceptanceError,
                    "command/output field digest does not match",
                ):
                    ha.load_recorded_domux_evidence(path)

    def test_evidence_failure_prevents_docker_prepare(self) -> None:
        docker = mock.Mock()

        def fail_evidence() -> dict[ha.DomuxEvidenceKey, ha.RecordedDomuxEvidence]:
            raise ha.AcceptanceError("synthetic evidence failure")

        with self.assertRaisesRegex(ha.AcceptanceError, "synthetic evidence failure"):
            ha.execute_acceptance(
                docker,
                recorded_evidence_loader=fail_evidence,
            )
        docker.prepare.assert_not_called()
        docker.cleanup.assert_called_once_with()


class FrozenScenarioEvidenceTests(unittest.TestCase):
    def test_loader_binds_exact_rows_gold_and_target_semantics(self) -> None:
        scenarios = ha.load_frozen_scenario_evidence()
        expected = {
            "eval-duplicate_entity-01": (
                17,
                "8b8f6498aef6bb4cb7fd5af3ac9adbef3e2cf0013c7a22451fc339e5f85cd871",
                "light.eval_de_01_living",
                "Turn off the light.",
                3,
            ),
            "eval-duplicate_entity-02": (
                18,
                "24f7b968a30cba095932b3f5da47ce8f8fc65d521546a830de1b6ea149db7fb4",
                "cover.eval_de_02_upstairs_hall",
                "Set the Curtain in the Hall on the First Floor to 20 percent.",
                2,
            ),
            "eval-duplicate_entity-03": (
                19,
                "ef0be54be7aaae140c846076fe9568dbea981fd6a96575b12cb6633057756bdf",
                "climate.eval_de_03_bedroom_second",
                "Set the AC in the Bedroom on the Second Floor to 22 Celsius.",
                2,
            ),
            "eval-duplicate_entity-04": (
                20,
                "d9b32826a3e0229d5943e69a8ba3192635194f5ba87120945a6a92274e24c78e",
                "light.eval_de_04_study",
                "Set the Light in the Study on the Ground Floor to 35 percent brightness.",
                3,
            ),
        }
        self.assertEqual(set(scenarios), set(expected))
        for base_id, (line, row_sha, target, variant_command, count) in expected.items():
            scenario = scenarios[base_id]
            self.assertEqual(scenario.line_number, line)
            self.assertEqual(scenario.row_sha256, row_sha)
            self.assertEqual(scenario.expected_target_entity_id, target)
            variant = "ambiguous" if base_id.endswith("01") else "clear"
            self.assertEqual(scenario.command_for_variant(variant), variant_command)
            self.assertEqual(len(scenario.candidate_entity_ids), count)
            self.assertEqual(
                scenario.artifact_sha256,
                ha.SCENARIO_EVIDENCE_SHA256,
            )
            self.assertEqual(
                scenario.binding_sha256(),
                ha.REQUIRED_FROZEN_SCENARIOS[base_id].binding_sha256,
            )
        clarified = scenarios["eval-duplicate_entity-01"]
        self.assertEqual(
            clarified.clarification_answer,
            "The Living Room light on the Ground Floor.",
        )
        self.assertEqual(
            clarified.confirmed_instruction.to_pipe(),
            "turnOff|Light|*|*|*|Living Room|Ground Floor",
        )
        self.assertEqual(
            clarified.target_inventory_semantics,
            {
                "aliases": (),
                "device": "Light",
                "domain": "light",
                "floor": "Ground Floor",
                "room": "Living Room",
            },
        )

    def test_loader_rejects_modified_scenario_artifact_and_row(self) -> None:
        payload = ha.SCENARIO_EVIDENCE_PATH.read_bytes().replace(
            b"The Living Room light on the Ground Floor.",
            b"The Bedroom light on the Ground Floor.",
            1,
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "scenarios.jsonl"
            path.write_bytes(payload)
            with self.assertRaisesRegex(
                ha.AcceptanceError,
                "scenario artifact digest does not match",
            ):
                ha.load_frozen_scenario_evidence(path)
            with mock.patch.object(
                ha,
                "SCENARIO_EVIDENCE_SHA256",
                ha._sha256_bytes(payload),
            ):
                with self.assertRaisesRegex(
                    ha.AcceptanceError,
                    "scenario row digest does not match",
                ):
                    ha.load_frozen_scenario_evidence(path)

    def test_injected_scenario_gold_change_is_rejected_by_binding(self) -> None:
        recorded = ha.load_recorded_domux_evidence()
        scenarios = ha.load_frozen_scenario_evidence()
        base_id = "eval-duplicate_entity-01"
        scenarios[base_id] = replace(
            scenarios[base_id],
            clarification_answer="The Bedroom light on the Ground Floor.",
        )
        with self.assertRaisesRegex(
            ha.AcceptanceError,
            "not bound to a verified frozen scenario",
        ):
            ha.validate_acceptance_evidence(recorded, scenarios)

    def test_scenario_failure_prevents_docker_prepare(self) -> None:
        docker = mock.Mock()

        def fail_scenario() -> dict[str, ha.FrozenScenarioEvidence]:
            raise ha.AcceptanceError("synthetic scenario failure")

        with self.assertRaisesRegex(ha.AcceptanceError, "synthetic scenario failure"):
            ha.execute_acceptance(
                docker,
                scenario_evidence_loader=fail_scenario,
            )
        docker.prepare.assert_not_called()
        docker.cleanup.assert_called_once_with()


class PinnedImageAndConfigTests(unittest.TestCase):
    def test_official_amd64_image_is_pinned_to_the_required_manifest(self) -> None:
        self.assertEqual(
            ha.IMAGE_REFERENCE,
            "ghcr.io/home-assistant/home-assistant@"
            "sha256:8e9751cb66d3ba6624f5360a7d31b0c6821f7f5b3fb8ba0d10d58f0f481c540c",
        )
        self.assertEqual(ha.HOME_ASSISTANT_VERSION, "2026.8.3")
        self.assertEqual(ha.PLATFORM, "linux/amd64")

    def test_configuration_is_an_in_memory_single_file_archive(self) -> None:
        archive_bytes = ha.configuration_archive()
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:") as archive:
            members = archive.getmembers()
            self.assertEqual([member.name for member in members], ["configuration.yaml"])
            content = archive.extractfile(members[0]).read().decode("utf-8")  # type: ignore[union-attr]
        self.assertEqual(content, ha.CONFIGURATION_YAML)
        self.assertIn("demo:\n", content)
        self.assertIn("onboarding:\n", content)
        self.assertNotIn("password", content.lower())
        self.assertNotIn("token", content.lower())


class DockerCliTests(unittest.TestCase):
    def test_prepare_and_cleanup_use_one_labelled_container_and_volume(self) -> None:
        runner = FakeDockerRunner()
        docker = ha.DockerCli(runner=runner, run_id=RUN_ID)

        runtime = docker.prepare()
        self.assertEqual(runtime.base_url, "http://127.0.0.1:34567")
        self.assertEqual(runtime.image["manifest_digest"], ha.IMAGE_DIGEST)
        self.assertTrue(runner.container_exists)
        self.assertTrue(runner.volume_exists)
        self.assertIsNotNone(runner.cp_archive)

        create = next(command for command in runner.commands if command[1] == "create")
        self.assertIn("--restart=no", create)
        self.assertIn("--cpus=1.5", create)
        self.assertIn("--memory=2g", create)
        self.assertIn("--pids-limit=512", create)
        self.assertIn("127.0.0.1::8123", create)
        self.assertEqual(create[-1], ha.IMAGE_REFERENCE)
        self.assertEqual(sum(command[1] == "create" for command in runner.commands), 1)
        self.assertEqual(
            sum(command[1:3] == ("volume", "create") for command in runner.commands),
            1,
        )

        docker.cleanup()
        self.assertFalse(runner.container_exists)
        self.assertFalse(runner.volume_exists)
        self.assertFalse(any("prune" in command for command in runner.commands))

    def test_cleanup_refuses_resources_with_a_different_run_label(self) -> None:
        runner = FakeDockerRunner()
        docker = ha.DockerCli(runner=runner, run_id=RUN_ID)
        docker.prepare()
        runner.container_label = "0" * 12

        with self.assertRaisesRegex(ha.AcceptanceError, "failed to clean"):
            docker.cleanup()

        self.assertTrue(runner.container_exists)
        self.assertTrue(runner.volume_exists)
        self.assertFalse(
            any(command[1:3] == ("rm", "--force") for command in runner.commands)
        )


class HomeAssistantWorkflowTests(unittest.TestCase):
    def test_full_mocked_sut_pipeline_is_controlled_one_time_and_redacted(self) -> None:
        backend = FakeRestBackend()
        initial_light = backend.state("light.bed_light")
        self.assertIsNone(initial_light["attributes"]["brightness"])
        self.assertIsNone(initial_light["attributes"]["color_temp_kelvin"])
        self.assertNotIn(
            "temperature_unit",
            backend.state("climate.hvac")["attributes"],
        )
        api = FakeHomeAssistantApi("http://127.0.0.1:45678", backend)
        result = ha.exercise_home_assistant(
            api,
            credential_factory=lambda: ("synthetic-user", "synthetic-password"),
            rest_adapter_factory=backend.adapter,
        )

        self.assertEqual(result["auth"]["ttl_seconds"], 1800)
        self.assertEqual(result["auth"]["refresh_after_revoke_http"], 400)
        setup = result["phases"]["setup"]
        sut = result["phases"]["sut"]
        self.assertEqual(setup["classification"], "direct_rest_state_normalization")
        self.assertFalse(setup["included_in_sut_dispatch_count"])
        self.assertEqual(len(setup["dispatches"]), 5)
        self.assertEqual(len(backend.setup_dispatches), 5)
        self.assertEqual(len(backend.external_dispatches), 1)
        self.assertEqual(
            backend.external_dispatches[0],
            {
                "data": {
                    "brightness_pct": 25.0,
                    "entity_id": "light.bed_light",
                },
                "domain": "light",
                "service": "turn_on",
            },
        )
        self.assertEqual(sut["classification"], "clarify_commit_sut")
        self.assertEqual(sut["adapter"], "HomeAssistantRESTAdapter")
        self.assertEqual(sut["case_count"], 4)
        self.assertEqual(sut["successful_transition_count"], 3)
        self.assertEqual(sut["rejected_before_dispatch_count"], 1)
        self.assertEqual(sut["external_fault_injection_count"], 1)
        self.assertEqual(sut["sut_dispatch_total"], 3)
        self.assertEqual(
            sut["domux_evidence"],
            {
                "artifact": "evidence/v1/domux_raw.jsonl",
                "artifact_sha256": ha.V1_DOMUX_EVIDENCE_SHA256,
                "pair_count": 4,
                "validation": "whole_artifact_and_per_field_sha256",
            },
        )
        self.assertEqual(
            sut["scenario_evidence"],
            {
                "artifact": "data/scenarios.jsonl",
                "artifact_sha256": ha.SCENARIO_EVIDENCE_SHA256,
                "case_count": 4,
                "ha_registry_profile": (
                    "semantic_target_mapping_subset_not_full_scenario_inventory"
                ),
            },
        )
        self.assertEqual(len(backend.sut_http_dispatches), 3)
        self.assertEqual(
            [item["domain"] for item in sut["cases"]],
            ["light", "cover", "climate", "light"],
        )
        self.assertEqual(
            [item["grounding"]["resolution"] for item in sut["cases"]],
            [
                "resolve_clarification_submission",
                "resolve_unique_request",
                "resolve_unique_request",
                "resolve_unique_request",
            ],
        )
        self.assertEqual(
            [
                (
                    item["domux_evidence"]["base_id"],
                    item["domux_evidence"]["variant"],
                )
                for item in sut["cases"]
            ],
            [
                ("eval-duplicate_entity-01", "ambiguous"),
                ("eval-duplicate_entity-02", "clear"),
                ("eval-duplicate_entity-03", "clear"),
                ("eval-duplicate_entity-04", "clear"),
            ],
        )
        for item in sut["cases"]:
            provenance = item["domux_evidence"]
            self.assertTrue(provenance["pair_verified"])
            self.assertEqual(
                provenance["artifact_sha256"],
                ha.V1_DOMUX_EVIDENCE_SHA256,
            )
            self.assertEqual(
                provenance["validation"],
                "whole_artifact_and_per_field_sha256",
            )
            self.assertNotIn(str(ha.CASE_DIR), json.dumps(provenance))
        scenario_targets = [
            (
                "light.eval_de_01_living",
                "light.ceiling_lights",
                3,
                2,
            ),
            (
                "cover.eval_de_02_upstairs_hall",
                "cover.hall_window",
                2,
                1,
            ),
            (
                "climate.eval_de_03_bedroom_second",
                "climate.hvac",
                2,
                1,
            ),
            (
                "light.eval_de_04_study",
                "light.bed_light",
                3,
                1,
            ),
        ]
        for index, item in enumerate(sut["cases"]):
            scenario = item["scenario_provenance"]
            scenario_target, ha_target, frozen_count, ha_count = scenario_targets[index]
            self.assertEqual(item["ha_registry_profile"], ha.HA_REGISTRY_PROFILE)
            self.assertEqual(scenario["ha_registry_profile"], ha.HA_REGISTRY_PROFILE)
            self.assertEqual(scenario["source"], "frozen_synthetic_scenario_gold")
            self.assertFalse(scenario["post_clarification_model_call"])
            self.assertEqual(scenario["frozen_candidate_count"], frozen_count)
            self.assertEqual(scenario["ha_matching_candidate_count"], ha_count)
            self.assertEqual(
                scenario["inventory_limitation"],
                {
                    "full_scenario_inventory_reproduced": False,
                    "profile": ha.HA_REGISTRY_PROFILE,
                },
            )
            self.assertEqual(
                scenario["scenario_target_to_ha_demo_entity"],
                {
                    "ha_demo_entity_id": ha_target,
                    "scenario_target_entity_id": scenario_target,
                    "semantic_fields_match": True,
                },
            )
            self.assertEqual(scenario["used_for_resolution"], index == 0)
            self.assertEqual(len(scenario["row_sha256"]), 64)
            self.assertEqual(len(scenario["binding_sha256"]), 64)
            self.assertNotIn(str(ha.CASE_DIR), json.dumps(scenario))
        clarified_scenario = sut["cases"][0]["scenario_provenance"]
        self.assertEqual(
            clarified_scenario["clarification_answer"],
            "The Living Room light on the Ground Floor.",
        )
        self.assertEqual(
            clarified_scenario["confirmed_instruction"],
            {
                "action": "turnOff",
                "attribute": "*",
                "device": "Light",
                "floor": "Ground Floor",
                "room": "Living Room",
                "unit": "*",
                "value": "*",
            },
        )
        self.assertEqual(
            [item["service_shape"]["service"] for item in sut["cases"]],
            ["turn_off", "set_cover_position", "set_temperature", "turn_on"],
        )
        self.assertEqual(
            backend.sut_http_dispatches,
            [
                {
                    "data": {
                        "entity_id": "light.ceiling_lights",
                    },
                    "domain": "light",
                    "service": "turn_off",
                },
                {
                    "data": {
                        "entity_id": "cover.hall_window",
                        "position": 20,
                    },
                    "domain": "cover",
                    "service": "set_cover_position",
                },
                {
                    "data": {
                        "entity_id": "climate.hvac",
                        "temperature": 22.0,
                    },
                    "domain": "climate",
                    "service": "set_temperature",
                },
            ],
        )
        self.assertEqual(
            [event["phase"] for event in backend.service_events],
            ["setup"] * 5 + ["sut"] * 3 + ["external_fault_injection"],
        )
        accounting = result["phases"]["service_call_accounting"]
        self.assertEqual(accounting["external_fault_injection"], 1)
        self.assertEqual(accounting["setup_direct_rest"], 5)
        self.assertEqual(accounting["sut_dispatches"], 3)
        self.assertEqual(accounting["total"], 9)
        self.assertEqual(accounting["direct_rest_events"], api.direct_service_calls)
        self.assertEqual(
            [event["phase"] for event in accounting["direct_rest_events"]],
            ["setup"] * 5 + ["external_fault_injection"],
        )
        expected_transitions = {
            "recorded_ambiguous_light_off": (
                {"brightness": 178, "state": "on"},
                {"brightness": None, "state": "off"},
            ),
            "recorded_unique_cover_position": (
                {"current_position": 80, "state": "open"},
                {"current_position": 20, "state": "open"},
            ),
            "recorded_unique_climate_temperature": (
                {"state": "cool", "temperature": 24},
                {"state": "cool", "temperature": 22},
            ),
        }
        for item in sut["cases"][:3]:
            expected_before, expected_after = expected_transitions[item["case"]]
            for key, value in expected_before.items():
                self.assertTrue(
                    ha._scalar_matches(item["controlled_before"][key], value)
                )
            for key, value in expected_after.items():
                self.assertTrue(
                    ha._scalar_matches(item["controlled_after"][key], value)
                )
            self.assertTrue(item["postcondition"]["matched_prepared_projection"])
            self.assertTrue(item["postcondition"]["all_registered_entities_exact"])
            self.assertEqual(item["postcondition"]["status"], "COMMITTED")
            self.assertFalse(item["replay"]["accepted"])
            self.assertFalse(item["replay"]["dispatched"])
            self.assertEqual(item["replay"]["reason"], "replayed_nonce")
            self.assertEqual(item["replay"]["sut_dispatch_delta"], 0)
            self.assertEqual(item["outcome"], "COMMITTED")

        drift = sut["cases"][3]
        self.assertEqual(
            drift["case"],
            "recorded_study_light_state_drift_rejected",
        )
        self.assertEqual(drift["outcome"], "REJECTED_BEFORE_DISPATCH")
        self.assertEqual(
            drift["external_mutation"],
            {
                "classification": "out_of_band_fault_injection",
                "data": {
                    "brightness_pct": 25.0,
                    "entity_id": "light.bed_light",
                },
                "domain": "light",
                "http_status": 200,
                "included_in_sut_dispatch_count": False,
                "observed_path": "/api/states/light.bed_light",
                "request_path": "/api/services/light/turn_on",
                "service": "turn_on",
                "transport": "home_assistant_rest_api",
            },
        )
        self.assertEqual(
            drift["rejection"],
            {
                "acknowledged": False,
                "accepted": False,
                "dispatched": False,
                "outcome_unknown": False,
                "reason": "state_changed",
                "status": "INVALIDATED",
                "sut_dispatch_delta": 0,
            },
        )
        self.assertEqual(
            drift["controlled_before_external_mutation"]["brightness"],
            166,
        )
        self.assertEqual(
            drift["controlled_after_external_mutation"]["brightness"],
            64,
        )
        self.assertNotEqual(
            drift["controlled_before_external_mutation"]["entity_id"],
            sut["cases"][0]["controlled_after"]["entity_id"],
        )
        binding = drift["binding"]
        self.assertTrue(binding["matched_before_external_mutation"])
        self.assertTrue(binding["changed_after_external_mutation"])
        self.assertEqual(
            binding["prepared_state_digest"],
            binding["before_external_mutation_state_digest"],
        )
        self.assertNotEqual(
            binding["prepared_state_digest"],
            binding["after_external_mutation_state_digest"],
        )
        for field in (
            "prepared_state_digest",
            "before_external_mutation_state_digest",
            "after_external_mutation_state_digest",
        ):
            self.assertEqual(len(binding[field]), 64)
            self.assertTrue(set(binding[field]) <= set("0123456789abcdef"))
        serialized = json.dumps(result, sort_keys=True)
        for private in (
            "synthetic-user",
            "synthetic-password",
            "synthetic-auth-code",
            "synthetic-integration-code",
            api.access_token,
            api.refresh_token,
            "45678",
        ):
            self.assertNotIn(private, serialized)

    def test_target_drift_injection_must_be_external_and_observed(self) -> None:
        for mode, expected_error in (
            ("sut_adapter", "counted as a SUT dispatch"),
            ("no_op", "mutated state"),
        ):
            with self.subTest(mode=mode):
                backend = FakeRestBackend()
                api = FakeHomeAssistantApi("http://127.0.0.1:45678", backend)
                ha.normalize_setup_state(api, api.access_token)
                adapter = backend.adapter(api.base_url, api.access_token)

                def mutate(case: ha.TargetDriftCase) -> dict[str, object]:
                    if mode == "sut_adapter":
                        adapter.call_service(
                            case.domain,
                            case.mutation_service,
                            case.mutation_service_data,
                        )
                    return {
                        "classification": "out_of_band_fault_injection",
                        "data": dict(case.mutation_service_data),
                        "domain": case.domain,
                        "http_status": 200,
                        "included_in_sut_dispatch_count": False,
                        "observed_path": f"/api/states/{case.entity_id}",
                        "request_path": (
                            f"/api/services/{case.domain}/{case.mutation_service}"
                        ),
                        "service": case.mutation_service,
                        "transport": "home_assistant_rest_api",
                    }

                with self.assertRaisesRegex(ha.AcceptanceError, expected_error):
                    ha.run_sut_cases(
                        adapter,
                        recorded_evidence=ha.load_recorded_domux_evidence(),
                        scenario_evidence=ha.load_frozen_scenario_evidence(),
                        mutate_target=mutate,
                    )

    def test_execute_always_cleans_runtime_on_success_and_failure(self) -> None:
        backend = FakeRestBackend()
        successful = FakeDockerRuntime()
        result = ha.execute_acceptance(
            successful,
            api_factory=lambda base_url: FakeHomeAssistantApi(base_url, backend),
            credential_factory=lambda: ("synthetic-user", "synthetic-password"),
            rest_adapter_factory=backend.adapter,
        )
        self.assertTrue(successful.cleaned)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["schema_version"], 4)
        provenance = result["execution_source_bindings"]
        self.assertEqual(
            set(provenance),
            {
                "binding_bundle_sha256",
                "digest_algorithm",
                "host_python",
                "inputs",
                "module_origins_verified",
                "path_base",
                "pre_post_execution_match",
                "schema_version",
                "sources",
            },
        )
        self.assertEqual(provenance["schema_version"], 1)
        self.assertEqual(provenance["digest_algorithm"], "sha256")
        self.assertEqual(provenance["path_base"], "case_directory")
        self.assertTrue(provenance["module_origins_verified"])
        self.assertTrue(provenance["pre_post_execution_match"])
        self.assertEqual(
            provenance["host_python"],
            {
                "implementation": sys.implementation.name,
                "version": ".".join(str(value) for value in sys.version_info[:3]),
            },
        )
        self.assertEqual(
            set(provenance["sources"]),
            set(ha.EXECUTION_SOURCE_PATHS),
        )
        self.assertEqual(
            set(provenance["inputs"]),
            set(ha.EXECUTION_INPUT_PATHS),
        )
        for group, paths in (
            ("inputs", ha.EXECUTION_INPUT_PATHS),
            ("sources", ha.EXECUTION_SOURCE_PATHS),
        ):
            for relative in paths:
                payload = (ha.CASE_DIR / relative).read_bytes()
                self.assertEqual(
                    provenance[group][relative],
                    {
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "size_bytes": len(payload),
                    },
                )
        bundle_payload = json.dumps(
            {"inputs": provenance["inputs"], "sources": provenance["sources"]},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        self.assertEqual(
            provenance["binding_bundle_sha256"],
            hashlib.sha256(bundle_payload).hexdigest(),
        )

        failing = FakeDockerRuntime(fail=True)
        with self.assertRaisesRegex(ha.AcceptanceError, "prepare failure"):
            ha.execute_acceptance(
                failing,
                api_factory=lambda base_url: FakeHomeAssistantApi(base_url),
            )
        self.assertTrue(failing.cleaned)

    def test_production_direct_service_call_records_redacted_success(self) -> None:
        observed: dict[str, object] = {}

        def recording_opener(request: Any, *, timeout: float) -> FakeRestResponse:
            observed.update({
                "authorization": request.headers.get("Authorization"),
                "body": json.loads(request.data),
                "method": request.method,
                "path": urlparse(request.full_url).path,
                "timeout": timeout,
            })
            return FakeRestResponse([])

        api = ha.HomeAssistantApi(
            "http://127.0.0.1:45678",
            opener=recording_opener,
        )
        result = api.call_service(
            "light",
            "turn_on",
            {"brightness_pct": 25, "entity_id": "light.bed_light"},
            "synthetic-access-secret",
            evidence_phase="external_fault_injection",
        )
        self.assertEqual(result.status, 200)
        self.assertEqual(observed["method"], "POST")
        self.assertEqual(observed["path"], "/api/services/light/turn_on")
        self.assertEqual(observed["authorization"], "Bearer synthetic-access-secret")
        self.assertEqual(
            observed["body"],
            {"brightness_pct": 25, "entity_id": "light.bed_light"},
        )
        self.assertEqual(
            api.direct_service_calls,
            [{
                "domain": "light",
                "http_status": 200,
                "phase": "external_fault_injection",
                "request_path": "/api/services/light/turn_on",
                "service": "turn_on",
            }],
        )
        self.assertNotIn("synthetic-access-secret", json.dumps(api.direct_service_calls))

    def test_execute_rejects_execution_file_drift_and_still_cleans(self) -> None:
        before = ha.capture_execution_bindings()
        after = copy.deepcopy(before)
        after["sources"]["ha_acceptance.py"]["sha256"] = "0" * 64
        runtime = FakeDockerRuntime()
        backend = FakeRestBackend()

        with mock.patch.object(
            ha,
            "capture_execution_bindings",
            side_effect=(before, after),
        ), self.assertRaisesRegex(
            ha.AcceptanceError,
            "execution sources or inputs changed during acceptance",
        ):
            ha.execute_acceptance(
                runtime,
                api_factory=lambda base_url: FakeHomeAssistantApi(base_url, backend),
                credential_factory=lambda: (
                    "synthetic-user",
                    "synthetic-password",
                ),
                rest_adapter_factory=backend.adapter,
            )
        self.assertTrue(runtime.cleaned)

    def test_http_transport_failure_does_not_echo_url_or_bearer(self) -> None:
        def failing_opener(*_args: Any, **_kwargs: Any) -> Any:
            raise URLError("synthetic-secret at http://127.0.0.1:45678")

        api = ha.HomeAssistantApi(
            "http://127.0.0.1:45678", opener=failing_opener
        )
        with self.assertRaises(ha.AcceptanceError) as caught:
            api.request("GET", "/api/", token="synthetic-bearer-secret")
        message = str(caught.exception)
        self.assertNotIn("synthetic-secret", message)
        self.assertNotIn("synthetic-bearer-secret", message)
        self.assertNotIn("45678", message)


class OutputAndCliTests(unittest.TestCase):
    def test_atomic_json_is_canonical_private_and_leaves_no_staging_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "nested" / "acceptance.json"
            payload = ha.atomic_write_json(output, {"z": 2, "a": 1})

            self.assertEqual(payload, b'{\n  "a": 1,\n  "z": 2\n}\n')
            self.assertEqual(output.read_bytes(), payload)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertEqual(list(output.parent.glob("*.tmp")), [])

    def test_atomic_json_removes_staging_file_when_publish_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "acceptance.json"
            with mock.patch.object(ha.os, "replace", side_effect=OSError("synthetic")):
                with self.assertRaises(OSError):
                    ha.atomic_write_json(output, {"status": "passed"})
            self.assertFalse(output.exists())
            self.assertEqual(list(Path(temporary).glob("*.tmp")), [])

    def test_cli_writes_same_redacted_json_to_file_and_stdout(self) -> None:
        runtime = FakeDockerRuntime()
        backend = FakeRestBackend()
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "acceptance.json"
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                returncode = ha.main(
                    ["--output", str(output)],
                    docker_factory=lambda: runtime,
                    api_factory=lambda base_url: FakeHomeAssistantApi(
                        base_url, backend
                    ),
                    credential_factory=lambda: (
                        "synthetic-user",
                        "synthetic-password",
                    ),
                    rest_adapter_factory=backend.adapter,
                )
            self.assertEqual(returncode, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertEqual(stdout.getvalue().encode(), output.read_bytes())
            self.assertNotIn(str(output.parent), stdout.getvalue())
            self.assertNotIn("45678", stdout.getvalue())

    def test_cli_returns_nonzero_and_does_not_publish_on_failure(self) -> None:
        runtime = FakeDockerRuntime(fail=True)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "acceptance.json"
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                returncode = ha.main(
                    ["--output", str(output)],
                    docker_factory=lambda: runtime,
                    api_factory=FakeHomeAssistantApi,
                )
            self.assertEqual(returncode, 1)
            self.assertEqual(stdout.getvalue(), "")
            self.assertFalse(output.exists())
            self.assertTrue(runtime.cleaned)
            self.assertIn("ha_acceptance failed", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
