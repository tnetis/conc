"""Empaqueta la salida de `flet build` en un archivo portable.

Uso:
    python scripts/archive.py zip build/windows conc-windows-x86_64.zip
    python scripts/archive.py tar.gz build/linux conc-linux-x86_64.tar.gz

El contenido queda dentro de una carpeta `conc/`, de modo que al extraer el
archivo no se mezclen los ficheros con los del directorio actual.
"""

from __future__ import annotations

import pathlib
import sys
import tarfile
import zipfile


def archive_zip(source: pathlib.Path, destination: pathlib.Path) -> None:
    files = sorted(path for path in source.rglob("*") if path.is_file())
    if not files:
        raise SystemExit(f"No hay ficheros que empaquetar en {source}")

    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, arcname=pathlib.Path("conc") / path.relative_to(source))


def archive_tar(source: pathlib.Path, destination: pathlib.Path) -> None:
    with tarfile.open(destination, "w:gz", format=tarfile.PAX_FORMAT) as archive:
        archive.add(source, arcname="conc", recursive=True)


def main() -> int:
    if len(sys.argv) != 4:
        raise SystemExit("Uso: archive.py <zip|tar.gz> <origen> <destino>")

    archive_format, source_arg, destination_arg = sys.argv[1:4]
    source = pathlib.Path(source_arg)
    destination = pathlib.Path(destination_arg)

    if not source.is_dir():
        raise SystemExit(f"No existe el directorio de compilación: {source}")

    destination.parent.mkdir(parents=True, exist_ok=True)

    if archive_format == "zip":
        archive_zip(source, destination)
    elif archive_format == "tar.gz":
        archive_tar(source, destination)
    else:
        raise SystemExit(f"Formato no soportado: {archive_format}")

    size_mb = destination.stat().st_size / (1024 * 1024)
    print(f"Creado {destination} ({size_mb:.1f} MiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
