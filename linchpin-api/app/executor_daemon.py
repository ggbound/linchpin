"""Standalone executor daemon for BYO executor nodes (Atlas M4, FR-4.6).

Run on the executor host:

    LINCHPIN_EXECUTOR_TOKEN=<node token> \
        uvicorn app.executor_daemon:app --host 0.0.0.0 --port 8100

The daemon embeds a local :class:`DockerSandbox` and exposes the
SandboxProtocol surface over HTTP for a remote Linchpin API:

    GET    /v1/ping                              — liveness (no auth)
    POST   /v1/containers                        — create + inject files
    POST   /v1/containers/{id}/exec              — run a command
    POST   /v1/containers/{id}/kill              — kill a tracked exec PID
    PUT    /v1/containers/{id}/files?path=...    — write bytes
    GET    /v1/containers/{id}/files?path=...    — read bytes
    GET    /v1/containers/{id}/tree?path=...     — list a directory
    DELETE /v1/containers/{id}                   — destroy

Every container endpoint requires ``Authorization: Bearer
$LINCHPIN_EXECUTOR_TOKEN`` (the token issued at node registration). When
``LINCHPIN_API_URL`` + ``LINCHPIN_EXECUTOR_NODE_ID`` are also configured,
an optional heartbeat loop refreshes the node's liveness every 30s.
"""

from __future__ import annotations

import asyncio
import base64
import hmac
import io
import logging
import os
import shlex
import tarfile
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from pydantic import BaseModel

from app.sandbox import (
    DockerSandbox,
    SandboxError,
    ensure_docker_networks,
)
from app.models import SandboxResources

logger = logging.getLogger("linchpin-api.executor-daemon")

_HEARTBEAT_INTERVAL_SEC = 30.0


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class ResourceSpec(BaseModel):
    tier: str


class FileEntry(BaseModel):
    path: str
    content_b64: str


class CreateContainerRequest(BaseModel):
    image: str
    network: str
    env: dict[str, str] = {}
    resources: ResourceSpec | None = None
    files: list[FileEntry] = []


class ExecRequest(BaseModel):
    command: str


class KillRequest(BaseModel):
    pid: int
    grace: float = 3.0


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def require_daemon_auth(request: Request) -> None:
    """Bearer-token gate shared by every container endpoint."""
    expected = os.environ.get("LINCHPIN_EXECUTOR_TOKEN", "")
    if not expected:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "executor_token_unconfigured",
                "message": "LINCHPIN_EXECUTOR_TOKEN is not set on the daemon",
            },
        )
    header = request.headers.get("authorization", "")
    parts = header.split(" ", 1)
    supplied = parts[1] if len(parts) == 2 and parts[0].lower() == "bearer" else ""
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=401,
            detail={"error": "unauthorized", "message": "Invalid executor token"},
        )


def _sandbox(request: Request) -> DockerSandbox:
    sandbox = getattr(request.app.state, "sandbox", None)
    if sandbox is None:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "sandbox_unavailable",
                "message": "Docker is unavailable on this executor host",
            },
        )
    return sandbox


# ---------------------------------------------------------------------------
# Container file plumbing
# ---------------------------------------------------------------------------


async def _inject_files(
    sandbox: DockerSandbox,
    container_id: str,
    files: list[FileEntry],
) -> None:
    """Inject base64 file entries into the container (best-effort rollback)."""
    import docker as _docker

    client = _docker.from_env()
    try:
        for entry in files:
            path = entry.path
            if not path.startswith("/"):
                raise SandboxError(f"file path must be absolute: {path!r}")
            data = base64.b64decode(entry.content_b64)
            directory = os.path.dirname(path) or "/"
            result = await sandbox.exec(
                container_id, f"mkdir -p {shlex_quote(directory)}"
            )
            if result.exit_code != 0:
                raise SandboxError(
                    f"mkdir -p {directory} failed: {result.stderr.strip()}"
                )
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode="w") as tar:
                info = tarfile.TarInfo(name=os.path.basename(path))
                info.size = len(data)
                info.mtime = int(time.time())
                tar.addfile(info, io.BytesIO(data))
            container = await asyncio.to_thread(
                client.containers.get, container_id
            )
            await asyncio.to_thread(
                container.put_archive, directory, buffer.getvalue()
            )
    except Exception:
        # Roll back the half-provisioned container.
        try:
            await sandbox.destroy(container_id)
        except Exception:
            logger.warning(
                "Failed to clean up container %s after file injection error",
                container_id,
            )
        raise


def shlex_quote(value: str) -> str:
    return shlex.quote(value)


# ---------------------------------------------------------------------------
# Optional heartbeat loop
# ---------------------------------------------------------------------------


