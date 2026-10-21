"""Atlas C3 (FR-4.1 / §9.1) — microVM sandbox isolation tests.

Covers:
- ``MicroVMSandbox`` contract over a fake driver: create (spec building,
  tier/network validation, mount injection + rw bookkeeping, failure
  cleanup), ensure_image package rejection, exec/kill_exec tracking,
  rw-mount flush on destroy.
- ``E2BDriver`` lazy-SDK semantics: construction imports nothing, a
  missing SDK degrades to a descriptive SandboxError, and the SDK
  surface is mapped 1:1 when present.
- Runtime selection: ``create_sandbox`` / ``runtime_for_config`` /
  ``resolve_sandbox`` / ``sandbox_for_session`` (metadata-pinned routing).
- ``EnvironmentConfig.isolation`` validation.
- Session lifecycle wiring: create-path 503 when the runtime is disabled,
  ``sandbox_runtime`` pin in the persisted metadata, terminate and
  filesystem endpoints routed to the pinned runtime.

Requirements: FR-4.1, §9.1
"""

from __future__ import annotations

import json
import logging
import sys
import types
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.models import EnvironmentConfig, EnvironmentPackages, SandboxResources
from app.sandbox import (
    MICROVM_RESOURCE_TIERS,
    DockerSandbox,
    E2BDriver,
    ExecResult,
    MicroVMSandbox,
    MicroVMSpec,
    ResourceMount,
    SandboxError,
    _parse_ls_la,
    create_sandbox,
    resolve_sandbox,
    runtime_for_config,
    sandbox_for_session,
)


AUTH = {"Authorization": "Bearer test-secret-key"}

SESSION_ID = "11111111-1111-1111-1111-111111111111"
PROJECT_ID = "22222222-2222-2222-2222-222222222222"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeDriver:
    """MicroVMDriver double that records calls and fakes a file store."""

    def __init__(self):
        self.created_specs: list[MicroVMSpec] = []
        self.vms: list[str] = []
        self.written: list[tuple[str, str, str]] = []
        self.text_files: dict[tuple[str, str], str] = {}
        self.binaries: dict[tuple[str, str], bytes] = {}
        self.dirs: dict[tuple[str, str], list[dict]] = {}
        self.destroyed: list[str] = []
        self.killed: list[tuple[str, int]] = []
        self.kill_result = True
        self.write_error: Exception | None = None

    async def create_vm(self, spec: MicroVMSpec) -> str:
        self.created_specs.append(spec)
        vm_id = f"vm-{len(self.created_specs)}"
        self.vms.append(vm_id)
        return vm_id

    async def exec_in_vm(self, vm_id: str, command: str) -> ExecResult:
        return ExecResult(stdout=f"out:{command}", stderr="", exit_code=0)

    async def kill_vm_process(self, vm_id: str, pid: int, grace: float = 3.0) -> bool:
        self.killed.append((vm_id, pid))
        return self.kill_result

    async def write_vm_file(self, vm_id: str, path: str, content: str) -> None:
        if self.write_error is not None:
            raise self.write_error
        self.written.append((vm_id, path, content))
        self.text_files[(vm_id, path)] = content

    async def read_vm_file(self, vm_id: str, path: str) -> str:
        return self.text_files[(vm_id, path)]

    async def copy_vm_file(self, vm_id: str, path: str) -> bytes:
        if (vm_id, path) in self.binaries:
            return self.binaries[(vm_id, path)]
        return self.text_files.get((vm_id, path), "").encode()

    async def list_vm_dir(self, vm_id: str, path: str) -> list[dict]:
        return self.dirs.get((vm_id, path), [])

    async def destroy_vm(self, vm_id: str) -> None:
        self.destroyed.append(vm_id)
        if vm_id in self.vms:
            self.vms.remove(vm_id)


def _make_vm_sandbox(**driver_kwargs) -> tuple[MicroVMSandbox, FakeDriver]:
    driver = FakeDriver()
    for key, value in driver_kwargs.items():
        setattr(driver, key, value)
    return MicroVMSandbox(driver), driver


def _entry(name: str, kind: str = "file", size: int = 2) -> dict:
    perms = {"dir": "drwxr-xr-x", "file": "-rw-r--r--", "symlink": "lrwxrwxrwx"}[kind]
    return {
        "name": name,
        "type": kind,
        "size": size,
        "permissions": perms,
        "owner": "root",
        "group": "root",
        "modified": "2026-05-13 10:00",
        "link_target": None,
    }


