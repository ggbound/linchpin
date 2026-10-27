"""Docker sandbox for per-session container management.

Provides a protocol-based abstraction over container runtimes and a
concrete Docker implementation using the docker-py SDK.

Requirements: 14.1, 14.2, 14.3, 14.5
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
import shlex
import tarfile
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import docker
import httpx
from docker.errors import APIError, BuildError, DockerException, ImageNotFound, NotFound

from app.models import EnvironmentPackages, SandboxResources, derived_image_tag


# v0.2.0 item #13 — sessions default to the richer linchpin-sandbox image
# (Python 3.12, Node 20, Go 1.22, Rust 1.77, Java 17, Ruby 3.1, PHP 8.2,
# GCC 13, plus psql/redis-cli/rg/tree/htop). Dockerfile lives at
# linchpin-sandbox/Dockerfile in the repo. Override with LINCHPIN_SANDBOX_IMAGE
# for tests or for ops to roll back to a slimmer image.
DEFAULT_BASE_IMAGE = "linchpinhq/sandbox:v0.2.0"

# Atlas B3 (FR-4.5) — user-facing sandbox spec tiers mapped to docker-py
# container resources. ``nano_cpus`` is CPU quota in 1e-9 cores (1e9 = 1
# core); ``mem_limit`` accepts docker-py size strings.
RESOURCE_TIERS: dict[str, dict] = {
    "1c2g": {"nano_cpus": 1_000_000_000, "mem_limit": "2g"},
    "2c4g": {"nano_cpus": 2_000_000_000, "mem_limit": "4g"},
    "4c8g": {"nano_cpus": 4_000_000_000, "mem_limit": "8g"},
}

logger = logging.getLogger("linchpin-api.sandbox")


@dataclass(frozen=True, slots=True)
class ExecResult:
    """Result of executing a command inside a container."""

    stdout: str
    stderr: str
    exit_code: int


@dataclass(frozen=True, slots=True)
class ResourceMount:
    """A single host->container bind mount for a session resource.

    v0.2 PR3 (per eng-review decision D1) uses plain Docker ``-v`` bind
    mounts rather than overlay2+tmpfs. ``mode='ro'`` is the default — file
    resources are read-only. The deliverables-output bind (mode='rw') is
    handled separately by PR5.
    """

    host_path: str
    container_path: str
    mode: Literal["ro", "rw"] = "ro"


class SandboxProtocol(Protocol):
    """Abstract sandbox interface for container runtimes."""

    async def create(
        self,
        image: str,
        network: str,
        mounts: Sequence[ResourceMount] | None = None,
        env: dict[str, str] | None = None,
    ) -> str:
        """Create and start a container, returning its id.

        ``mounts`` is an optional list of host->container bind mounts applied
        at boot. Empty/None preserves v0.1 behavior (no mounts).
        ``env`` is an optional mapping of environment variables injected
        into the container at creation time. Empty/None injects nothing.
        """
        ...

    async def ensure_image(
        self,
        *,
        base_image: str,
        packages: EnvironmentPackages | None,
    ) -> str:
        """Return the image tag to use for a session, building if needed.

        Empty/None ``packages`` returns ``base_image`` unchanged. Non-empty
        package sets are baked into a content-hashed derived image; the
        Docker daemon's image cache makes the second call a no-op.
        """
        ...

    async def exec(self, container_id: str, command: str) -> ExecResult:
        """Run *command* inside the container and return the result."""
        ...

    def active_exec_pid(self, container_id: str) -> int | None:
        """Return the tracked in-container PID of the running exec, if any.

        Atlas B2 — populated while :meth:`exec` is in flight; ``None``
        means no tracked command is running.
        """
        ...

    async def kill_exec(
        self, container_id: str, pid: int, grace: float = 3.0
    ) -> bool:
        """Terminate a tracked exec's process inside the container.

        Sends SIGTERM to the process (and its group), waits *grace*
        seconds, then SIGKILL if still alive. Best-effort: returns False
        when the container is gone or docker calls fail.
        """
        ...

    async def write_file(
        self, container_id: str, path: str, content: str
    ) -> None:
        """Write *content* to *path* inside the container."""
        ...

    async def read_file(self, container_id: str, path: str) -> str:
        """Read and return the contents of *path* from the container."""
        ...

    async def copy_from_container(
        self, container_id: str, path: str,
    ) -> bytes:
        """Binary-safe copy of a file's raw bytes out of the container."""
        ...

    async def list_dir(
        self, container_id: str, path: str,
    ) -> list[dict[str, Any]]:
        """List a directory inside the container (Atlas B5, FR-9.2)."""
        ...

    async def destroy(self, container_id: str) -> None:
        """Stop and remove the container."""
        ...


