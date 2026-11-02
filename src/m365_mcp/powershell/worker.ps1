#!/usr/bin/env pwsh
# Governed, one-shot PowerShell worker. It accepts JSON over stdin only and
# executes one catalog-declared cmdlet. No caller-supplied script is evaluated.
Set-StrictMode -Version 3.0
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$WarningPreference = 'SilentlyContinue'
$InformationPreference = 'SilentlyContinue'
$VerbosePreference = 'SilentlyContinue'
$DebugPreference = 'SilentlyContinue'
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$script:ResultPrefix = '__M365_RESULT__'
$script:MaxOutputBytes = 2000000
if ($env:M365_PS_MAX_OUTPUT_BYTES) {
    $script:MaxOutputBytes = [int]$env:M365_PS_MAX_OUTPUT_BYTES
}

function Write-Envelope {
    param(
        [Parameter(Mandatory = $true)][bool]$Ok,
        [object]$Result = $null,
        [string]$Code = '',
        [string]$Message = ''
    )
    $envelope = [ordered]@{ ok = $Ok }
    if ($Ok) {
        $envelope.result = @($Result)
    }
    else {
        $envelope.error = [ordered]@{ code = $Code; message = $Message }
    }
    $json = $envelope | ConvertTo-Json -Depth 12 -Compress
    [Console]::Out.WriteLine($script:ResultPrefix + $json)
}

function Get-CertificatePassword {
    if (-not $env:M365_CERTIFICATE_PASSWORD) {
        return $null
    }
    return ConvertTo-SecureString -String $env:M365_CERTIFICATE_PASSWORD -AsPlainText -Force
}

function Connect-M365Service {
    param([Parameter(Mandatory = $true)][string]$Service)

    $certificatePath = $env:M365_CERTIFICATE_PATH
    if (-not $certificatePath -or -not (Test-Path -LiteralPath $certificatePath -PathType Leaf)) {
        throw "Certificate file is unavailable for service '$Service'."
    }
    $certificatePassword = Get-CertificatePassword

    switch ($Service) {
        'exchange' {
            Import-Module ExchangeOnlineManagement -ErrorAction Stop
            $arguments = @{
                AppId               = $env:M365_CLIENT_ID
                Organization        = $env:M365_ORGANIZATION
                CertificateFilePath = $certificatePath
                ShowBanner          = $false
                ShowProgress        = $false
                ErrorAction         = 'Stop'
            }
            if ($certificatePassword) {
                $arguments.CertificatePassword = $certificatePassword
            }
            $null = Connect-ExchangeOnline @arguments
        }
        'compliance' {
            Import-Module ExchangeOnlineManagement -ErrorAction Stop
            $arguments = @{
                AppId               = $env:M365_CLIENT_ID
                Organization        = $env:M365_ORGANIZATION
                CertificateFilePath = $certificatePath
                ShowBanner          = $false
                ErrorAction         = 'Stop'
            }
            if ($certificatePassword) {
                $arguments.CertificatePassword = $certificatePassword
            }
            $null = Connect-IPPSSession @arguments
        }
        'teams' {
            Import-Module MicrosoftTeams -ErrorAction Stop
            if (-not $certificatePassword) {
                throw 'Teams certificate authentication requires M365_CERTIFICATE_PASSWORD.'
            }
            $certificate = [System.Security.Cryptography.X509Certificates.X509Certificate2]::new(
                $certificatePath,
                $certificatePassword
            )
            $null = Connect-MicrosoftTeams `
                -Certificate $certificate `
                -ApplicationId $env:M365_CLIENT_ID `
                -TenantId $env:M365_TENANT_ID `
                -ErrorAction Stop
        }
        'sharepoint' {
            Import-Module PnP.PowerShell -ErrorAction Stop
            if (-not $env:M365_SHAREPOINT_ADMIN_URL) {
                throw 'M365_SHAREPOINT_ADMIN_URL is required for SharePoint actions.'
            }
            if (-not $certificatePassword) {
                throw 'SharePoint certificate authentication requires M365_CERTIFICATE_PASSWORD.'
            }
            $arguments = @{
                Url                 = $env:M365_SHAREPOINT_ADMIN_URL
                ClientId            = $env:M365_CLIENT_ID
                Tenant              = $env:M365_TENANT_ID
                CertificatePath     = $certificatePath
                CertificatePassword = $certificatePassword
                ErrorAction         = 'Stop'
            }
            $null = Connect-PnPOnline @arguments
        }
        default {
            throw "Unsupported service '$Service'."
        }
    }
}

