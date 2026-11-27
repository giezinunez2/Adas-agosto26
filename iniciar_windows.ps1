#Requires -Version 5.1
<#
.SYNOPSIS
    Arranque portable del ADAS Cluster HMI en Windows 10/11 (HMI nativo + nodos Edge).

.DESCRIPTION
    Deja el PC Windows listo y arranca adas_dashboard.py sin Docker local:

      1. Comprueba Python 3.10+ (64 bits) y crea/reutiliza el entorno virtual .venv.
      2. Instala requirements-windows.txt.
      3. Verifica el binding de eCAL 5.12 y deja ECAL_DATA apuntando a ecal_windows.ini.
      4. Revisa/crea las reglas de firewall para UDP 14000 y TCP 14000.
      5. Prueba el acceso SSH sin contraseña a ADASCore y zorros-desk.
      6. Lanza el panel Tkinter (adas_dashboard.py).

    Todas las rutas se resuelven con $PSScriptRoot: el repositorio puede estar en
    cualquier disco o carpeta (C:\..., D:\..., E:\...) sin tocar el script.

.PARAMETER CoreUser
    Usuario SSH de ADASCore (por defecto core).

.PARAMETER CoreIp
    IP de ADASCore (por defecto 192.168.50.61).

.PARAMETER DeskUser
    Usuario SSH de zorros-desk (por defecto zorro).

.PARAMETER DeskIp
    IP de zorros-desk (por defecto 192.168.50.32).

.PARAMETER SkipInstall
    No crea el venv ni instala dependencias (arranque rápido).

.PARAMETER SkipFirewall
    No revisa ni crea reglas de firewall.

.PARAMETER SkipSshCheck
    No prueba la conectividad SSH con los nodos Edge.

.PARAMETER SkipEcalCheck
    Arranca aunque el binding ecal5 no esté instalado.

.EXAMPLE
    .\iniciar_windows.ps1

.EXAMPLE
    .\iniciar_windows.ps1 -SkipInstall -SkipFirewall -CoreIp 192.168.50.61

.NOTES
    Si PowerShell bloquea el script, ejecuta en la misma ventana:
        Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#>
[CmdletBinding()]
param(
    [string]$CoreUser = "core",
    [string]$CoreIp = "192.168.50.61",
    [string]$DeskUser = "zorro",
    [string]$DeskIp = "192.168.50.32",
    [switch]$SkipInstall,
    [switch]$SkipFirewall,
    [switch]$SkipSshCheck,
    [switch]$SkipEcalCheck
)

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot
Set-Location -Path $ProjectRoot

function Write-Step { param([string]$Text) Write-Host ""; Write-Host "==> $Text" -ForegroundColor Cyan }
function Write-Ok { param([string]$Text) Write-Host "  [OK] $Text" -ForegroundColor Green }
function Write-Info { param([string]$Text) Write-Host "  [i] $Text" -ForegroundColor Gray }
function Write-Warn2 { param([string]$Text) Write-Host "  [!] $Text" -ForegroundColor Yellow }
function Write-Fail { param([string]$Text) Write-Host "  [X] $Text" -ForegroundColor Red }

function Get-SystemPython {
    <# Devuelve @{ Path; Prefix; Version } del primer Python 3.10+ disponible, o $null. #>
    foreach ($name in @("py", "python")) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        $prefix = @()
        if ($name -eq "py") { $prefix = @("-3") }
        $raw = (& $cmd.Source @prefix -c "import sys; print('%d.%d.%d' % sys.version_info[:3])" 2>$null) -join ""
        if ($LASTEXITCODE -ne 0 -or -not $raw) { continue }
        $parts = $raw.Split(".")
        if ([int]$parts[0] -eq 3 -and [int]$parts[1] -ge 10) {
            return @{ Path = $cmd.Source; Prefix = $prefix; Version = $raw }
        }
    }
    return $null
}

# UTF-8 en la consola y en los subprocesos: los main_*.py imprimen emojis y sus
# logs se leen desde el panel, así que la codificación debe ser siempre UTF-8.
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

Write-Host "ADAS Cluster - HMI nativo en Windows" -ForegroundColor White
Write-Info "Proyecto: $ProjectRoot"

