# Disposable local PostgreSQL checks. Requires Docker Desktop and the project .venv.
$ErrorActionPreference = 'Stop'
$testContainerName = 'taskiq-tests-' + [Guid]::NewGuid().ToString('N').Substring(0, 10)
$previousTestDatabase = $env:TASKIQ_TEST_DATABASE_URL
$testContainerCreated = $false
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    docker run --detach --rm --name $testContainerName --label taskiq.purpose=disposable-tests -e POSTGRES_USER=taskiq -e POSTGRES_PASSWORD=test-only-password -e POSTGRES_DB=taskiq_test -p 127.0.0.1::5432 postgres:16
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the disposable PostgreSQL container' }
    $testContainerCreated = $true
    $databaseReady = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        docker exec $testContainerName pg_isready -U taskiq -d taskiq_test *> $null
        if ($LASTEXITCODE -eq 0) { $databaseReady = $true; break }
        Start-Sleep -Seconds 1
    }
    if (-not $databaseReady) { throw 'PostgreSQL did not become ready' }
    $testPortMapping = docker port $testContainerName 5432
    if ($LASTEXITCODE -ne 0 -or $testPortMapping -notmatch '^127\.0\.0\.1:(\d+)$') { throw 'Could not identify the private test port' }
    $testPort = $Matches[1]
    $env:TASKIQ_TEST_DATABASE_URL = "postgresql+psycopg://taskiq:test-only-password@127.0.0.1:${testPort}/taskiq_test"
    & .\.venv\Scripts\python.exe -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL test suite failed' }
}
finally {
    $env:TASKIQ_TEST_DATABASE_URL = $previousTestDatabase
    # Only stop the exact disposable container created by this invocation; no volumes are attached.
    if ($testContainerCreated) { docker stop $testContainerName | Out-Null }
    Pop-Location
}
