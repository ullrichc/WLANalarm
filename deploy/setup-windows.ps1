<#
.SYNOPSIS
    Richtet WLANalarm unter Windows in einem Rutsch ein.

.DESCRIPTION
    Holt das Projekt von GitHub, legt eine virtuelle Python-Umgebung an,
    installiert alles Noetige, prueft die Installation mit der Testsuite und
    erzeugt eine Konfigurationsvorlage.

    Laeuft in einer normalen PowerShell, Administratorrechte sind nicht noetig.
    Ein erneuter Aufruf im selben Verzeichnis aktualisiert die Installation,
    ohne die eigene config.yaml anzufassen.

.PARAMETER Zielpfad
    Wohin das Projekt soll. Vorgabe: C:\Code\WLAN

.PARAMETER OhneTests
    Die Testsuite ueberspringen (spart etwa eine halbe Minute).

.EXAMPLE
    .\setup-windows.ps1

.EXAMPLE
    .\setup-windows.ps1 -Zielpfad D:\Projekte\WLANalarm
#>
[CmdletBinding()]
param(
    [string]$Zielpfad = "C:\Code\WLAN",
    [switch]$OhneTests
)

$ErrorActionPreference = "Stop"
$RepoUrl = "https://github.com/ullrichc/WLANalarm.git"

function Schritt($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }
function Erfolg($text)  { Write-Host "    $text" -ForegroundColor Green }
function Hinweis($text) { Write-Host "    $text" -ForegroundColor Yellow }

function Ausfuehren {
    <#
      Externe Programme loesen in PowerShell kein abbrechendes Fehlerereignis
      aus - ein fehlgeschlagenes git oder pip liefe sonst stillschweigend
      durch. Deshalb wird der Rueckgabewert hier ausdruecklich geprueft.
    #>
    param([Parameter(Mandatory)][string]$Datei,
          [string[]]$Argumente = @(),
          [string]$Fehlertext)
    & $Datei @Argumente
    if ($LASTEXITCODE -ne 0) {
        if ($Fehlertext) { throw $Fehlertext }
        throw "$Datei $($Argumente -join ' ') ist fehlgeschlagen (Code $LASTEXITCODE)."
    }
}

# --- Voraussetzungen ------------------------------------------------------ #

Schritt "Voraussetzungen pruefen"

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "Git fehlt. Installieren mit:  winget install --id Git.Git`n" +
          "Danach ein neues PowerShell-Fenster oeffnen und dieses Skript erneut starten."
}
Erfolg "git: $((git --version) -replace '^git version ')"

# 'py' ist der offizielle Starter von python.org; 'python' kommt aus dem Store.
$python = $null
foreach ($kandidat in @("py", "python")) {
    $befehl = Get-Command $kandidat -ErrorAction SilentlyContinue
    if (-not $befehl) { continue }
    # Die Store-Platzhalter unter WindowsApps sind keine echte Installation.
    if ($befehl.Source -like "*\WindowsApps\*" -and -not (& $kandidat --version 2>$null)) { continue }
    $python = $kandidat
    break
}
if (-not $python) {
    throw "Python fehlt. Installieren mit:  winget install --id Python.Python.3.12`n" +
          "Danach ein neues PowerShell-Fenster oeffnen und dieses Skript erneut starten."
}

$version = (& $python --version 2>&1) -replace '^Python\s+'
$teile = $version.Split('.')
if ([int]$teile[0] -lt 3 -or ([int]$teile[0] -eq 3 -and [int]$teile[1] -lt 11)) {
    throw "Python $version ist zu alt, benoetigt wird 3.11 oder neuer."
}
Erfolg "python: $version (Aufruf '$python')"

# --- Projekt holen -------------------------------------------------------- #

Schritt "Projekt nach $Zielpfad holen"

