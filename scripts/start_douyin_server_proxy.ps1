param(
    [string]$Server = "121.48.162.165",
    [int]$Port = 32769,
    [string]$User = "ubuntu",
    [int]$RemoteSocksPort = 10808
)

$ErrorActionPreference = "Stop"
$remoteForward = "127.0.0.1:$RemoteSocksPort"
$destination = "${User}@${Server}"

Write-Host "Starting the Douyin server proxy tunnel."
Write-Host "Keep this window open while DouK is downloading."
Write-Host "Server proxy: socks5://$remoteForward"

$sshArguments = @(
    "-p", "$Port",
    "-N",
    "-o", "ExitOnForwardFailure=yes",
    "-o", "ServerAliveInterval=30",
    "-o", "ServerAliveCountMax=3",
    "-R", $remoteForward,
    $destination
)

& ssh @sshArguments
if ($LASTEXITCODE -ne 0) {
    throw "SSH proxy tunnel stopped with exit code $LASTEXITCODE."
}
