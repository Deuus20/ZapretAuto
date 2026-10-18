# Build dist\ZapretAuto\ZapretAuto.exe (onedir, admin manifest) and bundle vendor/
$ErrorActionPreference = 'Stop'
& .venv\Scripts\pyinstaller --noconfirm --clean --windowed --uac-admin --name ZapretAuto `
  --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.Qt3DCore --exclude-module PySide6.QtQuick `
  main.py
Copy-Item vendor dist\ZapretAuto\vendor -Recurse -Force
"built: dist\ZapretAuto\ZapretAuto.exe"
