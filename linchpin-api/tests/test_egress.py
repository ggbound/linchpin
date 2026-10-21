"""Atlas B4 — egress proxy for `limited` networking (FR-4.3).

Covers: squid.conf rendering (allowlist mapping, anti-rebinding ACL
ordering), config hashing, container naming, proxy env-var injection,
the ensure_egress_proxy lifecycle against a fake Docker client, and the
sandbox-container gauge excluding egress proxies.
"""

from __future__ import annotations

import io
import tarfile
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from docker.errors import DockerException, ImageNotFound, NotFound

from app import egress
from app.egress import (
    egress_config_hash,
    proxy_container_name,
    proxy_env_from_networking,
    proxy_env_vars,
    render_squid_conf,
)
from app.metrics import _count_sandbox_containers
from app.sandbox import SandboxError


class TestRenderSquidConf:
    def test_ports_and_deny_ordering(self):
        conf = render_squid_conf(["api.github.com"])
        assert "http_port 3128" in conf
        assert "http_access deny !Safe_ports" in conf
        assert "http_access deny CONNECT !SSL_ports" in conf
        # Anti-rebinding deny must precede the allowlist allow.
        assert conf.index("http_access deny private_dst") < conf.index(
            "http_access allow allowed_hosts"
        )
        # Final catch-all deny closes the policy.
        assert conf.index("http_access deny all") > conf.index(
            "http_access allow allowed_hosts"
        )
        # Private space covered (IPv4 RFC1918/loopback/link-local/carrier
        # NAT + IPv6 loopback/ULA/link-local).
        for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
                     "127.0.0.0/8", "169.254.0.0/16", "::1/128", "fc00::/7"):
            assert cidr in conf

    def test_wildcard_maps_to_leading_dot(self):
        conf = render_squid_conf(["*.example.com"])
        assert "acl allowed_hosts dstdomain .example.com" in conf

    def test_bare_host_matches_exactly(self):
        conf = render_squid_conf(["example.com"])
        assert "acl allowed_hosts dstdomain example.com" in conf
        # No accidental subdomain broadening for bare entries.
        assert "dstdomain .example.com" not in conf

    def test_ip_literal_passes_through(self):
        conf = render_squid_conf(["93.184.216.34"])
        assert "dstdomain 93.184.216.34" in conf

    def test_empty_allowlist_denies_all(self):
        conf = render_squid_conf([])
        assert "http_access allow allowed_hosts" not in conf
        assert "http_access deny all" in conf

    def test_multiple_hosts_space_separated(self):
        conf = render_squid_conf(["a.com", "*.b.org"])
        assert "acl allowed_hosts dstdomain a.com .b.org" in conf


def test_proxy_env_vars_shape():
    env = proxy_env_vars("linchpin-egress-abc123")
    assert env["HTTP_PROXY"] == "http://linchpin-egress-abc123:3128"
    assert env["HTTPS_PROXY"] == env["HTTP_PROXY"]
    assert env["http_proxy"] == env["HTTP_PROXY"]
    assert env["https_proxy"] == env["HTTP_PROXY"]
    assert "169.254.169.254" in env["NO_PROXY"]


def test_egress_config_hash_is_deterministic_and_discriminating():
    h1 = egress_config_hash("env-1", ["a.com"])
    assert h1 == egress_config_hash("env-1", ["a.com"])
    assert h1 != egress_config_hash("env-1", ["b.com"])      # hosts matter
    assert h1 != egress_config_hash("env-2", ["a.com"])      # env matters


def test_proxy_container_name_is_stable_and_scoped():
    env_id = str(uuid.uuid4())
    name = proxy_container_name(env_id)
    assert name == "linchpin-egress-" + env_id.replace("-", "")[:12]
    assert name == proxy_container_name(env_id)
    assert name != proxy_container_name(str(uuid.uuid4()))


# ---------------------------------------------------------------------------
# ensure_egress_proxy lifecycle (fake Docker client)
# ---------------------------------------------------------------------------


class _FakeContainer:
    def __init__(self, name, labels=None, status="created"):
        self.name = name
        self.labels = labels or {}
        self.status = status
        self.calls: list = []
        self.archive: bytes | None = None
        self.removed = False

    def start(self):
        self.status = "running"
        self.calls.append("start")

    def remove(self, force=False):
        self.calls.append(f"remove:{force}")
        self.removed = True

    def connect(self, network, **kwargs):
        self.calls.append(f"connect:{network}")

    def put_archive(self, path, data):
        self.calls.append(f"put_archive:{path}")
        self.archive = data


class _FakeContainers:
    def __init__(self):
        self._by_name: dict = {}
        self.created: list = []
        self.fail_first_create = None  # exception to raise once

    def get(self, name):
        c = self._by_name.get(name)
        if c is None or c.removed:
            raise NotFound(f"no such container: {name}")
        return c

    def create(self, image, name=None, **kwargs):
        if self.fail_first_create is not None:
            exc, self.fail_first_create = self.fail_first_create, None
            raise exc
        c = _FakeContainer(name, labels=kwargs.get("labels"))
        c.create_image = image
        c.create_kwargs = kwargs
        self._by_name[name] = c
        self.created.append((image, name, kwargs))
        return c


