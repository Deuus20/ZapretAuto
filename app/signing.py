"""Подписанный манифест релиза: чем именно проверяется обновление ZapretAuto.

Файл `.sha256` лежит в том же релизе, что и установщик, поэтому подмена релиза закрывает и его: кто
получил доступ к аккаунту или CI, тот заменит оба файла согласованно. Манифест подписывается ключом,
которого на GitHub нет, приватный хранится только у сопровождающего и в секрете GitHub Actions, а в exe
зашит открытый. Без приватного ключа манифест под чужим установщиком не сделать.

Формат manifest.json (подпись, отдельный файл manifest.json.sig, base64 поверх ровно этих байт):
    {"schema": 1, "app": "ZapretAuto", "repo": "Deuus20/ZapretAuto", "tag": "v2.2.0",
     "created": "2026-11-17T12:00:00Z", "assets": [{"name": ..., "size": ..., "sha256": ...}]}
"""
import base64
import hashlib
import json
import re
from pathlib import Path

from .ed25519 import verify

SCHEMA = 1
APP = "ZapretAuto"
MANIFEST = "manifest.json"
SIGNATURE = "manifest.json.sig"
SHA_RE = re.compile(r"^[0-9a-f]{64}$")

# id ключа → открытый ключ (base64, 32 байта). Старые ключи не удаляются: ими подписаны прошлые релизы,
# и обновление с них на новую версию должно проверяться тем ключом, что был в их exe.
KEYS: dict[str, str] = {"k1-b7f4db33": "16a6qNBHHO8qXf2pKG9WePV6Mrumq3HiEwEuUd+U318="}


class ManifestError(RuntimeError):
    """Манифест не подписан нашим ключом или не сходится с файлом релиза."""


def key_id(public: bytes) -> str:
    """Короткое имя ключа: им же подписан манифест, по нему видно, каким ключом проверять."""
    return "k1-" + hashlib.sha256(public).hexdigest()[:8]


def _public(text: str) -> bytes:
    try:
        return base64.b64decode(text.strip(), validate=True)
    except ValueError:
        raise ManifestError("открытый ключ в программе испорчен") from None


def read_manifest(data: bytes, signature: str, keys: dict | None = None) -> dict:
    """Проверить подпись и вернуть разобранный манифест; подпись проверяется до разбора."""
    keys = KEYS if keys is None else keys
    try:
        sig = base64.b64decode(signature.strip(), validate=True)
    except ValueError:
        raise ManifestError("файл подписи манифеста повреждён") from None
    if len(sig) != 64:
        raise ManifestError("подпись манифеста имеет неверную длину")
    for text in keys.values():
        if verify(_public(text), data, sig):
            break
    else:
        raise ManifestError("подпись манифеста не совпала ни с одним из ключей программы")
    try:
        m = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ManifestError("манифест не читается как JSON") from None
    if not isinstance(m, dict) or m.get("schema") != SCHEMA or m.get("app") != APP:
        raise ManifestError("неизвестный формат манифеста")
    if not isinstance(m.get("assets"), list):
        raise ManifestError("в манифесте нет списка файлов")
    return m


def asset(manifest: dict, name: str) -> tuple[str, int]:
    """(sha256, размер) файла name из манифеста; имя сравнивается без учёта регистра, как в релизе."""
    for a in manifest["assets"]:
        if isinstance(a, dict) and str(a.get("name", "")).lower() == name.lower():
            sha, size = str(a.get("sha256", "")).lower(), a.get("size")
            if not SHA_RE.match(sha) or not isinstance(size, int) or isinstance(size, bool) or size < 0:
                raise ManifestError(f"в манифесте испорчены данные файла {name}")
            return sha, size
    raise ManifestError(f"файла {name} нет в манифесте релиза")


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_release(manifest: dict, tag: str, repo: str, path: Path) -> None:
    """Манифест должен быть про этот релиз, этот репозиторий и ровно про скачанный файл."""
    if manifest.get("tag") != tag:
        raise ManifestError("манифест подписан для другой версии")
    if manifest.get("repo") != repo:
        raise ManifestError("манифест выпущен другим репозиторием")
    sha, size = asset(manifest, path.name)
    real = path.stat().st_size
    if real != size:
        raise ManifestError(f"размер файла не совпал с манифестом ({real} вместо {size})")
    if file_sha256(path) != sha:
        raise ManifestError("контрольная сумма не совпала с подписанным манифестом")
