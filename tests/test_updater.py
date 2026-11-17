"""Обновление программы и zapret, подпись манифеста."""
import base64
import importlib.util
import json
from pathlib import Path

import pytest
from helpers import _patch_keys, _release_net

from app import paths, sharedb, signing, updater

ROOT = Path(__file__).resolve().parent.parent

@pytest.mark.parametrize("a,b,newer", [("1.10.0", "1.9.9d", True), ("1.9.9d", "1.9.9", True),
                                       ("1.10.3", "1.10.3", False), ("v1.0.1", "1.0.0", True)])
def test_is_newer(a, b, newer):
    assert updater.is_newer(a, b) is newer

def test_tag_and_host_validation():
    assert updater.TAG_RE.match("1.10.3") and not updater.TAG_RE.match("../../x")
    updater._check_url("https://github.com/x/y/releases/download/v1/a.zip")
    with pytest.raises(RuntimeError):
        updater._check_url("https://evil.example.com/a.zip")
    with pytest.raises(RuntimeError):
        updater._check_url("http://github.com/a.zip")

def test_app_check_explains_hidden_project(monkeypatch):
    import threading

    import requests
    resp = requests.Response()
    resp.status_code = 404
    def gone():
        raise requests.HTTPError(response=resp)
    monkeypatch.setattr(updater, "app_release", gone)
    ev, got = threading.Event(), []
    updater.app_check_async(lambda t, n: (got.append((t, n)), ev.set()))
    assert ev.wait(5) and got == [(None, False)] and "недоступна" in updater.APP_CHECK_ERROR

def test_portable_update_uses_zip_and_keeps_data(monkeypatch):
    class R:
        status_code = 200
        def raise_for_status(self):
            pass
        def json(self):
            return {"tag_name": "v9.9.9", "assets": [
                {"name": n, "browser_download_url": f"https://github.com/x/{n}"} for n in
                ("ZapretAuto-Setup.exe", "ZapretAuto-Setup.exe.sha256", "ZapretAuto-portable.zip",
                 "ZapretAuto-portable.zip.sha256", "manifest.json", "manifest.json.sig")]}
    monkeypatch.setattr(updater.requests, "get", lambda *a, **k: R())
    monkeypatch.setattr(paths, "PORTABLE", False)
    rel = updater.app_release()
    assert (rel.tag, rel.name) == ("v9.9.9", "ZapretAuto-Setup.exe")
    assert (rel.url, rel.manifest, rel.signature) == ("https://github.com/x/ZapretAuto-Setup.exe",
                                                     "https://github.com/x/manifest.json",
                                                     "https://github.com/x/manifest.json.sig")
    monkeypatch.setattr(paths, "PORTABLE", True)
    assert updater.app_release().url.endswith("ZapretAuto-portable.zip")
    cmd = updater.portable_swap_command(Path("C:/t/new/ZapretAuto"), Path("D:/Apps/O'Neil/ZapretAuto"), Path("C:/t"))
    ps = cmd[-1]
    assert "/XD data vendor" in ps and r"'D:\Apps\O''Neil\ZapretAuto'" in ps and "--tray" in ps


def test_portable_swap_keeps_a_copy_and_marks_completion(tmp_path):
    """Обрыв посреди копирования не должен оставить смесь версий: рядом лежит копия, в конце, отметка."""
    ps = updater.portable_swap_command(tmp_path / "new", tmp_path / "root", tmp_path / "tmp")[-1]
    assert "update.done" in ps and "root.update-old" in ps and "zapretauto-backup" in ps
    assert "/XD data vendor" in ps and "$LASTEXITCODE -ge 8" in ps  # ошибка robocopy: старые файлы возвращаются
    assert "update.error.txt" in ps and "exit 1" in ps  # чужая папка/сбой копии: обновление отменяется


def test_interrupted_portable_update_is_rolled_back(tmp_path):
    root = tmp_path / "ZapretAuto"
    (root / "data").mkdir(parents=True)
    (root / "data" / "settings.json").write_text("{}", encoding="utf-8")
    (root / "ZapretAuto.exe").write_bytes(b"half new")  # копирование оборвалось на середине
    old = root.with_name(root.name + ".update-old")
    old.mkdir()
    (old / ".zapretauto-backup").write_text("", encoding="utf-8")
    (old / "ZapretAuto.exe").write_bytes(b"old exe")
    (old / "data").mkdir()
    (old / "data" / "settings.json").write_text('{"old": 1}', encoding="utf-8")
    assert updater.recover_portable(root) is True
    assert (root / "ZapretAuto.exe").read_bytes() == b"old exe"
    assert json.loads((root / "data" / "settings.json").read_text(encoding="utf-8")) == {}  # данные не откатываем
    assert not old.exists()


