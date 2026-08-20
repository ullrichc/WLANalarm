<#
.SYNOPSIS
    Richtet WLANalarm unter Windows als Autostart-Aufgabe ein.

.DESCRIPTION
    Windows kennt kein systemd. Das Gegenstueck ist die Aufgabenplanung:
    Die Aufgabe startet beim Hochfahren des Rechners, laeuft ohne angemeldeten
    Benutzer und startet nach einem Absturz neu.

    Muss in einer PowerShell mit Administratorrechten laufen.

.EXAMPLE
    .\windows-autostart.ps1 -FritzPasswort (Read-Host -AsSecureString)

.EXAMPLE
    .\windows-autostart.ps1 -ProjektPfad D:\Projekte\WLANalarm -FritzPasswort (Read-Host -AsSecureString)
#>
param(
    # Das Verzeichnis, in dem WLANalarm liegt - dasselbe, das setup-windows.ps1
    # verwendet hat. Darin liegen venv und config.yaml.
    [string]$ProjektPfad = "C:\Code\WLAN",

    # Kennwort des FRITZ!Box-Benutzers.
    [Parameter(Mandatory = $true)]
    [SecureString]$FritzPasswort,

    [string]$AufgabenName = "WLANalarm"
)

$ErrorActionPreference = "Stop"

$exe = Join-Path $ProjektPfad "venv\Scripts\wlanalarm.exe"
$config = Join-Path $ProjektPfad "config.yaml"

if (-not (Test-Path $exe)) {
    throw "Nicht gefunden: $exe. Zuerst die virtuelle Umgebung anlegen und 'pip install -e .' ausfuehren."
}
if (-not (Test-Path $config)) {
    throw "Nicht gefunden: $config. Zuerst 'wlanalarm init-config config.yaml' ausfuehren."
}

# Das Kennwort wird als Umgebungsvariable des Rechners hinterlegt, damit es
# nicht in der Konfigurationsdatei steht. Nur Administratoren koennen es lesen.
$klartext = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($FritzPasswort))
[Environment]::SetEnvironmentVariable("FRITZ_PASSWORD", $klartext, "Machine")

$aktion = New-ScheduledTaskAction -Execute $exe `
    -Argument "run -c `"$config`"" -WorkingDirectory $ProjektPfad

$ausloeser = New-ScheduledTaskTrigger -AtStartup

# SYSTEM braucht kein hinterlegtes Kennwort und ueberlebt Kennwortwechsel.
$konto = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest

$einstellungen = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0)

Register-ScheduledTask -TaskName $AufgabenName -Action $aktion -Trigger $ausloeser `
    -Principal $konto -Settings $einstellungen -Force | Out-Null

Write-Host "Aufgabe '$AufgabenName' eingerichtet." -ForegroundColor Green
Write-Host "Starten:  Start-ScheduledTask -TaskName $AufgabenName"
Write-Host "Zustand:  Get-ScheduledTask -TaskName $AufgabenName"
Write-Host "Entfernen: Unregister-ScheduledTask -TaskName $AufgabenName"
Write-Host ""
Write-Host "Dashboard nach dem Start: http://127.0.0.1:8723/"
