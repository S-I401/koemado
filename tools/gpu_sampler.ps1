# Samples GPU Process Memory (dedicated, per process) every 0.3 s into a CSV (pid,bytes). Used by tts_bench.py. Stops when the Stop file exists.
param([string]$Out, [string]$Stop)
'pid,bytes' | Out-File -Encoding ascii $Out
while (-not (Test-Path $Stop)) {
  $s = (Get-Counter '\GPU Process Memory(*)\Dedicated Usage' -ErrorAction SilentlyContinue).CounterSamples
  $l = foreach ($x in $s) { if ($x.InstanceName -match 'pid_(\d+)_') { "$($Matches[1]),$([int64]$x.CookedValue)" } }
  $l | Out-File -Encoding ascii -Append $Out
  Start-Sleep -Milliseconds 300
}