def test_foreign_backup_folder_is_not_touched(tmp_path):
    root = tmp_path / "ZapretAuto"
    root.mkdir()
    (root / "ZapretAuto.exe").write_bytes(b"new")
    foreign = root.with_name(root.name + ".update-old")
    foreign.mkdir()
    (foreign / "ZapretAuto.exe").write_bytes(b"someone else's folder")
    assert updater.recover_portable(root) is False
    assert (foreign / "ZapretAuto.exe").read_bytes() == b"someone else's folder"


def test_completed_portable_update_only_cleans_up(tmp_path):
    root = tmp_path / "ZapretAuto"
    root.mkdir()
    (root / "ZapretAuto.exe").write_bytes(b"new")
    (root / "update.done").write_text("", encoding="utf-8")
    old = root.with_name(root.name + ".update-old")
    old.mkdir()
    (old / ".zapretauto-backup").write_text("", encoding="utf-8")
    (old / "ZapretAuto.exe").write_bytes(b"old")
    assert updater.recover_portable(root) is False
    assert (root / "ZapretAuto.exe").read_bytes() == b"new" and not old.exists()

def test_ed25519_matches_rfc8032_and_rejects_malleable_s():
    from app import ed25519
    for sk, pk, msg, sig in (
            ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
             "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
             "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
             "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
            ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
             "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
             "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da"
             "085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00")):
        sk, pk, msg, sig = bytes.fromhex(sk), bytes.fromhex(pk), bytes.fromhex(msg), bytes.fromhex(sig)
        assert ed25519.public_key(sk) == pk and ed25519.sign(sk, msg) == sig
        assert ed25519.verify(pk, msg, sig)
        assert not ed25519.verify(pk, msg + b"x", sig)          # подменённое сообщение
        assert not ed25519.verify(ed25519.public_key(b"o" * 32), msg, sig)  # чужой ключ
    sk = bytes(range(32))
    pk, sig = ed25519.public_key(sk), ed25519.sign(sk, b"msg")
    loose = sig[:32] + (int.from_bytes(sig[32:], "little") + ed25519.L).to_bytes(32, "little")
    assert not ed25519.verify(pk, b"msg", loose)  # S + L: без проверки S < L подпись «сходится»
    assert not ed25519.verify(pk, b"msg", sig[:63]) and not ed25519.verify(pk[:31], b"msg", sig)


def test_ed25519_rejects_small_order_points():
    """identity-ключ и identity-R с S=0 «сходятся» при любом сообщении, если не отсеять малый порядок."""
    from app import ed25519
    ident = b"\x01" + b"\x00" * 31
    assert not ed25519.verify(ident, b"any message", ident + b"\x00" * 32)
    pk, sig = ed25519.public_key(b"k" * 32), ed25519.sign(b"k" * 32, b"m")
    assert ed25519.verify(pk, b"m", sig)  # обычная подпись проходит
    assert not ed25519.verify(pk, b"m", ident + sig[32:])  # подменённый R малого порядка

def test_release_manifest_needs_our_signature(monkeypatch):
    from app import ed25519
    seed = b"m" * 32
    _patch_keys(monkeypatch, seed)
    body = json.dumps({"schema": 1, "app": "ZapretAuto", "repo": updater.APP_REPO, "tag": "v9.0.0",
                       "assets": [{"name": "ZapretAuto-Setup.exe", "size": 3, "sha256": "a" * 64}]}).encode()
    sig = base64.b64encode(ed25519.sign(seed, body)).decode()
    assert signing.read_manifest(body, sig)["tag"] == "v9.0.0"
    for data, text in ((body[:-2] + b"x}", sig),                                   # подменённый байт
                       (body, base64.b64encode(ed25519.sign(b"z" * 32, body)).decode()),  # чужой ключ
                       (body, "не base64"),                                        # мусор в подписи
                       (body, base64.b64encode(b"short").decode()),                 # подпись не 64 байта
                       (json.dumps({"assets": []}).encode(), sig)):                 # нет полей формата
        with pytest.raises(signing.ManifestError):
            signing.read_manifest(data, text)