class _AppState(SimpleNamespace):
    """Stand-in for app.state with the two sandbox attributes."""


# ---------------------------------------------------------------------------
# MicroVMSandbox.create
# ---------------------------------------------------------------------------


class TestMicroVMCreate:
    @pytest.mark.asyncio
    async def test_create_returns_vm_id_and_builds_spec(self):
        sandbox, driver = _make_vm_sandbox()
        cid = await sandbox.create("img:v1", "linchpin-none")

        assert cid == "vm-1"
        spec = driver.created_specs[0]
        assert spec.image == "img:v1"
        assert spec.env == {}
        # Default tier 1c2g.
        assert spec.vcpu == 1
        assert spec.mem_mib == 2048

    @pytest.mark.asyncio
    async def test_create_maps_resource_tier(self):
        sandbox, driver = _make_vm_sandbox()
        await sandbox.create(
            "img:v1", "linchpin-none",
            resources=SandboxResources(tier="2c4g"),
        )
        spec = driver.created_specs[0]
        assert spec.vcpu == 2
        assert spec.mem_mib == 4096
        assert set(MICROVM_RESOURCE_TIERS) == {"1c2g", "2c4g", "4c8g"}

    @pytest.mark.asyncio
    async def test_create_rejects_unknown_tier(self):
        sandbox, _ = _make_vm_sandbox()
        # Bypass the pydantic Literal by passing a stub with a bad tier.
        resources = SimpleNamespace(tier="9c9g")
        with pytest.raises(SandboxError, match="Unknown sandbox resource tier"):
            await sandbox.create("img", "linchpin-none", resources=resources)

    @pytest.mark.asyncio
    async def test_create_rejects_non_microvm_network(self):
        sandbox, _ = _make_vm_sandbox()
        with pytest.raises(SandboxError, match="Unknown sandbox network"):
            await sandbox.create("img", "bridge")

    @pytest.mark.asyncio
    async def test_create_passes_env_into_spec(self):
        sandbox, driver = _make_vm_sandbox()
        await sandbox.create("img", "linchpin-open", env={"HTTP_PROXY": "p:1"})
        assert driver.created_specs[0].env == {"HTTP_PROXY": "p:1"}

    @pytest.mark.asyncio
    async def test_ro_mount_file_is_injected_and_not_tracked_rw(self, tmp_path):
        host_file = tmp_path / "data.csv"
        host_file.write_text("hello")

        sandbox, driver = _make_vm_sandbox()
        mounts = [ResourceMount(host_path=str(host_file), container_path="/mnt/data.csv")]
        await sandbox.create("img", "linchpin-none", mounts=mounts)

        assert driver.written == [("vm-1", "/mnt/data.csv", "hello")]
        # ro mounts are snapshot-injected only — no flush bookkeeping.
        assert sandbox._rw_mounts == {}

    @pytest.mark.asyncio
    async def test_rw_mount_file_recorded_and_host_content_injected(self, tmp_path):
        host_file = tmp_path / "seed.txt"
        host_file.write_text("seed")

        sandbox, driver = _make_vm_sandbox()
        mounts = [ResourceMount(
            host_path=str(host_file),
            container_path="/mnt/work/seed.txt",
            mode="rw",
        )]
        await sandbox.create("img", "linchpin-none", mounts=mounts)

        assert ("vm-1", "/mnt/work/seed.txt", "seed") in driver.written
        assert [m.container_path for m in sandbox._rw_mounts["vm-1"]] == [
            "/mnt/work/seed.txt",
        ]

    @pytest.mark.asyncio
    async def test_rw_mount_with_missing_host_path_still_recorded(self, tmp_path):
        sandbox, driver = _make_vm_sandbox()
        mounts = [ResourceMount(
            host_path=str(tmp_path / "nope"),
            container_path="/mnt/session/outputs",
            mode="rw",
        )]
        await sandbox.create("img", "linchpin-none", mounts=mounts)

        assert driver.written == []
        assert sandbox._rw_mounts["vm-1"] == mounts

    @pytest.mark.asyncio
    async def test_create_mount_failure_destroys_vm(self, tmp_path):
        host_file = tmp_path / "data.csv"
        host_file.write_text("hello")

        sandbox, driver = _make_vm_sandbox(write_error=RuntimeError("disk full"))
        mounts = [ResourceMount(host_path=str(host_file), container_path="/mnt/data.csv")]

        with pytest.raises(SandboxError, match="Failed to apply mounts"):
            await sandbox.create("img", "linchpin-none", mounts=mounts)

        # Boot failure must not leak the VM.
        assert driver.destroyed == ["vm-1"]

    @pytest.mark.asyncio
    async def test_create_driver_failure_wrapped_in_sandbox_error(self):
        driver = FakeDriver()
        driver.create_vm = AsyncMock(side_effect=RuntimeError("hypervisor gone"))
        sandbox = MicroVMSandbox(driver)

        with pytest.raises(SandboxError, match="Failed to create microVM"):
            await sandbox.create("img", "linchpin-none")
        assert driver.destroyed == []

    @pytest.mark.asyncio
    async def test_create_uses_env_image_when_empty(self, monkeypatch):
        monkeypatch.setenv("LINCHPIN_SANDBOX_IMAGE", "custom:v9")
        sandbox, driver = _make_vm_sandbox()
        await sandbox.create("", "linchpin-none")
        assert driver.created_specs[0].image == "custom:v9"


