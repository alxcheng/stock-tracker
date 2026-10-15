# Stock Tracker installer for Windows. Paste into PowerShell:
#
#   powershell -ExecutionPolicy Bypass -c "irm https://github.com/alxcheng/stock-tracker/releases/latest/download/install.ps1 | iex"
#
# No admin rights or Python needed. Running it again repairs the install and
# moves to the latest version. Your watchlist and positions are kept.
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # Invoke-WebRequest is very slow with the progress bar

$Repo = if ($env:STOCK_TRACKER_REPO) { $env:STOCK_TRACKER_REPO } else { "alxcheng/stock-tracker" }
$Root = if ($env:STOCK_TRACKER_HOME) { $env:STOCK_TRACKER_HOME } else { Join-Path $env:LOCALAPPDATA "StockTracker" }
$ZipUrl = if ($env:STOCK_TRACKER_ZIP_URL) { $env:STOCK_TRACKER_ZIP_URL } else { "https://github.com/$Repo/releases/latest/download/stock-tracker.zip" }
$Port = if ($env:STOCK_TRACKER_PORT) { [int]$env:STOCK_TRACKER_PORT } else { 8050 }
$Url = "http://127.0.0.1:$Port"
$env:UV_MANAGED_PYTHON = "1"  # use uv's own Python, never whatever else is installed

function Say($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host "`nError: $msg" -ForegroundColor Red; throw $msg }
function Download($from, $to) {
    if (Test-Path $from) { Copy-Item $from $to } else { Invoke-WebRequest -UseBasicParsing -Uri $from -OutFile $to }
}

Say "Getting uv (runs the app's Python; installs into your user folder)"
$Uv = Join-Path $env:USERPROFILE ".local\bin\uv.exe"
if (-not (Test-Path $Uv)) {
    $env:UV_NO_MODIFY_PATH = "1"
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
}
if (-not (Test-Path $Uv)) { Fail "Couldn't install uv." }

Say "Downloading the latest Stock Tracker"
$Tmp = Join-Path ([IO.Path]::GetTempPath()) ("stock-tracker-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $Tmp | Out-Null
$Zip = Join-Path $Tmp "stock-tracker.zip"
Download $ZipUrl $Zip
Download "$ZipUrl.sha256" "$Zip.sha256"
$Expected = ((Get-Content "$Zip.sha256" -Raw).Trim() -split "\s+")[0].ToLower()
$Actual = (Get-FileHash $Zip -Algorithm SHA256).Hash.ToLower()
if ($Expected -ne $Actual) { Fail "Checksum mismatch; the download may be corrupted. Try again." }
$Extract = Join-Path $Tmp "x"
Expand-Archive -Path $Zip -DestinationPath $Extract
$Version = (Get-Content (Join-Path $Extract "VERSION") -Raw).Trim()
$VDir = Join-Path $Root "versions\$Version"

# Stop a running copy so its files can be replaced.
Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
    Where-Object { $_.CommandLine -and $_.CommandLine.Contains($Root) } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 1

Say "Installing version $Version (first install downloads Python, about a minute)"
foreach ($d in "versions", "data", "logs") { New-Item -ItemType Directory -Force -Path (Join-Path $Root $d) | Out-Null }
if (Test-Path $VDir) { Remove-Item -Recurse -Force $VDir }
Move-Item $Extract $VDir
& $Uv sync --frozen --no-dev --project $VDir -q
if ($LASTEXITCODE -ne 0) { Fail "Installing dependencies failed." }
& (Join-Path $VDir ".venv\Scripts\python.exe") (Join-Path $VDir "selftest.py") | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "Self-test failed." }

$CurrentFile = Join-Path $Root "current.txt"
$Current = if (Test-Path $CurrentFile) { (Get-Content $CurrentFile -Raw).Trim() } else { "" }
if ($Current -and $Current -ne $Version) { Set-Content (Join-Path $Root "previous.txt") $Current }
Set-Content $CurrentFile $Version
Remove-Item (Join-Path $Root "trial.txt") -ErrorAction SilentlyContinue
@{ uv = $Uv; repo = $Repo; port = $Port; releases_api = "$env:STOCK_TRACKER_RELEASES_API" } | ConvertTo-Json | Set-Content (Join-Path $Root "install.json")
Copy-Item (Join-Path $VDir "bootstrap.py"), (Join-Path $VDir "uninstall.ps1") $Root -Force

$Py = (& $Uv python find 3.12).Trim()
$Pyw = Join-Path (Split-Path $Py) "pythonw.exe"
if (-not (Test-Path $Pyw)) { Fail "Couldn't find pythonw.exe next to $Py" }
$Bootstrap = Join-Path $Root "bootstrap.py"

Say "Starting Stock Tracker in the background (it also starts when you sign in)"
$Shell = New-Object -ComObject WScript.Shell
$Startup = [Environment]::GetFolderPath("Startup")
$Lnk = $Shell.CreateShortcut((Join-Path $Startup "Stock Tracker (background).lnk"))
$Lnk.TargetPath = $Pyw
$Lnk.Arguments = "`"$Bootstrap`""
$Lnk.WorkingDirectory = $Root
$Lnk.WindowStyle = 7
$Lnk.Save()
Start-Process -FilePath $Pyw -ArgumentList "`"$Bootstrap`"" -WorkingDirectory $Root -WindowStyle Hidden

# Shortcuts that open the page.
$Shortcut = "[InternetShortcut]`r`nURL=$Url/`r`n"
$Programs = [Environment]::GetFolderPath("Programs")
Set-Content -Path (Join-Path $Programs "Stock Tracker.url") -Value $Shortcut
Set-Content -Path (Join-Path ([Environment]::GetFolderPath("Desktop")) "Stock Tracker.url") -Value $Shortcut

Write-Host -NoNewline "Waiting for it to start"
$ok = $false
for ($i = 0; $i -lt 90; $i++) {
    try { Invoke-WebRequest -UseBasicParsing "$Url/api/status" -TimeoutSec 2 | Out-Null; $ok = $true; break } catch { Write-Host -NoNewline "."; Start-Sleep 1 }
}
Write-Host ""
Remove-Item -Recurse -Force $Tmp -ErrorAction SilentlyContinue
if (-not $ok) { Fail "It didn't start. See $Root\logs\app.log" }

Say "Done! Stock Tracker $Version is running at $Url"
Write-Host "  - Open it any time from the 'Stock Tracker' shortcut on your desktop or Start menu."
Write-Host "  - Phone alerts: open the page and follow 'Alerts & updates' at the bottom."
Write-Host "  - It updates itself. To uninstall: powershell -ExecutionPolicy Bypass -File `"$Root\uninstall.ps1`""
if (-not $env:STOCK_TRACKER_NO_OPEN) { Start-Process $Url }
