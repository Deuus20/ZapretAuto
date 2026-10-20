$ver = (Select-String -Path app\__init__.py -Pattern '"(.+)"').Matches[0].Groups[1].Value
# Build dist\ZapretAuto\ZapretAuto.exe (onedir, admin manifest), prune Qt, bundle vendor/, make installer
$ErrorActionPreference = 'Continue'
# own basetemp: pytest's numbered dirs + "pytest-current" symlink broke the run with access denied at cleanup
& .venv\Scripts\python -m pytest -q -p no:cacheprovider --basetemp "$env:TEMP\zapretauto-pytest" tests
if ($LASTEXITCODE -ne 0) { throw 'tests failed' }
& .venv\Scripts\pyinstaller --noconfirm --clean --windowed --uac-admin --icon assets\icon.ico --collect-all curl_cffi --name ZapretAuto `
  --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.Qt3DCore --exclude-module PySide6.QtQuick `
  --exclude-module PySide6.QtQml --exclude-module PySide6.QtPdf --exclude-module PySide6.QtSvg `
  --exclude-module PySide6.QtOpenGL --exclude-module PySide6.QtVirtualKeyboard `
  main.py
if ($LASTEXITCODE -ne 0) { throw "pyinstaller failed" }
$q = 'dist\ZapretAuto\_internal\PySide6'
foreach ($p in 'translations', 'plugins\platforminputcontexts', 'plugins\platforms\qdirect2d.dll', 'plugins\platforms\qminimal.dll',
  'plugins\platforms\qoffscreen.dll', 'opengl32sw.dll', 'Qt6Quick.dll', 'Qt6Qml.dll', 'Qt6QmlModels.dll', 'Qt6Pdf.dll',
  'Qt6VirtualKeyboard.dll', 'plugins\imageformats\qpdf.dll', 'plugins\imageformats\qtiff.dll', 'plugins\imageformats\qwebp.dll',
  'plugins\imageformats\qicns.dll', 'plugins\imageformats\qtga.dll', 'plugins\imageformats\qwbmp.dll') {
  Remove-Item "$q\$p" -Recurse -Force -ErrorAction SilentlyContinue
}
Copy-Item vendor dist\ZapretAuto\vendor -Recurse -Force
# Russian Yes/No/Cancel in dialogs (Qt's translations folder is pruned above)
New-Item -ItemType Directory -Force dist\ZapretAuto\assets | Out-Null
Copy-Item assets\qtbase_ru.qm dist\ZapretAuto\assets\ -Force
"built: dist\ZapretAuto\ZapretAuto.exe"
# portable zip: the same folder plus portable.txt (the app then updates itself from the zip, not the installer)
$pt = 'dist\portable\ZapretAuto'
Remove-Item dist\portable, dist\ZapretAuto-portable.zip* -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $pt | Out-Null
Copy-Item dist\ZapretAuto\* $pt -Recurse -Force
Remove-Item "$pt\data", "$pt\selftest.txt" -Recurse -Force -ErrorAction SilentlyContinue
"Portable copy: settings are kept in the data folder next to ZapretAuto.exe. Delete this file to stop self-updates from the zip." | Out-File -Encoding utf8 "$pt\portable.txt"
Compress-Archive -Path $pt -DestinationPath dist\ZapretAuto-portable.zip -CompressionLevel Optimal
$h = (Get-FileHash dist\ZapretAuto-portable.zip -Algorithm SHA256).Hash.ToLower(); "$h  ZapretAuto-portable.zip" | Out-File -Encoding ascii dist\ZapretAuto-portable.zip.sha256
Remove-Item dist\portable -Recurse -Force
"portable: dist\ZapretAuto-portable.zip (+ .sha256)"
$iscc = @("$env:ProgramFiles\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($iscc) { Remove-Item dist\ZapretAuto-Setup.exe* -ErrorAction SilentlyContinue; & $iscc /Qp "/DAppVer=$ver" installer.iss; if ($LASTEXITCODE -ne 0) { throw "ISCC failed" }; $h = (Get-FileHash dist\ZapretAuto-Setup.exe -Algorithm SHA256).Hash.ToLower(); "$h  ZapretAuto-Setup.exe" | Out-File -Encoding ascii dist\ZapretAuto-Setup.exe.sha256; "installer: dist\ZapretAuto-Setup.exe (+ .sha256)" } else { "Inno Setup not found, skipping installer" }