# ─────────────────────────────────────────────────────────────────────────────
# 1) Python y entorno virtual
# ─────────────────────────────────────────────────────────────────────────────
Write-Step "1/7 Comprobando Python"
$venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $venvPython) -and -not $SkipInstall) {
    $systemPython = Get-SystemPython
    if (-not $systemPython) {
        Write-Fail "No se encontro Python 3.10+ de 64 bits."
        Write-Info "Instalalo desde https://www.python.org/downloads/windows/ (marca 'Add python.exe to PATH')."
        exit 1
    }
    Write-Ok "Python $($systemPython.Version) en $($systemPython.Path)"
    Write-Step "2/7 Creando el entorno virtual .venv"
    $prefix = $systemPython.Prefix
    & $systemPython.Path @prefix -m venv (Join-Path $ProjectRoot ".venv")
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "No se pudo crear el entorno virtual."
        exit 1
    }
}

if (-not (Test-Path $venvPython)) {
    Write-Fail "No existe $venvPython"
    Write-Info "Ejecuta este script sin -SkipInstall para crear el entorno."
    exit 1
}
Write-Ok "Interprete del proyecto: $venvPython"

# ─────────────────────────────────────────────────────────────────────────────
# 2) Dependencias Python
# ─────────────────────────────────────────────────────────────────────────────
$requirements = Join-Path $ProjectRoot "requirements-windows.txt"
if (-not $SkipInstall) {
    if (-not (Test-Path $requirements)) {
        Write-Fail "Falta requirements-windows.txt"
        exit 1
    }
    Write-Step "3/7 Instalando dependencias (requirements-windows.txt)"
    & $venvPython -m pip install --upgrade pip --disable-pip-version-check
    & $venvPython -m pip install -r $requirements --disable-pip-version-check
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "pip install fallo; revisa el log anterior."
        exit 1
    }
    Write-Ok "Dependencias instaladas."
} else {
    Write-Info "Instalacion omitida (-SkipInstall)."
}

# ─────────────────────────────────────────────────────────────────────────────
# 3) eCAL 5.12 + ecal_windows.ini
# ─────────────────────────────────────────────────────────────────────────────
Write-Step "4/7 Comprobando eCAL 5.12 y ecal_windows.ini"
$ecalVersion = (& $venvPython -c "import ecal.core.core as e; print(e.getversion())" 2>$null) -join ""
if ($LASTEXITCODE -eq 0 -and $ecalVersion) {
    Write-Ok "Binding eCAL disponible: $ecalVersion"
} else {
    Write-Warn2 "No se pudo importar ecal.core.core en el venv."
    Write-Info "1) Instala eCAL 5.12 (msi) desde https://github.com/eclipse-ecal/ecal/releases"
    Write-Info "2) Instala el wheel del binding: pip install ecal5-<ver>-<py>-win_amd64.whl"
    Write-Info "   (misma pagina de releases; usa el wheel para tu version de Python y win_amd64)"
    if (-not $SkipEcalCheck) {
        Write-Fail "Sin el binding de eCAL el HMI no puede publicar ni recibir topics."
        Write-Info "Usa -SkipEcalCheck para arrancar la interfaz de todos modos."
        exit 1
    }
}

$ecalIni = Join-Path $ProjectRoot "ecal_windows.ini"
if (-not (Test-Path $ecalIni)) {
    Write-Fail "Falta ecal_windows.ini en $ProjectRoot"
    exit 1
}
$ini = Get-Content $ecalIni -Raw
$expected = @(
    @{ Pattern = "network_enabled\s*=\s*true"; Label = "network_enabled = true" },
    @{ Pattern = "multicast_group\s*=\s*239\.0\.0\.1"; Label = "multicast_group = 239.0.0.1" },
    @{ Pattern = "multicast_port\s*=\s*14000"; Label = "multicast_port = 14000" },
    @{ Pattern = "use_tcp\s*=\s*1"; Label = "use_tcp = 1" }
)
foreach ($item in $expected) {
    if ($ini -match $item.Pattern) { Write-Ok "ecal_windows.ini: $($item.Label)" }
    else { Write-Warn2 "ecal_windows.ini no declara '$($item.Label)'" }
}

# eCAL 5.12 lee %ECAL_DATA%\ecal.ini antes que %ProgramData%\eCAL\ecal.ini.
$ecalData = Join-Path $env:LOCALAPPDATA "ADAS\ecal"
New-Item -ItemType Directory -Force -Path $ecalData | Out-Null
Copy-Item -Path $ecalIni -Destination (Join-Path $ecalData "ecal.ini") -Force
$env:ECAL_DATA = $ecalData
Write-Ok "ECAL_DATA = $ecalData"