class DockerSandbox:
    """Docker-based sandbox using the docker-py SDK.

    All blocking docker-py calls are wrapped with ``asyncio.to_thread``
    so they can be used from async code without blocking the event loop.
    """

    def __init__(self, client: docker.DockerClient | None = None) -> None:
        self._client = client or docker.from_env()
        # Atlas B2 — container_id -> in-container PID of the exec currently
        # running via :meth:`exec`. Entries live only while the exec is in
        # flight; kill_exec/_exec_raw bypass this registry.
        self._active_pids: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def create(
        self,
        image: str,
        network: str,
        mounts: Sequence[ResourceMount] | None = None,
        env: dict[str, str] | None = None,
        *,
        resources: SandboxResources | None = None,
    ) -> str:
        """Pull (if needed) and start a container on *network*.

        ``mounts`` is applied as docker-py ``volumes={host: {bind, mode}}``.
        Each ResourceMount is validated for absolute paths; the FileStore
        path is the source of truth for host_path resolution at call sites.

        ``env`` is an optional mapping of environment variables injected
        into the container at creation time (docker-py ``environment=``).
        Empty/None injects nothing, preserving v0.1 behavior.

        ``resources`` (Atlas B3) selects the container spec tier; defaults
        to ``1c2g`` when omitted.

        Returns the container id.
        """
        image = image or os.getenv("LINCHPIN_SANDBOX_IMAGE", DEFAULT_BASE_IMAGE)
        volumes = self._build_volumes(mounts)
        tier = resources.tier if resources is not None else "1c2g"
        limits = RESOURCE_TIERS.get(tier)
        if limits is None:
            raise SandboxError(f"Unknown sandbox resource tier: {tier!r}")
        run_kwargs: dict = dict(
            command="sleep infinity",
            network=network,
            detach=True,
            stdin_open=True,
            tty=False,
            volumes=volumes,
            environment=dict(env) if env else None,
            **limits,
        )

        try:
            container = await asyncio.to_thread(
                self._client.containers.run, image, **run_kwargs
            )
            return container.id
        except ImageNotFound:
            # Attempt to pull the image and retry
            try:
                await asyncio.to_thread(self._client.images.pull, image)
            except DockerException as pull_err:
                raise SandboxError(
                    f"Failed to pull image '{image}': {pull_err}"
                ) from pull_err

            container = await asyncio.to_thread(
                self._client.containers.run, image, **run_kwargs
            )
            return container.id
        except DockerException as exc:
            raise SandboxError(
                f"Failed to create container from '{image}': {exc}"
            ) from exc

    async def ensure_image(
        self,
        *,
        base_image: str,
        packages: EnvironmentPackages | None,
    ) -> str:
        """Build (if absent) and return the derived-image tag for ``packages``.

        v0.2.0 item #3. The derived image is keyed by a content hash of the
        normalized package set, so two environments with the same package
        list share one image and the second session pays zero build cost.
        Empty/None packages returns ``base_image`` unchanged.
        """
        if packages is None or packages.is_empty():
            return base_image

        tag = derived_image_tag(packages)
        assert tag is not None, "non-empty packages must yield a tag"

        # Fast path: image already cached on this host's daemon.
        try:
            await asyncio.to_thread(self._client.images.get, tag)
            return tag
        except ImageNotFound:
            pass

        dockerfile = self._generate_dockerfile(base_image, packages)
        logger.info(
            "building derived sandbox image %s from %s (apt=%d pip=%d npm=%d "
            "cargo=%d gem=%d go=%d)",
            tag,
            base_image,
            len(packages.apt),
            len(packages.pip),
            len(packages.npm),
            len(packages.cargo),
            len(packages.gem),
            len(packages.go),
        )
        try:
            await asyncio.to_thread(
                self._build_image,
                dockerfile=dockerfile,
                tag=tag,
            )
        except BuildError as exc:
            raise SandboxError(
                f"Failed to build derived image {tag!r} from {base_image!r}: {exc}"
            ) from exc
        except DockerException as exc:
            raise SandboxError(
                f"Docker error while building {tag!r}: {exc}"
            ) from exc
        return tag

    @staticmethod
    def _generate_dockerfile(
        base_image: str, packages: EnvironmentPackages
    ) -> str:
        """Render a single-stage Dockerfile that installs *packages* on top
        of *base_image*.

        Package names are pre-validated against ``_PACKAGE_NAME_RE`` (in
        ``models.py``) so embedding them directly in the RUN line is safe.
        Each manager gets its own RUN to keep the image layer cache useful
        across edits that only touch one ecosystem.
        """
        lines: list[str] = [f"FROM {base_image}", ""]

        if packages.apt:
            joined = " ".join(packages.apt)
            lines.append(
                "RUN apt-get update "
                f"&& apt-get install -y --no-install-recommends {joined} "
                "&& rm -rf /var/lib/apt/lists/*"
            )
        if packages.pip:
            joined = " ".join(packages.pip)
            # --break-system-packages is required on Debian trixie's
            # PEP 668-marked python3.13. Safe inside the sandbox image.
            lines.append(
                f"RUN pip install --no-cache-dir --break-system-packages {joined}"
            )
        if packages.npm:
            joined = " ".join(packages.npm)
            lines.append(
                f"RUN npm install -g --no-fund --no-audit {joined}"
            )
        if packages.cargo:
            joined = " ".join(packages.cargo)
            lines.append(f"RUN cargo install --locked {joined}")
        if packages.gem:
            joined = " ".join(packages.gem)
            lines.append(f"RUN gem install --no-document {joined}")
        if packages.go:
            # `go install <pkg>@<ver>` is the modern (1.16+) form. One RUN
            # per package keeps each install's failure isolated in logs.
            for pkg in packages.go:
                lines.append(f"RUN go install {pkg}")

        return "\n".join(lines) + "\n"

    def _build_image(self, *, dockerfile: str, tag: str) -> None:
        """Synchronous docker-py build helper.

        Passes the rendered Dockerfile as ``fileobj`` — docker-py treats it
        as the build context when ``custom_context`` is False. No COPY/ADD
        instructions are emitted, so no extra files are needed in the tar.
        """
        fileobj = io.BytesIO(dockerfile.encode("utf-8"))
        self._client.images.build(
            fileobj=fileobj,
            tag=tag,
            rm=True,
            forcerm=True,
            pull=False,
        )

    @staticmethod
    def _build_volumes(
        mounts: Sequence[ResourceMount] | None,
    ) -> dict[str, dict[str, str]] | None:
        """Translate ResourceMount list into docker-py ``volumes`` dict.

        Returns ``None`` (not ``{}``) when mounts is empty so docker-py
        treats the container as un-mounted, matching v0.1 behavior.
        """
        if not mounts:
            return None
        volumes: dict[str, dict[str, str]] = {}
        for m in mounts:
            if not m.host_path.startswith("/"):
                raise SandboxError(
                    f"ResourceMount.host_path must be absolute, got {m.host_path!r}"
                )
            if not m.container_path.startswith("/"):
                raise SandboxError(
                    f"ResourceMount.container_path must be absolute, got {m.container_path!r}"
                )
            # docker-py's ``volumes=`` dict is keyed by host_path: two mounts
            # sharing a host_path silently collapse into the last one. The
            # FileStore is content-addressed, so two distinct file_ids can
            # share the same storage_path (and therefore the same host_path).
            # Refuse loudly rather than ship a session whose container is
            # missing a mount the API said was 'mounted'.
            if m.host_path in volumes:
                raise SandboxError(
                    "duplicate host_path in mounts: "
                    f"{m.host_path!r} appears for both "
                    f"{volumes[m.host_path]['bind']!r} and {m.container_path!r}"
                )
            volumes[m.host_path] = {"bind": m.container_path, "mode": m.mode}
        return volumes

    async def exec(self, container_id: str, command: str) -> ExecResult:
        """Execute *command* inside the container via a tracked exec.

        Atlas B2 — the command runs as ``setsid bash -c 'echo $$; ...'`` so
        its in-container PID is captured from the first stdout line and
        registered for :meth:`kill_exec` while the exec is in flight.
        """
        try:
            return await asyncio.to_thread(
                self._exec_stream_sync, container_id, command
            )
        except NotFound:
            raise SandboxError(f"Container '{container_id}' not found")
        except DockerException as exc:
            raise SandboxError(
                f"Failed to exec in container '{container_id}': {exc}"
            ) from exc

    def _exec_stream_sync(self, container_id: str, command: str) -> ExecResult:
        """Synchronous tracked-exec body for :meth:`exec` (runs in a thread).

        The inner bash becomes a session leader via ``setsid`` (so killing
        its process group reaps children too) and prints its PID as the
        first stdout line; that line is stripped from the command's real
        output. The PID is registered in ``self._active_pids`` as soon as
        it is parsed and dropped when the exec finishes.
        """
        # Raises NotFound when the container is gone (same as before).
        self._client.containers.get(container_id)
        inner = f"echo $$; {command}"
        cmd = ["bash", "-c", f"setsid bash -c {shlex.quote(inner)}"]
        exec_id = self._client.api.exec_create(
            container_id, cmd, stdout=True, stderr=True,
        )["Id"]
        stream = self._client.api.exec_start(exec_id, stream=True, demux=True)

        stdout_parts: list[bytes] = []
        stderr_parts: list[bytes] = []
        pid: int | None = None
        pid_failed = False
        pending = b""
        for out, err in stream:
            if err:
                stderr_parts.append(err)
            if not out:
                continue
            if pid is None and not pid_failed:
                # First stdout chunk: the leading line is the PID.
                pending += out
                nl = pending.find(b"\n")
                if nl == -1:
                    continue
                head, rest = pending[:nl], pending[nl + 1:]
                try:
                    pid = int(head.strip())
                except ValueError:
                    # First line wasn't a PID — keep it as plain output.
                    pid_failed = True
                    rest = pending
                if not pid_failed:
                    self._active_pids[container_id] = pid
                if rest:
                    stdout_parts.append(rest)
                pending = b""
            else:
                stdout_parts.append(out)
        if pending:
            # Stream ended without a complete first line — plain output.
            stdout_parts.append(pending)

        inspect = self._client.api.exec_inspect(exec_id)
        exit_code = inspect.get("ExitCode", -1)
        # Exec finished — drop the tracking entry (unless a newer exec on
        # the same container re-registered in the meantime).
        if pid is not None and self._active_pids.get(container_id) == pid:
            self._active_pids.pop(container_id, None)
        return ExecResult(
            stdout=b"".join(stdout_parts).decode("utf-8", errors="replace"),
            stderr=b"".join(stderr_parts).decode("utf-8", errors="replace"),
            exit_code=exit_code,
        )

    async def _exec_raw(self, container_id: str, command: str) -> ExecResult:
        """Plain untracked exec (``exec_run``) used by kill_exec probes.

        Deliberately bypasses the PID registry so control commands can't
        clobber the tracked exec they are managing.
        """
        try:
            container = await asyncio.to_thread(
                self._client.containers.get, container_id
            )
            exit_code, output = await asyncio.to_thread(
                container.exec_run,
                ["bash", "-c", command],
                demux=True,
            )
            stdout = (output[0] or b"").decode("utf-8", errors="replace")
            stderr = (output[1] or b"").decode("utf-8", errors="replace")
            return ExecResult(stdout=stdout, stderr=stderr, exit_code=exit_code)
        except NotFound:
            raise SandboxError(f"Container '{container_id}' not found")
        except DockerException as exc:
            raise SandboxError(
                f"Failed to exec in container '{container_id}': {exc}"
            ) from exc

    def active_exec_pid(self, container_id: str) -> int | None:
        """Return the tracked PID of the in-flight exec, or None."""
        return self._active_pids.get(container_id)

    async def kill_exec(
        self, container_id: str, pid: int, grace: float = 3.0
    ) -> bool:
        """Terminate a tracked exec inside the container (Atlas B2).

        Sequence: ``kill -TERM`` on the process group then the PID itself
        (the group kill covers children; both are best-effort), wait *grace*
        seconds, probe with ``kill -0``, and escalate to ``kill -KILL`` if
        the process is still alive. Returns False when the container is
        missing or docker calls fail — callers treat kill as best-effort.
        """
        try:
            await self._exec_raw(
                container_id,
                f"kill -TERM -{pid} 2>/dev/null; kill -TERM {pid} 2>/dev/null; true",
            )
            await asyncio.sleep(grace)
            probe = await self._exec_raw(
                container_id, f"kill -0 {pid} 2>/dev/null; echo $?"
            )
            if probe.stdout.strip() == "0":
                await self._exec_raw(
                    container_id,
                    f"kill -KILL -{pid} 2>/dev/null; kill -KILL {pid} 2>/dev/null; true",
                )
            return True
        except (SandboxError, DockerException):
            logger.warning(
                "Failed to kill exec pid %s in container %s",
                pid, container_id, exc_info=True,
            )
            return False
        finally:
            if self._active_pids.get(container_id) == pid:
                self._active_pids.pop(container_id, None)

    async def write_file(
        self, container_id: str, path: str, content: str
    ) -> None:
        """Write *content* to *path* inside the container using a tar archive."""
        try:
            container = await asyncio.to_thread(
                self._client.containers.get, container_id
            )

            # Build an in-memory tar with the file
            data = content.encode("utf-8")
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w") as tar:
                info = tarfile.TarInfo(name=os.path.basename(path))
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
            buf.seek(0)

            dest_dir = os.path.dirname(path) or "/"
            await asyncio.to_thread(container.put_archive, dest_dir, buf)
        except NotFound:
            raise SandboxError(f"Container '{container_id}' not found")
        except DockerException as exc:
            raise SandboxError(
                f"Failed to write file '{path}' in container '{container_id}': {exc}"
            ) from exc

    async def copy_from_container(
        self, container_id: str, path: str,
    ) -> bytes:
        """Binary-safe copy of a single file out of the container (Atlas B5).

        Streams the docker ``get_archive`` tar and returns the first
        member's raw bytes. ``read_file`` decodes this as UTF-8; artifact
        ingestion (``tools._save_artifact``) and the filesystem ``raw``
        download endpoint use the bytes form so binary files survive.
        """
        try:
            container = await asyncio.to_thread(
                self._client.containers.get, container_id
            )
            bits, stat = await asyncio.to_thread(container.get_archive, path)

            # The X-Docker-Container-Path-Stat header arrives before the tar
            # body, so a directory target can be rejected without streaming
            # (and tarring up) the whole subtree first. Go's FileMode marks
            # directories with 1<<31; some stat shapes expose raw Unix
            # S_IFDIR bits instead — accept either.
            mode = stat.get("mode", 0) if isinstance(stat, dict) else 0
            if (mode & 0o170000) == 0o040000 or (mode & (1 << 31)) != 0:
                raise SandboxError(
                    f"Cannot read '{path}': is a directory, not a regular file"
                )

            # bits is a generator of chunks; reassemble and extract
            raw = b"".join(bits)
            buf = io.BytesIO(raw)
            with tarfile.open(fileobj=buf, mode="r") as tar:
                member = tar.getmembers()[0]
                f = tar.extractfile(member)
                if f is None:
                    raise SandboxError(
                        f"Cannot read '{path}': not a regular file"
                    )
                return f.read()
        except NotFound:
            raise SandboxError(f"Container '{container_id}' not found")
        except DockerException as exc:
            raise SandboxError(
                f"Failed to read file '{path}' from container '{container_id}': {exc}"
            ) from exc

    async def read_file(self, container_id: str, path: str) -> str:
        """Read and return the contents of *path* from the container."""
        data = await self.copy_from_container(container_id, path)
        return data.decode("utf-8", errors="replace")

    async def list_dir(
        self, container_id: str, path: str,
    ) -> list[dict[str, Any]]:
        """List a directory inside the container (Atlas B5, FR-9.2).

        Runs ``ls -la --time-style=long-iso`` (the long-iso style yields a
        stable ``YYYY-MM-DD HH:MM`` timestamp that parses without locale
        drift) and returns one dict per entry:
        ``name``/``type``/``size``/``permissions``/``owner``/``group``/
        ``modified``/``link_target``. Raises ``SandboxError`` when the
        path doesn't exist or ls exits non-zero.
        """
        result = await self.exec(
            container_id, f"ls -la --time-style=long-iso {shlex.quote(path)}"
        )
        if result.exit_code != 0:
            raise SandboxError(
                f"Cannot list '{path}': {result.stderr.strip() or 'ls failed'}"
            )
        entries: list[dict[str, Any]] = []
        for line in result.stdout.splitlines():
            if not line.strip() or line.startswith("total "):
                continue
            # perms links owner group size date time name — name may contain
            # spaces, so split at most 7 times.
            parts = line.split(None, 7)
            if len(parts) < 8:
                continue
            perms, owner, group, size, date, time_, name = (
                parts[0], parts[2], parts[3], parts[4], parts[5], parts[6], parts[7],
            )
            if perms.startswith("d"):
                kind = "dir"
            elif perms.startswith("l"):
                kind = "symlink"
            elif perms.startswith("-"):
                kind = "file"
            else:
                kind = "other"
            link_target: str | None = None
            if kind == "symlink" and " -> " in name:
                name, link_target = name.split(" -> ", 1)
            entries.append({
                "name": name,
                "type": kind,
                "size": int(size) if size.isdigit() else 0,
                "permissions": perms,
                "owner": owner,
                "group": group,
                "modified": f"{date} {time_}",
                "link_target": link_target,
            })
        return entries

    async def destroy(self, container_id: str) -> None:
        """Stop and remove the container, releasing resources."""
        try:
            container = await asyncio.to_thread(
                self._client.containers.get, container_id
            )
            await asyncio.to_thread(container.stop, timeout=5)
            await asyncio.to_thread(container.remove, force=True)
        except NotFound:
            # Already gone — nothing to do
            pass
        except DockerException as exc:
            raise SandboxError(
                f"Failed to destroy container '{container_id}': {exc}"
            ) from exc