# ---------------------------------------------------------------------------
# MicroVMSandbox.ensure_image
# ---------------------------------------------------------------------------


class TestMicroVMEnsureImage:
    @pytest.mark.asyncio
    async def test_empty_packages_returns_base_image(self):
        sandbox, _ = _make_vm_sandbox()
        assert await sandbox.ensure_image(
            base_image="img:v1", packages=None,
        ) == "img:v1"
        assert await sandbox.ensure_image(
            base_image="img:v1", packages=EnvironmentPackages(),
        ) == "img:v1"

    @pytest.mark.asyncio
    async def test_non_empty_packages_raise(self):
        sandbox, _ = _make_vm_sandbox()
        packages = EnvironmentPackages(apt=["curl"])
        with pytest.raises(SandboxError, match="not supported with isolation=microvm"):
            await sandbox.ensure_image(base_image="img:v1", packages=packages)


# ---------------------------------------------------------------------------
# MicroVMSandbox exec / file surface / destroy
# ---------------------------------------------------------------------------


class TestMicroVMExecAndFiles:
    @pytest.mark.asyncio
    async def test_exec_delegates_to_driver(self):
        sandbox, driver = _make_vm_sandbox()
        result = await sandbox.exec("vm-1", "echo hi")
        assert result == ExecResult(stdout="out:echo hi", stderr="", exit_code=0)

    def test_active_exec_pid_tracks_registry(self):
        sandbox, _ = _make_vm_sandbox()
        assert sandbox.active_exec_pid("vm-1") is None
        sandbox._active_pids["vm-1"] = 4242
        assert sandbox.active_exec_pid("vm-1") == 4242

    @pytest.mark.asyncio
    async def test_kill_exec_true_pops_pid(self):
        sandbox, driver = _make_vm_sandbox()
        sandbox._active_pids["vm-1"] = 4242
        driver.kill_result = True

        assert await sandbox.kill_exec("vm-1", 4242) is True
        assert sandbox.active_exec_pid("vm-1") is None
        assert driver.killed == [("vm-1", 4242)]

    @pytest.mark.asyncio
    async def test_kill_exec_false_keeps_pid(self):
        sandbox, driver = _make_vm_sandbox()
        sandbox._active_pids["vm-1"] = 4242
        driver.kill_result = False

        assert await sandbox.kill_exec("vm-1", 4242) is False
        assert sandbox.active_exec_pid("vm-1") == 4242

    @pytest.mark.asyncio
    async def test_file_ops_delegate(self):
        sandbox, driver = _make_vm_sandbox()

        await sandbox.write_file("vm-1", "/tmp/a.txt", "abc")
        assert await sandbox.read_file("vm-1", "/tmp/a.txt") == "abc"

        driver.binaries[("vm-1", "/tmp/a.bin")] = b"\x00\x01"
        assert await sandbox.copy_from_container("vm-1", "/tmp/a.bin") == b"\x00\x01"

        driver.dirs[("vm-1", "/tmp")] = [_entry("a.txt")]
        assert await sandbox.list_dir("vm-1", "/tmp") == [_entry("a.txt")]

    def test_microvm_sandbox_satisfies_protocol_shape(self):
        for method in (
            "create", "ensure_image", "exec", "active_exec_pid", "kill_exec",
            "write_file", "read_file", "copy_from_container", "list_dir", "destroy",
        ):
            assert callable(getattr(MicroVMSandbox, method, None)), method


