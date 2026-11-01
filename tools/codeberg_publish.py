"""Publish a release to the Codeberg mirror (fallback source while GitHub hides the repo).

    set CODEBERG_TOKEN=<token with write:repository>
    python tools/codeberg_publish.py v2.0.1            # push main + tag, create the release, upload dist assets

Needs a built dist/ (build.ps1) and the git remote "codeberg" (added on first run).
"""
import base64
import os
import re
import subprocess
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.updater import MIRROR, MIRROR_REPO  # noqa: E402

ASSETS = ("ZapretAuto-Setup.exe", "ZapretAuto-Setup.exe.sha256",
          "ZapretAuto-portable.zip", "ZapretAuto-portable.zip.sha256")


def notes(tag: str) -> str:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    m = re.search(rf"^## {re.escape(tag.lstrip('v'))}\b.*?$\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    return m.group(1).strip() if m else ""


def main() -> None:
    tag = sys.argv[1]
    token = os.environ["CODEBERG_TOKEN"]
    missing = [a for a in ASSETS if not (ROOT / "dist" / a).exists()]
    if missing:
        sys.exit(f"нет в dist: {', '.join(missing)} — сначала build.ps1")
    remote = f"{MIRROR}/{MIRROR_REPO}.git"
    if subprocess.run(["git", "remote", "get-url", "codeberg"], cwd=ROOT, capture_output=True).returncode:
        subprocess.run(["git", "remote", "add", "codeberg", remote], cwd=ROOT, check=True)
    auth = base64.b64encode(f"{MIRROR_REPO.split('/')[0]}:{token}".encode()).decode()
    subprocess.run(["git", "-c", f"http.{MIRROR}/.extraheader=Authorization: Basic {auth}",
                    "push", "codeberg", "main", tag], cwd=ROOT, check=True)
    api = f"{MIRROR}/api/v1/repos/{MIRROR_REPO}/releases"
    h = {"Authorization": f"token {token}"}
    r = requests.get(f"{api}/tags/{tag}", headers=h, timeout=30)
    if r.status_code == 404:
        r = requests.post(api, headers=h, timeout=30, json={
            "tag_name": tag, "name": f"ZapretAuto {tag.lstrip('v')}", "body": notes(tag)})
    r.raise_for_status()
    rel = r.json()
    have = {a["name"]: a["id"] for a in rel.get("assets", [])}
    for name in ASSETS:
        if name in have:  # re-run: replace
            requests.delete(f"{api}/{rel['id']}/assets/{have[name]}", headers=h, timeout=30).raise_for_status()
        with open(ROOT / "dist" / name, "rb") as f:
            requests.post(f"{api}/{rel['id']}/assets", headers=h, params={"name": name},
                          files={"attachment": (name, f)}, timeout=600).raise_for_status()
        print("загружен", name)
    print(f"готово: {MIRROR}/{MIRROR_REPO}/releases/tag/{tag}")


if __name__ == "__main__":
    main()