async def ensure_docker_networks(
    client: docker.DockerClient | None = None,
) -> None:
    """Pre-create the linchpin Docker networks if they don't already exist.

    Creates three networks:
    - ``linchpin-none``: internal bridge network (no external access)
    - ``linchpin-open``: bridge network (unrestricted external access)
    - ``linchpin-limited``: internal bridge network (v0.2.0 item #4 —
      surface for limited mode; v0.2.0 enforces same denial as `none`,
      v0.2.x adds the egress proxy that honors per-env allowlists).

    Idempotent — silently skips networks that already exist.

    Requirements: 14.4, 5.3, 5.4
    """
    _client = client or docker.from_env()

    # v0.2.0 item #4 — `linchpin-limited` lands here so the bridge exists
    # by the time the first limited-mode session boots. For v0.2.0 it's an
    # internal bridge (same denial level as `linchpin-none`); v0.2.x adds
    # the egress proxy that consults each env's allowlist on the wire.
    networks = [
        {"name": "linchpin-none", "driver": "bridge", "internal": True},
        {"name": "linchpin-open", "driver": "bridge", "internal": False},
        {"name": "linchpin-limited", "driver": "bridge", "internal": True},
    ]

    for net in networks:
        try:
            await asyncio.to_thread(
                _client.networks.create,
                net["name"],
                driver=net["driver"],
                internal=net["internal"],
                check_duplicate=True,
            )
            logging.getLogger("linchpin-api").info(
                "Created Docker network '%s'", net["name"]
            )
        except APIError as exc:
            # 409 Conflict means the network already exists — skip it
            if exc.status_code == 409:
                logging.getLogger("linchpin-api").info(
                    "Docker network '%s' already exists, skipping", net["name"]
                )
            else:
                raise SandboxError(
                    f"Failed to create Docker network '{net['name']}': {exc}"
                ) from exc


