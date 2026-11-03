param([string]$Python = 'python', [string]$Iscc = '', [string]$Version = '0.8.0')
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
function Check-Exit { if ($LASTEXITCODE -ne 0) { throw "Build step failed: $LASTEXITCODE" } }
& $Python packaging/make_icon.py
Check-Exit
$tlsAssets = & $Python packaging/runtime_tls_assets.py | ConvertFrom-Json
Check-Exit
$tlsArgs = @()
foreach ($asset in $tlsAssets) { $tlsArgs += @('--add-data', "$asset;.") }
& $Python -m PyInstaller --noconfirm --clean --windowed --onedir --name AIFinanceHelper --icon assets/app.ico --add-data 'assets;assets' @tlsArgs --collect-all openpyxl --exclude-module streamlit --exclude-module pytest --exclude-module pyarrow desktop.py
Check-Exit
$packageSmokeDir = Join-Path (Get-Location).Path ('build\package-smoke-' + [guid]::NewGuid().ToString('N'))
$previousFinanceData = $env:FINANCE_DESKTOP_DATA_DIR
try {
    $env:FINANCE_DESKTOP_DATA_DIR = $packageSmokeDir
    $packageSmoke = Start-Process -FilePath 'dist\AIFinanceHelper\AIFinanceHelper.exe' -ArgumentList '--self-test' -WindowStyle Hidden -PassThru
    if (-not $packageSmoke.WaitForExit(60000)) { $packageSmoke.Kill(); throw 'Packaged self-test timed out' }
    if ($packageSmoke.ExitCode -ne 0 -or -not (Test-Path -LiteralPath (Join-Path $packageSmokeDir 'self-test.ok'))) { throw 'Packaged HTTPS/UI self-test failed; installer build stopped' }
    $m1Smoke = Start-Process -FilePath 'dist\AIFinanceHelper\AIFinanceHelper.exe' -ArgumentList '--self-test-m1a' -WindowStyle Hidden -PassThru
    if (-not $m1Smoke.WaitForExit(60000)) { $m1Smoke.Kill(); throw 'Packaged M1a self-test timed out' }
    if ($m1Smoke.ExitCode -ne 0 -or -not (Test-Path -LiteralPath (Join-Path $packageSmokeDir 'm1a-self-test.json'))) { throw 'Packaged M1a acceptance failed; installer build stopped' }
} finally { $env:FINANCE_DESKTOP_DATA_DIR = $previousFinanceData }
if (-not $Iscc) {
    $candidates = @('tools\inno\ISCC.exe', "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe")
    $Iscc = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if (-not $Iscc) { throw 'Install Inno Setup 6 or pass -Iscc.' }
& $Iscc "/DAppVersion=$Version" packaging/installer.iss
Check-Exit
Compress-Archive -Path dist/AIFinanceHelper -DestinationPath "release/AIFinanceHelper-$Version-Windows-x64.zip" -Force
& $Python packaging/source_archive.py
Check-Exit
$releaseFiles = @("AIFinanceHelper-$Version-Setup-x64.exe", "AIFinanceHelper-$Version-Windows-x64.zip", 'AIFinanceHelper-source.zip')
$releaseFiles | ForEach-Object {
    if (-not (Test-Path -LiteralPath "release/$_")) { throw "Missing release asset: $_" }
    Get-Item -LiteralPath "release/$_"
} | ForEach-Object {
    $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLower()
    "$hash  $($_.Name)"
} | Set-Content release/SHA256SUMS.txt -Encoding ascii
