param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$CorpusPath,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$OutputRoot,

    [int]$TimeoutSeconds = 90,
    [string]$Backend = "auto",
    [switch]$CompareFeishuExport,
    [switch]$Headless,
    [int]$Limit = 0
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$corpus = Get-Content -LiteralPath $CorpusPath -Raw -Encoding UTF8 | ConvertFrom-Json

$runId = Get-Date -Format "yyyyMMdd-HHmmss"
$runRoot = Join-Path $OutputRoot $runId
New-Item -ItemType Directory -Force -Path $runRoot | Out-Null

$env:PYTHONPATH = $RepoRoot
$items = @($corpus.links)
if ($Limit -gt 0) {
    $items = @($items | Select-Object -First $Limit)
}

$results = @()
foreach ($item in $items) {
    $archiveDir = Join-Path $runRoot "archives"
    New-Item -ItemType Directory -Force -Path $archiveDir | Out-Null
    $args = @(
        "-m", "web_content_fetcher.cli",
        "fetch-doc", [string]$item.url,
        "--archive-dir", $archiveDir,
        "--timeout", [string]$TimeoutSeconds,
        "--backend", $Backend,
        "--format", "json"
    )
    if ($CompareFeishuExport) {
        $args += "--compare-feishu-export"
    }
    if ($Headless) {
        $args += "--headless"
    }
    $startedAt = Get-Date
    $stdout = ""
    $stderr = ""
    $exitCode = 0
    try {
        $output = & python @args 2>&1
        $exitCode = $LASTEXITCODE
        $stdout = ($output | Out-String)
    } catch {
        $exitCode = 1
        $stderr = $_.Exception.Message
    }
    $status = "failed"
    $transport = ""
    $strategy = ""
    $route = ""
    $textChars = 0
    $imageCount = 0
    $tableCount = 0
    $qualityStatus = ""
    $contentStatus = ""
    $archivePath = ""
    $errors = @()
    try {
        $parsed = $stdout | ConvertFrom-Json
        $status = [string]$parsed.status
        $transport = [string]$parsed.transport
        $qualityStatus = [string]$parsed.extraction_quality.status
        $archivePath = [string]$parsed.archive.archive_path
        $textChars = [int]($parsed.extraction_quality.text_chars)
        if (-not $textChars) {
            $textChars = [int](([string]$parsed.extracted_text).Length)
        }
        if ($archivePath) {
            $structuredPath = Join-Path $archivePath "structured.json"
            if (Test-Path -LiteralPath $structuredPath) {
                $structured = Get-Content -LiteralPath $structuredPath -Raw -Encoding UTF8 | ConvertFrom-Json
                $strategy = [string]$structured.archive_index.site_data.site_strategy.site_key
                $route = [string]$structured.archive_index.site_data.site_strategy.kind
                $contentStatus = [string]$structured.archive_index.content_completeness.overall_status
                $imageCount = [int]$structured.archive_index.preferred_markdown.structure.image_count
                $tableCount = [int]$structured.archive_index.preferred_markdown.structure.table_count
            }
        }
        $errors = @($parsed.errors)
    } catch {
        $errors = @("summary_parse_error:$($_.Exception.Message)", $stderr)
    }
    if ($exitCode -ne 0) {
        $status = "failed"
        $errors += "process_exit_code:$exitCode"
    }
    $minimum = $item.minimum
    $meetsMinimum = $true
    if ($minimum) {
        if ($minimum.text_chars -and $textChars -lt [int]$minimum.text_chars) {
            $meetsMinimum = $false
        }
        if ($minimum.images -and $imageCount -lt [int]$minimum.images) {
            $meetsMinimum = $false
        }
        if ($minimum.tables -and $tableCount -lt [int]$minimum.tables) {
            $meetsMinimum = $false
        }
    }
    $results += [pscustomobject]@{
        id = [string]$item.id
        category = [string]$item.category
        url = [string]$item.url
        status = $status
        exit_code = $exitCode
        transport = $transport
        expected_strategy = [string]$item.expected_strategy
        strategy = $strategy
        expected_route = [string]$item.expected_route
        route = $route
        text_chars = $textChars
        image_count = $imageCount
        table_count = $tableCount
        quality_status = $qualityStatus
        content_completeness_status = $contentStatus
        meets_minimum = $meetsMinimum
        archive_path = $archivePath
        errors = $errors
        elapsed_seconds = [math]::Round(((Get-Date) - $startedAt).TotalSeconds, 2)
    }
}

$summary = [pscustomobject]@{
    status = "ok"
    run_id = $runId
    corpus_path = $CorpusPath
    output_root = $runRoot
    total = $results.Count
    ok = @($results | Where-Object { $_.status -eq "ok" }).Count
    failed = @($results | Where-Object { $_.status -ne "ok" }).Count
    below_minimum = @($results | Where-Object { -not $_.meets_minimum }).Count
    items = $results
}

$summaryJsonPath = Join-Path $runRoot "smoke_summary.json"
$summaryMdPath = Join-Path $runRoot "smoke_summary.md"
$summary | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $summaryJsonPath -Encoding UTF8

$lines = @()
$lines += "# Web Content Fetcher Real-Link Smoke Summary"
$lines += ""
$lines += "- Run ID: $runId"
$lines += "- Total: $($summary.total)"
$lines += "- OK: $($summary.ok)"
$lines += "- Failed: $($summary.failed)"
$lines += "- Below minimum: $($summary.below_minimum)"
$lines += "- Output root: $runRoot"
$lines += ""
$lines += "| ID | Status | Strategy | Route | Text | Images | Tables | Quality | Content | Minimum | Archive |"
$lines += "| --- | --- | --- | --- | ---: | ---: | ---: | --- | --- | --- | --- |"
foreach ($result in $results) {
    $minimumText = if ($result.meets_minimum) { "ok" } else { "below" }
    $lines += "| $($result.id) | $($result.status) | $($result.strategy) | $($result.route) | $($result.text_chars) | $($result.image_count) | $($result.table_count) | $($result.quality_status) | $($result.content_completeness_status) | $minimumText | `$($result.archive_path)` |"
}
$lines += ""
$lines += "This smoke runner is opt-in. It may require logged-in local browser state for private/member content and must not be treated as a default unit test."
$lines | Set-Content -LiteralPath $summaryMdPath -Encoding UTF8

Write-Output "Smoke summary JSON: $summaryJsonPath"
Write-Output "Smoke summary Markdown: $summaryMdPath"
if ($summary.failed -gt 0) {
    exit 2
}
if ($summary.below_minimum -gt 0) {
    exit 1
}
exit 0