class SandboxError(Exception):
    """Descriptive error raised by sandbox operations (Req 14.5)."""


# ---------------------------------------------------------------------------
# Atlas C3 (FR-4.1 / §9.1) — microVM isolation
# ---------------------------------------------------------------------------
#
# Docker isolation (cgroups/namespaces + network policy) is not sufficient
# for hostile multi-tenant SaaS workloads. MicroVMSandbox routes a session
# into a hardware-virtualized microVM (E2B self-hosted / Firecracker family)
# behind the same SandboxProtocol interface, selected per environment via
# ``EnvironmentConfig.isolation``.
#
# Semantics (documented trade-offs, not accidental gaps):
# - ro mounts  → snapshot-injected into the VM filesystem at boot.
# - rw mounts  → existing host content is injected, and the tree is flushed
#   back to the host on destroy(). Deliverables / memory-store watchers that
#   poll host dirs therefore see writes only after session teardown (the
#   Docker bind-mount semantics degrade; M4 BYO executor restores streaming).
# - ensure_image with non-empty packages raises: microVMs boot a pre-baked
#   image; per-session package baking stays a Docker capability.
# - in-VM exec PID tracking is driver-dependent; the E2B driver cannot kill
#   individual in-VM processes, so kill_exec reports False (interrupt flows
#   already tolerate that) and the exec runs to completion.

