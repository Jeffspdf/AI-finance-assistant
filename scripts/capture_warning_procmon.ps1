#Requires -RunAsAdministrator
param(
    [Parameter(Mandatory=$true)][string]$Session,
    [Parameter(Mandatory=$true)][string]$Profile,
    [string]$Procmon = (Join-Path $PSScriptRoot '..\tools\procmon\Procmon64.exe'),
    [ValidateRange(15,600)][int]$DurationSeconds = 300,
    [switch]$ConfirmFilteredProfile
)
$ErrorActionPreference = 'Stop'
if (-not $ConfirmFilteredProfile) { throw 'Review the saved filters and Drop Filtered Events first; then explicitly supply -ConfirmFilteredProfile.' }
$Session = (Resolve-Path -LiteralPath $Session).Path
$Profile = (Resolve-Path -LiteralPath $Profile).Path
$Procmon = (Resolve-Path -LiteralPath $Procmon).Path
if (-not (Test-Path -LiteralPath (Join-Path $Session 'session.json'))) { throw 'Use a session made by warning_experiment.py prepare.' }
if ([IO.Path]::GetExtension($Profile) -ne '.pmc') { throw 'A saved Procmon .pmc profile is required.' }
if (@(Get-Process -Name Procmon,Procmon64,Procmon64a -ErrorAction SilentlyContinue).Count -ne 0) {
    throw 'A Procmon instance already exists. Save and close it yourself first; this script will not take over an existing capture.'
}
$signature = Get-AuthenticodeSignature -LiteralPath $Procmon
if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Microsoft Corporation') { throw 'Expected a valid Microsoft-signed Process Monitor.' }
$captureMetadata = Join-Path $Session 'capture.json'
if (Test-Path -LiteralPath $captureMetadata) { throw 'Capture metadata already exists. Use a new session; no capture is overwritten.' }
$capturePath = Join-Path $Session 'capture.pml'
$stopFile = Join-Path $Session 'capture.stop'
if ((Test-Path -LiteralPath $capturePath) -or (Test-Path -LiteralPath $stopFile)) { throw 'Capture paths must be new.' }
$metadata = [ordered]@{
    started=[DateTimeOffset]::Now.ToString('o');ended=$null;process_id=$null
    profile_sha256=(Get-FileHash -LiteralPath $Profile -Algorithm SHA256).Hash.ToLower()
    procmon_sha256=(Get-FileHash -LiteralPath $Procmon -Algorithm SHA256).Hash.ToLower()
    profile_verification='operator confirmed; proprietary .pmc contents not programmatically validated'
    duration_limit_seconds=$DurationSeconds;readiness='not yet observed';stop_method=$null
    pml_bytes=0;pml_sha256=$null;native_stack_reviewed=$false;root_cause_closed=$false
}
function Save-CaptureMetadata { $metadata | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $captureMetadata -Encoding utf8 }
Save-CaptureMetadata
$ownedProcmon = $null
try {
    # File paths are quoted native arguments, not shell command substitutions.
    $nativeArgs = @('/Quiet','/Minimized','/LoadConfig',('"' + $Profile + '"'),'/BackingFile',('"' + $capturePath + '"'))
    $ownedProcmon = Start-Process -FilePath $Procmon -ArgumentList $nativeArgs -WindowStyle Hidden -PassThru
    $metadata.process_id = $ownedProcmon.Id
    $readyLimit = [DateTime]::UtcNow.AddSeconds(15)
    while (-not (Test-Path -LiteralPath $capturePath)) {
        if ($ownedProcmon.HasExited) { throw 'Procmon exited before creating its backing file.' }
        if ([DateTime]::UtcNow -gt $readyLimit) { throw 'Backing file was not created within 15 seconds.' }
        Start-Sleep -Milliseconds 100
    }
    $metadata.readiness='backing file observed; first captured event must still be verified in the PML'
    Save-CaptureMetadata
    Write-Output 'CAPTURE_BACKING_FILE_READY: run A/B in a separate non-administrator terminal.'
    Write-Output ('To stop early, create this empty file: ' + $stopFile)
    $deadline = [DateTime]::UtcNow.AddSeconds($DurationSeconds)
    while ([DateTime]::UtcNow -lt $deadline -and -not (Test-Path -LiteralPath $stopFile)) {
        if ($ownedProcmon.HasExited) { break }
        Start-Sleep -Milliseconds 250
    }
} finally {
    if ($null -ne $ownedProcmon -and -not $ownedProcmon.HasExited) {
        $instances = @(Get-Process -Name Procmon,Procmon64,Procmon64a -ErrorAction SilentlyContinue)
        if ($instances.Count -eq 1 -and $instances[0].Id -eq $ownedProcmon.Id) {
            $stopper = Start-Process -FilePath $Procmon -ArgumentList '/Terminate','/Quiet' -WindowStyle Hidden -PassThru
            if (-not $stopper.WaitForExit(10000)) { $stopper.Kill() }
            $metadata.stop_method='official terminate command for sole owned instance'
        } else {
            $metadata.stop_method='refused global terminate; forced only owned process, PML may be incomplete'
        }
        if (-not $ownedProcmon.WaitForExit(10000)) {
            $ownedProcmon.Kill()
            $metadata.stop_method='forced only owned process after stop failure; PML may be incomplete'
        }
    }
    $metadata.ended=[DateTimeOffset]::Now.ToString('o')
    if (Test-Path -LiteralPath $capturePath) {
        $metadata.pml_bytes=(Get-Item -LiteralPath $capturePath).Length
        $metadata.pml_sha256=(Get-FileHash -LiteralPath $capturePath -Algorithm SHA256).Hash.ToLower()
    }
    Save-CaptureMetadata
}
