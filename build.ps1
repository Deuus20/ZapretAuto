$ver = (Select-String -Path app\__init__.py -Pattern '"(.+)"').Matches[0].Groups[1].Value
# Build dist\ZapretAuto\ZapretAuto.exe (onedir, admin manifest), prune Qt, bundle vendor/, make installer
$ErrorActionPreference = 'Continue'
& .venv\Scripts\pyinstaller --noconfirm --clean --windowed --uac-admin --name ZapretAuto `
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
"built: dist\ZapretAuto\ZapretAuto.exe"
$iscc = @("$env:ProgramFiles\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($iscc) { & $iscc /Qp "/DAppVer=$ver" installer.iss; "installer: dist\ZapretAuto-Setup.exe" } else { "Inno Setup not found, skipping installer" }
