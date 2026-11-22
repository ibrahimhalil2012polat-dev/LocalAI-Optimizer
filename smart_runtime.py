"""Local Ollama identity, memory preflight and guarded Smart requests."""

import http.client
import json
import platform
import re
import socket
import threading
import time
from dataclasses import dataclass

import psutil

from benchmark import GB, MB, commit_available, memory_limits
from compatibility import estimate_kv_cache

HOST, PORT = "127.0.0.1", 11434
CONTEXTS = (1024, 2048, 4096, 8192)
REQUEST_SECONDS = 120
MAX_RESPONSE = 1024 * 1024


def request(endpoint, payload=None):
    connection = http.client.HTTPConnection(HOST, PORT, timeout=8)
    try:
        connection.request("POST" if payload is not None else "GET", endpoint,
                           body=json.dumps(payload) if payload is not None else None,
                           headers={"Content-Type": "application/json"})
        with connection.getresponse() as response:
            if response.status != 200:
                raise RuntimeError(f"Ollama {endpoint} returned HTTP {response.status}")
            data = json.loads(response.read(MAX_RESPONSE + 1))
        if not isinstance(data, dict) or data.get("error"):
            raise ValueError("Expected an Ollama result object")
        return data
    except (OSError, ValueError, http.client.HTTPException) as exc:
        raise RuntimeError(f"Cannot verify Ollama {endpoint}: {exc}") from exc
    finally:
        connection.close()


def model_list(endpoint):
    models = request(endpoint).get("models")
    if not isinstance(models, list) or any(not isinstance(m, dict) for m in models):
        raise RuntimeError(f"Invalid model inventory from {endpoint}")
    return models


def local_model(model):
    name = model.get("name")
    if not isinstance(name, str) or not name:
        return False
    tag = name.rsplit("/", 1)[-1].lower()
    if tag.endswith(":cloud") or tag.partition(":")[0].endswith("-cloud"):
        return False
    # Ollama also accepts registry-qualified names. Smart only uses local tags.
    first, slash, _ = name.partition("/")
    if slash and ("." in first or ":" in first or first == "localhost"):
        return False
    return (not model.get("remote_host") and not model.get("remote_model")
            and type(model.get("size")) is int and model["size"] > 0)


def digest_for(model):
    value = model.get("digest")
    if isinstance(value, str):
        value = value.removeprefix("sha256:").lower()
        if re.fullmatch(r"[a-f0-9]{64}", value):
            return value
    raise RuntimeError("Ollama did not provide a complete SHA-256 model digest")


def model_by_name(name):
    matches = [model for model in model_list("/api/tags") if model.get("name") == name]
    if len(matches) != 1 or not local_model(matches[0]):
        raise RuntimeError("Model is missing, remote or unsupported")
    return matches[0]


def ollama_version():
    value = request("/api/version").get("version")
    if not isinstance(value, str) or not re.fullmatch(r"\d+\.\d+\.\d+[\w.+-]*", value):
        raise RuntimeError("Ollama server version is unavailable")
    return value


def hardware_identity():
    if platform.system() != "Windows":
        raise RuntimeError("Smart Auto Optimizer requires Windows commit monitoring")
    import winreg

    fields = ("ProcessorNameString", "VendorIdentifier", "Identifier")
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"HARDWARE\DESCRIPTION\System\CentralProcessor") as registry:
            cpus = []
            count, _, _ = winreg.QueryInfoKey(registry)
            for index in sorted(winreg.EnumKey(registry, i) for i in range(count)):
                with winreg.OpenKey(registry, index) as cpu:
                    cpus.append({field: winreg.QueryValueEx(cpu, field)[0].strip()
                                 for field in fields})
        physical = psutil.cpu_count(logical=False)
        logical = psutil.cpu_count()
        affinity = sorted(psutil.Process().cpu_affinity())
        if not cpus or not physical or not logical or not affinity:
            raise ValueError("Incomplete CPU inventory")
    except (OSError, ValueError, TypeError, psutil.Error) as exc:
        raise RuntimeError(f"Cannot identify the Windows hardware: {exc}") from exc
    return {
        "os": platform.platform(), "os_build": platform.version(),
        "machine": platform.machine(), "cpus": cpus,
        "physical_cores": physical, "logical_cores": logical,
        "affinity": affinity, "ram_bytes": psutil.virtual_memory().total,
    }


def context_options(maximum):
    limit = min(maximum, CONTEXTS[-1])
    return sorted({size for size in CONTEXTS if size <= limit} | {limit}) if limit >= 1024 else []


def memory_required(model_size, info, context, batch):
    kv = estimate_kv_cache(info, context)
    if kv is None:
        raise RuntimeError("No reliable KV cache estimate for this model architecture")
    batch_extra = max(0, batch - 128) / 384 * 512 * MB
    # Include staging space beyond the single-sequence F16 estimate.
    return int(model_size + 2 * kv + max(GB, model_size * 0.15) + batch_extra)