try {
    $rawRequest = [Console]::In.ReadToEnd()
    if ([string]::IsNullOrWhiteSpace($rawRequest)) {
        throw 'No JSON request was supplied on stdin.'
    }
    $request = $rawRequest | ConvertFrom-Json -Depth 10
    if (-not $request.action) {
        throw 'Request is missing action.'
    }
    $inventoryPath = $env:M365_PS_INVENTORY_PATH
    if (-not $inventoryPath) {
        $inventoryPath = Join-Path $PSScriptRoot '../catalogs/powershell.json'
    }
    if (-not (Test-Path -LiteralPath $inventoryPath -PathType Leaf)) {
        throw 'PowerShell action inventory is unavailable.'
    }
    $inventory = Get-Content -LiteralPath $inventoryPath -Raw | ConvertFrom-Json -Depth 20
    $action = @($inventory | Where-Object { $_.name -eq $request.action }) | Select-Object -First 1
    if (-not $action) {
        throw 'Action is not present in the PowerShell inventory.'
    }
    if ($action.implementation_status -ne 'implemented') {
        throw 'Action implementation is not enabled in the PowerShell inventory.'
    }
    if ($action.backend -ne 'powershell' -or -not $action.powershell) {
        throw 'Action has no PowerShell command binding.'
    }

    $binding = $action.powershell
    $service = [string]$binding.service
    $cmdlet = [string]$binding.cmdlet
    $declaredParameters = @(
        if ($binding.parameters) {
            $binding.parameters.PSObject.Properties | ForEach-Object { [string]$_.Value }
        }
    )
    $parameters = @{}
    if ($request.parameters) {
        foreach ($property in $request.parameters.PSObject.Properties) {
            if ($declaredParameters -notcontains $property.Name) {
                throw "Undeclared PowerShell parameter '$($property.Name)'."
            }
            if ($null -ne $property.Value) {
                $parameters[$property.Name] = $property.Value
            }
        }
    }
    if ($binding.constant_parameters) {
        foreach ($property in $binding.constant_parameters.PSObject.Properties) {
            if ($parameters.ContainsKey($property.Name)) {
                throw "Constant parameter '$($property.Name)' cannot be overridden."
            }
            $parameters[$property.Name] = $property.Value
        }
    }

    $command = Get-Command -Name $cmdlet -ErrorAction Stop
    if ($command.CommandType -notin @('Cmdlet', 'Function')) {
        throw "Resolved command '$cmdlet' has an unsupported command type."
    }

    Connect-M365Service -Service $service
    $result = & $cmdlet @parameters
    $json = [ordered]@{ ok = $true; result = @($result) } | ConvertTo-Json -Depth 12 -Compress
    if ($json.Length -gt $script:MaxOutputBytes) {
        Write-Envelope -Ok $false -Code 'powershell_result_too_large' `
            -Message 'The PowerShell result exceeded the configured output limit; narrow the query.'
    }
    else {
        [Console]::Out.WriteLine($script:ResultPrefix + $json)
    }
}
catch {
    $message = $_.Exception.Message
    if ($message.Length -gt 1500) {
        $message = $message.Substring(0, 1500)
    }
    Write-Envelope -Ok $false -Code 'powershell_command_failed' -Message $message
    exit 0
}