class TestMicroVMDestroy:
    @pytest.mark.asyncio
    async def test_destroy_flushes_rw_trees_to_host(self, tmp_path):
        host_dir = tmp_path / "outputs"
        host_dir.mkdir()

        sandbox, driver = _make_vm_sandbox()
        vm_id = await sandbox.create("img", "linchpin-none", mounts=[ResourceMount(
            host_path=str(host_dir),
            container_path="/mnt/session/outputs",
            mode="rw",
        )])
        driver.dirs[(vm_id, "/mnt/session/outputs")] = [
            _entry("report.csv"),
            _entry("sub", kind="dir"),
        ]
        driver.dirs[(vm_id, "/mnt/session/outputs/sub")] = [_entry("deep.txt")]
        driver.binaries[(vm_id, "/mnt/session/outputs/report.csv")] = b"hi"
        driver.binaries[(vm_id, "/mnt/session/outputs/sub/deep.txt")] = b"deep"

        await sandbox.destroy(vm_id)

        assert (host_dir / "report.csv").read_bytes() == b"hi"
        assert (host_dir / "sub" / "deep.txt").read_bytes() == b"deep"
        assert driver.destroyed == [vm_id]
        assert vm_id not in sandbox._rw_mounts

    @pytest.mark.asyncio
    async def test_destroy_flush_failure_is_best_effort(self, tmp_path):
        host_dir = tmp_path / "outputs"
        host_dir.mkdir()

        sandbox, driver = _make_vm_sandbox()
        vm_id = await sandbox.create("img", "linchpin-none", mounts=[ResourceMount(
            host_path=str(host_dir),
            container_path="/mnt/session/outputs",
            mode="rw",
        )])
        driver.dirs[(vm_id, "/mnt/session/outputs")] = [_entry("x.csv")]
        async def _boom(*args, **kwargs):
            raise RuntimeError("network reset")
        driver.copy_vm_file = _boom

        # Flush failure must not prevent the VM teardown.
        await sandbox.destroy(vm_id)
        assert driver.destroyed == [vm_id]

    @pytest.mark.asyncio
    async def test_destroy_clears_pid_registry(self):
        sandbox, driver = _make_vm_sandbox()
        sandbox._active_pids["vm-1"] = 7
        await sandbox.destroy("vm-1")
        assert sandbox.active_exec_pid("vm-1") is None
        assert driver.destroyed == ["vm-1"]


# ---------------------------------------------------------------------------
# ls -la parser (shared with the Docker runtime)
# ---------------------------------------------------------------------------


class TestParseLsLa:
    def test_parses_mixed_entries(self):
        stdout = (
            "total 12\n"
            "drwxr-xr-x 2 root root 4096 2026-05-13 10:00 .\n"
            "-rw-r--r-- 1 root root   14 2026-05-13 10:01 result.csv\n"
            "lrwxrwxrwx 1 root root    5 2026-05-13 10:02 latest -> v9.txt\n"
            "garbage\n"
        )
        entries = _parse_ls_la(stdout)
        assert [e["name"] for e in entries] == [".", "result.csv", "latest"]
        assert [e["type"] for e in entries] == ["dir", "file", "symlink"]
        assert entries[1]["size"] == 14
        assert entries[1]["modified"] == "2026-05-13 10:01"
        assert entries[2]["link_target"] == "v9.txt"
        assert entries[0]["link_target"] is None

    def test_empty_output(self):
        assert _parse_ls_la("") == []


# ---------------------------------------------------------------------------
# E2BDriver — lazy SDK
# ---------------------------------------------------------------------------


def _fake_e2b_module(run_result=None):
    """A minimal stand-in for the e2b module."""
    module = types.ModuleType("e2b")
    module.booted: list[dict] = []

    def Sandbox(_cls=None, *, image=None, envs=None, timeout=None, sandbox_id=None, **kwargs):
        if sandbox_id is None:
            module.booted.append({"image": image, "envs": envs, "timeout": timeout})
        vm = SimpleNamespace(
            sandbox_id=sandbox_id or f"e2b-{len(module.booted)}",
            kill=MagicMock(return_value=None),
        )
        vm.commands = SimpleNamespace(run=MagicMock(return_value=run_result or SimpleNamespace(
            stdout="ls-out", stderr="", exit_code=0,
        )))
        vm.files = SimpleNamespace(
            write=MagicMock(return_value=None),
            read=MagicMock(side_effect=lambda path, format=None: (
                b"raw-bytes" if format == "binary" else "text-content"
            )),
        )
        return vm

    module.Sandbox = Sandbox
    return module