@dataclass
class Session:
    identity: dict
    model_info: dict
    max_context: int

    @classmethod
    def inspect(cls, name):
        model = model_by_name(name)
        digest = digest_for(model)
        model_details = model.get("details") or {}
        meta = request("/api/show", {"model": name})
        info = meta.get("model_info") or {}
        if (meta.get("remote_host") or meta.get("remote_model")
                or model_details.get("format") != "gguf"
                or "completion" not in (meta.get("capabilities") or [])
                or "vision" in (meta.get("capabilities") or [])):
            raise RuntimeError("Smart requires a local text completion GGUF model")
        arch = info.get("general.architecture")
        maximum = info.get(f"{arch}.context_length")
        if type(maximum) is not int or not context_options(maximum):
            raise RuntimeError("Unknown or unsupported model context capacity")
        identity = {
            "model": name, "digest": digest, "size": model["size"],
            "architecture": arch, "max_context": maximum,
            "quantization": model_details.get("quantization_level"),
            "runner": model_details.get("runner"), "ollama_version": ollama_version(),
            "hardware": hardware_identity(),
        }
        memory_required(model["size"], info, 1024, 512)
        return cls(identity, info, maximum)

    def check_runtime(self):
        current = model_by_name(self.identity["model"])
        if (digest_for(current) != self.identity["digest"]
                or current["size"] != self.identity["size"]
                or ollama_version() != self.identity["ollama_version"]):
            raise RuntimeError("Model identity or Ollama version changed during the run")

    def memory_margin(self, context, batch):
        if context not in context_options(self.max_context):
            raise RuntimeError("Context is outside the tested model limits")
        ram = psutil.virtual_memory()
        commit = commit_available()
        if commit is None:
            raise RuntimeError("Windows commit headroom is unavailable")
        reserve, _ = memory_limits(ram.total)
        required = memory_required(self.identity["size"], self.model_info, context, batch)
        return int(min(ram.available, commit) - reserve - required)

    def preflight(self, options):
        self.check_runtime()
        if model_list("/api/ps"):
            raise RuntimeError("Ollama has a loaded model. Let it unload before retrying")
        margin = self.memory_margin(options["num_ctx"], options["num_batch"])
        if margin < 0:
            raise RuntimeError(f"Insufficient physical RAM or Windows commit ({margin / GB:+.2f} GiB margin)")
        return margin


def wait_idle():
    deadline = time.monotonic() + 10
    while model_list("/api/ps"):
        if time.monotonic() >= deadline:
            raise RuntimeError("The model did not unload after testing")
        time.sleep(0.5)


def run_prompt(session, prompt, options):
    """Measure one bounded, cold CPU request. Cancel on pressure or timeout."""
    session.preflight(options)
    payload = {"model": session.identity["model"], "stream": True, "keep_alive": 0,
               "messages": [{"role": "user", "content": prompt}], "options": options}
    connection = http.client.HTTPConnection(HOST, PORT, timeout=8)
    stop = threading.Event()
    reason = []
    reserve, critical = memory_limits(psutil.virtual_memory().total)
    deadline = time.monotonic() + REQUEST_SECONDS
    pieces = []
    final = None
    received = 0

    def monitor(sock):
        last = None
        while not stop.is_set():
            now = time.monotonic()
            try:
                commit = commit_available()
                ram = psutil.virtual_memory().available
                if commit is None or min(ram, commit) < critical:
                    reason.append("Critical RAM or commit headroom")
                elif last:
                    rate = (last[1] - ram) / max(now - last[0], .01)
                    if ram < reserve and rate > 128 * MB and ram - 2 * rate < critical:
                        reason.append("Rapid RAM depletion")
                if now >= deadline:
                    reason.append("Request exceeded its time limit")
                last = (now, ram)
            except Exception as exc:
                reason.append(f"Memory monitor failed: {exc}")
            if reason:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                return
            stop.wait(0.25)

    worker = None
    try:
        connection.connect()
        connection.sock.settimeout(REQUEST_SECONDS)
        worker = threading.Thread(target=monitor, args=(connection.sock,), daemon=True)
        worker.start()
        connection.request("POST", "/api/chat", body=json.dumps(payload),
                           headers={"Content-Type": "application/json"})
        with connection.getresponse() as response:
            if response.status != 200:
                raise RuntimeError(f"Ollama chat returned HTTP {response.status}")
            while line := response.readline(MAX_RESPONSE + 1):
                received += len(line)
                if received > MAX_RESPONSE:
                    raise RuntimeError("Ollama response exceeded the benchmark limit")
                chunk = json.loads(line)
                if not isinstance(chunk, dict) or chunk.get("error"):
                    raise RuntimeError("Ollama returned a generation error")
                message = chunk.get("message") or {}
                if not isinstance(message, dict):
                    raise RuntimeError("Ollama returned an invalid chat message")
                piece = message.get("content", "")
                if not isinstance(piece, str):
                    raise RuntimeError("Ollama returned invalid content")
                pieces.append(piece)
                if chunk.get("done") is True:
                    final = chunk
                    break
    except (OSError, ValueError, http.client.HTTPException) as exc:
        raise RuntimeError(reason[0] if reason else f"Ollama request interrupted: {exc}") from exc
    finally:
        stop.set()
        if worker is not None:
            worker.join(timeout=2)
        connection.close()
    if reason or final is None:
        raise RuntimeError(reason[0] if reason else "Incomplete Ollama response")
    wait_idle()
    session.check_runtime()
    return final, "".join(pieces)
