param(
    [string]$Server = "121.48.162.165",
    [int]$Port = 32769,
    [string]$User = "ubuntu",
    [string]$RemoteRoot = "/data/users/ccw/intent_mgsv/repo/MGSV_preprocessor",
    [string]$LocalOutput = "E:\MGSV_preprocessor\server_reports\latest.txt",
    [int]$Recent = 20
)

$ErrorActionPreference = "Stop"
$remoteReport = "$RemoteRoot/outputs/server/diagnostics/latest.txt"
$remoteCommand = "cd '$RemoteRoot' && source config/server.env && python -m intent_mgsv_pipeline.server.collect_diagnostics --recent $Recent --out '$remoteReport'"

Write-Host "Generating a server diagnostic report..."
& ssh -p $Port "$User@$Server" $remoteCommand
if ($LASTEXITCODE -ne 0) {
    throw "The server diagnostic command failed."
}

$localPath = [System.IO.Path]::GetFullPath($LocalOutput)
[System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($localPath)) | Out-Null
Write-Host "Downloading the report..."
& scp -P $Port "${User}@${Server}:$remoteReport" $localPath
if ($LASTEXITCODE -ne 0) {
    throw "The diagnostic report download failed."
}

Write-Host "Diagnostic report saved to: $localPath"