# VM sizing mirrors RESOURCE_TIERS so a tier swap doesn't change pricing
# semantics — vCPUs and RAM map 1:1 from the Docker tiers.
MICROVM_RESOURCE_TIERS: dict[str, dict] = {
    "1c2g": {"vcpu": 1, "mem_mib": 2048},
    "2c4g": {"vcpu": 2, "mem_mib": 4096},
    "4c8g": {"vcpu": 4, "mem_mib": 8192},
}


@dataclass(frozen=True, slots=True)
class MicroVMSpec:
    """Boot spec handed to a MicroVMDriver."""

    image: str
    env: dict[str, str]
    vcpu: int
    mem_mib: int


class MicroVMDriver(Protocol):
    """Narrow driver interface over a microVM provider.

    Drivers implement only per-VM atomic operations; runtime selection,
    mount injection/flush and PID bookkeeping live in MicroVMSandbox so
    swapping providers (E2B → raw Firecracker) stays a leaf change.
    """

    async def create_vm(self, spec: MicroVMSpec) -> str:
        """Boot a VM and return its opaque id."""
        ...

    async def exec_in_vm(self, vm_id: str, command: str) -> ExecResult:
        """Run *command* inside the VM and return the result."""
        ...

    async def kill_vm_process(
        self, vm_id: str, pid: int, grace: float = 3.0
    ) -> bool:
        """Terminate an in-VM process group. False when unsupported/gone."""
        ...

    async def write_vm_file(self, vm_id: str, path: str, content: str) -> None:
        """Write text content to *path* inside the VM (parents created)."""
        ...

    async def read_vm_file(self, vm_id: str, path: str) -> str:
        """Read text content of *path* from the VM."""
        ...

    async def copy_vm_file(self, vm_id: str, path: str) -> bytes:
        """Binary-safe read of *path* from the VM."""
        ...

    async def list_vm_dir(self, vm_id: str, path: str) -> list[dict[str, Any]]:
        """List a directory inside the VM (same entry shape as list_dir)."""
        ...

    async def destroy_vm(self, vm_id: str) -> None:
        """Terminate the VM. Destroying is the isolation boundary reset."""
        ...


class E2BDriver:
    """MicroVMDriver over the E2B self-hosted SDK (imported lazily).

    The SDK is an optional dependency — constructing the driver never
    imports it; the first operation does, and a missing SDK surfaces as a
    descriptive SandboxError instead of an ImportError at boot time.
    """

    def _sdk(self):
        try:
            import e2b  # noqa: PLC0415 — optional dependency, lazy import
        except ImportError as exc:  # pragma: no cover - exercised via monkeypatch
            raise SandboxError(
                "microvm isolation requires the e2b SDK (pip install e2b); "
                "install it or keep LINCHPIN_ENABLE_MICROVM=false"
            ) from exc
        return e2b

    async def create_vm(self, spec: MicroVMSpec) -> str:
        e2b = self._sdk()
        timeout = int(os.getenv("LINCHPIN_E2B_TIMEOUT_SEC", "0")) or None

        def _boot() -> Any:
            return e2b.Sandbox(
                image=spec.image,
                envs=dict(spec.env) if spec.env else None,
                timeout=timeout,
            )

        vm = await asyncio.to_thread(_boot)
        return vm.sandbox_id

    async def exec_in_vm(self, vm_id: str, command: str) -> ExecResult:
        e2b = self._sdk()
        vm = await asyncio.to_thread(e2b.Sandbox, sandbox_id=vm_id)
        result = await asyncio.to_thread(
            lambda: vm.commands.run(command, on_stderr=lambda line: None)
        )
        return ExecResult(
            stdout=result.stdout or "",
            stderr=result.stderr or "",
            exit_code=result.exit_code,
        )

    async def kill_vm_process(
        self, vm_id: str, pid: int, grace: float = 3.0
    ) -> bool:
        # The E2B SDK exposes no per-process handle inside the VM; the
        # session-level exec cannot be individually terminated.
        logger.warning(
            "E2BDriver.kill_vm_process is unsupported (pid %s in %s); "
            "the exec will run to completion.", pid, vm_id,
        )
        return False

    async def write_vm_file(self, vm_id: str, path: str, content: str) -> None:
        e2b = self._sdk()
        vm = await asyncio.to_thread(e2b.Sandbox, sandbox_id=vm_id)
        await asyncio.to_thread(lambda: vm.files.write(path, content))

    async def read_vm_file(self, vm_id: str, path: str) -> str:
        e2b = self._sdk()
        vm = await asyncio.to_thread(e2b.Sandbox, sandbox_id=vm_id)
        return await asyncio.to_thread(lambda: vm.files.read(path))

    async def copy_vm_file(self, vm_id: str, path: str) -> bytes:
        e2b = self._sdk()
        vm = await asyncio.to_thread(e2b.Sandbox, sandbox_id=vm_id)
        return await asyncio.to_thread(lambda: vm.files.read(path, format="binary"))

    async def list_vm_dir(self, vm_id: str, path: str) -> list[dict[str, Any]]:
        # Reuse the stable `ls -la --time-style=long-iso` parser from the
        # Docker implementation by shelling out inside the VM.
        e2b = self._sdk()
        vm = await asyncio.to_thread(e2b.Sandbox, sandbox_id=vm_id)
        result = await asyncio.to_thread(
            lambda: vm.commands.run(
                f"ls -la --time-style=long-iso {shlex.quote(path)}"
            )
        )
        if result.exit_code != 0:
            raise SandboxError(
                f"Cannot list '{path}': {(result.stderr or '').strip() or 'ls failed'}"
            )
        return _parse_ls_la(result.stdout or "")

    async def destroy_vm(self, vm_id: str) -> None:
        e2b = self._sdk()
        vm = await asyncio.to_thread(e2b.Sandbox, sandbox_id=vm_id)
        await asyncio.to_thread(lambda: vm.kill())


