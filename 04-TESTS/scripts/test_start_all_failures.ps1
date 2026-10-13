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


$DockerShim = @'
@echo off
if defined AIVF_FAKE_DOCKER_LOG echo %*>>"%AIVF_FAKE_DOCKER_LOG%"
if /I "%~1"=="info" (
  if /I "%AIVF_FAKE_DOCKER_MODE%"=="not-running" (
    echo Cannot connect to Docker Desktop 1>&2
    exit /b 13
  )
  exit /b 0
)
if /I "%~1"=="compose" (
  echo %*| findstr /I /C:" config" >nul
  if not errorlevel 1 exit /b 0
  echo %*| findstr /I /C:" up " >nul
  if not errorlevel 1 (
    if /I "%AIVF_FAKE_DOCKER_MODE%"=="port-conflict" (
      echo Ports are not available: port is already allocated 1>&2
      exit /b 17
    )
    exit /b 0
  )
  echo %*| findstr /I /C:" ps " >nul
  if not errorlevel 1 (
    echo fake-container-id
    exit /b 0
  )
  echo %*| findstr /I /C:" logs " >nul
  if not errorlevel 1 (
    echo fake recent container logs
    exit /b 0
  )
)
exit /b 0
'@

$UvShim = @'
@echo off
if defined AIVF_FAKE_UV_LOG echo %*>>"%AIVF_FAKE_UV_LOG%"
if /I "%~1"=="sync" if /I "%AIVF_FAKE_UV_MODE%"=="fail-sync" (
  echo Simulated dependency installation failure 1>&2
  exit /b 19
)
exit /b 0
'@

function Add-FakeTools($Fixture) {
  Set-Content -LiteralPath (Join-Path $Fixture.Bin "docker.cmd") -Value $DockerShim -Encoding ASCII
  Set-Content -LiteralPath (Join-Path $Fixture.Bin "uv.cmd") -Value $UvShim -Encoding ASCII
}

function Invoke-Launcher($Fixture,[string]$DockerMode,[string]$UvMode,[int]$Port,[int]$HealthTimeoutSeconds=1) {
  $systemPath = @($env:SystemRoot + "\System32",$env:SystemRoot) -join [IO.Path]::PathSeparator
  $info = [System.Diagnostics.ProcessStartInfo]::new()
  $info.FileName = Join-Path $PSHOME "pwsh.exe"
  $info.UseShellExecute = $false
  $info.CreateNoWindow = $true
  $info.RedirectStandardOutput = $true
  $info.RedirectStandardError = $true
  $info.WorkingDirectory = $Fixture.Root
  foreach ($argument in @("-NoProfile","-ExecutionPolicy","Bypass","-File",(Join-Path $Fixture.Root "START-ALL.ps1"),"-NoBrowser","-HealthTimeoutSeconds","$HealthTimeoutSeconds")) {
    [void]$info.ArgumentList.Add([string]$argument)
  }
  $info.Environment["PATH"] = $Fixture.Bin + [IO.Path]::PathSeparator + $systemPath
  $info.Environment["AIVF_DASHBOARD_PORT"] = "$Port"
  $info.Environment["AIVF_FAKE_DOCKER_LOG"] = $Fixture.DockerLog
  $info.Environment["AIVF_FAKE_UV_LOG"] = $Fixture.UvLog
  $info.Environment["AIVF_FAKE_DOCKER_MODE"] = $DockerMode
  $info.Environment["AIVF_FAKE_UV_MODE"] = $UvMode
  $info.Environment["AIVF_COOKIE_SECURE"] = "0"
  $process = [System.Diagnostics.Process]::Start($info)
  $stdout = $process.StandardOutput.ReadToEndAsync()
  $stderr = $process.StandardError.ReadToEndAsync()
  if (-not $process.WaitForExit(120000)) {
    $process.Kill()
    throw "Launcher fixture timed out: $($Fixture.Root)"
  }
  $process.WaitForExit()
  $stdout.Wait()
  $stderr.Wait()
  return @{ExitCode=$process.ExitCode;Output=($stdout.Result + [Environment]::NewLine + $stderr.Result)}
}

function Set-PlaceholderEnv($Fixture) {
  @(
    "FLASK_SECRET_KEY=change-me"
    "AIVF_DASHBOARD_TOKEN=placeholder-token"
    "AIVF_COOKIE_SECURE=0"
  ) | Set-Content -LiteralPath $Fixture.EnvFile -Encoding ASCII
}

function Get-EnvValue([string]$EnvFile,[string]$Name) {
  if (-not (Test-Path -LiteralPath $EnvFile)) { return $null }
  $line = Get-Content -LiteralPath $EnvFile | Where-Object { $_ -match ("^\s*" + [regex]::Escape($Name) + "\s*=") } | Select-Object -First 1
  if ($null -eq $line) { return $null }
  return ($line -replace ("^\s*" + [regex]::Escape($Name) + "\s*=\s*"), "")
}

function Get-FreeLoopbackPort {
  $tcp = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback,0)
  $tcp.Start()
  $port = ([System.Net.IPEndPoint]$tcp.LocalEndpoint).Port
  $tcp.Stop()
  return [int]$port
}

function Start-HealthResponder([string]$FixtureRoot,[int]$Port) {
  $scriptPath = Join-Path $FixtureRoot "health-responder.ps1"
  $server = @'
param([int]$Port)
$ErrorActionPreference = "Stop"
$listener = [System.Net.HttpListener]::new()
$listener.Prefixes.Add("http://127.0.0.1:$Port/")
$listener.Start()
try {
  while ($true) {
    $context = $listener.GetContext()
    $body = [System.Text.Encoding]::UTF8.GetBytes('{"status":"ok"}')
    $context.Response.StatusCode = 200
    $context.Response.ContentType = "application/json"
    $context.Response.ContentLength64 = $body.Length
    $context.Response.OutputStream.Write($body,0,$body.Length)
    $context.Response.Close()
  }
} finally {
  $listener.Stop()
}
'@
  Set-Content -LiteralPath $scriptPath -Value $server -Encoding UTF8
  $info = [System.Diagnostics.ProcessStartInfo]::new()
  $info.FileName = Join-Path $PSHOME "pwsh.exe"
  $info.UseShellExecute = $false
  $info.CreateNoWindow = $true
  [void]$info.ArgumentList.Add("-NoProfile")
  [void]$info.ArgumentList.Add("-ExecutionPolicy")
  [void]$info.ArgumentList.Add("Bypass")
  [void]$info.ArgumentList.Add("-File")
  [void]$info.ArgumentList.Add($scriptPath)
  [void]$info.ArgumentList.Add("$Port")
  $process = [System.Diagnostics.Process]::Start($info)
  for ($attempt=0; $attempt -lt 40; $attempt++) {
    if ($process.HasExited) { throw "Health responder exited before listening on port $Port." }
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
      $client.Connect("127.0.0.1",$Port)
      return $process
    } catch {
      Start-Sleep -Milliseconds 100
    } finally {
      $client.Dispose()
    }
  }
  $process.Kill()
  throw "Health responder did not start on port $Port."
}

function Stop-HealthResponder($Process) {
  if ($null -ne $Process -and -not $Process.HasExited) {
    $Process.Kill()
    [void]$Process.WaitForExit(5000)
  }
}