# ─────────────────────────────────────────────────────────────────────────────
# 4) Firewall (UDP 14000 multicast + TCP 14000 payload)
# ─────────────────────────────────────────────────────────────────────────────
Write-Step "5/7 Reglas de firewall (UDP 14000 + TCP 14000)"
if ($SkipFirewall) {
    Write-Info "Omitido (-SkipFirewall)."
} else {
    try {
        $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
        $rules = @(
            @{ Name = "ADAS eCAL UDP 14000 (in)"; Protocol = "UDP"; Direction = "Inbound" },
            @{ Name = "ADAS eCAL UDP 14000 (out)"; Protocol = "UDP"; Direction = "Outbound" },
            @{ Name = "ADAS eCAL TCP 14000 (in)"; Protocol = "TCP"; Direction = "Inbound" },
            @{ Name = "ADAS eCAL TCP 14000 (out)"; Protocol = "TCP"; Direction = "Outbound" }
        )
        foreach ($rule in $rules) {
            $existing = Get-NetFirewallRule -DisplayName $rule.Name -ErrorAction SilentlyContinue
            if ($existing) {
                Write-Ok "Regla existente: $($rule.Name)"
                continue
            }
            if (-not $isAdmin) {
                Write-Warn2 "Falta la regla '$($rule.Name)'. Ejecuta PowerShell como administrador:"
                Write-Info ("New-NetFirewallRule -DisplayName '{0}' -Direction {1} -Action Allow -Protocol {2} -LocalPort 14000 -Profile Private,Domain" -f $rule.Name, $rule.Direction, $rule.Protocol)
                continue
            }
            New-NetFirewallRule -DisplayName $rule.Name -Direction $rule.Direction -Action Allow `
                -Protocol $rule.Protocol -LocalPort 14000 -Profile Private,Domain | Out-Null
            Write-Ok "Regla creada: $($rule.Name)"
        }
    } catch {
        Write-Warn2 "No se pudieron revisar las reglas de firewall: $($_.Exception.Message)"
        Write-Info "Permite manualmente UDP 14000 y TCP 14000 para el perfil de red privada."
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# 5) SSH sin contraseña a los nodos Edge
# ─────────────────────────────────────────────────────────────────────────────
Write-Step "6/7 Conectividad SSH con los nodos Edge"
$sshCmd = Get-Command ssh -ErrorAction SilentlyContinue
if (-not $sshCmd) {
    Write-Warn2 "No se encontro el cliente OpenSSH."
    Write-Info "Instalalo en Configuracion > Aplicaciones > Caracteristicas opcionales > Cliente OpenSSH."
} elseif ($SkipSshCheck) {
    Write-Info "Omitido (-SkipSshCheck)."
} else {
    $nodes = @(
        @{ User = $CoreUser; Ip = $CoreIp; Label = "ADASCore" },
        @{ User = $DeskUser; Ip = $DeskIp; Label = "zorros-desk" }
    )
    $remoteScript = "echo PING=1; command -v docker >/dev/null 2>&1 && echo DOCKER=1 || echo DOCKER=0"
    foreach ($node in $nodes) {
        $target = "$($node.User)@$($node.Ip)"
        $out = (& ssh -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new $target $remoteScript 2>&1) -join "`n"
        if ($LASTEXITCODE -eq 0 -and $out -match "PING=1") {
            if ($out -match "DOCKER=1") { Write-Ok "$($node.Label) ($target): SSH OK, docker OK" }
            else { Write-Warn2 "$($node.Label) ($target): SSH OK, pero docker no responde" }
        } else {
            Write-Warn2 "$($node.Label) ($target): sin acceso SSH sin contrasena"
            Write-Info "1) ssh-keygen -t ed25519  (si aun no tienes llave)"
            Write-Info ('2) type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh {0} "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"' -f $target)
        }
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# 6) Panel ADAS (Tkinter nativo)
# ─────────────────────────────────────────────────────────────────────────────
Write-Step "7/7 Iniciando el panel ADAS (Tkinter nativo, sin Docker local)"
$dashboard = Join-Path $ProjectRoot "adas_dashboard.py"
if (-not (Test-Path $dashboard)) {
    Write-Fail "No se encontro adas_dashboard.py en $ProjectRoot"
    exit 1
}
Write-Info "Los modos 1/2/3/5 lanzan main_sub_ai.py nativo y los bordes por SSH."
Write-Info "El MODO 4 (o cerrar la ventana) detiene contenedores remotos y procesos locales."
& $venvPython $dashboard
exit $LASTEXITCODE