if (Test-Path (Join-Path $Zielpfad ".git")) {
    Push-Location $Zielpfad
    try {
        Ausfuehren git @("fetch", "origin", "main")
        Ausfuehren git @("checkout", "main") `
            -Fehlertext "Wechsel auf main fehlgeschlagen - vermutlich liegen eigene, ungesicherte Aenderungen vor."
        Ausfuehren git @("pull", "--ff-only", "origin", "main")
        Erfolg "vorhandene Installation aktualisiert"
    } finally { Pop-Location }
} elseif ((Test-Path $Zielpfad) -and (Get-ChildItem $Zielpfad -Force | Select-Object -First 1)) {
    throw "$Zielpfad existiert bereits und ist nicht leer, enthaelt aber kein Git-Projekt. " +
          "Bitte ein anderes Verzeichnis waehlen (-Zielpfad) oder das vorhandene leeren."
} else {
    $eltern = Split-Path $Zielpfad -Parent
    if ($eltern -and -not (Test-Path $eltern)) { New-Item -ItemType Directory -Path $eltern -Force | Out-Null }
    Ausfuehren git @("clone", $RepoUrl, $Zielpfad)
    Erfolg "geklont"
}

Set-Location $Zielpfad

# --- Virtuelle Umgebung --------------------------------------------------- #

Schritt "Virtuelle Python-Umgebung einrichten"

$venvPython = Join-Path $Zielpfad "venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Ausfuehren $python @("-m", "venv", "venv") `
        -Fehlertext "Die virtuelle Umgebung liess sich nicht anlegen."
    Erfolg "venv angelegt"
} else {
    Erfolg "venv vorhanden"
}

& $venvPython -m pip install --quiet --upgrade pip
& $venvPython -m pip install --quiet -e ".[mqtt,dev]"
if ($LASTEXITCODE -ne 0) { throw "Installation der Pakete fehlgeschlagen." }
Erfolg "WLANalarm und Abhaengigkeiten installiert"

# --- Selbsttest ----------------------------------------------------------- #

if (-not $OhneTests) {
    Schritt "Testsuite ausfuehren (belegt, dass alles laeuft - ohne FRITZ!Box)"
    & $venvPython -m pytest -q
    if ($LASTEXITCODE -ne 0) {
        Hinweis "Es sind Tests fehlgeschlagen. Bitte die Ausgabe oben melden."
    } else {
        Erfolg "alle Tests bestanden"
    }
}

# --- Konfiguration -------------------------------------------------------- #

Schritt "Konfiguration vorbereiten"

$wlanalarm = Join-Path $Zielpfad "venv\Scripts\wlanalarm.exe"
$config = Join-Path $Zielpfad "config.yaml"

if (Test-Path $config) {
    Erfolg "config.yaml ist bereits vorhanden und bleibt unveraendert"
} else {
    & $wlanalarm init-config $config | Out-Null
    Erfolg "config.yaml aus der Vorlage angelegt"
}

# --- Wie es weitergeht ---------------------------------------------------- #

Write-Host "`n" -NoNewline
Write-Host "Fertig. " -ForegroundColor Green -NoNewline
Write-Host "WLANalarm liegt in $Zielpfad"
Write-Host @"

Naechste Schritte:

 1. In der FRITZ!Box einen Benutzer anlegen
      System > FRITZ!Box-Benutzer > Benutzer hinzufuegen
      Berechtigung "FRITZ!Box Einstellungen" anhaken
    und den Anwendungszugriff freigeben
      Heimnetz > Netzwerk > Netzwerkeinstellungen
      "Zugriff fuer Anwendungen zulassen"

 2. Benutzernamen in die Konfiguration eintragen:
      notepad $config

 3. Kennwort hinterlegen (gilt fuer das aktuelle Fenster):
      `$env:FRITZ_PASSWORD = 'IhrKennwort'

 4. Verbindung pruefen und loslegen:
      cd $Zielpfad
      .\venv\Scripts\Activate.ps1
      wlanalarm check
      wlanalarm discover
      wlanalarm calibrate --minutes 15    # dabei die Wohnung verlassen
      wlanalarm run                       # Dashboard: http://127.0.0.1:8723/

Ausfuehrliche Anleitung: $Zielpfad\README.md
"@