def test_release_manifest_checks_file_bytes(tmp_path):
    f = tmp_path / "ZapretAuto-Setup.exe"
    f.write_bytes(b"MZ")
    good = {"name": "zapretauto-setup.exe", "size": 2, "sha256": signing.file_sha256(f)}
    man = {"schema": 1, "app": "ZapretAuto", "repo": updater.APP_REPO, "tag": "v9.0.0", "assets": [good]}
    signing.check_release(man, "v9.0.0", updater.APP_REPO, f)  # регистр имени, как в релизе, не важен
    for man2, tag, repo in (({**man, "tag": "v9.0.1"}, "v9.0.0", updater.APP_REPO),
                            (man, "v9.0.0", "evil/zapretauto"),
                            ({**man, "assets": [{**good, "size": 3}]}, "v9.0.0", updater.APP_REPO),
                            ({**man, "assets": [{**good, "sha256": "b" * 64}]}, "v9.0.0", updater.APP_REPO),
                            ({**man, "assets": [{**good, "name": "other.exe"}]}, "v9.0.0", updater.APP_REPO),
                            ({**man, "assets": [{**good, "sha256": "nope"}]}, "v9.0.0", updater.APP_REPO)):
        with pytest.raises(signing.ManifestError):
            signing.check_release(man2, tag, repo, f)

def test_app_update_installs_only_signed_release(tmp_path, monkeypatch):
    import hashlib
    import subprocess

    from app import ed25519
    seed = b"u" * 32
    _patch_keys(monkeypatch, seed)
    monkeypatch.setattr(paths, "DATA", tmp_path / "data")
    monkeypatch.setattr(paths, "PORTABLE", False)
    exe = b"MZ signed installer"
    def release(body=exe, sha=None, key=seed):
        m = {"schema": 1, "app": "ZapretAuto", "repo": updater.APP_REPO, "tag": "v99.0.0", "assets": [
            {"name": "ZapretAuto-Setup.exe", "size": len(body),
             "sha256": sha or hashlib.sha256(body).hexdigest()}]}
        data = (json.dumps(m, indent=2, sort_keys=True) + "\n").encode()
        return {"ZapretAuto-Setup.exe": body, "manifest.json": data,
                "manifest.json.sig": base64.b64encode(ed25519.sign(key, data))}
    started = []
    monkeypatch.setattr(subprocess, "Popen", lambda args, **kw: started.append(args))
    monkeypatch.setattr(updater.requests, "get", _release_net("v99.0.0", release()))
    assert updater.app_update(log=lambda m: None) is True
    assert Path(started[0][0]).read_bytes() == exe and started[0][1:] == [
        "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/UPDATE=1"]
    tampered = release()
    tampered["ZapretAuto-Setup.exe"] = b"MZ evil installer"  # подменили файл, манифест остался прежний
    for files in (tampered,
                  release(sha="c" * 64),                        # манифест про другой файл
                  release(key=b"q" * 32)):                      # подписано чужим ключом
        started.clear()
        monkeypatch.setattr(updater.requests, "get", _release_net("v99.0.0", files))
        with pytest.raises(RuntimeError, match="не принят"):
            updater.app_update(log=lambda m: None)
        assert not started, "неподписанный файл не должен запускаться"

def test_app_update_refuses_release_without_manifest(monkeypatch):
    class R:
        status_code = 200
        def raise_for_status(self):
            pass
        def json(self):
            return {"tag_name": "v99.0.0", "assets": [
                {"name": "ZapretAuto-Setup.exe", "browser_download_url": "https://github.com/x/a"},
                {"name": "ZapretAuto-Setup.exe.sha256", "browser_download_url": "https://github.com/x/b"}]}
    monkeypatch.setattr(updater.requests, "get", lambda *a, **k: R())
    monkeypatch.setattr(paths, "PORTABLE", False)
    rel = updater.app_release()  # старый релиз без манифеста виден проверке обновлений
    assert (rel.tag, rel.manifest, rel.signature) == ("v99.0.0", None, None)
    with pytest.raises(RuntimeError, match="подписанного манифеста"):
        updater.app_update(log=lambda m: None)

