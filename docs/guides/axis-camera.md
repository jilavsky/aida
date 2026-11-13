# Axis cameras in AIDA (`axis-cam-mcp`)

> **What you end up with:** a small MCP server, `axis-cam-mcp`, that lets the
> agent ask an Axis network camera for a still image — "show me the hutch",
> "is the sample still mounted?" — and get a JPEG back as an attachment in
> the conversation. Built and tested on macOS, then deployed to the beamline
> Linux machine over the shared home directory.
>
> **Follow this top to bottom.** Part A is on your Mac with the camera on a
> network you can reach. Part B is the beamline machine. Part B assumes you
> have finished Part A, because it reuses the repo and the config file you
> create there.

## Why VAPIX and not `framegrab-mcp-server`

Recorded here so it does not get re-litigated later. The obvious off-the-shelf
choice is [groundlight/framegrab-mcp-server](https://github.com/groundlight/framegrab-mcp-server),
which is fine software but a poor fit for this particular job:

- **It has no Axis input type.** You would drive it through its generic RTSP
  path, which means the camera password goes in as a *tool argument* — so it
  lands in the conversation transcript, in `aida.db`, and in the MCP call log.
  AIDA's `keyring:` secret resolution covers a server's `env` block, not tool
  arguments, so there is no clean way to keep it out.
- **It pulls dependencies you do not need.** `pypylon` (the Basler GigE SDK)
  and `streamlink` (YouTube) are hard dependencies, not extras, plus
  `opencv-python` to decode the RTSP stream. That is a few hundred MB to move
  onto an isolated machine for a camera that will hand you a finished JPEG
  over one HTTP GET.
- **An Axis camera does the work for you.** VAPIX scales, rotates and
  compresses server-side. There is nothing left to decode locally, so this
  server needs no image library at all — `mcp` and `pyyaml`, and nothing else.

The cost is that you maintain ~200 lines yourself. Given it is three HTTP
endpoints and the code is printed in full below, that is the cheaper side.

## 0. Facts to record before you start

Fill these in as you go through Part A; Part B needs all of them. Keep a copy
somewhere that is not this file if the repo is ever public.

| Fact | Value | Where you find it |
|---|---|---|
| Camera hostname/IP (test) | | The camera's own web UI, or your network notes |
| Camera hostname/IP (beamline) | | Likely a different, private-subnet address |
| VAPIX account name | | Create a **viewer**-role account; do not use `root` |
| VAPIX password | | Store in the keychain, not in this table |
| Camera model | | `axis-cam-mcp --check` prints it |
| Beamline machine arch | | `uname -m` on the beamline box (expect `x86_64`) |
| Shared home path | | `echo $HOME` — must be **identical** on both machines |
| Networked Linux host sharing that home | | The host you will build the conda env from |

## Part A — build and test on macOS

### A1. Create the repo

```bash
mkdir -p ~/GitHub/axis-cam-mcp/src/axis_cam_mcp ~/GitHub/axis-cam-mcp/tests ~/GitHub/axis-cam-mcp/examples
cd ~/GitHub/axis-cam-mcp
git init
```

Layout mirrors `epics-mcp`, deliberately — if you can find your way around
that repo you can find your way around this one.

```
axis-cam-mcp/
├── LICENSE  README.md  CHANGELOG.md  PLAN.md
├── environment.yml
├── pyproject.toml
├── examples/cameras.yaml
├── src/axis_cam_mcp/{__init__,cameras,vapix,server}.py
└── tests/
```

### A2. `pyproject.toml`

```toml
[build-system]
requires = ["setuptools>=77.0", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "axis-cam-mcp"
version = "0.1.0.dev0"
description = "MCP server for still images from Axis network cameras over VAPIX."
readme = {file = "README.md", content-type = "text/markdown"}
requires-python = ">=3.10"
license = "MIT"
license-files = ["LICENSE"]
authors = [{name = "Jan Ilavsky", email = "ilavsky@anl.gov"}]
keywords = ["Axis", "VAPIX", "camera", "MCP", "Model Context Protocol", "beamline"]
classifiers = [
    "Development Status :: 3 - Alpha",
    "Intended Audience :: Science/Engineering",
    "Programming Language :: Python :: 3",
    "Topic :: Multimedia :: Video :: Capture",
]

dependencies = [
    # Same pin, and the same reason, as epics-mcp and pyirena[mcp]: mcp 2.0
    # removed `mcp.server.fastmcp`, which this server is built on.
    "mcp>=1.0.0,<2.0",
    # The camera registry is a YAML file; parsing it is not optional.
    "pyyaml>=6.0",
]

[project.optional-dependencies]
dev = ["pytest>=7.0", "pytest-cov", "ruff", "build", "twine"]

[project.scripts]
axis-cam-mcp = "axis_cam_mcp.server:main"

# DEVIATION from the house standard's flat layout: src/ layout, matching
# epics-mcp and aievaluator. Reason: the console script name
# (`axis-cam-mcp`) and the import package name (`axis_cam_mcp`) differ, and
# a flat layout puts an importable `axis_cam_mcp/` on sys.path whenever cwd
# happens to be the repo root -- which masks a broken install during exactly
# the offline-deployment testing this package exists to survive.
[tool.setuptools.packages.find]
where = ["src"]

[tool.ruff]
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "W", "I", "UP", "B"]
ignore = ["E501", "E741", "E701", "E702", "E402"]
```

### A3. `environment.yml`

```yaml
name: axis-cam-mcp

# Its own environment, kept separate from pyirena/aievaluator/epics-mcp: this
# package has two pure-Python dependencies and no native libraries, so there
# is nothing to gain by sharing an env and one more thing to re-resolve if
# you do. AIDA launches it by absolute interpreter path, so it does not care.
#
# Python floor 3.11 (not the package's own >=3.10) and cap <3.14 mirror
# epics-mcp, so the two envs stay interchangeable if you ever do merge them.

channels:
  - conda-forge

dependencies:
  - python>=3.11,<3.14
  - pyyaml>=6.0

  # Development / testing
  - pytest>=7.0
  - pytest-cov

  - pip
  - pip:
      # `mcp` is not on conda-forge as a maintained build; pip it.
      - -e ".[dev]"
```

### A4. The source

Four files under `src/axis_cam_mcp/`. All of this is tested — the listings
below are the exact code that passed the checks in A7.

#### `src/axis_cam_mcp/__init__.py`

```python
"""Axis network camera stills for MCP clients."""

__version__ = "0.1.0.dev0"
```

#### `src/axis_cam_mcp/cameras.py`

The camera registry. Note what is *not* here: passwords. Each camera names
the environment variables its credentials come from, so the secret can live
in the OS keychain on your Mac and in an `AIDA_SECRET_*` variable on the
headless beamline machine, without the config file changing.

```python
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_CONFIG_PATH = Path.home() / ".axis-cam-mcp" / "cameras.yaml"


class ConfigError(RuntimeError):
    """Malformed or missing camera configuration."""


@dataclass(frozen=True)
class Camera:
    name: str
    host: str
    description: str = ""
    scheme: str = "http"
    user_env: str = "AXIS_USER"
    password_env: str = "AXIS_PASS"
    resolution: str | None = None
    compression: int | None = None
    rotation: int | None = None
    verify_tls: bool = True
    timeout_seconds: float = 10.0

    @property
    def base_url(self) -> str:
        return f"{self.scheme}://{self.host}"

    def credentials(self) -> tuple[str, str]:
        user = os.environ.get(self.user_env)
        password = os.environ.get(self.password_env)
        missing = [n for n, v in ((self.user_env, user), (self.password_env, password)) if not v]
        if missing:
            raise ConfigError(
                f"camera {self.name!r}: environment variable(s) {', '.join(missing)} are not "
                f"set in this server's environment. AIDA passes through only HOME, LOGNAME, "
                f"PATH, SHELL, TERM, USER plus this server's own `env` block in mcp.json."
            )
        return user, password


_ALLOWED = set(Camera.__dataclass_fields__) - {"name"}


def config_path() -> Path:
    override = os.environ.get("AXIS_CAM_CONFIG")
    return Path(override).expanduser() if override else DEFAULT_CONFIG_PATH


def load_cameras(path: Path | None = None) -> dict[str, Camera]:
    path = path or config_path()
    if not path.is_file():
        raise ConfigError(f"no camera configuration at {path} (set AXIS_CAM_CONFIG to override)")
    raw = yaml.safe_load(path.read_text()) or {}
    entries = raw.get("cameras")
    if not isinstance(entries, dict) or not entries:
        raise ConfigError(f"{path}: expected a non-empty top-level `cameras:` mapping")
    cameras: dict[str, Camera] = {}
    for name, spec in entries.items():
        if not isinstance(spec, dict):
            raise ConfigError(f"{path}: camera {name!r} must be a mapping")
        unknown = set(spec) - _ALLOWED
        if unknown:
            raise ConfigError(
                f"{path}: camera {name!r} has unknown key(s) {sorted(unknown)}; "
                f"allowed: {sorted(_ALLOWED)}"
            )
        if "host" not in spec:
            raise ConfigError(f"{path}: camera {name!r} has no `host`")
        cameras[str(name)] = Camera(name=str(name), **spec)
    return cameras
```

#### `src/axis_cam_mcp/vapix.py`

```python
from __future__ import annotations

import ssl
import urllib.error
import urllib.parse
import urllib.request

from axis_cam_mcp.cameras import Camera

_JPEG_SOI = b"\xff\xd8"


class VapixError(RuntimeError):
    """A VAPIX request failed. The message is safe to show the user."""


def _opener(cam: Camera) -> urllib.request.OpenerDirector:
    user, password = cam.credentials()
    mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    mgr.add_password(None, cam.base_url, user, password)
    handlers: list[urllib.request.BaseHandler] = [
        urllib.request.HTTPDigestAuthHandler(mgr),
        urllib.request.HTTPBasicAuthHandler(mgr),
    ]
    if cam.scheme == "https" and not cam.verify_tls:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        handlers.append(urllib.request.HTTPSHandler(context=ctx))
    return urllib.request.build_opener(*handlers)


def _get(cam: Camera, path: str, params: dict[str, str]) -> tuple[bytes, str]:
    url = f"{cam.base_url}{path}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    try:
        with _opener(cam).open(url, timeout=cam.timeout_seconds) as resp:
            return resp.read(), resp.headers.get_content_type()
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise VapixError(
                f"camera {cam.name!r} rejected the credentials in "
                f"${cam.user_env}/${cam.password_env} (HTTP 401)"
            ) from exc
        raise VapixError(f"camera {cam.name!r} returned HTTP {exc.code} for {path}") from exc
    except urllib.error.URLError as exc:
        raise VapixError(
            f"cannot reach camera {cam.name!r} at {cam.base_url}: {exc.reason}"
        ) from exc
    except TimeoutError as exc:
        raise VapixError(
            f"camera {cam.name!r} did not answer within {cam.timeout_seconds}s"
        ) from exc


def snapshot(
    cam: Camera,
    *,
    resolution: str | None = None,
    compression: int | None = None,
    rotation: int | None = None,
) -> bytes:
    params: dict[str, str] = {}
    res = resolution if resolution is not None else cam.resolution
    comp = compression if compression is not None else cam.compression
    rot = rotation if rotation is not None else cam.rotation
    if res:
        params["resolution"] = res
    if comp is not None:
        params["compression"] = str(comp)
    if rot is not None:
        params["rotation"] = str(rot)

    data, content_type = _get(cam, "/axis-cgi/jpg/image.cgi", params)
    if not data.startswith(_JPEG_SOI):
        raise VapixError(
            f"camera {cam.name!r} returned {content_type} ({len(data)} bytes), not a JPEG "
            f"- usually an unsupported resolution/compression value. Body starts: {data[:120]!r}"
        )
    return data


def brand_info(cam: Camera) -> dict[str, str]:
    data, _ = _get(cam, "/axis-cgi/param.cgi", {"action": "list", "group": "Brand"})
    info: dict[str, str] = {}
    for line in data.decode("utf-8", "replace").splitlines():
        key, sep, value = line.partition("=")
        if sep:
            info[key.strip().removeprefix("root.Brand.")] = value.strip()
    return info
```

#### `src/axis_cam_mcp/server.py`

```python
"""``axis-cam-mcp`` - MCP server exposing Axis network camera stills."""

from __future__ import annotations

import argparse
import sys

from mcp.server.fastmcp import FastMCP, Image

from axis_cam_mcp.cameras import Camera, ConfigError, config_path, load_cameras
from axis_cam_mcp.vapix import VapixError, brand_info, snapshot

INSTRUCTIONS = """\
Still images from Axis network cameras (for example a beamline hutch or
sample camera) over VAPIX. Call `list_cameras` first to learn the
configured names; every other tool takes one of those names. Frames come
back as JPEG. Ask for the smallest `resolution` that answers the question -
these are full-frame stills, not a video feed, and a 1920x1080 frame is a
large attachment. This server reads single frames only: no recording, no
motion detection, no PTZ control.
"""

mcp = FastMCP("axis-cam", instructions=INSTRUCTIONS)


def _camera(name: str) -> Camera:
    cameras = load_cameras()
    if name not in cameras:
        raise ValueError(f"unknown camera {name!r}; configured: {sorted(cameras)}")
    return cameras[name]


@mcp.tool()
def list_cameras() -> list[dict[str, str]]:
    """List the configured cameras: name, description and address.

    Never returns credentials - only the names of the environment
    variables they are read from.
    """
    return [
        {
            "name": c.name,
            "description": c.description,
            "address": c.base_url,
            "default_resolution": c.resolution or "camera default",
            "credentials_from": f"${c.user_env} / ${c.password_env}",
        }
        for c in load_cameras().values()
    ]


@mcp.tool()
def grab_frame(
    camera: str,
    resolution: str | None = None,
    compression: int | None = None,
    rotation: int | None = None,
) -> Image:
    """Capture one still frame from `camera` and return it as a JPEG.

    Args:
        camera: A name from `list_cameras`.
        resolution: "WIDTHxHEIGHT", e.g. "1280x720". The camera scales
            server-side; omit for the camera's configured default.
        compression: 0-100, where 0 is best quality and the largest file.
        rotation: 0, 90, 180 or 270 degrees.
    """
    data = snapshot(
        _camera(camera), resolution=resolution, compression=compression, rotation=rotation
    )
    return Image(data=data, format="jpeg")


@mcp.tool()
def camera_info(camera: str) -> dict[str, str]:
    """Model, product number and firmware for `camera`.

    The cheapest way to prove credentials and network reach are good
    without moving an image.
    """
    return brand_info(_camera(camera))


def main() -> int:
    parser = argparse.ArgumentParser(prog="axis-cam-mcp", description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate the camera config, probe each camera, and exit without serving",
    )
    args = parser.parse_args()

    if args.check:
        try:
            cameras = load_cameras()
        except ConfigError as exc:
            print(f"FAILED: {exc}", file=sys.stderr)
            return 1
        print(f"config: {config_path()}  ({len(cameras)} camera(s))")
        failed = 0
        for c in cameras.values():
            try:
                info = brand_info(c)
                status = info.get("ProdNbr") or info.get("ProdFullName") or "reachable"
            except (ConfigError, VapixError) as exc:
                status, failed = f"UNREACHABLE - {exc}", failed + 1
            print(f"  {c.name:<16} {c.base_url:<30} {status}")
        return 1 if failed else 0

    mcp.run(transport="stdio")
    return 0
```

### A5. `examples/cameras.yaml`

The camera registry. Copy it to `~/.axis-cam-mcp/cameras.yaml` and edit;
`examples/` stays as the documented reference.

```yaml
# Camera registry for axis-cam-mcp.
#
# Copy to ~/.axis-cam-mcp/cameras.yaml (or point AXIS_CAM_CONFIG elsewhere).
# Credentials are NOT in this file -- each camera names the environment
# variables they are read from, so the file is safe to commit and diff.

cameras:

  hutch:
    host: 10.54.122.80            # IP or hostname, optionally with :port
    description: USAXS hutch overview, looking downstream at the sample stage
    user_env: AXIS_USER_HUTCH     # env var holding the VAPIX account name
    password_env: AXIS_PASS_HUTCH # env var holding its password
    resolution: 1280x720          # default; a tool call can override it
    # compression: 30             # 0-100, 0 = best quality / largest file
    # rotation: 0                 # 0, 90, 180, 270
    # scheme: https               # default http
    # verify_tls: false           # only with scheme: https and a self-signed cert
    # timeout_seconds: 10.0

  # sample:
  #   host: 10.54.122.81
  #   description: Sample stage close-up
  #   user_env: AXIS_USER_SAMPLE
  #   password_env: AXIS_PASS_SAMPLE
  #   resolution: 800x600
```

### A6. Tests

The suite runs against a fake Axis camera on localhost, so it passes with no
camera present — which matters later, because it is how you prove the install
on the beamline machine is sound before you have network access to the real
camera.

#### `tests/fake_axis.py`

```python
"""A stand-in Axis camera: digest auth + the two VAPIX endpoints we use."""

import base64
import hashlib
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

REALM, NONCE, USER, PASSWD = "AXIS_ACCC8E123456", "deadbeefcafe0001", "viewer", "s3cr#t"
JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0a"
    "HBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAABAAAAAAAA"
    "AAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AKp//2Q=="
)


def _parse(h):
    return {
        m.group(1): (m.group(3) if m.group(3) is not None else m.group(2))
        for m in re.finditer(r'(\w+)=("([^"]*)"|[^,\s]+)', h)
    }


def _md5(s):
    return hashlib.md5(s.encode()).hexdigest()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _authorized(self):
        hdr = self.headers.get("Authorization", "")
        if not hdr.startswith("Digest "):
            return False
        d = _parse(hdr[7:])
        ha1, ha2 = _md5(f"{USER}:{REALM}:{PASSWD}"), _md5(f"GET:{d.get('uri', '')}")
        if d.get("qop"):
            want = _md5(f"{ha1}:{d['nonce']}:{d['nc']}:{d['cnonce']}:{d['qop']}:{ha2}")
        else:
            want = _md5(f"{ha1}:{d['nonce']}:{ha2}")
        return d.get("response") == want and d.get("username") == USER

    def do_GET(self):
        if not self._authorized():
            self.send_response(401)
            self.send_header(
                "WWW-Authenticate", f'Digest realm="{REALM}", qop="auth", nonce="{NONCE}"'
            )
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        path = self.path.split("?")[0]
        if path == "/axis-cgi/jpg/image.cgi":
            if "resolution=9999x9999" in self.path:  # the HTML-error-with-200 failure mode
                body, ctype = b"<HTML><BODY>Error: bad resolution</BODY></HTML>", "text/html"
            else:
                body, ctype = JPEG, "image/jpeg"
        elif path == "/axis-cgi/param.cgi":
            body, ctype = (
                (
                    b"root.Brand.Brand=AXIS\r\nroot.Brand.ProdNbr=P1455-LE\r\n"
                    b"root.Brand.ProdFullName=AXIS P1455-LE Network Camera\r\n"
                ),
                "text/plain",
            )
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start():
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]
```

#### `tests/conftest.py`

```python
"""A stand-in Axis camera on localhost, so the whole suite runs with no camera."""

from __future__ import annotations

import textwrap

import pytest

from tests.fake_axis import PASSWD, USER, start


@pytest.fixture(scope="session")
def fake_camera_port():
    srv, port = start()
    yield port
    srv.shutdown()


@pytest.fixture
def cameras_yaml(tmp_path, fake_camera_port, monkeypatch):
    """A one-camera config pointed at the fake, with credentials in env."""
    path = tmp_path / "cameras.yaml"
    path.write_text(
        textwrap.dedent(f"""
        cameras:
          hutch:
            host: 127.0.0.1:{fake_camera_port}
            description: Test camera
            user_env: AXIS_USER_HUTCH
            password_env: AXIS_PASS_HUTCH
            resolution: 1280x720
        """)
    )
    monkeypatch.setenv("AXIS_CAM_CONFIG", str(path))
    monkeypatch.setenv("AXIS_USER_HUTCH", USER)
    monkeypatch.setenv("AXIS_PASS_HUTCH", PASSWD)
    return path
```

#### `tests/test_cameras.py`

```python
"""Camera registry loading and its error messages."""

from __future__ import annotations

import pytest

from axis_cam_mcp.cameras import ConfigError, load_cameras


def test_loads_defaults_for_unspecified_keys(cameras_yaml):
    cam = load_cameras()["hutch"]
    assert cam.scheme == "http" and cam.verify_tls is True and cam.timeout_seconds == 10.0
    assert cam.base_url.startswith("http://127.0.0.1:")


def test_missing_file_names_the_override_variable(tmp_path, monkeypatch):
    monkeypatch.setenv("AXIS_CAM_CONFIG", str(tmp_path / "nope.yaml"))
    with pytest.raises(ConfigError, match="AXIS_CAM_CONFIG"):
        load_cameras()


def test_typo_in_a_key_is_rejected_rather_than_silently_ignored(tmp_path, monkeypatch):
    """`resolution` misspelled must not silently fall back to the default."""
    path = tmp_path / "c.yaml"
    path.write_text("cameras:\n  a:\n    host: h\n    resolutoin: 640x480\n")
    monkeypatch.setenv("AXIS_CAM_CONFIG", str(path))
    with pytest.raises(ConfigError, match="unknown key"):
        load_cameras()


def test_camera_without_host_is_rejected(tmp_path, monkeypatch):
    path = tmp_path / "c.yaml"
    path.write_text("cameras:\n  a:\n    description: no host\n")
    monkeypatch.setenv("AXIS_CAM_CONFIG", str(path))
    with pytest.raises(ConfigError, match="no `host`"):
        load_cameras()
```

#### `tests/test_vapix.py`

```python
"""VAPIX transport: digest auth, and every failure mode worth a clear message."""

from __future__ import annotations

import pytest

from axis_cam_mcp.cameras import ConfigError, load_cameras
from axis_cam_mcp.vapix import VapixError, brand_info, snapshot


def test_snapshot_returns_a_jpeg_over_digest_auth(cameras_yaml):
    data = snapshot(load_cameras()["hutch"])
    assert data.startswith(b"\xff\xd8")


def test_brand_info_strips_the_root_brand_prefix(cameras_yaml):
    assert brand_info(load_cameras()["hutch"])["ProdNbr"] == "P1455-LE"


def test_html_error_page_with_http_200_is_not_passed_off_as_an_image(cameras_yaml):
    """An Axis camera answers some bad requests with 200 + HTML. Without the
    magic-byte check that reaches the user as a corrupt image in chat."""
    with pytest.raises(VapixError, match="not a JPEG"):
        snapshot(load_cameras()["hutch"], resolution="9999x9999")


def test_wrong_password_names_the_env_var_not_the_value(cameras_yaml, monkeypatch):
    monkeypatch.setenv("AXIS_PASS_HUTCH", "wrong")
    with pytest.raises(VapixError) as exc:
        snapshot(load_cameras()["hutch"])
    assert "AXIS_PASS_HUTCH" in str(exc.value) and "wrong" not in str(exc.value)


def test_missing_credential_env_var_explains_aida_env_passthrough(cameras_yaml, monkeypatch):
    monkeypatch.delenv("AXIS_USER_HUTCH")
    with pytest.raises(ConfigError, match="AXIS_USER_HUTCH"):
        snapshot(load_cameras()["hutch"])


def test_unreachable_host_says_so(cameras_yaml, monkeypatch, tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("cameras:\n  gone:\n    host: 127.0.0.1:1\n")
    monkeypatch.setenv("AXIS_CAM_CONFIG", str(path))
    monkeypatch.setenv("AXIS_USER", "u")
    monkeypatch.setenv("AXIS_PASS", "p")
    with pytest.raises(VapixError, match="cannot reach"):
        snapshot(load_cameras()["gone"])
```

Also create an empty `tests/__init__.py` (the fixtures import
`tests.fake_axis`).

### A7. Create the environment and install

```bash
cd ~/GitHub/axis-cam-mcp
conda env create -f environment.yml
conda activate axis-cam-mcp

# Record this path -- it is what goes in mcp.json.
which axis-cam-mcp
# -> /opt/miniconda3/envs/axis-cam-mcp/bin/axis-cam-mcp
```

Prove the install before involving a camera:

```bash
pytest -q                  # expect: 10 passed
ruff check . && ruff format --check .
```

### A8. Prove the camera works — before any Python

Do this first whenever something is wrong. If `curl` cannot get a JPEG, no
amount of MCP debugging will help.

```bash
curl -s --digest -u 'viewer:PASSWORD' \
     'http://CAMERA-IP/axis-cgi/jpg/image.cgi?resolution=1280x720' \
     -o /tmp/axis-test.jpg
file /tmp/axis-test.jpg      # -> JPEG image data, ... 1280x720
open /tmp/axis-test.jpg
```

If that 401s, try `--basic` instead of `--digest` (older firmware), and
confirm the account exists with at least the **viewer** role in the camera's
web UI under *System → Accounts*.

### A9. Configure and check

```bash
mkdir -p ~/.axis-cam-mcp
cp examples/cameras.yaml ~/.axis-cam-mcp/cameras.yaml
$EDITOR ~/.axis-cam-mcp/cameras.yaml       # real host, real description

export AXIS_USER_HUTCH='viewer'
export AXIS_PASS_HUTCH='...'

axis-cam-mcp --check
```

Expected:

```
config: /Users/ilavsky/.axis-cam-mcp/cameras.yaml  (1 camera(s))
  hutch            http://10.54.122.46            P1455-LE
```

`--check` exits non-zero if any camera is unreachable, so it also works as a
one-liner in a script. Record the model in the table in §0.

### A10. Wire into AIDA

Store the password in the keychain — never in `mcp.json`:

```bash
aida config secret set axis_cam_hutch      # prompts without echo
aida config secret get axis_cam_hutch      # confirms it is set; never prints it
```

*NOTE* for axisserver withotu credentials user guest and space for password seems to work? 

Then add the server. Either edit `~/.aida/mcp.json` directly:

```json
"axis-cam": {
  "command": "/opt/miniconda3/envs/axis-cam-mcp/bin/axis-cam-mcp",
  "args": [],
  "env": {
    "AXIS_CAM_CONFIG": "/Users/ilavsky/.axis-cam-mcp/cameras.yaml",
    "AXIS_USER_HUTCH": "viewer",
    "AXIS_PASS_HUTCH": "keyring:axis_cam_hutch"
  },
  "type": "stdio",
  "url": "",
  "headers": {},
  "groups": ["instrument-status"],
  "skills": [],
  "disabled_tools": [],
  "confirm_tools": [],
  "timeout_seconds": null
}
```

or via the CLI:

```bash
aida mcp server add axis-cam \
  --command /opt/miniconda3/envs/axis-cam-mcp/bin/axis-cam-mcp \
  --env AXIS_CAM_CONFIG=/Users/ilavsky/.axis-cam-mcp/cameras.yaml \
  --env AXIS_USER_HUTCH=viewer \
  --env AXIS_PASS_HUTCH=keyring:axis_cam_hutch \
  --groups instrument-status
```

Three things about that `env` block are load-bearing:

- **`AXIS_PASS_HUTCH` uses `keyring:`.** AIDA resolves `keyring:NAME` and
  `secret:NAME` values before spawning the subprocess, so the plaintext
  password never touches `mcp.json`. A missing secret fails that one server
  at startup with a clear message rather than producing a confusing 401 later.
- **`AXIS_CAM_CONFIG` is set explicitly** even though the default path would
  work. AIDA passes only `HOME, LOGNAME, PATH, SHELL, TERM, USER` through to
  an MCP subprocess, plus whatever this block sets — being explicit means the
  server does not depend on `HOME` being what you think it is.
- **`command` is an absolute path** into the conda env, like every other
  server in your `mcp.json`. A bare name would depend on which environment
  was active when AIDA launched.

Restart AIDA and check the server came up:

```bash
aida mcp server list
aida doctor
```

Then, in a chat with a workspace that includes the `instrument-status` group:

> Grab a frame from the hutch camera.

You should get the image inline. Behind the scenes `grab_frame` returns MCP
`ImageContent`, which AIDA converts to an `ImageArtifact` in
`src/aida/mcp/results.py` — no AIDA changes are needed for this to work.

---

## Part B — deploy to the beamline Linux machine

### B0. Read this first: what "shared home" does and does not buy you

The beamline machine and a networked Linux host mount the same `$HOME`. That
removes the need to bundle wheels — but it constrains **where** you build:

- **You cannot build the environment on your Mac.** A conda environment is
  platform-specific compiled binaries plus absolute paths baked into
  shebangs. A macOS env in a shared home is useless to a Linux machine.
  The environment must be created **from a Linux host of the same
  architecture** that mounts the same home.
- **The home must be mounted at the identical path on both machines.** Conda
  writes absolute paths into console-script shebangs. If the networked host
  sees `/home/beams/USAXS` and the beamline box sees `/nfs/home/USAXS`, every
  script breaks. Verify with `echo $HOME` on both.
- **Check where conda puts environments.** If `conda` is a system install
  under `/opt`, `conda env create` may write to `/opt/...`, which is *not*
  shared. Confirm first:

```bash
conda config --show envs_dirs      # want a path under $HOME, e.g. ~/.conda/envs
```

  If it is not under `$HOME`, create by prefix instead:
  `conda env create -f environment.yml --prefix $HOME/.conda/envs/axis-cam-mcp`.

If any of those three turn out not to hold, skip to Appendix C.

### B1. Get the repo into the shared home

From your Mac, push the repo you built in Part A:

```bash
cd ~/GitHub/axis-cam-mcp
git add -A && git commit -m "Initial axis-cam-mcp"
gh repo create jilavsky/axis-cam-mcp --private --source=. --push
```

Then from the **networked Linux host** (the one with internet that shares the
home):

```bash
ssh <networked-linux-host>
uname -m                                  # record: expect x86_64
echo $HOME                                # record; must match the beamline box
mkdir -p ~/GitHub && cd ~/GitHub
git clone https://github.com/jilavsky/axis-cam-mcp.git
cd axis-cam-mcp
```

### B2. Create the environment, from the Linux host

```bash
conda env create -f environment.yml
conda activate axis-cam-mcp
which axis-cam-mcp
# -> /home/beams/USAXS/.conda/envs/axis-cam-mcp/bin/axis-cam-mcp   (record this)

pytest -q        # 10 passed -- proves the install without needing a camera
```

That is the only step that needs the internet. Everything after this happens
on the isolated machine.

### B3. Verify on the beamline machine — install first, camera second

```bash
ssh <beamline-machine>
echo $HOME                                   # must match what you recorded

# 1. The install is visible and sound, with no camera involved:
~/.conda/envs/axis-cam-mcp/bin/axis-cam-mcp --help
cd ~/GitHub/axis-cam-mcp && ~/.conda/envs/axis-cam-mcp/bin/pytest -q

# 2. The camera is reachable on the private subnet:
ping -c 2 CAMERA-IP
curl -s --digest -u 'viewer:PASSWORD' \
     'http://CAMERA-IP/axis-cgi/jpg/image.cgi?resolution=640x480' \
     -o /tmp/axis-test.jpg && file /tmp/axis-test.jpg
```

Splitting it this way matters: if step 1 passes and step 2 fails, the problem
is the network or the camera account, not the deployment.

### B4. Camera config on the beamline machine

`~/.axis-cam-mcp/` is in the shared home, so if you created it from the
networked host it is already there — but the **camera address is almost
certainly different** on the private subnet. Edit it for the beamline:

```bash
mkdir -p ~/.axis-cam-mcp
cp ~/GitHub/axis-cam-mcp/examples/cameras.yaml ~/.axis-cam-mcp/cameras.yaml
$EDITOR ~/.axis-cam-mcp/cameras.yaml
```

### B5. Credentials without a keychain

This is the one real difference from the Mac. A headless Linux machine
usually has no D-Bus secret service, so `keyring` has nothing to talk to and
`aida config secret set` will not work.

You do not need it. `aida.config.secrets.get_secret` checks the environment
variable **first**, and only then the keychain — so the same
`"keyring:axis_cam_hutch"` reference in `mcp.json` works unchanged, fed from
an environment variable instead.

The variable name is `AIDA_SECRET_` + the profile name uppercased with `-`
replaced by `_`. So `axis_cam_hutch` → **`AIDA_SECRET_AXIS_CAM_HUTCH`**.

Put it in a mode-600 file rather than `.bashrc`:

```bash
mkdir -p ~/.axis-cam-mcp
umask 077
cat > ~/.axis-cam-mcp/secrets.env <<'EOF'
export AIDA_SECRET_AXIS_CAM_HUTCH='the-camera-password'
EOF
chmod 600 ~/.axis-cam-mcp/secrets.env
ls -l ~/.axis-cam-mcp/secrets.env      # confirm -rw-------
```

and source it before launching AIDA:

```bash
source ~/.axis-cam-mcp/secrets.env
aida
```

Note the shared home means that file is visible to anything that mounts it —
which is the argument for a viewer-role account whose password grants nothing
but the ability to look at a picture.

### B6. Wire into AIDA on the beamline machine

`~/.aida/mcp.json` is also in the shared home. Add:

```json
"axis-cam": {
  "command": "/home/beams/USAXS/.conda/envs/axis-cam-mcp/bin/axis-cam-mcp",
  "args": [],
  "env": {
    "AXIS_CAM_CONFIG": "/home/beams/USAXS/.axis-cam-mcp/cameras.yaml",
    "AXIS_USER_HUTCH": "viewer",
    "AXIS_PASS_HUTCH": "keyring:axis_cam_hutch"
  },
  "type": "stdio",
  "url": "",
  "headers": {},
  "groups": ["instrument-status"],
  "skills": [],
  "disabled_tools": [],
  "confirm_tools": [],
  "timeout_seconds": null
}
```

Substitute the absolute paths you recorded in B2. Then:

```bash
source ~/.axis-cam-mcp/secrets.env
axis-cam-mcp --check          # last check outside AIDA
aida mcp server list
```

and ask for a frame in chat.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `mcp server 'axis-cam' did not finish starting within 30s` | The subprocess crashed on import or never answered the handshake | Run the `command` from `mcp.json` by hand in a terminal — the traceback will be obvious. AIDA also captures the server's stderr. |
| `'AXIS_PASS_HUTCH' references secret 'axis_cam_hutch' ... but nothing is stored` | Keychain empty (Mac) or `AIDA_SECRET_*` unset (Linux) | Mac: `aida config secret set axis_cam_hutch`. Linux: `source ~/.axis-cam-mcp/secrets.env` **before** launching AIDA. |
| Tool error: `environment variable(s) AXIS_USER_HUTCH are not set` | The variable is in your shell but not in the server's `env` block | AIDA passes only `HOME, LOGNAME, PATH, SHELL, TERM, USER` through. Add it to `env` in `mcp.json`. |
| Tool error: `rejected the credentials ... (HTTP 401)` | Wrong password, or the camera wants basic not digest | Reproduce with `curl --digest`, then `curl --basic`. Check the account's role in the camera web UI. |
| Tool error: `returned text/html ..., not a JPEG` | Unsupported `resolution` or `compression` | Ask for a resolution the camera actually supports; omit the argument to use its default. |
| Tool error: `cannot reach camera ...` | Wrong subnet, firewall, or camera off | `ping` then `curl` from the same machine AIDA runs on. |
| `axis-cam-mcp: command not found` on the beamline box | Env built on the wrong platform, or `$HOME` mounted at a different path | See B0. Rebuild from a Linux host of the same arch. |
| `keyring.errors.NoKeyringError` on Linux | No D-Bus secret service on a headless box | Expected — use `AIDA_SECRET_*` as in B5. Do not install a keyring daemon. |
| The image blows up the context | Full-frame stills are large | Pass `resolution` (e.g. `640x480`) — the camera scales server-side, so it costs nothing. |

## Appendix A — the VAPIX endpoints used

Both need only a **viewer**-role account.

**Still image** — `GET /axis-cgi/jpg/image.cgi`

| Parameter | Meaning |
|---|---|
| `resolution` | `WIDTHxHEIGHT`, e.g. `1280x720`. Scaled by the camera. |
| `compression` | `0`–`100`; `0` is best quality and the largest file. |
| `rotation` | `0`, `90`, `180`, `270`. |
| `camera` | Sensor number on multi-sensor models. |

**Device identity** — `GET /axis-cgi/param.cgi?action=list&group=Brand`,
returning `root.Brand.ProdNbr=...` lines. Used by `camera_info` and
`--check`, and the cheapest way to prove credentials and routing are good
without moving an image.

For a camera that only speaks HTTPS with a self-signed certificate, set
`scheme: https` and `verify_tls: false` on that camera in `cameras.yaml`.

## Appendix B — why these version pins

- **`mcp>=1.0.0,<2.0`** — mcp 2.0 removed `mcp.server.fastmcp`, which this
  server is built on. Same pin and same reason as `epics-mcp`,
  `aievaluator` and `pyirena[mcp]`. If the cap is ever lifted, it is a port,
  not a version bump.
- **`python>=3.11,<3.14`** in `environment.yml`, against the package's own
  `requires-python = ">=3.10"` — mirrors `epics-mcp` so the two
  environments stay interchangeable.
- **No image library.** `opencv`, `pillow`, `pypylon` and `streamlink` are
  all absent by design: the camera returns a finished JPEG. Keeping it that
  way is what makes the offline deployment in Part B a two-package install.

## Appendix C — if the shared home turns out not to be shared

Fallback if B0's assumptions fail. Run on a networked **Linux** machine of
the same architecture as the beamline box:

```bash
# Match the target's Python minor version exactly.
pip download --only-binary=:all: \
    --platform manylinux_2_17_x86_64 --python-version 3.12 \
    -d wheels mcp 'pyyaml>=6.0'
tar czf axis-cam-bundle.tgz wheels/ axis-cam-mcp/
```

Move the tarball across, then on the beamline machine:

```bash
conda create -n axis-cam-mcp 'python>=3.11,<3.14'
conda activate axis-cam-mcp
pip install --no-index --find-links wheels/ mcp pyyaml
pip install --no-index --no-deps -e axis-cam-mcp/
axis-cam-mcp --check
```

`--platform` and `--python-version` must match the target or `pip download`
silently fetches the wrong wheels. If you would rather move the whole
environment than resolve it twice, `conda-pack` is the other option — but it
needs the same architecture and a relocatable prefix, so it is not obviously
simpler.
