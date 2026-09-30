"""Shared helpers: secrets, fal uploads and queue calls, downloads, media probing, errors.

Standard library only. The fal key is read from the FAL_KEY environment variable or,
failing that, from the nearest `.env` file (current directory upwards, then this folder
upwards). It is never printed or logged.
"""

from __future__ import annotations

import json
import mimetypes
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

UPLOAD_INITIATE_URL = "https://rest.alpha.fal.ai/storage/upload/initiate?storage_type=fal-cdn-v3"
QUEUE_URL = "https://queue.fal.run"


class ToolError(RuntimeError):
    """An error with a machine-readable code, returned to callers as JSON."""

    def __init__(self, code: str, message: str, **extra):
        super().__init__(message)
        self.code = code
        self.extra = extra

    def as_dict(self) -> dict:
        return {"status": "failed", "error": {"code": self.code, "message": str(self), **self.extra}}


# --- secrets -----------------------------------------------------------------

def _find_env_file() -> Path | None:
    for start in (Path.cwd(), Path(__file__).resolve().parent):
        for folder in (start, *start.parents):
            candidate = folder / ".env"
            if candidate.is_file():
                return candidate
    return None


def _read_env_value(path: Path, name: str) -> str | None:
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip().removeprefix("export ").strip() == name:
            return value.strip().strip('"').strip("'")
    return None


def get_secret(name: str = "FAL_KEY") -> str:
    """Single place to swap in the platform's secret system later."""
    value = os.environ.get(name)
    if not value:
        env_file = _find_env_file()
        if env_file:
            value = _read_env_value(env_file, name)
    if not value:
        raise ToolError("missing_secret", f"{name} is not set (environment variable or .env)")
    return value.strip()


# --- fal ---------------------------------------------------------------------

def _request(url: str, *, data: bytes | None = None, method: str = "GET",
             headers: dict[str, str] | None = None, timeout: float = 120) -> dict:
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        raise _http_error(e, url) from None
    except urllib.error.URLError as e:
        raise ToolError("network_error", f"{url.split('?')[0]}: {e.reason}") from None
    return json.loads(body) if body else {}


def _http_error(e: urllib.error.HTTPError, url: str) -> ToolError:
    raw = e.read().decode("utf-8", "replace")
    try:
        detail = json.loads(raw).get("detail")
    except (ValueError, AttributeError):
        detail = None
    if isinstance(detail, list) and detail and isinstance(detail[0], dict):
        d = detail[0]
        kind = d.get("type", "provider_error")
        reason = (d.get("ctx") or {}).get("extra_info", {}).get("reason")
        code = {"content_policy_violation": "content_policy_violation",
                "file_download_error": "file_download_error"}.get(kind, "provider_error")
        return ToolError(code, d.get("msg", raw[:300]), http_status=e.code,
                         **({"reason": reason} if reason else {}))
    code = "unauthorized" if e.code in (401, 403) else "provider_error"
    return ToolError(code, f"HTTP {e.code} from {url.split('?')[0]}: {raw[:300]}", http_status=e.code)


def _auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Key {key}", "Content-Type": "application/json"}


def upload(path: str | Path, *, key: str | None = None) -> str:
    """Upload a local file to fal storage and return its public URL."""
    key = key or get_secret()
    path = Path(path)
    if not path.is_file():
        raise ToolError("file_not_found", f"file not found: {path}")
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    init = _request(UPLOAD_INITIATE_URL, method="POST", headers=_auth(key),
                    data=json.dumps({"content_type": content_type, "file_name": path.name}).encode())
    put = urllib.request.Request(init["upload_url"], data=path.read_bytes(), method="PUT",
                                 headers={"Content-Type": content_type})
    with urllib.request.urlopen(put, timeout=600):
        pass
    return init["file_url"]


def is_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


def ensure_url(path_or_url: str, *, key: str | None = None) -> str:
    """Return the input if it is already a URL, otherwise upload the local file."""
    return path_or_url if is_url(path_or_url) else upload(path_or_url, key=key)


def run(endpoint: str, payload: dict, *, key: str | None = None,
        poll_every: float = 5, timeout: float = 1800) -> tuple[dict, str]:
    """Submit a job to the fal queue, wait for it; returns (result JSON, request id).

    A job the provider rejected still reports COMPLETED; fetching its result then fails
    with HTTP 422 and the reason, which is raised as ToolError.
    """
    key = key or get_secret()
    job = _request(f"{QUEUE_URL}/{endpoint}", method="POST", headers=_auth(key),
                   data=json.dumps(payload).encode())
    request_id = job.get("request_id", "")
    deadline = time.monotonic() + timeout
    while True:
        status = _request(job["status_url"], headers=_auth(key)).get("status")
        if status == "COMPLETED":
            try:
                return _request(job["response_url"], headers=_auth(key)), request_id
            except ToolError as e:
                e.extra["request_id"] = request_id
                raise
        if status not in ("IN_QUEUE", "IN_PROGRESS"):
            raise ToolError("provider_error", f"{endpoint} job ended with status {status}",
                            request_id=request_id)
        if time.monotonic() > deadline:
            raise ToolError("timeout", f"{endpoint} job did not finish in {timeout:.0f}s",
                            request_id=request_id)
        time.sleep(poll_every)


# --- local files and media -----------------------------------------------------

def download(url: str, dest: str | Path) -> Path:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (nim-tools)"})
    try:
        with urllib.request.urlopen(req, timeout=600) as resp, open(dest, "wb") as f:
            shutil.copyfileobj(resp, f)
    except urllib.error.HTTPError as e:
        raise ToolError("download_error", f"HTTP {e.code} downloading {url.split('?')[0]}") from None
    except urllib.error.URLError as e:
        raise ToolError("network_error", f"{url.split('?')[0]}: {e.reason}") from None
    return dest


def local_copy(path_or_url: str, dest: str | Path) -> Path:
    """Return a local path for the input, downloading it if it is a URL."""
    if is_url(path_or_url):
        return download(path_or_url, dest)
    path = Path(path_or_url)
    if not path.is_file():
        raise ToolError("file_not_found", f"file not found: {path}")
    return path


def require_binary(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise ToolError("missing_dependency", f"{name} is not installed or not on PATH")
    return found


def probe(path_or_url: str) -> dict | None:
    """Video facts via ffprobe (local path or URL); None if ffprobe is unavailable."""
    if not shutil.which("ffprobe"):
        return None
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets",
         "-show_entries", "stream=width,height,r_frame_rate,nb_read_packets:format=duration",
         "-of", "json", path_or_url],
        capture_output=True, text=True)
    if out.returncode != 0:
        return None
    data = json.loads(out.stdout)
    stream = (data.get("streams") or [{}])[0]
    num, _, den = stream.get("r_frame_rate", "0/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 0.0
    return {
        "width": stream.get("width"),
        "height": stream.get("height"),
        "fps": round(fps, 3),
        "frames": int(stream["nb_read_packets"]) if "nb_read_packets" in stream else None,
        "duration": round(float(data.get("format", {}).get("duration", 0)), 3),
    }
