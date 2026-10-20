"""Guards on the CI workflows and the Docker files: what runs when, and what the image is.

The PC has no Docker: the proof that the image works is the ``docker`` job of ``ci.yml`` (build, run,
``/v1/health``, ``/v1/embedded/frame``). These tests only keep the files from drifting.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def load(name: str) -> dict[str, Any]:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def triggers(workflow: dict[str, Any]) -> dict[str, Any]:
    """The ``on:`` block (YAML 1.1 reads the key ``on`` as the boolean ``True``)."""
    return workflow.get("on") or workflow.get(True) or {}


def steps_text(workflow: dict[str, Any], job: str) -> str:
    return "\n".join(str(step.get("run", "")) + " " + str(step.get("uses", "")) for step in workflow["jobs"][job]["steps"])


# ============================================================================
# Which workflow runs when
# ============================================================================


def test_ci_keeps_ruff_and_pytest_on_every_push_and_pull_request() -> None:
    ci = load("ci.yml")
    assert "pull_request" in triggers(ci) and triggers(ci)["push"]["branches"] == ["main"]
    test = ci["jobs"]["test"]
    assert test["strategy"]["matrix"]["python-version"] == ["3.12", "3.13"]
    assert "continue-on-error" not in test  # ruff and pytest stay blocking
    text = steps_text(ci, "test")
    assert "ruff check" in text and "pytest" in text


def test_ci_builds_and_runs_the_image() -> None:
    ci = load("ci.yml")
    text = steps_text(ci, "docker")
    assert "docker build" in text and "scripts/docker_smoke.sh" in text
    smoke = (ROOT / "scripts" / "docker_smoke.sh").read_text(encoding="utf-8")
    for needed in ("/v1/health", "/v1/embedded/frame", "--retry", "/v1/simulate", "id -u", "Health.Status"):
        assert needed in smoke, needed


def test_live_feeds_run_on_a_schedule_or_by_hand_never_in_a_pull_request() -> None:
    on = triggers(load("live-feeds.yml"))
    assert set(on) == {"schedule", "workflow_dispatch"}
    (entry,) = on["schedule"]
    minute, hour, day_of_month, month, day_of_week = entry["cron"].split()
    assert day_of_month == "*" and month == "*" and day_of_week != "*"  # once a week
    assert "check_live_feeds.py" in steps_text(load("live-feeds.yml"), "live-feeds")


def test_the_image_is_published_only_for_a_version_tag() -> None:
    publish = load("publish-image.yml")
    assert triggers(publish) == {"push": {"tags": ["v*"]}}  # no branch, no pull request
    assert publish["env"]["IMAGE"] == "ghcr.io/axellum/vigie-api"
    assert publish["permissions"] == {"contents": "read", "packages": "write"}
    text = steps_text(publish, "publish")
    assert text.index("docker_smoke.sh") < text.index("docker push")  # tested before it is pushed
    for other in ("ci.yml", "live-feeds.yml", "secret-scan.yml"):
        assert "packages" not in str(load(other).get("permissions", {}))  # nothing else can publish


def test_the_secret_scan_reads_the_whole_history_with_a_pinned_checked_binary() -> None:
    workflow = load("secret-scan.yml")
    checkout = next(s for s in workflow["jobs"]["gitleaks"]["steps"] if "checkout" in str(s.get("uses", "")))
    assert checkout["with"]["fetch-depth"] == 0
    assert re.fullmatch(r"\d+\.\d+\.\d+", workflow["env"]["GITLEAKS_VERSION"])
    assert re.fullmatch(r"[0-9a-f]{64}", workflow["env"]["GITLEAKS_SHA256"])
    text = steps_text(workflow, "gitleaks")
    assert "sha256sum --check" in text and "--log-opts" in text and "--all" in text
    assert "gitleaks-action" not in (WORKFLOWS / "secret-scan.yml").read_text(encoding="utf-8")  # no licence key
    assert "pull_request" in triggers(workflow)


def test_every_action_is_pinned_to_a_version() -> None:
    for path in WORKFLOWS.glob("*.yml"):
        for ref in re.findall(r"uses:\s*([\w./-]+@[\w.-]+)", path.read_text(encoding="utf-8")):
            assert re.search(r"@v\d+", ref), f"{path.name}: {ref} is not pinned to a version"


# ============================================================================
# The image
# ============================================================================


def test_dockerfile_runs_the_app_as_a_non_root_user_with_a_health_check() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert dockerfile.splitlines()[next(i for i, line in enumerate(dockerfile.splitlines()) if line.startswith("FROM"))] == "FROM python:3.13-slim"
    assert re.search(r"^USER vigie$", dockerfile, re.M)
    assert dockerfile.index("USER vigie") < dockerfile.index("CMD")
    assert "HEALTHCHECK" in dockerfile and "/v1/health" in dockerfile
    assert '"uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"' in dockerfile
    assert "EXPOSE 8080" in dockerfile
    assert "VIGIE_DB_PATH=/data/vigie.db" in dockerfile and "VOLUME /data" in dockerfile


def test_the_image_does_not_ask_rainviewer_by_default() -> None:
    assert "VIGIE_ENABLE_RAINVIEWER=0" in (ROOT / "Dockerfile").read_text(encoding="utf-8")


def test_dockerignore_keeps_secrets_databases_and_the_history_out_of_the_image() -> None:
    lines = set((ROOT / ".dockerignore").read_text(encoding="utf-8").split())
    assert {".git", ".venv", ".env", "*.db", "*.log", "logs"} <= lines
