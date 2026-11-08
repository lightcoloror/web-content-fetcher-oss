# Source-checkout launcher. No install, profile, proxy or security changes.
[CmdletBinding()]
param(
    [string]$PythonExecutable = 'python',
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$UiArgs = @()
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$previousLocation = Get-Location
$hadPythonPath = Test-Path -LiteralPath Env:PYTHONPATH
$previousPythonPath = $env:PYTHONPATH
$nativeExit = $null
try {
    Set-Location -LiteralPath $repo
    $env:PYTHONPATH = $repo
    $pythonCommand = Get-Command -Name $PythonExecutable -CommandType Application -ErrorAction Stop | Select-Object -First 1
    $global:LASTEXITCODE = $null
    & $pythonCommand.Source -m web_content_fetcher.ui @UiArgs
    $nativeExit = $global:LASTEXITCODE
    if ($null -eq $nativeExit) {
        throw 'Python did not report a native exit code; refusing stale success.'
    }
}
finally {
    Set-Location -LiteralPath $previousLocation.Path
    if ($hadPythonPath) {
        $env:PYTHONPATH = $previousPythonPath
    }
    else {
        Remove-Item -LiteralPath Env:PYTHONPATH -ErrorAction SilentlyContinue
    }
}
exit ([int]$nativeExit)
