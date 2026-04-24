"""sing-box subprocess lifecycle helpers for Stage 5 prober."""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
from typing import Any, Iterator

from .errors import ControlledProbeError, ProbeErrorCode


class SingBoxRuntime:
    """Manage sing-box process startup/teardown for one probe attempt."""

    def __init__(
        self,
        *,
        binary: str,
        bind_host: str,
        base_local_port: int,
        start_timeout_seconds: int,
        temp_dir: str | None,
    ) -> None:
        self._binary = self._resolve_binary(binary)
        self._bind_host = bind_host
        self._base_local_port = base_local_port
        self._start_timeout_seconds = start_timeout_seconds
        self._temp_dir = Path(temp_dir).expanduser() if temp_dir else None

        if self._temp_dir:
            self._temp_dir.mkdir(parents=True, exist_ok=True)

    @property
    def bind_host(self) -> str:
        return self._bind_host

    def allocate_port(self) -> int:
        """Pick local TCP port for temporary sing-box inbound."""
        if self._base_local_port > 0:
            for offset in range(0, 300):
                candidate = self._base_local_port + offset
                if candidate > 65535:
                    break
                if self._is_port_available(candidate):
                    return candidate

        return self._allocate_ephemeral_port()

    @contextmanager
    def run(self, *, config: dict[str, Any], listen_port: int) -> Iterator[None]:
        """Run sing-box until caller finishes probe logic."""
        config_path = self._write_temp_config(config)
        process: subprocess.Popen[str] | None = None
        try:
            process = self._start_process(config_path)
            self._wait_for_inbound_ready(process, listen_port)
            yield
        finally:
            if process is not None:
                self._stop_process(process)
            self._cleanup_temp_config(config_path)

    def _resolve_binary(self, binary: str) -> str:
        binary_path = Path(binary)
        if binary_path.is_file():
            return str(binary_path)

        resolved = shutil.which(binary)
        if resolved:
            return resolved

        raise ControlledProbeError(
            code=ProbeErrorCode.BACKEND_NOT_AVAILABLE,
            text=f"sing-box binary not found: {binary}",
        )

    def _write_temp_config(self, config: dict[str, Any]) -> Path:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".json",
            prefix="prober-singbox-",
            dir=str(self._temp_dir) if self._temp_dir else None,
            delete=False,
        ) as handle:
            json.dump(config, handle, ensure_ascii=True)
            handle.flush()
            return Path(handle.name)

    def _start_process(self, config_path: Path) -> subprocess.Popen[str]:
        try:
            return subprocess.Popen(
                [self._binary, "run", "-c", str(config_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
        except FileNotFoundError as exc:
            raise ControlledProbeError(
                code=ProbeErrorCode.BACKEND_NOT_AVAILABLE,
                text=f"sing-box binary not found: {self._binary}",
            ) from exc
        except OSError as exc:
            raise ControlledProbeError(
                code=ProbeErrorCode.BACKEND_NOT_AVAILABLE,
                text=f"Failed to start sing-box process: {exc}",
            ) from exc

    def _wait_for_inbound_ready(self, process: subprocess.Popen[str], listen_port: int) -> None:
        deadline = time.monotonic() + self._start_timeout_seconds

        while time.monotonic() < deadline:
            if process.poll() is not None:
                details = self._read_stderr(process)
                raise ControlledProbeError(
                    code=ProbeErrorCode.PROBE_FAILED,
                    text=f"sing-box exited early: {details}",
                )

            if self._can_connect(listen_port):
                return

            time.sleep(0.1)

        raise ControlledProbeError(
            code=ProbeErrorCode.BACKEND_START_TIMEOUT,
            text=(
                f"sing-box inbound start timeout after {self._start_timeout_seconds}s "
                f"on {self._bind_host}:{listen_port}"
            ),
        )

    def _stop_process(self, process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            self._read_stderr(process)
            return

        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)

        self._read_stderr(process)

    def _cleanup_temp_config(self, path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    def _is_port_available(self, port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((self._bind_host, port))
            except OSError:
                return False
            return True

    def _allocate_ephemeral_port(self) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((self._bind_host, 0))
            return int(sock.getsockname()[1])

    def _can_connect(self, port: int) -> bool:
        try:
            with socket.create_connection((self._bind_host, port), timeout=0.25):
                return True
        except OSError:
            return False

    @staticmethod
    def _read_stderr(process: subprocess.Popen[str], *, max_chars: int = 800) -> str:
        if process.stderr is None:
            return "stderr unavailable"

        try:
            raw = process.stderr.read()
        except Exception:
            return "failed to read sing-box stderr"

        text = (raw or "").strip()
        if not text:
            return "no stderr output"
        if len(text) <= max_chars:
            return text
        return text[-max_chars:]