def _parse_ls_la(stdout: str) -> list[dict[str, Any]]:
    """Parse `ls -la --time-style=long-iso` output into entry dicts.

    Shared by DockerSandbox.list_dir and the microVM drivers so the B5
    filesystem surface returns an identical shape across runtimes.
    """
    entries: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        if not line.strip() or line.startswith("total "):
            continue
        # perms links owner group size date time name — name may contain
        # spaces, so split at most 7 times.
        parts = line.split(None, 7)
        if len(parts) < 8:
            continue
        perms, owner, group, size, date, time_, name = (
            parts[0], parts[2], parts[3], parts[4], parts[5], parts[6], parts[7],
        )
        if perms.startswith("d"):
            kind = "dir"
        elif perms.startswith("l"):
            kind = "symlink"
        elif perms.startswith("-"):
            kind = "file"
        else:
            kind = "other"
        link_target: str | None = None
        if kind == "symlink" and " -> " in name:
            name, link_target = name.split(" -> ", 1)
        entries.append({
            "name": name,
            "type": kind,
            "size": int(size) if size.isdigit() else 0,
            "permissions": perms,
            "owner": owner,
            "group": group,
            "modified": f"{date} {time_}",
            "link_target": link_target,
        })
    return entries


class MicroVMSandbox:
    """SandboxProtocol implementation backed by hardware-isolated microVMs.

    Wraps a :class:`MicroVMDriver`; all Docker-specific policy (mount
    injection, resource tiers, deliverables flush) lives here so driver
    implementations stay thin.
    """

    def __init__(self, driver: MicroVMDriver) -> None:
        self._driver = driver
        # vm_id -> pid of the exec currently in flight (driver-dependent).
        self._active_pids: dict[str, int] = {}
        # vm_id -> [(container_path, host_path)] for rw mounts, flushed
        # back to the host on destroy (see module docstring).
        self._rw_mounts: dict[str, list[ResourceMount]] = {}

    # ------------------------------------------------------------------
    # Public API (SandboxProtocol)
    # ------------------------------------------------------------------

    async def create(
        self,
        image: str,
        network: str,
        mounts: Sequence[ResourceMount] | None = None,
        env: dict[str, str] | None = None,
        *,
        resources: SandboxResources | None = None,
    ) -> str:
        image = image or os.getenv("LINCHPIN_SANDBOX_IMAGE", DEFAULT_BASE_IMAGE)
        tier = resources.tier if resources is not None else "1c2g"
        limits = MICROVM_RESOURCE_TIERS.get(tier)
        if limits is None:
            raise SandboxError(f"Unknown sandbox resource tier: {tier!r}")

        # Docker bridges don't exist inside a microVM host; network policy
        # for microVM sessions comes from the driver's host networking plus
        # the egress proxy env vars already injected via ``env``.
        if network not in ("linchpin-none", "linchpin-open", "linchpin-limited"):
            raise SandboxError(f"Unknown sandbox network: {network!r}")

        mounts = list(mounts or [])
        spec = MicroVMSpec(
            image=image,
            env=dict(env) if env else {},
            vcpu=limits["vcpu"],
            mem_mib=limits["mem_mib"],
        )
        try:
            vm_id = await self._driver.create_vm(spec)
        except SandboxError:
            raise
        except Exception as exc:
            raise SandboxError(
                f"Failed to create microVM from '{image}': {exc}"
            ) from exc

        try:
            for mount in mounts:
                if mount.mode == "rw":
                    self._rw_mounts.setdefault(vm_id, []).append(mount)
                if os.path.isfile(mount.host_path):
                    with open(mount.host_path, "r", encoding="utf-8") as fh:
                        content = fh.read()
                    await self._driver.write_vm_file(
                        vm_id, mount.container_path, content
                    )
        except Exception as exc:
            await self._destroy_quietly(vm_id)
            raise SandboxError(
                f"Failed to apply mounts to microVM '{vm_id}': {exc}"
            ) from exc
        return vm_id

    async def ensure_image(
        self,
        *,
        base_image: str,
        packages: EnvironmentPackages | None,
    ) -> str:
        """Return the VM image to boot.

        microVMs boot pre-baked images; per-session package baking is a
        Docker capability. Non-empty package sets are rejected so callers
        fail fast instead of silently losing their packages.
        """
        if packages is None or packages.is_empty():
            return base_image or os.getenv("LINCHPIN_SANDBOX_IMAGE", DEFAULT_BASE_IMAGE)
        raise SandboxError(
            "environment packages are not supported with isolation=microvm; "
            "bake them into the VM image or use isolation=docker"
        )

    async def exec(self, container_id: str, command: str) -> ExecResult:
        return await self._driver.exec_in_vm(container_id, command)

    def active_exec_pid(self, container_id: str) -> int | None:
        return self._active_pids.get(container_id)

    async def kill_exec(
        self, container_id: str, pid: int, grace: float = 3.0
    ) -> bool:
        killed = await self._driver.kill_vm_process(container_id, pid, grace)
        if killed:
            self._active_pids.pop(container_id, None)
        return killed

    async def write_file(
        self, container_id: str, path: str, content: str
    ) -> None:
        await self._driver.write_vm_file(container_id, path, content)

    async def read_file(self, container_id: str, path: str) -> str:
        return await self._driver.read_vm_file(container_id, path)

    async def copy_from_container(
        self, container_id: str, path: str,
    ) -> bytes:
        return await self._driver.copy_vm_file(container_id, path)

    async def list_dir(
        self, container_id: str, path: str,
    ) -> list[dict[str, Any]]:
        return await self._driver.list_vm_dir(container_id, path)

    async def destroy(self, container_id: str) -> None:
        await self._flush_rw_mounts(container_id)
        try:
            await self._driver.destroy_vm(container_id)
        finally:
            self._active_pids.pop(container_id, None)
            self._rw_mounts.pop(container_id, None)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _flush_rw_mounts(self, vm_id: str) -> None:
        """Mirror rw-mounted VM trees back to their host dirs (best effort).

        Deliverables and memory-store watchers poll host directories, which
        a microVM cannot bind-mount into; this one-shot pull at teardown is
        the M4 bridge until BYO executor restores streaming watchers.
        """
        mounts = self._rw_mounts.get(vm_id) or []
        for mount in mounts:
            try:
                await self._pull_tree(vm_id, mount.container_path, mount.host_path)
            except Exception as exc:
                logger.warning(
                    "rw mount flush failed for VM %s (%s -> %s): %s",
                    vm_id, mount.container_path, mount.host_path, exc,
                )

    async def _pull_tree(self, vm_id: str, vm_path: str, host_dir: str) -> None:
        """Recursively copy *vm_path* into host_dir (files only)."""
        entries = await self._driver.list_vm_dir(vm_id, vm_path)
        for entry in entries:
            name = entry["name"]
            vm_child = f"{vm_path.rstrip('/')}/{name}"
            host_child = os.path.join(host_dir, name)
            if entry["type"] == "dir":
                os.makedirs(host_child, exist_ok=True)
                await self._pull_tree(vm_id, vm_child, host_child)
            elif entry["type"] in ("file", "other"):
                data = await self._driver.copy_vm_file(vm_id, vm_child)
                await asyncio.to_thread(self._write_host_file, host_child, data)

    @staticmethod
    def _write_host_file(host_path: str, data: bytes) -> None:
        os.makedirs(os.path.dirname(host_path), exist_ok=True)
        with open(host_path, "wb") as fh:
            fh.write(data)

    async def _destroy_quietly(self, vm_id: str) -> None:
        try:
            await self._driver.destroy_vm(vm_id)
        except Exception:
            logger.warning("Failed to clean up microVM %s after mount error", vm_id)


