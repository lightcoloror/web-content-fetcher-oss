[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Candidate,
    [ValidateSet("All", "NonzeroJsonOk")]
    [string]$Case = "All"
)
$ErrorActionPreference = "Stop"

$script:stubExit = 0
$script:stubJson = '{"status":"ok","transport":"direct","extraction_quality":{"status":"ok","text_chars":100},"archive":{"archive_path":""},"errors":[]}'
$script:stubThrow = $false
$script:recordedArgs = $null

$candText = [System.IO.File]::ReadAllText($Candidate, [System.Text.Encoding]::UTF8)

function python {
    param([Parameter(ValueFromRemainingArguments = $true)]$PyArgs)
    $script:recordedArgs = $PyArgs
    if ($script:stubThrow) { throw "Synthetic Python error" }
    Set-Variable -Name LASTEXITCODE -Value $script:stubExit -Scope 1
    return $script:stubJson
}
function New-Item { param([Parameter(ValueFromRemainingArguments = $true)]$Args) }
function Test-Path { param([Parameter(ValueFromRemainingArguments = $true)]$Args) return $false }
function Get-Content { param([Parameter(ValueFromRemainingArguments = $true)]$Args) throw "Get-Content called unexpectedly" }

foreach ($fn in @("python", "New-Item", "Test-Path", "Get-Content")) {
    $cmd = Get-Command -Name $fn -CommandType Function -ErrorAction Stop
    if ($cmd.CommandType -ne [System.Management.Automation.CommandTypes]::Function) { throw "Stub $fn is not a Function" }
}

