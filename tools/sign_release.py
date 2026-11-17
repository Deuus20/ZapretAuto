"""Подпись манифеста релиза ZapretAuto (Ed25519, ключ хранится вне репозитория).

    python tools/sign_release.py new-key C:\\keys\\zapretauto.ed25519
    python tools/sign_release.py sign --key C:\\keys\\zapretauto.ed25519 --tag v2.2.0
    python tools/sign_release.py verify --tag v2.2.0

`sign` пишет dist/manifest.json и dist/manifest.json.sig — их публикует release.yml; `verify` проверяет
собранные файлы тем же кодом, которым их проверяет приложение, и годится как шаг перед публикацией.
Ключ, открытая часть которого не вписана в app/signing.py, для сборки не годится: приложение такой
манифест отвергнет, поэтому sign в этом случае падает.
"""
import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # pyright: ignore[reportAttributeAccessIssue]  # иначе в перенаправленном выводе кракозябры

from app import ed25519, signing  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
APP_REPO = "Deuus20/ZapretAuto"  # тот же репозиторий, что в app/updater.py: проверка не должна брать его из манифеста
FILES = ("ZapretAuto-Setup.exe", "ZapretAuto-Setup.exe.sha256", "ZapretAuto-portable.zip",
         "ZapretAuto-portable.zip.sha256")


def _read_key(path: Path) -> bytes:
    raw = base64.b64decode(path.read_text(encoding="ascii").strip(), validate=True)
    if len(raw) != 32:
        raise SystemExit(f"{path}: ключ должен быть 32 байтами в base64")
    return raw


def new_key(path: Path) -> None:
    if path.exists():
        raise SystemExit(f"{path} уже существует — старый ключ не перезаписываю")
    seed = os.urandom(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(base64.b64encode(seed).decode("ascii") + "\n", encoding="ascii")
    if os.name == "nt":  # ключ не должен наследоваться другими процессами
        os.system(f'icacls "{path}" /inheritance:r /grant:r "%USERNAME%:F" >nul')
    public = ed25519.public_key(seed)
    kid = signing.key_id(public)
    print(f"ключ: {path}\nid:   {kid}\nдобавьте в app/signing.py:\n    KEYS = {{{kid!r}: {base64.b64encode(public).decode()!r}}}")
    print("сохраните файл ключа в надёжном месте и в секрете GitHub Actions ZAPRETAUTO_SIGNING_KEY")


def _manifest(tag: str, dist: Path) -> tuple[dict, bytes]:
    assets = []
    for name in FILES:
        f = dist / name
        if f.is_file():
            assets.append({"name": name, "size": f.stat().st_size, "sha256": signing.file_sha256(f)})
    if not any(a["name"].endswith((".exe", ".zip")) for a in assets):
        raise SystemExit(f"в {dist} нет собранных файлов релиза")
    m = {"schema": signing.SCHEMA, "app": signing.APP, "repo": APP_REPO, "tag": tag,
         "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "assets": assets}
    return m, (json.dumps(m, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def sign(key: Path, tag: str, dist: Path, allow_unknown: bool = False) -> None:
    seed = _read_key(key)
    public = ed25519.public_key(seed)
    kid = signing.key_id(public)
    if kid not in signing.KEYS and not allow_unknown:
        raise SystemExit(f"открытый ключ {kid} не вписан в app/signing.py — приложение отвергнет такой манифест "
                         "(--allow-unknown-key, если это временный ключ)")
    m, data = _manifest(tag, dist)
    dist.mkdir(parents=True, exist_ok=True)
    (dist / signing.MANIFEST).write_bytes(data)
    (dist / signing.SIGNATURE).write_text(base64.b64encode(ed25519.sign(seed, data)).decode("ascii") + "\n",
                                          encoding="ascii", newline="\n")
    print(f"{kid}: {signing.MANIFEST} подписан, файлов {len(m['assets'])}")
    for a in m["assets"]:
        print(f"  {a['name']}  {a['size']}  {a['sha256'][:16]}…")


def verify(tag: str, dist: Path) -> None:
    data = (dist / signing.MANIFEST).read_bytes()
    sig = (dist / signing.SIGNATURE).read_text(encoding="ascii")
    m = signing.read_manifest(data, sig)
    if m.get("tag") != tag:
        raise SystemExit(f"манифест про {m.get('tag')}, а тег {tag}")
    names = {a["name"] for a in m["assets"]}
    missing = [n for n in FILES if n not in names]
    if missing:  # подписанный манифест без части файлов: выпуск будет неполным
        raise SystemExit(f"в манифесте нет файлов релиза: {', '.join(missing)}")
    for a in m["assets"]:
        signing.check_release(m, tag, APP_REPO, dist / a["name"])  # репозиторий — константа, не из манифеста
    print(f"манифест {tag} верен: подпись и {len(m['assets'])} файлов сходятся")


def main() -> None:
    p = argparse.ArgumentParser(description="Подпись манифеста релиза ZapretAuto")
    sub = p.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("new-key", help="создать ключ Ed25519 вне репозитория")
    n.add_argument("path", type=Path)
    s = sub.add_parser("sign", help="собрать и подписать manifest.json")
    s.add_argument("--key", type=Path, required=True)
    s.add_argument("--tag", required=True)
    s.add_argument("--dist", type=Path, default=ROOT / "dist")
    s.add_argument("--allow-unknown-key", action="store_true")
    v = sub.add_parser("verify", help="проверить подписанный манифест в dist")
    v.add_argument("--tag", required=True)
    v.add_argument("--dist", type=Path, default=ROOT / "dist")
    a = p.parse_args()
    if a.cmd == "new-key":
        new_key(a.path)
    elif a.cmd == "sign":
        sign(a.key, a.tag, a.dist, a.allow_unknown_key)
    else:
        verify(a.tag, a.dist)


if __name__ == "__main__":
    main()