class _FakeImages:
    def __init__(self):
        self.pulled: list = []

    def pull(self, image):
        self.pulled.append(image)


class _FakeDockerClient:
    def __init__(self):
        self.containers = _FakeContainers()
        self.images = _FakeImages()


def _unpack_conf(tar_bytes: bytes) -> str:
    with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tar:
        member = tar.getmember("squid.conf")
        return tar.extractfile(member).read().decode()


class TestEnsureEgressProxySync:
    def test_creates_and_bootstraps_new_proxy(self):
        client = _FakeDockerClient()
        env_id = "aaaaaaaa-1111-2222-3333-444444444444"
        hosts = ["api.github.com", "*.example.com"]

        name = egress._ensure_egress_proxy_sync(env_id, hosts, client)

        assert name == proxy_container_name(env_id)
        assert len(client.containers.created) == 1
        image, created_name, kwargs = client.containers.created[0]
        assert image == egress.DEFAULT_EGRESS_IMAGE
        assert created_name == name
        assert kwargs["network"] == "linchpin-limited"
        assert kwargs["labels"]["linchpin.egress.config_hash"] == (
            egress_config_hash(env_id, hosts)
        )
        assert kwargs["labels"]["linchpin.role"] == "egress-proxy"

        container = client.containers.get(name)
        # bootstrap order: config written, then open-net attach, then start.
        assert container.calls[0] == "put_archive:/etc/squid"
        assert container.calls[1] == "connect:linchpin-open"
        assert container.calls[2] == "start"
        conf = _unpack_conf(container.archive)
        assert "http_port 3128" in conf
        assert "dstdomain api.github.com .example.com" in conf

    def test_reuses_running_proxy_with_same_hash(self):
        client = _FakeDockerClient()
        env_id = "aaaaaaaa-1111-2222-3333-444444444444"
        hosts = ["api.github.com"]

        first = egress._ensure_egress_proxy_sync(env_id, hosts, client)
        container = client.containers.get(first)
        n_creates = len(client.containers.created)

        second = egress._ensure_egress_proxy_sync(env_id, hosts, client)

        assert second == first
        assert len(client.containers.created) == n_creates  # no new container
        assert container.calls.count("start") == 1          # no second boot

    def test_starts_stopped_proxy_with_same_hash(self):
        client = _FakeDockerClient()
        env_id = "aaaaaaaa-1111-2222-3333-444444444444"
        name = egress._ensure_egress_proxy_sync(env_id, ["a.com"], client)
        client.containers.get(name).status = "exited"

        result = egress._ensure_egress_proxy_sync(env_id, ["a.com"], client)

        assert result == name
        assert client.containers.get(name).status == "running"

    def test_swaps_proxy_on_allowlist_change(self):
        client = _FakeDockerClient()
        env_id = "aaaaaaaa-1111-2222-3333-444444444444"
        name = egress._ensure_egress_proxy_sync(env_id, ["a.com"], client)
        old = client.containers.get(name)

        result = egress._ensure_egress_proxy_sync(env_id, ["a.com", "b.com"], client)

        assert result == name
        assert "remove:True" in old.calls
        assert len(client.containers.created) == 2
        conf = _unpack_conf(client.containers.get(name).archive)
        assert "dstdomain a.com b.com" in conf

    def test_pulls_image_when_missing(self):
        client = _FakeDockerClient()
        client.containers.fail_first_create = ImageNotFound("no such image")
        env_id = "aaaaaaaa-1111-2222-3333-444444444444"

        name = egress._ensure_egress_proxy_sync(env_id, ["a.com"], client)

        assert client.images.pulled == [egress.DEFAULT_EGRESS_IMAGE]
        assert len(client.containers.created) == 1  # retried successfully
        assert client.containers.get(name).status == "running"

    def test_docker_failure_wraps_as_sandbox_error(self):
        client = _FakeDockerClient()
        client.containers.fail_first_create = DockerException("daemon gone")

        with pytest.raises(SandboxError, match="daemon gone"):
            egress._ensure_egress_proxy_sync(
                "aaaaaaaa-1111-2222-3333-444444444444", ["a.com"], client
            )


@pytest.mark.asyncio
async def test_ensure_egress_proxy_async_offloads():
    client = _FakeDockerClient()
    env_id = "aaaaaaaa-1111-2222-3333-444444444444"
    name = await egress.ensure_egress_proxy(env_id, ["a.com"], client)
    assert name == proxy_container_name(env_id)


