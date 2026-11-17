"""Workflow'ы должны быть валидным YAML: сломанный файл GitHub молча не загружает, и обязательный чек
branch protection перестаёт выставляться (так уже случилось с tests.yml — `name:` с «: » внутри)."""
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
PIN = re.compile(r"^[0-9a-f]{40}$")


def _load(p):
    return yaml.safe_load(p.read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_workflow_is_valid_yaml(path):
    d = _load(path)
    assert isinstance(d, dict) and d.get("jobs"), f"{path.name}: нет jobs"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_actions_are_pinned_to_commit_sha(path):
    text = path.read_text(encoding="utf-8")
    for use in re.findall(r"uses:\s*(\S+)", text):
        if use.startswith("./"):
            continue
        name, _, ref = use.partition("@")
        assert PIN.match(ref), f"{path.name}: {name} не прибит к SHA ({ref!r})"


def test_tests_workflow_has_blocking_pytest_job():
    d = _load(ROOT / ".github" / "workflows" / "tests.yml")
    assert "pytest" in d["jobs"]
    steps = d["jobs"]["pytest"]["steps"]
    names = " ".join(s.get("name", "") for s in steps)
    assert "--require-hashes" in (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    assert "pyright" in names and "coverage" in names.lower()
    assert not any(s.get("continue-on-error") for s in steps if "pyright" in s.get("name", "")
                   and "полный" not in s.get("name", ""))


def test_release_is_split_into_build_and_publish():
    d = _load(ROOT / ".github" / "workflows" / "release.yml")
    assert {"build", "publish"} <= set(d["jobs"])
    assert d["jobs"]["publish"].get("environment") == "release"
    assert d["permissions"] == {"contents": "read"}  # запись — только у publish