class RemoteExecutorSandbox:
    """SandboxProtocol implementation proxied to a customer-run executor daemon.

    Atlas M4 (FR-4.6) — BYO executor. Sessions pinned to the ``remote``
    runtime never touch this host's Docker daemon: every SandboxProtocol
    call becomes an authenticated HTTP request (``Authorization: Bearer
    <node token>``) against the daemon exposed at the node's ``url``
    (``uvicorn app.executor_daemon:app``).

    Host files cannot be bind-mounted into a daemon we don't own, so
    :meth:`create` packs mount trees into the provisioning request and
    :meth:`destroy` flushes rw-mount trees back to the API host — the
    same one-shot semantics MicroVMSandbox uses for deliverables.
    """

    def __init__(
        self, url: str, token: str, timeout: float = 300.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = url.rstrip("/")
        self._token = token
        self._timeout = timeout
        # Injectable httpx transport (tests use MockTransport; None = real).
        self._transport = transport
        # container_id -> rw mounts, flushed back to the host on destroy
        # (mirrors MicroVMSandbox._rw_mounts).
        self._rw_mounts: dict[str, list[ResourceMount]] = {}

    # ------------------------------------------------------------------
    # HTTP plumbing
    # ------------------------------------------------------------------

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                response = await client.request(method, path, **kwargs)
                response.raise_for_status()
                return response
        except httpx.HTTPStatusError as exc:
            detail = (exc.response.text or "")[:300]
            raise SandboxError(
                f"executor daemon returned {exc.response.status_code} "
                f"for {method} {path}: {detail}"
            ) from exc
        except httpx.TransportError as exc:
            raise SandboxError(
                f"executor daemon unreachable at {self._base_url}: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Public API (SandboxProtocol)
    # ------------------------------------------------------------------

    async def create(
        self,
        image: str,
        network: str,
        mounts: Sequence[ResourceMount] | None = None,
        env: dict[str, str] | None = None,
        *,
        resources: SandboxResources | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "image": image or os.getenv("LINCHPIN_SANDBOX_IMAGE", DEFAULT_BASE_IMAGE),
            "network": network,
            "env": dict(env) if env else {},
        }
        if resources is not None:
            payload["resources"] = {"tier": resources.tier}
        mount_list = list(mounts or [])
        try:
            payload["files"] = await asyncio.to_thread(
                self._pack_mounts, mount_list
            )
        except OSError as exc:
            raise SandboxError(
                f"Failed to read mount files for executor upload: {exc}"
            ) from exc
        response = await self._request("POST", "/v1/containers", json=payload)
        container_id = str((response.json() or {}).get("container_id", ""))
        if not container_id:
            raise SandboxError(
                "executor daemon returned no container_id for session create"
            )
        for mount in mount_list:
            if mount.mode == "rw":
                self._rw_mounts.setdefault(container_id, []).append(mount)
        return container_id

    async def ensure_image(
        self,
        *,
        base_image: str,
        packages: EnvironmentPackages | None,
    ) -> str:
        """Return the image the daemon should boot.

        Package baking is a local-Docker capability; daemons in M4 boot
        pre-pulled images only. Non-empty package sets are rejected so
        callers fail fast instead of silently losing their packages.
        """
        if packages is None or packages.is_empty():
            return base_image or os.getenv("LINCHPIN_SANDBOX_IMAGE", DEFAULT_BASE_IMAGE)
        raise SandboxError(
            "environment packages are not supported with executor nodes; "
            "bake them into the image or use local isolation"
        )

    async def exec(self, container_id: str, command: str) -> ExecResult:
        response = await self._request(
            "POST", f"/v1/containers/{container_id}/exec",
            json={"command": command},
        )
        data = response.json() or {}
        return ExecResult(
            stdout=str(data.get("stdout", "")),
            stderr=str(data.get("stderr", "")),
            exit_code=int(data.get("exit_code", -1)),
        )

    def active_exec_pid(self, container_id: str) -> int | None:
        # Remote execs are tracked daemon-side; the API process holds no
        # in-flight PID state for them.
        return None

    async def kill_exec(
        self, container_id: str, pid: int, grace: float = 3.0
    ) -> bool:
        try:
            response = await self._request(
                "POST", f"/v1/containers/{container_id}/kill",
                json={"pid": pid, "grace": grace},
            )
        except SandboxError:
            return False
        return bool((response.json() or {}).get("killed", False))

    async def write_file(
        self, container_id: str, path: str, content: str
    ) -> None:
        await self._request(
            "PUT", f"/v1/containers/{container_id}/files",
            params={"path": path},
            content=content.encode("utf-8"),
        )

    async def read_file(self, container_id: str, path: str) -> str:
        response = await self._request(
            "GET", f"/v1/containers/{container_id}/files",
            params={"path": path},
        )
        return response.text

    async def copy_from_container(
        self, container_id: str, path: str,
    ) -> bytes:
        response = await self._request(
            "GET", f"/v1/containers/{container_id}/files",
            params={"path": path},
        )
        return response.content

    async def list_dir(
        self, container_id: str, path: str,
    ) -> list[dict[str, Any]]:
        response = await self._request(
            "GET", f"/v1/containers/{container_id}/tree",
            params={"path": path},
        )
        return list((response.json() or {}).get("entries", []))

    async def destroy(self, container_id: str) -> None:
        await self._flush_rw_mounts(container_id)
        try:
            await self._request("DELETE", f"/v1/containers/{container_id}")
        finally:
            self._rw_mounts.pop(container_id, None)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _pack_mounts(mounts: list[ResourceMount]) -> list[dict[str, str]]:
        """Serialize mount trees into base64 file entries for the daemon.

        Single-file mounts land exactly at ``container_path``; directory
        mounts are walked recursively and re-created relative to it.
        """
        files: list[dict[str, str]] = []
        for mount in mounts:
            host = mount.host_path
            if os.path.isfile(host):
                files.append({
                    "path": mount.container_path,
                    "content_b64": _read_file_b64(host),
                })
            elif os.path.isdir(host):
                root = host.rstrip(os.sep)
                for dirpath, _dirnames, filenames in os.walk(host):
                    for filename in filenames:
                        abs_path = os.path.join(dirpath, filename)
                        rel = os.path.relpath(abs_path, root)
                        target = f"{mount.container_path.rstrip('/')}/{rel}"
                        files.append({
                            "path": target,
                            "content_b64": _read_file_b64(abs_path),
                        })
        return files

    async def _flush_rw_mounts(self, container_id: str) -> None:
        """Mirror rw-mounted container trees back to their host dirs."""
        mounts = self._rw_mounts.get(container_id) or []
        for mount in mounts:
            try:
                await self._pull_tree(
                    container_id, mount.container_path, mount.host_path
                )
            except Exception as exc:
                logger.warning(
                    "rw mount flush failed for remote container %s "
                    "(%s -> %s): %s",
                    container_id, mount.container_path, mount.host_path, exc,
                )

    async def _pull_tree(
        self, container_id: str, remote_path: str, host_dir: str
    ) -> None:
        """Recursively copy *remote_path* into host_dir (files only)."""
        entries = await self.list_dir(container_id, remote_path)
        for entry in entries:
            name = entry["name"]
            remote_child = f"{remote_path.rstrip('/')}/{name}"
            host_child = os.path.join(host_dir, name)
            if entry["type"] == "dir":
                os.makedirs(host_child, exist_ok=True)
                await self._pull_tree(container_id, remote_child, host_child)
            elif entry["type"] in ("file", "other"):
                data = await self.copy_from_container(container_id, remote_child)
                await asyncio.to_thread(self._write_host_file, host_child, data)

    @staticmethod
    def _write_host_file(host_path: str, data: bytes) -> None:
        os.makedirs(os.path.dirname(host_path), exist_ok=True)
        with open(host_path, "wb") as fh:
            fh.write(data)


def _read_file_b64(host_path: str) -> str:
    with open(host_path, "rb") as fh:
        return base64.b64encode(fh.read()).decode("ascii")


# ---------------------------------------------------------------------------
# Runtime selection (Atlas C3)
# ---------------------------------------------------------------------------


def create_sandbox(isolation: str) -> DockerSandbox | MicroVMSandbox:
    """Build the sandbox runtime named by *isolation*.

    "docker" → cgroups-isolated DockerSandbox; "microvm" → hardware-
    isolated MicroVMSandbox over the E2B driver. Unknown names raise —
    configuration typos must not silently downgrade isolation.
    """
    if isolation == "docker":
        return DockerSandbox()
    if isolation == "microvm":
        return MicroVMSandbox(E2BDriver())
    raise SandboxError(f"Unknown sandbox isolation: {isolation!r}")


def _isolation_for_config(env_config: Any) -> str:
    """Extract the isolation name from a raw environment config mapping."""
    if isinstance(env_config, dict):
        return env_config.get("isolation") or "docker"
    return "docker"


def runtime_for_config(env_config: Any) -> str:
    """Validate and return the isolation name for an environment config.

    Atlas M4 (FR-4.6) — a non-empty ``executor_node`` pins the runtime to
    ``remote`` regardless of ``isolation`` (the node carries its own
    Docker daemon; local isolation settings don't apply).
    """
    if isinstance(env_config, dict) and env_config.get("executor_node"):
        return "remote"
    isolation = _isolation_for_config(env_config)
    if isolation not in ("docker", "microvm"):
        raise SandboxError(f"Unknown sandbox isolation: {isolation!r}")
    return isolation


def sandbox_for_runtime(app_state: Any, runtime: str) -> DockerSandbox | MicroVMSandbox:
    """Resolve a runtime name to the app-level sandbox instance.

    Raises SandboxError when the deployment doesn't run the requested
    runtime (LINCHPIN_SKIP_DOCKER=true or LINCHPIN_ENABLE_MICROVM unset).
    """
    if runtime == "docker":
        sandbox = getattr(app_state, "sandbox", None)
        if sandbox is None:
            raise SandboxError(
                "Docker sandbox is unavailable on this instance "
                "(LINCHPIN_SKIP_DOCKER=true)"
            )
        return sandbox
    if runtime == "microvm":
        sandbox = getattr(app_state, "microvm_sandbox", None)
        if sandbox is None:
            raise SandboxError(
                "microVM sandbox is not enabled on this instance "
                "(set LINCHPIN_ENABLE_MICROVM=true)"
            )
        return sandbox
    if runtime == "remote":
        # Remote sandboxes are per-node, not app-level singletons — they
        # must be rebuilt from the executor_nodes row via
        # app.sandbox_resolver.resolve_session_sandbox.
        raise SandboxError(
            "remote runtime sandboxes are resolved per executor node, "
            "not from app state"
        )
    raise SandboxError(f"Unknown sandbox isolation: {runtime!r}")


def resolve_sandbox(app_state: Any, env_config: Any) -> DockerSandbox | MicroVMSandbox:
    """Pick the sandbox runtime for a session created from *env_config*."""
    return sandbox_for_runtime(app_state, runtime_for_config(env_config))


def remote_sandbox_for_node_row(row: Any) -> RemoteExecutorSandbox:
    """Rebuild a RemoteExecutorSandbox from an ``executor_nodes`` row.

    The node token is stored Fernet-encrypted (the API must present the
    plaintext to the daemon on every call), so decryption failure —
    missing VAULT_ENCRYPTION_KEY — surfaces as a SandboxError.
    """
    from app.encryption import get_encryption_service

    try:
        token = get_encryption_service().decrypt(row["token_encrypted"])
    except RuntimeError as exc:
        raise SandboxError(
            "VAULT_ENCRYPTION_KEY is not configured; executor nodes "
            "cannot be reached"
        ) from exc
    return RemoteExecutorSandbox(url=row["url"], token=token)


def sandbox_for_session(app_state: Any, session_row: Any) -> DockerSandbox | MicroVMSandbox:
    """Pick the sandbox runtime that owns an existing session.

    The runtime is pinned at creation time in ``session.metadata``
    ("sandbox_runtime") — environments are editable, so re-reading their
    isolation later could misroute a live VM/container to the wrong
    backend. Rows created before C3 default to "docker".
    """
    metadata = session_row.get("metadata") if hasattr(session_row, "get") else None
    runtime = metadata.get("sandbox_runtime") if isinstance(metadata, dict) else None
    return sandbox_for_runtime(app_state, runtime or "docker")