def test_sign_release_tool_roundtrip(tmp_path, monkeypatch, capsys):
    from app import ed25519
    spec = importlib.util.spec_from_file_location("sign_release", ROOT / "tools" / "sign_release.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    key = tmp_path / "release.ed25519"
    tool.new_key(key)
    kid = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("id:")][0].split()[-1]
    seed = base64.b64decode(key.read_text(encoding="ascii").strip())
    assert signing.key_id(ed25519.public_key(seed)) == kid
    with pytest.raises(SystemExit):  # существующий ключ не перезаписывается
        tool.new_key(key)
    monkeypatch.setattr(signing, "KEYS", {kid: base64.b64encode(ed25519.public_key(seed)).decode()})
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "ZapretAuto-Setup.exe").write_bytes(b"MZ")
    (dist / "ZapretAuto-Setup.exe.sha256").write_text("x" * 64, encoding="ascii")
    (dist / "ZapretAuto-portable.zip").write_bytes(b"PK")
    (dist / "ZapretAuto-portable.zip.sha256").write_text("y" * 64, encoding="ascii")
    tool.sign(key, "v2.2.0", dist)
    tool.verify("v2.2.0", dist)
    (dist / "ZapretAuto-Setup.exe").write_bytes(b"MZ!")  # файл изменился после подписи
    with pytest.raises(signing.ManifestError):
        tool.verify("v2.2.0", dist)


