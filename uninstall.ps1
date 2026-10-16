# Removes Stock Tracker from this PC. Your watchlist and positions are kept in
# the data folder unless you pass -DeleteData.
param([switch]$DeleteData)
$Root = if ($env:STOCK_TRACKER_HOME) { $env:STOCK_TRACKER_HOME } else { Join-Path $env:LOCALAPPDATA "StockTracker" }
Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
    Where-Object { $_.CommandLine -and $_.CommandLine.Contains($Root) } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 1
Remove-Item (Join-Path ([Environment]::GetFolderPath("Startup")) "Stock Tracker (background).lnk") -ErrorAction SilentlyContinue
foreach ($dir in [Environment]::GetFolderPath("Programs"), [Environment]::GetFolderPath("Desktop")) {
    foreach ($name in "Stock Tracker.lnk", "Stock Tracker.url") {
        Remove-Item (Join-Path $dir $name) -ErrorAction SilentlyContinue
    }
}
if ($DeleteData) {
    Remove-Item -Recurse -Force $Root -ErrorAction SilentlyContinue
    Write-Host "Stock Tracker and all its data were removed."
} else {
    Get-ChildItem $Root | Where-Object { $_.Name -ne "data" } | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "Stock Tracker was removed. Your data is still in: $Root\data"
}
