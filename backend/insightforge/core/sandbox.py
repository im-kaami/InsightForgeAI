import json
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

MAX_CODE_CHARS = 8000


@dataclass
class SandboxResult:
    ok: bool
    frame: pd.DataFrame | None = None
    value: Any = None
    stdout: str = ""
    error: str | None = None
    seconds: float = 0.0
    limits: dict[str, str] = field(default_factory=dict)


def build_sandbox(settings: Any) -> "DockerSandbox | None":
    if not getattr(settings, "sandbox_enabled", False):
        return None
    sandbox = DockerSandbox(
        settings.sandbox_image,
        timeout=settings.sandbox_timeout_seconds,
        memory=settings.sandbox_memory,
        cpus=settings.sandbox_cpus,
    )
    return sandbox if sandbox.available() else None


class DockerSandbox:
    """Runs model-written Python against one result table in a locked-down container.

    The container has no network, a read-only root filesystem, no Linux capabilities, an
    unprivileged user, capped memory, CPU and processes, and only two mounts: the input table
    (read-only) and an output folder.
    """

    def __init__(self, image: str, timeout: float = 30, memory: str = "512m", cpus: str = "1"):
        self.image, self.timeout, self.memory, self.cpus = image, timeout, memory, cpus
        self._available: bool | None = None

    def available(self) -> bool:
        if self._available is None:
            docker = shutil.which("docker")
            self._available = bool(docker) and (
                subprocess.run(
                    [docker, "image", "inspect", self.image], capture_output=True, timeout=20
                ).returncode
                == 0
            )
        return self._available

    @property
    def limits(self) -> dict[str, str]:
        return {
            "network": "none",
            "memory": self.memory,
            "cpus": self.cpus,
            "timeout_seconds": str(int(self.timeout)),
            "filesystem": "read-only; input mounted read-only",
        }

    def run(self, code: str, frame: pd.DataFrame) -> SandboxResult:
        started = time.perf_counter()
        if len(code) > MAX_CODE_CHARS:
            return SandboxResult(False, error=f"Code is longer than {MAX_CODE_CHARS} characters")
        if not self.available():
            return SandboxResult(False, error=f"The Python sandbox is not available (image {self.image})")
        name = f"insightforge-sandbox-{uuid.uuid4().hex[:12]}"
        with tempfile.TemporaryDirectory() as work_dir, tempfile.TemporaryDirectory() as out_dir:
            work, out = Path(work_dir), Path(out_dir)
            frame.to_parquet(work / "input.parquet", index=False)
            (work / "code.py").write_text(code, encoding="utf-8")
            command = [
                "docker", "run", "--rm", "--name", name,
                "--network", "none", "--read-only",
                "--memory", self.memory, "--memory-swap", self.memory, "--cpus", self.cpus,
                "--pids-limit", "128", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--user", "65534:65534", "--tmpfs", "/tmp:rw,size=64m",
                "-v", f"{work}:/work:ro", "-v", f"{out}:/out:rw",
                self.image, "python", "/sandbox/runner.py",
            ]  # fmt: skip
            try:
                process = subprocess.run(command, capture_output=True, text=True, timeout=self.timeout)
            except subprocess.TimeoutExpired:
                subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)
                return SandboxResult(
                    False,
                    error=f"The code ran longer than {int(self.timeout)} seconds and was stopped",
                    seconds=time.perf_counter() - started,
                    limits=self.limits,
                )
            status_path = out / "status.json"
            if not status_path.exists():
                detail = (process.stderr or process.stdout or "").strip()[-800:]
                oom = process.returncode == 137
                return SandboxResult(
                    False,
                    error="The code was stopped for using too much memory"
                    if oom
                    else f"The sandbox failed (exit {process.returncode}): {detail}",
                    seconds=time.perf_counter() - started,
                    limits=self.limits,
                )
            status = json.loads(status_path.read_text(encoding="utf-8"))
            result = SandboxResult(
                bool(status.get("ok")),
                stdout=str(status.get("stdout", "")),
                error=status.get("error"),
                seconds=time.perf_counter() - started,
                limits=self.limits,
            )
            if status.get("kind") == "table" and (out / "result.parquet").exists():
                result.frame = pd.read_parquet(out / "result.parquet")
            elif status.get("kind") == "value":
                result.value = status.get("value")
            return result
