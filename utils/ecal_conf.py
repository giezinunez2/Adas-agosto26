"""
Configuración de eCAL en Windows — ecal_windows.ini
===================================================

eCAL 5.12 busca su configuración, por orden, en:

    Windows:  %ECAL_DATA%/ecal.ini  ->  %ProgramData%/eCAL/ecal.ini
    Ubuntu:   $ECAL_DATA/ecal.ini   ->  /etc/ecal/ecal.ini

Los nodos Linux (ADASCore, zorros-desk y el HMI en Raspberry Pi) siguen usando
`ecal.ini`, montado en /app/ecal.ini y /etc/ecal/ecal.ini por el dashboard.

En Windows prepare_ecal_config() copia `ecal_windows.ini` a un directorio de datos
del usuario y exporta ECAL_DATA ANTES de importar `ecal`: así eCAL arranca con
multicast 239.0.0.1:14000 y payload TCP sin permisos de administrador y sin pisar
una configuración global ya existente.

Este módulo usa solo la biblioteca estándar, por lo que puede importarse sin cv2,
torch ni ultralytics (útil para preparar eCAL antes de instalar lo pesado).
"""

import os
import shutil
import tempfile
from pathlib import Path


def is_windows() -> bool:
    """True si el proceso se ejecuta sobre Windows."""
    return os.name == "nt"


def prepare_ecal_config():
    """
    Publica ecal_windows.ini donde eCAL 5.12 lo carga automáticamente (Windows).

    Orden de resolución del directorio destino:

    1. `ECAL_DATA` si ya está definido (se respeta una configuración propia).
    2. `%LOCALAPPDATA%\\ADAS\\ecal` (por defecto).
    3. El directorio temporal del sistema si no hay LOCALAPPDATA.

    En Linux/macOS no hace nada: los nodos Raspberry Pi siguen usando ecal.ini
    (/etc/ecal/ecal.ini o el volumen montado en el contenedor).

    Returns:
        Path | None: Ruta del ecal.ini activo, o None si no aplica o falla.
    """
    if not is_windows():
        return None

    project_root = Path(__file__).resolve().parent.parent
    source = project_root / "ecal_windows.ini"
    if not source.is_file():
        print(f"⚠️ No se encontró {source.name} en {project_root}; "
              f"eCAL usará su configuración por defecto")
        return None

    configured = os.environ.get("ECAL_DATA")
    target_dir = Path(configured) if configured else (
        Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / "ADAS" / "ecal"
    )

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / "ecal.ini"
        if not target.is_file() or target.read_bytes() != source.read_bytes():
            shutil.copyfile(source, target)
        os.environ["ECAL_DATA"] = str(target_dir)
        return target
    except OSError as exc:
        print(f"⚠️ No se pudo preparar ecal_windows.ini en {target_dir}: {exc}")
        return None
