"""Лицензии и уведомления о сторонних компонентах должны ехать вместе с поставкой."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED = [
    "LICENSE",
    "THIRD-PARTY-NOTICES.md",
    "LICENSES/INSTALLER-NOTICE.txt",
    "LICENSES/LGPL-3.0.txt",
    "LICENSES/GPL-3.0.txt",
    "LICENSES/GPL-2.0.txt",
    "LICENSES/MIT-zapret.txt",
    "LICENSES/MIT-flowseal.txt",
    "LICENSES/WinDivert-LICENSE.txt",
    "LICENSES/PSF-2.0.txt",
]


def test_license_files_exist() -> None:
    missing = [p for p in REQUIRED if not (ROOT / p).is_file()]
    assert not missing, f"нет файлов лицензий: {missing}"


def test_mit_license_names_author() -> None:
    text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "MIT License" in text
    assert "Deusu20" in text


def test_third_party_notices_cover_bundled_components() -> None:
    text = (ROOT / "THIRD-PARTY-NOTICES.md").read_text(encoding="utf-8")
    names = (
        "zapret",
        "Flowseal",
        "WinDivert",
        "Qt",
        "PySide6",
        "Cygwin",
        "Python",
        "PyInstaller",
        "curl_cffi",
    )
    for name in names:
        assert name in text, name


def test_installer_shows_license() -> None:
    iss = (ROOT / "installer.iss").read_text(encoding="utf-8")
    assert "LicenseFile=LICENSES\\INSTALLER-NOTICE.txt" in iss


def test_build_copies_licenses_into_dist() -> None:
    build = (ROOT / "build.ps1").read_text(encoding="utf-8")
    assert "Copy-Item LICENSES\\* dist\\ZapretAuto\\LICENSES" in build
    assert "THIRD-PARTY-NOTICES.md" in build
