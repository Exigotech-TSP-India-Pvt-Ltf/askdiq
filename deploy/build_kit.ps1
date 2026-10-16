# Build the AskDIQ deploy kit (run locally on Windows, from any directory):
#   powershell -ExecutionPolicy Bypass -File deploy\build_kit.ps1 [-Ref HEAD] [-OutDir <dir>]
#
# Produces askdiq-deploy-<sha>.tgz containing ONLY committed source:
#   backend/     git archive of backend/   (extracted on the server, see RUNBOOK)
#   frontend/    git archive of frontend/
#   deploy-kit/  git archive of deploy/    (docs + examples; NEVER extracted into /opt/askdiq)
# No node_modules, no .env, no compose file, no nginx conf at the top level:
# the script verifies that with `tar -tzf` and fails if any slips in.
param(
  [string]$Ref = 'HEAD',
  [string]$OutDir = ''
)
$ErrorActionPreference = 'Stop'

$repo = (& git -C $PSScriptRoot rev-parse --show-toplevel).Trim()
if ($LASTEXITCODE -ne 0) { throw 'not inside the askdiq git repository' }
$sha = (& git -C $repo rev-parse --short=12 $Ref).Trim()
if (-not $OutDir) { $OutDir = $repo }
$kit = Join-Path $OutDir "askdiq-deploy-$sha.tgz"

$dirty = & git -C $repo status --porcelain -- backend frontend deploy
if ($dirty) {
  Write-Host 'WARNING: uncommitted changes under backend/, frontend/ or deploy/ are NOT in the kit (it is built from git).' -ForegroundColor Yellow
}

$stage = Join-Path ([IO.Path]::GetTempPath()) "askdiq-kit-$sha-$PID"
New-Item -ItemType Directory -Force -Path $stage | Out-Null
try {
  $src = Join-Path $stage 'src.tar'
  $docs = Join-Path $stage 'docs.tar'
  & git -C $repo archive --format=tar -o $src $Ref -- backend frontend
  if ($LASTEXITCODE -ne 0) { throw 'git archive backend/frontend failed' }
  & git -C $repo archive --format=tar --prefix=deploy-kit/ -o $docs $Ref -- deploy
  if ($LASTEXITCODE -ne 0) { throw 'git archive deploy failed' }
  $root = Join-Path $stage 'root'
  New-Item -ItemType Directory -Force -Path $root | Out-Null
  & tar -xf $src -C $root
  & tar -xf $docs -C $root
  if ($LASTEXITCODE -ne 0) { throw 'tar extract failed' }
  Set-Content -Path (Join-Path $root 'deploy-kit\KIT_VERSION') -Value "commit=$((& git -C $repo rev-parse $Ref).Trim())`nbuilt=$((Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ'))" -Encoding ascii
  if (Test-Path $kit) { Remove-Item -Force $kit }
  & tar -czf $kit -C $root backend frontend deploy-kit
  if ($LASTEXITCODE -ne 0) { throw 'tar create failed' }
} finally {
  Remove-Item -Recurse -Force $stage -ErrorAction SilentlyContinue
}

# ---- verify the contents ----
$entries = & tar -tzf $kit | ForEach-Object { $_.TrimStart('.', '/') } | Where-Object { $_ }
$problems = @()
$tops = $entries | ForEach-Object { ($_ -split '/')[0] } | Sort-Object -Unique
foreach ($t in $tops) { if ($t -notin @('backend', 'frontend', 'deploy-kit')) { $problems += "unexpected top-level entry: $t" } }
foreach ($e in $entries) {
  $leaf = ($e.TrimEnd('/') -split '/')[-1]
  if ($e -match '(^|/)node_modules/') { $problems += "node_modules: $e" }
  if ($e -match '(^|/)\.next/') { $problems += ".next build output: $e" }
  if ($leaf -match '^\.env(\..*)?$' -and $leaf -notmatch '\.example$') { $problems += "env file: $e" }
  if ($e -notmatch '^deploy-kit/' -and $leaf -match '^docker-compose.*\.ya?ml$') { $problems += "compose file outside deploy-kit: $e" }
  if ($e -notmatch '^deploy-kit/' -and $leaf -match '\.conf$') { $problems += "nginx conf outside deploy-kit: $e" }
}
$required = @(
  'backend/Dockerfile.prod', 'backend/docker-entrypoint.sh', 'backend/scripts/prestart.py',
  'backend/scripts/ingest_real_docs.py', 'backend/alembic.ini', 'backend/requirements.txt',
  'frontend/Dockerfile.prod', 'frontend/.dockerignore', 'frontend/package-lock.json', 'frontend/.npmrc',
  'deploy-kit/deploy/RUNBOOK.md', 'deploy-kit/deploy/docker-compose.prod.example.yml',
  'deploy-kit/deploy/nginx/askdiq-admin.conf.example', 'deploy-kit/deploy/frontend.env.prod.example'
)
foreach ($r in $required) { if ($r -notin $entries) { $problems += "missing: $r" } }
$kb = @($entries | Where-Object { $_ -match '^backend/data/real_docs/[^/]+\.md$' })
if ($kb.Count -eq 0) { $problems += 'missing: backend/data/real_docs/*.md' }

$size = [math]::Round((Get-Item $kit).Length / 1MB, 2)
Write-Host ''
Write-Host "Kit:      $kit ($size MB, $($entries.Count) entries)"
Write-Host "Commit:   $sha"
Write-Host "Top level: $($tops -join ', ')"
Write-Host "KB docs:  $($kb.Count) x backend/data/real_docs/*.md"
Write-Host ''
Write-Host 'Checklist:'
$checks = [ordered]@{
  'only backend/, frontend/, deploy-kit/ at top level'   = -not ($problems -match 'unexpected top-level')
  'no .env / .env.local (only *.example)'                = -not ($problems -match '^env file')
  'no node_modules or .next'                             = -not ($problems -match 'node_modules|\.next')
  'no compose file or nginx conf outside deploy-kit/'    = -not ($problems -match 'outside deploy-kit')
  'Dockerfiles, entrypoint, prestart, runbook present'   = -not ($problems -match '^missing: (?!backend/data)')
  'knowledge-base markdown present'                      = $kb.Count -gt 0
}
foreach ($k in $checks.Keys) { Write-Host ("  [{0}] {1}" -f $(if ($checks[$k]) { 'x' } else { ' ' }), $k) }
if ($problems) {
  Write-Host ''
  $problems | ForEach-Object { Write-Host "  PROBLEM: $_" -ForegroundColor Red }
  Remove-Item -Force $kit
  Write-Host 'KIT REJECTED (deleted).' -ForegroundColor Red
  exit 1
}
Write-Host ''
Write-Host 'KIT OK. Next: deploy-kit/deploy/RUNBOOK.md, step 1 (scp).'
