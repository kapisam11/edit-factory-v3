$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
$Temp = Join-Path ([IO.Path]::GetTempPath()) ("aivf-start-tests-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $Temp -Force | Out-Null
function Assert-True([bool]$ok,[string]$message) { if (-not $ok) { throw "ASSERTION FAILED: $message" } }
function New-Fixture([string]$name) {
  $root = Join-Path $Temp $name
  $bin = Join-Path $root "bin"
  $dep = Join-Path $root "06-CONFIG-AND-DEPLOYMENT"
  New-Item -ItemType Directory -Path $bin,$dep -Force | Out-Null
  Copy-Item (Join-Path $Root "START-ALL.ps1") $root
  Copy-Item (Join-Path $Root "06-CONFIG-AND-DEPLOYMENT\docker-compose.dev.yml") $dep
  return @{Root=$root;Bin=$bin;EnvFile=(Join-Path $dep ".env");DockerLog=(Join-Path $root "docker.log");UvLog=(Join-Path $root "uv.log")}
}
