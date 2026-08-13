param(
    [string]$Server = "121.48.162.165",
    [int]$Port = 32769,
    [string]$User = "ubuntu",
    [string]$RemoteRoot = "/data/users/ccw/intent_mgsv/repo/MGSV_preprocessor",
    [string[]]$KeywordFiles = @(
        "config\douyin_collection_keywords_expand_round3.csv",
        "config\douyin_collection_keywords_expand_round4.csv"
    ),
    [int]$BatchSize = 20,
    [int]$MaxScrolls = 100,
    [int]$ScrollPauseMs = 2500,
    [int]$DetailDwellMs = 5000,
    [int]$MaxDurationSeconds = 300,
    [switch]$SkipServerMetadataSync
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$CollectionRoot = Join-Path $ProjectRoot "outputs\douyin_link_collection"
$SyncRoot = Join-Path $ProjectRoot "outputs\server_sync"
$ServerMetadata = Join-Path $SyncRoot "Download.server.latest.xlsx"
$Python = if ($env:CONDA_PREFIX) {
    Join-Path $env:CONDA_PREFIX "python.exe"
} else {
    "python"
}

New-Item -ItemType Directory -Force -Path $CollectionRoot, $SyncRoot | Out-Null
Set-Location $ProjectRoot

if (-not $SkipServerMetadataSync) {
    Write-Host "Syncing the latest DouK metadata from the server..."
    $remoteMetadata = "${User}@${Server}:${RemoteRoot}/DouK-Source/Volume/Data/Download.xlsx"
    & scp -P $Port $remoteMetadata $ServerMetadata
    if ($LASTEXITCODE -ne 0) {
        throw "Could not download the latest server Download.xlsx."
    }
}

if (-not (Test-Path $ServerMetadata)) {
    throw "Server metadata not found: $ServerMetadata"
}

$before = @{}
Get-ChildItem $CollectionRoot -Directory | ForEach-Object { $before[$_.FullName] = $true }
$newSessions = [System.Collections.Generic.List[string]]::new()

for ($index = 0; $index -lt $KeywordFiles.Count; $index++) {
    $keywordPath = (Resolve-Path (Join-Path $ProjectRoot $KeywordFiles[$index])).Path
    Write-Host "Collecting round $($index + 1)/$($KeywordFiles.Count): $keywordPath"
    $arguments = @(
        "-m", "intent_mgsv_pipeline.data_collection.collect_douyin_browser_links",
        "--keywords", $keywordPath,
        "--douk-metadata", $ServerMetadata,
        "--batch-size", "$BatchSize",
        "--max-scrolls", "$MaxScrolls",
        "--scroll-pause-ms", "$ScrollPauseMs",
        "--detail-dwell-ms", "$DetailDwellMs",
        "--max-duration-seconds", "$MaxDurationSeconds"
    )
    if ($index -gt 0) {
        $arguments += "--skip-login-wait"
    }
    & $Python @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Collection round failed: $keywordPath"
    }

    $latest = Get-ChildItem $CollectionRoot -Directory |
        Where-Object { -not $before.ContainsKey($_.FullName) } |
        Sort-Object LastWriteTime |
        Select-Object -Last 1
    if (-not $latest) {
        throw "No new collection session was created."
    }
    $newSessions.Add($latest.Name)
    $before[$latest.FullName] = $true
}

$packageName = "douyin_new_" + (Get-Date -Format "yyyyMMdd_HHmmss")
$mergeArguments = @(
    "-m", "intent_mgsv_pipeline.data_collection.merge_douyin_collection",
    "--collection-root", $CollectionRoot,
    "--douk-metadata", $ServerMetadata,
    "--package-name", $packageName,
    "--batch-size", "$BatchSize"
)
foreach ($session in $newSessions) {
    $mergeArguments += @("--session", $session)
}

Write-Host "Packaging only the new sessions: $($newSessions -join ', ')"
& $Python @mergeArguments
if ($LASTEXITCODE -ne 0) {
    throw "Could not create the server upload package."
}

$zipPath = Join-Path $ProjectRoot "outputs\douyin_server_upload\${packageName}.zip"
Write-Host "Collection complete."
Write-Host "Sessions: $($newSessions -join ', ')"
Write-Host "Package: $zipPath"
