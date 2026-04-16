Stop-Process -Name chrome -Force -ErrorAction SilentlyContinue
$workers = Get-CimInstance Win32_Process -Filter "Name='python.exe' AND CommandLine LIKE '%worker%'" -ErrorAction SilentlyContinue
foreach ($w in $workers) {
    Stop-Process -Id $w.ProcessId -Force -ErrorAction SilentlyContinue
}
Write-Output "Worker and Chrome killed"