class TestE2BDriver:
    def test_construction_never_imports_sdk(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "e2b", None)
        E2BDriver()  # must not raise

    @pytest.mark.asyncio
    async def test_missing_sdk_raises_descriptive_error(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "e2b", None)
        driver = E2BDriver()

        with pytest.raises(SandboxError, match="pip install e2b"):
            await driver.create_vm(MicroVMSpec(image="i", env={}, vcpu=1, mem_mib=2))

    @pytest.mark.asyncio
    async def test_create_vm_maps_spec(self, monkeypatch):
        fake = _fake_e2b_module()
        monkeypatch.setitem(sys.modules, "e2b", fake)
        monkeypatch.delenv("LINCHPIN_E2B_TIMEOUT_SEC", raising=False)

        driver = E2BDriver()
        vm_id = await driver.create_vm(MicroVMSpec(
            image="img:v1", env={"HTTP_PROXY": "p:1"}, vcpu=2, mem_mib=4096,
        ))

        assert vm_id == "e2b-1"
        assert fake.booted == [{
            "image": "img:v1",
            "envs": {"HTTP_PROXY": "p:1"},
            "timeout": None,
        }]

    @pytest.mark.asyncio
    async def test_exec_in_vm_maps_result(self, monkeypatch):
        fake = _fake_e2b_module()
        monkeypatch.setitem(sys.modules, "e2b", fake)
        driver = E2BDriver()

        result = await driver.exec_in_vm("e2b-9", "ls /")
        assert result == ExecResult(stdout="ls-out", stderr="", exit_code=0)

    @pytest.mark.asyncio
    async def test_file_roundtrip_and_destroy(self, monkeypatch):
        fake = _fake_e2b_module()
        monkeypatch.setitem(sys.modules, "e2b", fake)
        driver = E2BDriver()

        await driver.write_vm_file("e2b-1", "/tmp/a.txt", "abc")
        assert await driver.read_vm_file("e2b-1", "/tmp/a.txt") == "text-content"
        assert await driver.copy_vm_file("e2b-1", "/tmp/a.txt") == b"raw-bytes"

        entries = await driver.list_vm_dir("e2b-1", "/tmp")
        assert entries == []  # fake returns empty stdout → no entries

        await driver.destroy_vm("e2b-1")

    @pytest.mark.asyncio
    async def test_kill_vm_process_reports_unsupported(self, monkeypatch, caplog):
        monkeypatch.setitem(sys.modules, "e2b", None)  # never reaches the SDK
        driver = E2BDriver()

        with caplog.at_level(logging.WARNING, logger="linchpin-api.sandbox"):
            killed = await driver.kill_vm_process("e2b-1", 42)
        assert killed is False
        assert "kill_vm_process is unsupported" in caplog.text

    @pytest.mark.asyncio
    async def test_list_vm_dir_nonzero_exit_raises(self, monkeypatch):
        fake = _fake_e2b_module(run_result=SimpleNamespace(
            stdout="", stderr="ls: cannot access", exit_code=2,
        ))
        monkeypatch.setitem(sys.modules, "e2b", fake)
        driver = E2BDriver()

        with pytest.raises(SandboxError, match="Cannot list"):
            await driver.list_vm_dir("e2b-1", "/nope")


# ---------------------------------------------------------------------------
# Runtime selection
# ---------------------------------------------------------------------------