def test_proxy_env_from_networking_end_to_end():
    client = _FakeDockerClient()
    env_id = "aaaaaaaa-1111-2222-3333-444444444444"
    networking = {"type": "limited", "allowed_hosts": ["api.github.com"]}

    env = proxy_env_from_networking(env_id, networking, client=client)

    expected_host = proxy_container_name(env_id)
    assert env["HTTP_PROXY"] == f"http://{expected_host}:3128"
    assert client.containers.get(expected_host).status == "running"


# ---------------------------------------------------------------------------
# Atlas M6 — curated domains for allow_package_managers / allow_mcp_servers
# ---------------------------------------------------------------------------


class TestCuratedDomains:
    def test_effective_hosts_plain_allowlist(self):
        assert egress.effective_allowed_hosts(
            {"allowed_hosts": ["api.github.com"]}
        ) == ["api.github.com"]

    def test_effective_hosts_empty_and_missing_fields(self):
        assert egress.effective_allowed_hosts({}) == []
        assert egress.effective_allowed_hosts({"allowed_hosts": None}) == []
        # Falsy entries are dropped, not merged.
        assert egress.effective_allowed_hosts({"allowed_hosts": ["", "a.com"]}) == ["a.com"]

    def test_package_manager_flag_merges_curated_set(self):
        hosts = egress.effective_allowed_hosts(
            {"allowed_hosts": ["api.github.com"], "allow_package_managers": True}
        )
        for d in ("pypi.org", "files.pythonhosted.org", "registry.npmjs.org",
                  "deb.debian.org", "archive.ubuntu.com", "crates.io",
                  "rubygems.org", "proxy.golang.org"):
            assert d in hosts
        assert hosts[0] == "api.github.com"  # user entries come first

    def test_mcp_flag_merges_registry_set(self):
        hosts = egress.effective_allowed_hosts(
            {"allowed_hosts": [], "allow_mcp_servers": True}
        )
        assert sorted(hosts) == sorted(egress.CURATED_MCP_DOMAINS)
        # MCP curated set is a strict subset of the package-manager one —
        # it only covers pulling server packages (npx/uvx), not the
        # servers' own APIs (those are explicit allowed_hosts entries).
        assert set(egress.CURATED_MCP_DOMAINS) <= set(egress.CURATED_PACKAGE_DOMAINS)

    def test_both_flags_dedupe(self):
        hosts = egress.effective_allowed_hosts({
            "allow_package_managers": True, "allow_mcp_servers": True,
        })
        assert len(hosts) == len(set(hosts))
        assert set(hosts) == (
            set(egress.CURATED_PACKAGE_DOMAINS) | set(egress.CURATED_MCP_DOMAINS)
        )
        # pypi.org etc. appear exactly once.
        assert hosts.count("pypi.org") == 1

    def test_curated_host_in_user_list_not_duplicated(self):
        hosts = egress.effective_allowed_hosts({
            "allowed_hosts": ["pypi.org"], "allow_package_managers": True,
        })
        assert hosts.count("pypi.org") == 1
        assert hosts[0] == "pypi.org"  # user order preserved, no dup appended

    def test_curated_merge_changes_config_hash(self):
        """Curated domains feed egress_config_hash — toggling a flag must
        swap the proxy (the hash is computed over the merged list)."""
        env_id = "aaaaaaaa-1111-2222-3333-444444444444"
        plain = egress.egress_config_hash(
            env_id, egress.effective_allowed_hosts({"allowed_hosts": []})
        )
        with_pm = egress.egress_config_hash(
            env_id,
            egress.effective_allowed_hosts({
                "allowed_hosts": [], "allow_package_managers": True,
            }),
        )
        assert plain != with_pm

    def test_proxy_env_from_networking_includes_curated(self):
        client = _FakeDockerClient()
        env_id = "aaaaaaaa-1111-2222-3333-444444444444"
        networking = {"type": "limited", "allow_mcp_servers": True}

        proxy_env_from_networking(env_id, networking, client=client)

        name = proxy_container_name(env_id)
        conf = _unpack_conf(client.containers.get(name).archive)
        for d in egress.CURATED_MCP_DOMAINS:
            assert d in conf
        # package-manager-only domains must NOT leak in via the MCP flag.
        assert "deb.debian.org" not in conf


# ---------------------------------------------------------------------------
# metrics gauge excludes egress proxies (Atlas B4)
# ---------------------------------------------------------------------------


def test_count_sandbox_containers_excludes_egress_proxies():
    class _Listable:
        def __init__(self, networks):
            self.attrs = {"NetworkSettings": {"Networks": networks}}

    class _C:
        def __init__(self, items):
            self._items = items

        def list(self, all=True):
            return self._items

    client = SimpleNamespace(containers=_C([
        _Listable({"linchpin-limited": {}}),                    # sandbox: count
        _Listable({"linchpin-open": {}}),                       # sandbox: count
        _Listable({"linchpin-egress-aabbccddeeff": {}}),        # proxy: skip
        _Listable({"bridge": {}}),                              # unrelated: skip
    ]))

    with patch("docker.from_env", return_value=client):
        assert _count_sandbox_containers() == 2
