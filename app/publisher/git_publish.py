"""Git publication for generated output artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import tempfile

from app.common.settings import PROJECT_ROOT, Settings, get_settings

_PUBLISH_FILES: tuple[str, ...] = (
    "output/BLACK-ETALON.txt",
    "output/WHITE-CIDR-ETALON.txt",
    "output/WHITE-SNI-ETALON.txt",
    "output/ALL-ETALON.txt",
    "output/export_manifest.json",
)


@dataclass(slots=True, frozen=True)
class PublishResult:
    """Result of one git publication attempt."""

    enabled: bool
    committed: bool
    pushed: bool
    changed_files: tuple[str, ...]
    commit_sha: str | None
    skipped_reason: str | None = None

    def to_log_extra(self) -> dict[str, object]:
        return {
            "publish_enabled": self.enabled,
            "publish_committed": self.committed,
            "publish_pushed": self.pushed,
            "publish_changed_files": list(self.changed_files),
            "publish_commit_sha": self.commit_sha,
            "publish_skipped_reason": self.skipped_reason,
        }


def publish_output(app_settings: Settings | None = None) -> PublishResult:
    """Commit and push output artifacts if there are staged changes."""
    settings = app_settings or get_settings()
    if not settings.PUBLISH_ENABLED:
        return PublishResult(
            enabled=False,
            committed=False,
            pushed=False,
            changed_files=(),
            commit_sha=None,
            skipped_reason="publish_disabled",
        )

    _ensure_output_files_ready()
    _ensure_git_repository(settings)

    timeout = settings.PUBLISH_PUSH_TIMEOUT_SECONDS
    _run_git(["add", "--", *_PUBLISH_FILES], timeout_seconds=timeout)

    changed_files = _collect_staged_changed_files(timeout_seconds=timeout)
    if not changed_files:
        return PublishResult(
            enabled=True,
            committed=False,
            pushed=False,
            changed_files=(),
            commit_sha=None,
            skipped_reason="no_changes",
        )

    commit_message = _build_commit_message(settings)
    git_env = _build_git_env(settings)

    _run_git(["commit", "-m", commit_message, "--", *_PUBLISH_FILES], timeout_seconds=timeout, env=git_env)
    commit_sha = _run_git(["rev-parse", "HEAD"], timeout_seconds=timeout).stdout.strip() or None

    push_refspec = f"HEAD:{settings.PUBLISH_BRANCH}"
    _push_with_runtime_auth(
        settings=settings,
        git_env=git_env,
        timeout_seconds=timeout,
        push_refspec=push_refspec,
    )

    return PublishResult(
        enabled=True,
        committed=True,
        pushed=True,
        changed_files=tuple(changed_files),
        commit_sha=commit_sha,
    )


def _ensure_output_files_ready() -> None:
    for relative_path in _PUBLISH_FILES:
        path = PROJECT_ROOT / relative_path
        if not path.is_file():
            raise RuntimeError(f"publish file does not exist: {relative_path}")


def _ensure_git_repository(settings: Settings) -> None:
    _run_git(["rev-parse", "--is-inside-work-tree"], timeout_seconds=settings.PUBLISH_PUSH_TIMEOUT_SECONDS)
    _run_git(["remote", "get-url", settings.PUBLISH_REMOTE], timeout_seconds=settings.PUBLISH_PUSH_TIMEOUT_SECONDS)


def _collect_staged_changed_files(*, timeout_seconds: int) -> list[str]:
    process = _run_git(
        ["diff", "--cached", "--name-only", "--", *_PUBLISH_FILES],
        timeout_seconds=timeout_seconds,
    )
    return [line.strip() for line in process.stdout.splitlines() if line.strip()]


def _build_commit_message(settings: Settings) -> str:
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    return f"{settings.PUBLISH_COMMIT_MESSAGE_PREFIX} ({generated_at})"


def _build_git_env(settings: Settings) -> dict[str, str]:
    env = dict(os.environ)
    env["GIT_AUTHOR_NAME"] = settings.PUBLISH_GIT_AUTHOR_NAME
    env["GIT_AUTHOR_EMAIL"] = settings.PUBLISH_GIT_AUTHOR_EMAIL
    env["GIT_COMMITTER_NAME"] = settings.PUBLISH_GIT_AUTHOR_NAME
    env["GIT_COMMITTER_EMAIL"] = settings.PUBLISH_GIT_AUTHOR_EMAIL
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _push_with_runtime_auth(
    *,
    settings: Settings,
    git_env: dict[str, str],
    timeout_seconds: int,
    push_refspec: str,
) -> None:
    auth_mode = _normalize_auth_mode(settings.PUBLISH_AUTH_MODE)
    remote_url = _run_git(
        ["remote", "get-url", settings.PUBLISH_REMOTE],
        timeout_seconds=timeout_seconds,
        env=git_env,
    ).stdout.strip()
    https_remote = remote_url.startswith("https://") or remote_url.startswith("http://")
    token = _resolve_publish_token(auth_mode=auth_mode)

    askpass_path: str | None = None
    push_env = dict(git_env)
    try:
        if auth_mode == "https_token" and not token:
            raise RuntimeError("Publish auth mode https_token requires GITHUB_TOKEN or GH_TOKEN.")
        if token and https_remote and auth_mode in ("auto", "https_token"):
            askpass_path = _create_runtime_askpass_script()
            push_env["GIT_ASKPASS"] = askpass_path
            push_env["PUBLISH_HTTPS_TOKEN"] = token
            push_env.setdefault("PUBLISH_HTTPS_USERNAME", "x-access-token")

        _run_git(
            ["push", settings.PUBLISH_REMOTE, push_refspec],
            timeout_seconds=timeout_seconds,
            env=push_env,
        )
    except RuntimeError as exc:
        if (
            https_remote
            and not token
            and "could not read Username" in str(exc)
            and auth_mode in ("auto", "https_token")
        ):
            raise RuntimeError(
                "Git push authentication failed for HTTPS remote in non-interactive mode. "
                "Set GITHUB_TOKEN or GH_TOKEN (or switch to SSH auth)."
            ) from exc
        raise
    finally:
        if askpass_path:
            Path(askpass_path).unlink(missing_ok=True)
        push_env.pop("PUBLISH_HTTPS_TOKEN", None)


def _normalize_auth_mode(raw_auth_mode: str) -> str:
    mode = (raw_auth_mode or "").strip().lower() or "auto"
    allowed = {"auto", "none", "https_token", "ssh"}
    if mode not in allowed:
        raise RuntimeError(
            f"Unsupported PUBLISH_AUTH_MODE={raw_auth_mode!r}. "
            "Use one of: auto, none, https_token, ssh."
        )
    return mode


def _resolve_publish_token(*, auth_mode: str) -> str | None:
    if auth_mode in {"none", "ssh"}:
        return None

    for env_key in ("GITHUB_TOKEN", "GH_TOKEN"):
        token = (os.getenv(env_key) or "").strip()
        if token:
            return token
    return None


def _create_runtime_askpass_script() -> str:
    fd, path = tempfile.mkstemp(prefix="publish-askpass-", suffix=".sh")
    os.close(fd)
    script_content = (
        "#!/bin/sh\n"
        "prompt=\"$1\"\n"
        "case \"$prompt\" in\n"
        "  *Username*|*username*) printf '%s\\n' \"${PUBLISH_HTTPS_USERNAME:-x-access-token}\" ;;\n"
        "  *Password*|*password*) printf '%s\\n' \"${PUBLISH_HTTPS_TOKEN:-}\" ;;\n"
        "  *) printf '\\n' ;;\n"
        "esac\n"
    )
    Path(path).write_text(script_content, encoding="utf-8")
    os.chmod(path, 0o700)
    return path


def _run_git(
    args: list[str],
    *,
    timeout_seconds: int,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    command = ["git", *args]
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        stdout = completed.stdout.strip()
        details = stderr or stdout or f"exit_code={completed.returncode}"
        raise RuntimeError(f"Git command failed: {' '.join(command)} :: {details}")
    return completed
