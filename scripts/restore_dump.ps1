<#
.SYNOPSIS
    Restore a Neo4j dump into the pressrelizagent Neo4j container.

.DESCRIPTION
    Wraps the four-step dance neo4j-admin requires: the database has to be
    offline while it is overwritten, so the server is stopped, the load runs in
    a throwaway container against the same volumes, and the server is started
    again.

    Two things that are easy to get wrong, both learned the hard way:

    * Run this from PowerShell, NOT Git Bash. Git Bash rewrites the container
      path `--from-path=/dumps` into `C:/Program Files/Git/dumps` and the load
      fails with "is not an existing directory".

    * Use -Reset when the Neo4j image changed edition or major version. A
      `system` database created by one edition leaves the graph
      "not currently allocated to any servers", and it cannot be repaired with
      ALTER DATABASE: allocation is required before altering is allowed. Only a
      fresh data volume clears it.

.PARAMETER DumpName
    Database name inside /dumps, without the .dump suffix. `neo4j` means the
    loader reads /dumps/neo4j.dump. Defaults to neo4j.

.PARAMETER Reset
    Delete the data volume first and let Neo4j rebuild `system` from scratch.
    Destroys everything already in the graph. Required after an image change.

.PARAMETER Password
    Neo4j password. Defaults to NEO4J_PASSWORD from .env.

.EXAMPLE
    .\scripts\restore_dump.ps1
    Load data/neo4j/dumps/neo4j.dump over the current graph.

.EXAMPLE
    .\scripts\restore_dump.ps1 -Reset
    Wipe the volume, rebuild system, then load. Use after changing NEO4J_IMAGE.
#>

[CmdletBinding()]
param(
    [string]$DumpName = "neo4j",
    [switch]$Reset,
    [string]$Password
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$service   = "neo4j"
$container = "pressrelizagent-neo4j"
$volume    = "pressrelizagent-neo4j-data"

function Write-Step($n, $text) {
    Write-Host ""
    Write-Host "=== $n. $text ===" -ForegroundColor Cyan
}

# --- password ---------------------------------------------------------------
if (-not $Password) {
    $envFile = Join-Path $root ".env"
    if (Test-Path $envFile) {
        $line = Select-String -Path $envFile -Pattern '^\s*NEO4J_PASSWORD\s*=' |
                Select-Object -First 1
        if ($line) {
            $Password = ($line.Line -split '=', 2)[1].Trim()
        }
    }
}
if (-not $Password) {
    throw "NEO4J_PASSWORD not found in .env - pass -Password explicitly."
}

# --- the dump must exist before anything is torn down -----------------------
$dumpFile = Join-Path $root "data\neo4j\dumps\$DumpName.dump"
if (-not (Test-Path $dumpFile)) {
    throw "Dump not found: $dumpFile"
}
$sizeMb = [math]::Round((Get-Item $dumpFile).Length / 1MB, 1)
Write-Host "Dump: $dumpFile ($sizeMb MB)" -ForegroundColor Green

# --- helper: current status of the target database --------------------------
function Get-DbStatus {
    $cypher = "SHOW DATABASES YIELD name, currentStatus WHERE name='$DumpName' RETURN currentStatus"
    $out = docker exec $container cypher-shell -u neo4j -p $Password -d system --format plain $cypher
    if ($LASTEXITCODE -ne 0) { return "" }
    return ($out | Select-Object -Last 1)
}

function Wait-Online($label) {
    for ($i = 0; $i -lt 45; $i++) {
        $st = Get-DbStatus
        if ($st -match "online") {
            Write-Host "$label online (after ~$($i * 10)s)" -ForegroundColor Green
            return $true
        }
        Start-Sleep -Seconds 10
    }
    Write-Warning "$label did not come online in 450s"
    return $false
}

# --- 1. optional reset ------------------------------------------------------
if ($Reset) {
    Write-Step 1 "Wiping data volume (-Reset)"
    docker compose stop $service
    docker compose rm -f $service
    docker volume rm $volume

    Write-Step 2 "Letting Neo4j rebuild the system database"
    docker compose up -d $service
    if (-not (Wait-Online "fresh database")) {
        throw "Fresh database never came online - check: docker logs $container"
    }
} else {
    Write-Step 1 "Using the existing data volume (pass -Reset after an image change)"
}

# --- 2. load ----------------------------------------------------------------
Write-Step 3 "Loading $DumpName.dump"
docker compose stop $service
docker compose run --rm --entrypoint neo4j-admin $service `
    database load $DumpName --from-path=/dumps --overwrite-destination=true |
    Select-Object -Last 3
$loadExit = $LASTEXITCODE

# --- 3. start ---------------------------------------------------------------
Write-Step 4 "Starting Neo4j"
docker compose start $service

if ($loadExit -ne 0) {
    throw "neo4j-admin load failed (exit $loadExit). Common causes: the dump is in ``block`` format (Enterprise-only) or was produced by a newer Neo4j than NEO4J_IMAGE - a downgrade load is refused."
}

if (-not (Wait-Online "restored database")) {
    throw "Restored database did not come online - check: docker logs $container"
}

# --- 4. report --------------------------------------------------------------
Write-Step 5 "Result"
docker exec $container cypher-shell -u neo4j -p $Password --format plain `
    "MATCH (n) RETURN count(n) AS nodes"
docker exec $container cypher-shell -u neo4j -p $Password --format plain `
    "MATCH ()-[r]->() RETURN count(r) AS relationships"
docker exec $container cypher-shell -u neo4j -p $Password --format plain `
    "MATCH (n) UNWIND labels(n) AS l RETURN l AS label, count(*) AS count ORDER BY count DESC"

Write-Host ""
Write-Host "Done. Browser: http://localhost:7476  (neo4j / <NEO4J_PASSWORD>)" -ForegroundColor Green
