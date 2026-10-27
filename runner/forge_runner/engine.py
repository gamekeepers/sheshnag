"""Starting and stopping the training engine as a subprocess.

DockerEngine is the production path on watchtower: the pinned LLaMA-Factory
image (G2), run rootless with the job directory mounted at the same path
inside the container, so the paths in the generated config are valid on
both sides. CommandEngine runs any command with `{config}` substituted —
`llamafactory-cli train {config}` on a machine with the engine installed,
or a fake engine in tests.
"""
import subprocess
from pathlib import Path
from typing import IO, Optional, Sequence


class EngineProcess:
    def __init__(self, popen: subprocess.Popen, log: IO, stop_cmd: Optional[list] = None):
        self._popen = popen
        self._log = log
        self._stop_cmd = stop_cmd

    def poll(self) -> Optional[int]:
        rc = self._popen.poll()
        if rc is not None and not self._log.closed:
            self._log.close()
        return rc

    def stop(self, timeout: float = 60) -> None:
        """Stop the run. For docker, the container must be stopped by name:
        killing the `docker run` client would leave training on the GPU."""
        if self._popen.poll() is None:
            if self._stop_cmd:
                subprocess.run(self._stop_cmd, capture_output=True, timeout=timeout)
            else:
                self._popen.terminate()
            try:
                self._popen.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._popen.kill()
                self._popen.wait()
        if not self._log.closed:
            self._log.close()


class CommandEngine:
    def __init__(self, command: Sequence[str], image: Optional[str] = None,
                 name: str = "llamafactory", version: Optional[str] = None):
        self.command = list(command)
        self.image = image
        self.name = name
        self.version = version

    def start(self, config_path: Path, workdir: Path, run_name: str, log_path: Path) -> EngineProcess:
        cmd = [part.replace("{config}", str(config_path)) for part in self.command]
        log = open(log_path, "w", encoding="utf-8")
        popen = subprocess.Popen(cmd, cwd=workdir, stdout=log, stderr=subprocess.STDOUT)
        return EngineProcess(popen, log)


class DockerEngine(CommandEngine):
    def __init__(self, image: str, hf_cache: Path, *, version: Optional[str] = None,
                 device: str = "nvidia.com/gpu=all", memory: str = "32g", cpus: str = "12"):
        super().__init__([], image=image, version=version)
        self.hf_cache = hf_cache
        self.device, self.memory, self.cpus = device, memory, cpus

    def start(self, config_path: Path, workdir: Path, run_name: str, log_path: Path) -> EngineProcess:
        cmd = [
            "docker", "run", "--rm", "--name", run_name,
            "--device", self.device, "--shm-size", "4g",
            "--memory", self.memory, "--cpus", self.cpus,
            "-v", f"{workdir}:{workdir}", "-w", str(workdir),
            "-v", f"{self.hf_cache}:/hf-cache", "-e", "HF_HOME=/hf-cache",
            "-e", "PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True",
            "-e", "DISABLE_VERSION_CHECK=1",
            self.image, "llamafactory-cli", "train", str(config_path),
        ]
        log = open(log_path, "w", encoding="utf-8")
        popen = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)
        return EngineProcess(popen, log, stop_cmd=["docker", "stop", run_name])