try {
    $tokens = $null; $candErrors = $null
    $candAst = [System.Management.Automation.Language.Parser]::ParseInput($candText, [ref]$tokens, [ref]$candErrors)
    if ($candErrors.Count -gt 0) { throw "Candidate parse error: $($candErrors[0].Message)" }

    $foreachAst = ($candAst.EndBlock.Statements | Where-Object { $_ -is [System.Management.Automation.Language.ForEachStatementAst] }) | Select-Object -First 1
    if ($null -eq $foreachAst) { throw "Missing ForEachStatementAst" }
    $foreachBodyStatements = ($foreachAst.Body.Statements | ForEach-Object { $_.Extent.Text }) -join "`n"

    $summaryAssign = ($candAst.EndBlock.Statements | Where-Object { $_ -is [System.Management.Automation.Language.AssignmentStatementAst] -and $_.Left.Extent.Text.Trim() -eq '$summary' }) | Select-Object -First 1
    if ($null -eq $summaryAssign) { throw "Missing summary assignment" }
    $summaryText = $summaryAssign.Extent.Text

    $allow = @("python", "New-Item", "Test-Path", "Get-Content", "Join-Path", "Get-Date", "Out-Null", "Out-String", "ConvertFrom-Json", "Where-Object")
    $cmds = @()
    $cmds += $foreachAst.Body.FindAll({ $args[0] -is [System.Management.Automation.Language.CommandAst] }, $true)
    $cmds += $summaryAssign.FindAll({ $args[0] -is [System.Management.Automation.Language.CommandAst] }, $true)
    foreach ($c in $cmds) {
        $n = $c.GetCommandName()
        if (-not $n) { throw "Dynamic command name detected in candidate extracted AST" }
        if ($n -notin $allow) { throw "Disallowed command '$n' detected in candidate extracted AST" }
    }

    $bodyBlock = [scriptblock]::Create($foreachBodyStatements)
    $sumBlock = [scriptblock]::Create($summaryText)

    function Run-Case([int]$Exit, [string]$Json, [bool]$Throws = $false, [object]$ItemOverride = $null, [bool]$Feishu = $false, [bool]$HeadlessMode = $false) {
        $script:stubExit = $Exit; $script:stubJson = $Json; $script:stubThrow = $Throws
        $runRoot = "C:\syn\out"; $TimeoutSeconds = 90; $Backend = "auto"
        $CompareFeishuExport = $Feishu; $Headless = $HeadlessMode
        $runId = "20261110-120000"; $CorpusPath = "C:\syn\c.json"; $results = @()
        $item = if ($null -ne $ItemOverride) { $ItemOverride } else { [pscustomobject]@{ id = "s1"; category = "t"; url = "https://example.invalid/synthetic"; expected_strategy = "d"; expected_route = "h"; minimum = $null } }

        . $bodyBlock
        . $sumBlock

        return [pscustomobject]@{ Result = $results[0]; Summary = $summary; RecordedArgs = $script:recordedArgs }
    }

    function Test-NonzeroJsonOk {
        $nz = Run-Case -Exit 7 -Json '{"status":"ok","transport":"direct","extraction_quality":{"status":"ok","text_chars":100},"archive":{"archive_path":""},"errors":[]}'
        if ($nz.Result.status -ne "failed") { throw "Nonzero exit with status:ok must force item status to failed" }
        if ($nz.Summary.failed -ne 1) { throw "Nonzero exit with status:ok must increment summary.failed" }
        if (@($nz.Result.errors | Where-Object { $_ -match "process_exit_code:7" }).Count -eq 0) { throw "Result errors must contain process_exit_code:7" }
    }

    Test-NonzeroJsonOk
    $checks = [System.Collections.Generic.List[string]]::new()
    $checks.Add("NonzeroJsonOk")

    if ($Case -eq "All") {
        # 1. exit0 + JSONok
        $c1 = Run-Case -Exit 0 -Json '{"status":"ok","transport":"direct","extraction_quality":{"status":"ok","text_chars":100},"archive":{"archive_path":""},"errors":[]}'
        if ($c1.Result.status -ne "ok" -or $c1.Summary.failed -ne 0) { throw "exit0 JSONok failed" }
        if ($c1.RecordedArgs[0] -ne "-m" -or $c1.RecordedArgs[1] -ne "web_content_fetcher.cli" -or $c1.RecordedArgs[2] -ne "fetch-doc" -or $c1.RecordedArgs[3] -ne "https://example.invalid/synthetic") {
            throw "Native CLI arguments sequence mismatch in base invocation"
        }
        $checks.Add("ZeroJsonOk")

        # 2. exit0 + JSONfailed
        $c2 = Run-Case -Exit 0 -Json '{"status":"failed","transport":"direct","extraction_quality":{"status":"failed","text_chars":0},"archive":{"archive_path":""},"errors":["downstream_failure"]}'
        if ($c2.Result.status -ne "failed" -or $c2.Summary.failed -ne 1) { throw "exit0 JSONfailed failed" }
        $checks.Add("JsonFailed")

        # 3. malformedJSON
        $c3 = Run-Case -Exit 0 -Json 'INVALID_JSON{{'
        if ($c3.Result.status -ne "failed" -or $c3.Summary.failed -ne 1 -or @($c3.Result.errors | Where-Object { $_ -match "summary_parse_error" }).Count -eq 0) { throw "malformed JSON failed" }
        $checks.Add("MalformedJson")

        # 4. python throws
        $c4 = Run-Case -Exit 1 -Json '' -Throws $true
        if ($c4.Result.status -ne "failed" -or $c4.Summary.failed -ne 1) { throw "python throws failed" }
        $checks.Add("PythonThrows")

        # 5. minimum text threshold not met
        $minItem = [pscustomobject]@{ id = "s2"; category = "t"; url = "https://example.invalid/synthetic"; expected_strategy = "d"; expected_route = "h"; minimum = [pscustomobject]@{ text_chars = 200 } }
        $c5 = Run-Case -Exit 0 -Json '{"status":"ok","transport":"direct","extraction_quality":{"status":"ok","text_chars":50},"archive":{"archive_path":""},"errors":[]}' -Throws $false -ItemOverride $minItem
        if ($c5.Result.status -ne "ok" -or $c5.Result.meets_minimum -ne $false -or $c5.Summary.below_minimum -ne 1 -or $c5.Summary.failed -ne 0) { throw "below minimum failed" }
        $checks.Add("BelowMinimum")

        # 6. Flag forwarding CompareFeishuExport / Headless
        $c6 = Run-Case -Exit 0 -Json '{"status":"ok","transport":"direct","extraction_quality":{"status":"ok","text_chars":100},"archive":{"archive_path":""},"errors":[]}' -Feishu $true -HeadlessMode $true
        if ($c6.RecordedArgs -notcontains "--compare-feishu-export" -or $c6.RecordedArgs -notcontains "--headless") {
            throw "Flag forwarding failed: expected --compare-feishu-export and --headless in recorded args"
        }
        $checks.Add("FlagForwarding")

        # 7. Static attribute validation on parameters
        function Assert-ParamAttr($pName) {
            $p = $candAst.ParamBlock.Parameters | Where-Object { $_.Name.VariablePath.UserPath -eq $pName }
            if ($null -eq $p) { throw "Parameter $pName missing" }
            $hasMandatory = $false; $hasNotNull = $false
            foreach ($attr in $p.Attributes) {
                if ($attr.TypeName.Name -in @("Parameter", "ParameterAttribute")) {
                    foreach ($arg in $attr.NamedArguments) {
                        if ($arg.ArgumentName -eq "Mandatory" -and ($arg.Argument.Extent.Text -in @('$true', 'true'))) { $hasMandatory = $true }
                    }
                }
                if ($attr.TypeName.Name -in @("ValidateNotNullOrEmpty", "ValidateNotNullOrEmptyAttribute")) { $hasNotNull = $true }
            }
            if (-not $hasMandatory -or -not $hasNotNull) { throw "Parameter $pName missing mandatory or ValidateNotNullOrEmpty attribute" }
        }
        Assert-ParamAttr "CorpusPath"
        Assert-ParamAttr "OutputRoot"
        $checks.Add("ParamAttributes")

        # 8. Limit branch structural check
        $limitIf = ($candAst.EndBlock.Statements | Where-Object { $_ -is [System.Management.Automation.Language.IfStatementAst] -and $_.Clauses[0].Item1.Extent.Text -match '\$Limit' }) | Select-Object -First 1
        if ($null -eq $limitIf -or $limitIf.Clauses[0].Item1.Extent.Text -notmatch '\$Limit\s*-gt\s*0') { throw "Limit branch condition modified" }
        if ($limitIf.Clauses[0].Item2.Extent.Text -notmatch 'Select-Object\s+-First\s+\$Limit') { throw "Limit branch selection modified" }
        $checks.Add("LimitBranch")

        # 9. Final exit statements & condition AST evaluations
        $ifFailed = ($candAst.EndBlock.Statements | Where-Object { $_ -is [System.Management.Automation.Language.IfStatementAst] -and $_.Clauses[0].Item1.Extent.Text -match '\$summary\.failed' }) | Select-Object -First 1
        $ifBelow = ($candAst.EndBlock.Statements | Where-Object { $_ -is [System.Management.Automation.Language.IfStatementAst] -and $_.Clauses[0].Item1.Extent.Text -match '\$summary\.below_minimum' }) | Select-Object -First 1
        $finalExit = ($candAst.EndBlock.Statements | Where-Object { $_ -is [System.Management.Automation.Language.ExitStatementAst] }) | Select-Object -Last 1

        if ($null -eq $ifFailed -or $null -eq $ifBelow -or $null -eq $finalExit) { throw "Missing final exit if statements or terminal exit" }
        if ($ifFailed.Clauses[0].Item2.Extent.Text -notmatch 'exit\s+2') { throw "summary.failed exit code must be 2" }
        if ($ifBelow.Clauses[0].Item2.Extent.Text -notmatch 'exit\s+1') { throw "summary.below_minimum exit code must be 1" }
        if ($finalExit.Extent.Text -notmatch 'exit\s+0') { throw "Terminal exit code must be 0" }

        $cond1 = [scriptblock]::Create($ifFailed.Clauses[0].Item1.Extent.Text)
        $cond2 = [scriptblock]::Create($ifBelow.Clauses[0].Item1.Extent.Text)

        $summary = [pscustomobject]@{ failed = 1; below_minimum = 0 }
        if (-not (& $cond1)) { throw "Exit condition 1 failed for summary.failed > 0" }

        $summary = [pscustomobject]@{ failed = 0; below_minimum = 1 }
        if ((& $cond1) -or -not (& $cond2)) { throw "Exit condition 2 failed for summary.below_minimum > 0" }

        $summary = [pscustomobject]@{ failed = 0; below_minimum = 0 }
        if ((& $cond1) -or (& $cond2)) { throw "Exit condition evaluation failed for clean summary" }
        $checks.Add("ExitPolicyAst")
    }

    Write-Host ([pscustomobject]@{ status = "passed"; case = $Case; checks = $checks; count = $checks.Count } | ConvertTo-Json -Compress)
    exit 0
} catch {
    Write-Host "TEST_GUARD_FAIL: $($_.Exception.Message)"
    exit 1
}
