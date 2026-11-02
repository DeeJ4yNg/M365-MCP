#!/usr/bin/env pwsh
<#
.SYNOPSIS
Validates PowerShell catalog cmdlets and parameter names against locally installed modules.

.DESCRIPTION
This checker does not connect to Microsoft 365. It verifies syntax and module metadata
for commands that PowerShell exports without a tenant session. Commands that only appear
after a provider connection are reported as unverified rather than treated as passing.
#>
[CmdletBinding()]
param(
    [string]$CatalogPath = (Join-Path $PSScriptRoot '../src/m365_mcp/catalogs/powershell.json'),
    [string]$ModulePath = ''
)

Set-StrictMode -Version 3.0
$ErrorActionPreference = 'Stop'

if ($ModulePath) {
    $env:PSModulePath = "$ModulePath$([IO.Path]::PathSeparator)$env:PSModulePath"
}

$worker = Join-Path $PSScriptRoot '../src/m365_mcp/powershell/worker.ps1'
$tokens = $null
$parseErrors = $null
$null = [System.Management.Automation.Language.Parser]::ParseFile(
    (Resolve-Path $worker),
    [ref]$tokens,
    [ref]$parseErrors
)
if ($parseErrors.Count -gt 0) {
    $parseErrors | ForEach-Object { Write-Error $_.Message }
    exit 1
}

Import-Module ExchangeOnlineManagement -ErrorAction Stop
Import-Module MicrosoftTeams -ErrorAction Stop
Import-Module PnP.PowerShell -ErrorAction Stop

$catalog = Get-Content -LiteralPath $CatalogPath -Raw | ConvertFrom-Json -Depth 30
$errors = [System.Collections.Generic.List[string]]::new()
$unverified = [System.Collections.Generic.List[string]]::new()
$checked = 0

foreach ($action in $catalog) {
    if ($action.implementation_status -ne 'implemented') {
        continue
    }
    $binding = $action.powershell
    if (-not $binding) {
        $errors.Add("$($action.name): missing powershell binding")
        continue
    }
    $command = Get-Command -Name $binding.cmdlet -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $command) {
        $unverified.Add("$($action.name):$($binding.cmdlet)")
        continue
    }
    $checked++
    $mapped = @()
    if ($binding.PSObject.Properties.Name -contains 'parameters') {
        foreach ($property in $binding.parameters.PSObject.Properties) {
            $mapped += $property.Value
        }
    }
    if ($binding.PSObject.Properties.Name -contains 'constant_parameters') {
        foreach ($property in $binding.constant_parameters.PSObject.Properties) {
            $mapped += $property.Name
        }
    }
    foreach ($parameterName in $mapped) {
        if (-not $command.Parameters.ContainsKey($parameterName)) {
            $errors.Add(
                "$($action.name): $($binding.cmdlet) has no parameter '$parameterName'"
            )
        }
    }
}

$result = [ordered]@{
    status = if ($errors.Count -eq 0) { 'ok' } else { 'failed' }
    workerSyntax = 'ok'
    localCommandsChecked = $checked
    unverifiedRemoteCommands = $unverified.Count
    errors = @($errors)
    unverified = @($unverified)
}
$result | ConvertTo-Json -Depth 5
if ($errors.Count -gt 0) {
    exit 1
}