class TestRuntimeSelection:
    def test_create_sandbox_docker(self):
        # DockerSandbox() constructs a real docker client from env; patch
        # docker.from_env to keep the test hermetic.
        with patch("app.sandbox.docker.from_env"):
            assert isinstance(create_sandbox("docker"), DockerSandbox)

    def test_create_sandbox_microvm(self):
        sandbox = create_sandbox("microvm")
        assert isinstance(sandbox, MicroVMSandbox)
        assert isinstance(sandbox._driver, E2BDriver)

    def test_create_sandbox_unknown_raises(self):
        with pytest.raises(SandboxError, match="Unknown sandbox isolation"):
            create_sandbox("gVisor")

    def test_runtime_for_config(self):
        assert runtime_for_config({"isolation": "microvm"}) == "microvm"
        assert runtime_for_config({"isolation": "docker"}) == "docker"
        assert runtime_for_config({}) == "docker"
        assert runtime_for_config(None) == "docker"
        assert runtime_for_config("not-a-dict") == "docker"
        with pytest.raises(SandboxError, match="Unknown sandbox isolation"):
            runtime_for_config({"isolation": "firecracker"})

    def test_resolve_sandbox_docker(self):
        state = _AppState(sandbox="docker-sandbox", microvm_sandbox=None)
        assert resolve_sandbox(state, {"isolation": "docker"}) == "docker-sandbox"
        assert resolve_sandbox(state, {}) == "docker-sandbox"

    def test_resolve_sandbox_microvm(self):
        state = _AppState(sandbox="docker-sandbox", microvm_sandbox="vm-sandbox")
        assert resolve_sandbox(state, {"isolation": "microvm"}) == "vm-sandbox"

    def test_resolve_sandbox_microvm_disabled(self):
        state = _AppState(sandbox="docker-sandbox", microvm_sandbox=None)
        with pytest.raises(SandboxError, match="LINCHPIN_ENABLE_MICROVM"):
            resolve_sandbox(state, {"isolation": "microvm"})

    def test_resolve_sandbox_docker_disabled(self):
        state = _AppState(sandbox=None, microvm_sandbox=None)
        with pytest.raises(SandboxError, match="LINCHPIN_SKIP_DOCKER"):
            resolve_sandbox(state, {})

    def test_sandbox_for_session_routes_by_pinned_metadata(self):
        state = _AppState(sandbox="docker-sandbox", microvm_sandbox="vm-sandbox")

        row = {"metadata": {"sandbox_runtime": "microvm"}}
        assert sandbox_for_session(state, row) == "vm-sandbox"

        row = {"metadata": {"sandbox_runtime": "docker"}}
        assert sandbox_for_session(state, row) == "docker-sandbox"

        # Pre-C3 rows: no pin (or no metadata at all) default to docker.
        assert sandbox_for_session(state, {"metadata": {}}) == "docker-sandbox"
        assert sandbox_for_session(state, {}) == "docker-sandbox"
        assert sandbox_for_session(state, {"metadata": None}) == "docker-sandbox"

    def test_sandbox_for_session_microvm_disabled(self):
        state = _AppState(sandbox="docker-sandbox", microvm_sandbox=None)
        row = {"metadata": {"sandbox_runtime": "microvm"}}
        with pytest.raises(SandboxError, match="LINCHPIN_ENABLE_MICROVM"):
            sandbox_for_session(state, row)


# ---------------------------------------------------------------------------
# EnvironmentConfig.isolation
# ---------------------------------------------------------------------------


class TestEnvironmentIsolationField:
    def test_default_is_docker(self):
        config = EnvironmentConfig(networking={"type": "none"})
        assert config.isolation == "docker"

    def test_microvm_accepted(self):
        config = EnvironmentConfig(networking={"type": "none"}, isolation="microvm")
        assert config.isolation == "microvm"

    def test_unknown_isolation_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            EnvironmentConfig(networking={"type": "none"}, isolation="gVisor")


# ---------------------------------------------------------------------------
# Session lifecycle wiring (create / terminate / filesystem)
# ---------------------------------------------------------------------------


def _make_agent_row(agent_id):
    return {
        "id": uuid.UUID(agent_id),
        "name": "test-agent",
        "version": 1,
        "model": {"provider": "openrouter", "id": "anthropic/claude-sonnet-4", "base_url": None},
        "system": "You are helpful.",
        "tools": [],
        "mcp_servers": [],
        "created_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
    }


def _make_env_row(env_id, *, config=None):
    return {
        "id": uuid.UUID(env_id),
        "name": "test-env",
        "config": config or {"networking": {"type": "none"}},
        "created_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
    }


def _make_session_row(*, metadata=None, container_id="container-abc"):
    return {
        "id": uuid.UUID(SESSION_ID),
        "agent_id": uuid.uuid4(),
        "agent_version": 1,
        "environment_id": uuid.uuid4(),
        "status": "running",
        "container_id": container_id,
        "title": None,
        "metadata": metadata or {},
        "created_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
        "archived_at": None,
        "last_event_cursor": None,
        "ttl_seconds": None,
        "stats": {"total_events": 0, "tool_calls": 0, "model_turns": 0},
        "usage": {"input_tokens": 0, "output_tokens": 0,
                  "cache_creation_tokens": 0, "cache_read_tokens": 0},
        "vault_ids": [],
    }