async def heartbeat_loop(
    api_url: str, node_id: str, token: str,
    interval: float = _HEARTBEAT_INTERVAL_SEC,
) -> None:
    """Refresh node liveness with the controlling API (best effort)."""
    url = f"{api_url.rstrip('/')}/v1/executor-nodes/{node_id}/heartbeat"
    async with httpx.AsyncClient(timeout=10.0) as client:
        while True:
            try:
                response = await client.post(
                    url, headers={"Authorization": f"Bearer {token}"}
                )
                if response.status_code != 200:
                    logger.warning(
                        "Heartbeat rejected: %s %s",
                        response.status_code, response.text[:200],
                    )
            except httpx.TransportError as exc:
                logger.warning("Heartbeat failed: %s", exc)
            await asyncio.sleep(interval)


# ---------------------------------------------------------------------------
# App + routes
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Wire the embedded DockerSandbox and optional heartbeat loop."""
    sandbox = DockerSandbox()
    try:
        await ensure_docker_networks()
    except SandboxError as exc:
        logger.warning("Docker network pre-creation failed: %s", exc)
    app.state.sandbox = sandbox

    api_url = os.environ.get("LINCHPIN_API_URL", "")
    node_id = os.environ.get("LINCHPIN_EXECUTOR_NODE_ID", "")
    token = os.environ.get("LINCHPIN_EXECUTOR_TOKEN", "")
    heartbeat_task: asyncio.Task | None = None
    if api_url and node_id and token:
        heartbeat_task = asyncio.create_task(
            heartbeat_loop(api_url, node_id, token)
        )
        logger.info("Executor heartbeat loop started (node %s).", node_id)

    logger.info("Executor daemon started.")
    yield
    if heartbeat_task is not None:
        heartbeat_task.cancel()


app = FastAPI(title="linchpin-executor-daemon", lifespan=lifespan)


@app.get("/v1/ping")
async def ping():
    """Unauthenticated liveness probe."""
    return {"status": "ok"}


@app.post("/v1/containers", status_code=201, dependencies=[Depends(require_daemon_auth)])
async def create_container(body: CreateContainerRequest, request: Request):
    """Create a container and inject the packed mount files."""
    sandbox = _sandbox(request)
    resources = (
        SandboxResources(tier=body.resources.tier)
        if body.resources is not None
        else None
    )
    try:
        container_id = await sandbox.create(
            body.image,
            body.network,
            mounts=None,
            env=dict(body.env) if body.env else None,
            resources=resources,
        )
    except SandboxError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    if body.files:
        try:
            await _inject_files(sandbox, container_id, body.files)
        except SandboxError as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"file injection failed: {exc}",
            )
    return {"container_id": container_id}


@app.post("/v1/containers/{container_id}/exec", dependencies=[Depends(require_daemon_auth)])
async def exec_in_container(
    container_id: str, body: ExecRequest, request: Request
):
    """Run a command inside the container."""
    sandbox = _sandbox(request)
    try:
        result = await sandbox.exec(container_id, body.command)
    except SandboxError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {
        "stdout": result.stdout,
        "stderr": result.stderr,
        "exit_code": result.exit_code,
    }


@app.post("/v1/containers/{container_id}/kill", dependencies=[Depends(require_daemon_auth)])
async def kill_container_exec(
    container_id: str, body: KillRequest, request: Request
):
    """Terminate a tracked exec process inside the container."""
    sandbox = _sandbox(request)
    try:
        killed = await sandbox.kill_exec(
            container_id, body.pid, grace=body.grace
        )
    except SandboxError:
        killed = False
    return {"killed": killed}


@app.put("/v1/containers/{container_id}/files", dependencies=[Depends(require_daemon_auth)])
async def write_container_file(
    container_id: str, request: Request, path: str
):
    """Write the raw request body to *path* inside the container."""
    sandbox = _sandbox(request)
    if not path.startswith("/"):
        raise HTTPException(
            status_code=400,
            detail="path must be an absolute path inside the container",
        )
    data = await request.body()
    try:
        await _inject_files(
            sandbox,
            container_id,
            [FileEntry(
                path=path,
                content_b64=base64.b64encode(data).decode("ascii"),
            )],
        )
    except SandboxError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"file write failed: {exc}"
        )
    return {"status": "written"}


@app.get("/v1/containers/{container_id}/files", dependencies=[Depends(require_daemon_auth)])
async def read_container_file(
    container_id: str, request: Request, path: str
) -> Response:
    """Read a file from the container as raw bytes."""
    sandbox = _sandbox(request)
    try:
        data = await sandbox.copy_from_container(container_id, path)
    except SandboxError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return Response(content=data, media_type="application/octet-stream")


@app.get("/v1/containers/{container_id}/tree", dependencies=[Depends(require_daemon_auth)])
async def list_container_tree(
    container_id: str, request: Request, path: str = "/"
):
    """List a directory inside the container."""
    sandbox = _sandbox(request)
    try:
        entries = await sandbox.list_dir(container_id, path)
    except SandboxError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"entries": entries}


@app.delete("/v1/containers/{container_id}", dependencies=[Depends(require_daemon_auth)])
async def destroy_container(container_id: str, request: Request):
    """Destroy the container (missing containers are a no-op)."""
    sandbox = _sandbox(request)
    try:
        await sandbox.destroy(container_id)
    except SandboxError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {"status": "destroyed"}