def test_sign_release_requires_all_four_files(tmp_path, monkeypatch, capsys):
    """Манифест без portable.zip проходил verify, выпуск мог остаться неполным."""
    import json as _json

    from app import ed25519
    spec = importlib.util.spec_from_file_location("sign_release2", ROOT / "tools" / "sign_release.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    key = tmp_path / "k.ed25519"
    tool.new_key(key)
    capsys.readouterr()
    seed = base64.b64decode(key.read_text(encoding="ascii").strip())
    monkeypatch.setattr(signing, "KEYS", {signing.key_id(ed25519.public_key(seed)):
                                          base64.b64encode(ed25519.public_key(seed)).decode()})
    dist = tmp_path / "dist"
    dist.mkdir()
    for n in ("ZapretAuto-Setup.exe", "ZapretAuto-Setup.exe.sha256"):
        (dist / n).write_bytes(b"MZ" if n.endswith(".exe") else b"x" * 64)
    assert tool.FILES == ("ZapretAuto-Setup.exe", "ZapretAuto-Setup.exe.sha256",
                          "ZapretAuto-portable.zip", "ZapretAuto-portable.zip.sha256")
    tool.sign(key, "v2.2.0", dist)  # sign собирает манифест из того, что есть
    data = (dist / signing.MANIFEST).read_bytes()
    m = _json.loads(data)
    m["assets"] = [a for a in m["assets"] if not a["name"].endswith(".zip")]
    body = (_json.dumps(m, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()
    (dist / signing.MANIFEST).write_bytes(body)
    (dist / signing.SIGNATURE).write_text(base64.b64encode(ed25519.sign(seed, body)).decode() + "\n",
                                          encoding="ascii")
    with pytest.raises(SystemExit, match="portable"):
        tool.verify("v2.2.0", dist)
    monkeypatch.setattr(signing, "KEYS", {})  # ключ не вписан в signing.py: релиз был бы необновляемым
    with pytest.raises(SystemExit):
        tool.sign(key, "v2.2.0", dist)

def test_github_requests_retried_before_giving_up(monkeypatch):
    import requests

    from app import netcache
    monkeypatch.setattr(netcache.time, "sleep", lambda s: None)  # паузы между попытками в тестах не нужны
    class R:
        def __init__(self, code):
            self.status_code = code
    calls = []
    answers = [503, 200]
    monkeypatch.setattr(requests, "get", lambda url, **kw: calls.append(url) or R(answers.pop(0)))
    assert netcache.get_retry("https://api.github.com/x").status_code == 200 and len(calls) == 2
    calls.clear()  # 404, окончательный ответ: повторять его нет смысла
    monkeypatch.setattr(requests, "get", lambda url, **kw: calls.append(url) or R(404))
    assert netcache.get_retry("https://api.github.com/x").status_code == 404 and len(calls) == 1
    calls.clear()
    def boom(url, **kw):
        calls.append(url)
        raise requests.Timeout("slow")
    monkeypatch.setattr(requests, "get", boom)
    with pytest.raises(requests.Timeout):
        netcache.get_retry("https://api.github.com/x")
    assert len(calls) == 3, "три попытки, и честная ошибка, а не бесконечные повторы"

def test_github_answers_cached_and_revalidated_by_etag(monkeypatch):
    """Повторная проверка не тратит лимит: свежий ответ из кэша, устаревший, условным запросом (304)."""
    import requests

    from app import netcache
    hit = {"n": 0, "headers": []}

    class R:
        def __init__(self, code, body=None):
            self.status_code = code
            self._body = body
            self.headers = {"ETag": 'W/"abc"'}
        def json(self):
            return self._body
        def raise_for_status(self):
            pass

    def get(url, **kw):
        hit["n"] += 1
        hit["headers"].append(kw.get("headers") or {})
        return R(200, {"tag_name": "v1"}) if hit["n"] == 1 else R(304)

    monkeypatch.setattr(requests, "get", get)
    netcache.reset()
    assert netcache.get_json("https://api.github.com/x")["tag_name"] == "v1"
    assert netcache.get_json("https://api.github.com/x")["tag_name"] == "v1" and hit["n"] == 1  # кэш
    assert netcache.get_json("https://api.github.com/x", ttl=0)["tag_name"] == "v1"              # 304
    assert hit["n"] == 2 and hit["headers"][-1].get("If-None-Match") == 'W/"abc"'


def test_repo_files_and_releases_come_from_github_only(monkeypatch):
    import requests

    class R:
        def __init__(self, url):
            self.url, self.status_code = url, 200
        def raise_for_status(self):
            pass
        def json(self):
            if "releases" in self.url:
                return {"tag_name": "v9.0.0", "assets": [
                    {"name": "ZapretAuto-Setup.exe", "browser_download_url": "https://github.com/x/a"},
                    {"name": "manifest.json", "browser_download_url": "https://github.com/x/m"},
                    {"name": "manifest.json.sig", "browser_download_url": "https://github.com/x/s"}]}
            return {"games": {"g.exe": ["1.2.3.4"]}, "web": []}
    seen = []
    monkeypatch.setattr(requests, "get", lambda url, **kw: seen.append(url) or R(url))
    monkeypatch.setattr(updater.paths, "PORTABLE", False)
    assert updater.app_release() == ("v9.0.0", "ZapretAuto-Setup.exe", "https://github.com/x/a",
                                     "https://github.com/x/m", "https://github.com/x/s")
    assert sharedb.games_db()["games"] == {"g.exe": ["1.2.3.4"]}
    assert seen == ["https://api.github.com/repos/Deuus20/ZapretAuto/releases/latest",
                    "https://raw.githubusercontent.com/Deuus20/ZapretAuto/main/games-db.json"]
    with pytest.raises(RuntimeError):  # any other download host is rejected
        updater._check_url("https://evil.example.com/a.zip")

def test_zapret_update_rejects_wrong_digest(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA", tmp_path / "data")
    monkeypatch.setattr(updater, "latest_release", lambda: ("1.99.0", "https://github.com/x.zip", "0" * 64))
    monkeypatch.setattr(updater, "_download", lambda url, dest, log=print: dest.write_bytes(b"zip"))
    v = tmp_path / "vendor"
    with pytest.raises(RuntimeError, match="контрольная сумма"):
        updater.update(log=lambda m: None, vendor=v)
    assert not v.exists() and list((tmp_path / "data" / "tmp").iterdir()) == []   # nothing installed, no leftovers


def test_zapret_update_needs_digest_even_when_forced(tmp_path, monkeypatch):
    """force, это «переустановить тот же выпуск», а не разрешение ставить непроверяемый архив."""
    monkeypatch.setattr(paths, "DATA", tmp_path / "data")
    monkeypatch.setattr(updater, "latest_release", lambda: ("1.99.0", "https://github.com/x.zip", None))
    monkeypatch.setattr(updater, "_download", lambda url, dest, log=print: dest.write_bytes(b"zip"))
    with pytest.raises(RuntimeError, match="контрольной суммы"):
        updater.update(log=lambda m: None, vendor=tmp_path / "vendor", force=True)
    assert not (tmp_path / "vendor").exists()