@pytest.fixture()
def sessions_client(tmp_path):
    """TestClient with the session-route dependencies patched out.

    Mirrors the fixture in test_sessions.py so the create path can run
    end-to-end against mocked persistence; yields (client, docker_sandbox).
    """
    from pathlib import Path

    def _fake_ensure(sid):
        p = tmp_path / "session-outputs" / sid
        p.mkdir(parents=True, exist_ok=True)
        return p

    with (
        patch("app.main.check_migrations_current"),
        patch("app.main.create_pool", new_callable=AsyncMock),
        patch("app.main.close_pool", new_callable=AsyncMock),
        patch("app.main.ensure_docker_networks", new_callable=AsyncMock),
        patch("app.main.DockerSandbox") as MockSandbox,
        patch("app.main.cleanup_expired_sessions", new_callable=AsyncMock),
        patch("app.main.recover_sessions", new_callable=AsyncMock),
        patch("app.routes.sessions.run_session", new_callable=AsyncMock),
        patch("app.routes.sessions.ensure_session_outputs_dir", side_effect=_fake_ensure),
        patch("app.routes.sessions.watch_session_deliverables", new_callable=AsyncMock),
        patch("app.routes.sessions.execute", new_callable=AsyncMock),
        patch("app.routes.sessions.transition", new_callable=AsyncMock),
        patch("app.routes.sessions.append_event", new_callable=AsyncMock),
    ):
        mock_sandbox = MagicMock()
        mock_sandbox.create = AsyncMock(return_value="container-abc")
        mock_sandbox.destroy = AsyncMock()
        mock_sandbox.ensure_image = AsyncMock(
            side_effect=lambda *, base_image, packages: base_image
        )
        MockSandbox.return_value = mock_sandbox

        from app.main import app

        with TestClient(app) as c:
            yield c, mock_sandbox


def _insert_metadata_json(mock_fetch) -> dict:
    """Pull the metadata dict from the INSERT INTO sessions fetch call."""
    insert_calls = [
        c for c in mock_fetch.call_args_list
        if c.args and "INSERT INTO sessions" in c.args[0]
    ]
    assert len(insert_calls) == 1, (
        f"expected exactly one INSERT INTO sessions, got {len(insert_calls)}"
    )
    metadata_json = next(
        a for a in insert_calls[0].args[1:]
        if isinstance(a, str) and '"sandbox_runtime"' in a
    )
    return json.loads(metadata_json)


