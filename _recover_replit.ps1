param(
  [string]$Base = 'https://0cfdd4ac-ddeb-44b2-af6c-9a7cf3c55c49-00-uidexvjb2sg2.janeway.replit.dev',
  [string]$OutRoot = 'C:\Users\ALGO\Desktop\SEAN-ALGO\frontend'
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

# Queue of dev-server paths (with leading /). Seed entry.
$queue = New-Object System.Collections.Queue
$queue.Enqueue('/src/main.tsx')
$seen = @{}
$deps = New-Object System.Collections.Generic.HashSet[string]
$saved = @()
$failed = @()

function Decode-SourceContent($text, $relForCss) {
  $m = [regex]::Match($text, 'sourceMappingURL=data:application/json;base64,([A-Za-z0-9+/=]+)')
  if ($m.Success) {
    try {
      $json = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($m.Groups[1].Value)) | ConvertFrom-Json
      if ($json.sourcesContent -and $json.sourcesContent.Count -ge 1) { return $json.sourcesContent[0] }
    } catch {}
  }
  return $null
}

$count = 0
while ($queue.Count -gt 0 -and $count -lt 400) {
  $path = $queue.Dequeue()
  $key = ($path -split '\?')[0]
  if ($seen.ContainsKey($key)) { continue }
  $seen[$key] = $true
  $count++
  try {
    $resp = Invoke-WebRequest -Uri ("$Base$path") -TimeoutSec 30 -UseBasicParsing
    $text = $resp.Content
  } catch {
    $failed += "$key  ($($_.Exception.Message))"; continue
  }

  # recover original source from sourcemap; fallback to raw text
  $orig = Decode-SourceContent $text $key
  if (-not $orig) { $orig = $text }  # css/json sometimes raw

  # write to mirror: /src/foo -> $OutRoot/src/foo
  $rel = $key.TrimStart('/')
  $dest = Join-Path $OutRoot $rel
  $dir = Split-Path $dest -Parent
  if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
  Set-Content -Path $dest -Value $orig -Encoding UTF8 -NoNewline
  $saved += $rel

  # find child module URLs from COMPILED text (have real extensions + ?v=)
  foreach ($mm in [regex]::Matches($text, '["'']/(src|@fs)/[^"''`]+["'']')) {
    $child = $mm.Value.Trim('"','''')
    $queue.Enqueue($child)
  }
  # record bare deps for package.json reconstruction (from /node_modules/.vite/deps/NAME.js)
  foreach ($dm in [regex]::Matches($text, '/node_modules/\.vite/deps/([^"''?`]+)')) {
    $null = $deps.Add(($dm.Groups[1].Value -replace '\.js$','' -replace '_','/'))
  }
}

Write-Output "SAVED $($saved.Count) files:"
$saved | Sort-Object | ForEach-Object { Write-Output "  $_" }
Write-Output ""
Write-Output "DEPS detected ($($deps.Count)):"
$deps | Sort-Object | ForEach-Object { Write-Output "  $_" }
if ($failed.Count) {
  Write-Output ""
  Write-Output "FAILED $($failed.Count):"
  $failed | ForEach-Object { Write-Output "  $_" }
}
