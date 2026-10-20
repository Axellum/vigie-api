"""Guards on the CI workflows and the Docker files: what runs when, and what the image is.

The PC has no Docker: the proof that the image works is the ``docker`` job of ``ci.yml`` (build, run,
``/v1/health``, ``/v1/embedded/frame``). These tests only keep the files from drifting.
"""

from __future__ import annotations

import json
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


def test_every_action_is_pinned_to_a_version_or_a_commit() -> None:
    """A version tag (`@v4`), or the full commit of an action that has no tag that moves (hassfest, the HACS action)."""
    for path in WORKFLOWS.glob("*.yml"):
        for ref in re.findall(r"uses:\s*([\w./-]+@[\w.-]+)", path.read_text(encoding="utf-8")):
            assert re.search(r"@(?:v\d+|[0-9a-f]{40})$", ref), f"{path.name}: {ref} is not pinned to a version or a commit"


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


# ============================================================================
# The Home Assistant integration
# ============================================================================


def test_the_home_assistant_workflow_has_its_own_python_and_leaves_the_api_jobs_alone() -> None:
    workflow = load("home-assistant.yml")
    assert workflow["permissions"] == {"contents": "read"}
    assert set(workflow["jobs"]) == {"tests", "hassfest", "hacs"}
    tests = workflow["jobs"]["tests"]
    setup = next(step for step in tests["steps"] if "setup-python" in str(step.get("uses", "")))
    assert setup["with"]["python-version"] == "3.14"  # what the pinned Home Assistant needs, not the API's 3.12 and 3.13
    text = steps_text(workflow, "tests")
    assert "tests_ha/requirements.txt" in text and "pytest tests_ha" in text
    assert "continue-on-error" not in tests
    # The API's jobs run the API's tests only (pyproject.toml: testpaths = ["tests"]) and never install Home Assistant.
    ci = load("ci.yml")
    assert "tests_ha" not in steps_text(ci, "test") and "homeassistant" not in steps_text(ci, "test")
    assert 'testpaths = ["tests"]' in (ROOT / "pyproject.toml").read_text(encoding="utf-8")


def test_hassfest_and_hacs_are_run_with_the_category_and_ignores_stated() -> None:
    workflow = load("home-assistant.yml")
    assert any("home-assistant/actions/hassfest@" in str(step.get("uses", "")) for step in workflow["jobs"]["hassfest"]["steps"])
    hacs = next(step for step in workflow["jobs"]["hacs"]["steps"] if "hacs/action@" in str(step.get("uses", "")))
    assert hacs["with"]["category"] == "integration"
    assert set(hacs["with"]["ignore"].split()) == {"brands", "topics"}  # no brand image and no topics yet
    assert str(hacs["with"]["comment"]).lower() == "false"  # read-only job
    # The action refuses a private repository: the job runs only once the repository is public.
    assert "github.event.repository.private" in workflow["jobs"]["hacs"]["if"]


def test_the_pinned_home_assistant_test_plugin_and_the_workflow_python_agree() -> None:
    requirements = (ROOT / "tests_ha" / "requirements.txt").read_text(encoding="utf-8")
    assert re.search(r"^pytest-homeassistant-custom-component==\d+\.\d+\.\d+$", requirements, re.M)
    hacs = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))
    assert re.fullmatch(r"\d{4}\.\d+\.\d+", hacs["homeassistant"])


def test_the_image_leaves_the_integration_and_its_tests_out() -> None:
    lines = set((ROOT / ".dockerignore").read_text(encoding="utf-8").split())
    assert {"custom_components", "tests_ha", "hacs.json", ".venv-ha"} <= lines