class TestSessionCreateRouting:
    def test_create_pins_docker_runtime_in_metadata(
        self, sessions_client,
    ):
        @patch("app.routes.sessions.fetch_one", new_callable=AsyncMock)
        def _run(mock_fetch):
            client, mock_sandbox = sessions_client
            agent_id, env_id = str(uuid.uuid4()), str(uuid.uuid4())
            session_row = _make_session_row()
            mock_fetch.side_effect = [
                _make_agent_row(agent_id), _make_env_row(env_id),
                session_row, session_row,
            ]

            resp = client.post(
                "/v1/sessions",
                json={"agent_id": agent_id, "environment_id": env_id},
                headers=AUTH,
            )

            assert resp.status_code == 201
            mock_sandbox.create.assert_awaited_once()
            assert _insert_metadata_json(mock_fetch)["sandbox_runtime"] == "docker"

        _run()

    def test_create_microvm_routes_to_microvm_sandbox(self, sessions_client):
        client, mock_sandbox = sessions_client

        @patch("app.routes.sessions.fetch_one", new_callable=AsyncMock)
        def _run(mock_fetch):
            agent_id, env_id = str(uuid.uuid4()), str(uuid.uuid4())
            session_row = _make_session_row(container_id="vm-1")
            mock_fetch.side_effect = [
                _make_agent_row(agent_id),
                _make_env_row(env_id, config={
                    "networking": {"type": "none"}, "isolation": "microvm",
                }),
                session_row, session_row,
            ]

            resp = client.post(
                "/v1/sessions",
                json={"agent_id": agent_id, "environment_id": env_id},
                headers=AUTH,
            )

            assert resp.status_code == 201
            assert _insert_metadata_json(mock_fetch)["sandbox_runtime"] == "microvm"

        mock_microvm = MagicMock()
        mock_microvm.ensure_image = AsyncMock(
            side_effect=lambda *, base_image, packages: base_image
        )
        mock_microvm.create = AsyncMock(return_value="vm-1")
        client.app.state.microvm_sandbox = mock_microvm

        _run()

        mock_microvm.create.assert_awaited_once()
        mock_sandbox.create.assert_not_called()
        client.app.state.microvm_sandbox = None

    def test_create_microvm_disabled_returns_503(self, sessions_client):
        client, mock_sandbox = sessions_client
        # Lifespan leaves microvm_sandbox None (LINCHPIN_ENABLE_MICROVM unset).

        @patch("app.routes.sessions.fetch_one", new_callable=AsyncMock)
        def _run(mock_fetch):
            agent_id, env_id = str(uuid.uuid4()), str(uuid.uuid4())
            mock_fetch.side_effect = [
                _make_agent_row(agent_id),
                _make_env_row(env_id, config={
                    "networking": {"type": "none"}, "isolation": "microvm",
                }),
            ]

            resp = client.post(
                "/v1/sessions",
                json={"agent_id": agent_id, "environment_id": env_id},
                headers=AUTH,
            )

            assert resp.status_code == 503
            # Atlas C3 — the app-wide error envelope is {error, message, detail}.
            assert resp.json()["error"] == "sandbox_runtime_unavailable"
            # Raised before the session row INSERT — no traceable orphan.
            assert len(mock_fetch.call_args_list) == 2
            mock_sandbox.create.assert_not_called()

        _run()

    def test_create_unknown_isolation_returns_503(self, sessions_client):
        client, _ = sessions_client

        @patch("app.routes.sessions.fetch_one", new_callable=AsyncMock)
        def _run(mock_fetch):
            agent_id, env_id = str(uuid.uuid4()), str(uuid.uuid4())
            mock_fetch.side_effect = [
                _make_agent_row(agent_id),
                _make_env_row(env_id, config={
                    "networking": {"type": "none"}, "isolation": "firecracker",
                }),
            ]

            resp = client.post(
                "/v1/sessions",
                json={"agent_id": agent_id, "environment_id": env_id},
                headers=AUTH,
            )

            assert resp.status_code == 503
            assert resp.json()["error"] == "sandbox_runtime_unavailable"

        _run()


class TestSessionLifecycleRouting:
    def test_terminate_routes_to_pinned_runtime(self, sessions_client):
        client, mock_sandbox = sessions_client

        @patch("app.routes.sessions.fetch_one", new_callable=AsyncMock)
        @patch("app.routes.sessions.fetch_all", new_callable=AsyncMock)
        def _run(mock_fetch_all, mock_fetch):
            session_row = _make_session_row(metadata={"sandbox_runtime": "microvm"})
            mock_fetch.side_effect = [session_row, {**session_row, "status": "terminated"}]
            mock_fetch_all.return_value = []

            resp = client.delete(f"/v1/sessions/{SESSION_ID}", headers=AUTH)

            assert resp.status_code == 200

        mock_microvm = MagicMock()
        mock_microvm.destroy = AsyncMock()
        client.app.state.microvm_sandbox = mock_microvm

        _run()

        mock_microvm.destroy.assert_awaited_once_with("container-abc")
        mock_sandbox.destroy.assert_not_called()
        client.app.state.microvm_sandbox = None

    def test_filesystem_routes_to_pinned_runtime(self, sessions_client):
        client, mock_sandbox = sessions_client

        @patch("app.routes.sessions.fetch_one", new_callable=AsyncMock)
        def _run(mock_fetch):
            session_row = {
                "id": uuid.UUID(SESSION_ID),
                "project_id": uuid.UUID(PROJECT_ID),
                "container_id": "vm-1",
                "status": "running",
                "metadata": {"sandbox_runtime": "microvm"},
            }
            mock_fetch.return_value = session_row

            resp = client.get(
                f"/v1/sessions/{SESSION_ID}/filesystem?path=/mnt/session",
                headers=AUTH,
            )

            assert resp.status_code == 200, resp.text
            assert len(resp.json()["entries"]) == 1

        mock_microvm = MagicMock()
        mock_microvm.list_dir = AsyncMock(return_value=[_entry("outputs", kind="dir")])
        client.app.state.microvm_sandbox = mock_microvm

        _run()

        mock_microvm.list_dir.assert_awaited_once_with("vm-1", "/mnt/session")
        mock_sandbox.list_dir.assert_not_called()
        client.app.state.microvm_sandbox = None
