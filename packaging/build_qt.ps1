param([string]$Python = 'python')

$ErrorActionPreference = 'Stop'
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    $tls = & $Python packaging/runtime_tls_assets.py
    if ($LASTEXITCODE -ne 0) { throw 'Unable to collect TLS runtime resources.' }
    $resources = @()
    foreach ($item in ($tls | ConvertFrom-Json)) { $resources += @('--add-data', "$item;.") }
    $packager = @('--noconfirm', '--clean', '--windowed', '--onedir', '--name', 'AIFinanceHelperQt',
        '--icon', 'assets/app.ico', '--add-data', 'assets;assets', '--collect-all', 'openpyxl',
        '--exclude-module', 'tkinter', '--exclude-module', 'streamlit', '--exclude-module', 'pytest', '--exclude-module', 'pyarrow')
    & $Python -m PyInstaller @packager @resources qt_desktop.py
    if ($LASTEXITCODE -ne 0) { throw 'Qt build failed.' }
    $exe = 'dist/AIFinanceHelperQt/AIFinanceHelperQt.exe'
    & $Python scripts/verify_qt_native.py .test_qt/packaged150 --exe $exe --scale 1.5 --tk-migration
    if ($LASTEXITCODE -ne 0) { throw 'Packaged Qt smoke test failed.' }
    & $Python scripts/verify_qt_native.py .test_qt/packaged200 --exe $exe --scale 2 --tk-migration
    if ($LASTEXITCODE -ne 0) { throw 'Packaged Qt smoke test failed at 200% DPI.' }
    New-Item -ItemType Directory -Force -Path release | Out-Null
    Compress-Archive -Path dist/AIFinanceHelperQt -DestinationPath release/AIFinanceHelperQt-preview-Windows-x64.zip -Force
    Get-FileHash -Algorithm SHA256 -LiteralPath release/AIFinanceHelperQt-preview-Windows-x64.zip
}
finally {
    Pop-Location
}
