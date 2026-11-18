"""Prepara la plantilla de build de Flet con la ventana inicial de conc.

Flet 1.1.2 crea la ventana nativa a 1280x720 y, en Windows, ignora el tamano
pedido desde Python hasta que se actualiza el control Window. Para que la
ventana nazca ya a 430x460 se parchean los valores por defecto del runner de
Windows y de GTK en Linux.

El script descarga la plantilla oficial correspondiente a la version de Flet,
la descomprime y deja una copia modificada lista para `flet build --template`.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

WINDOW_WIDTH = 430
WINDOW_HEIGHT = 460
TEMPLATE_URL = (
    "https://github.com/flet-dev/flet/releases/download/"
    "v{version}/flet-build-template.zip"
)

REPLACEMENTS = {
    Path("windows") / "runner" / "main.cpp": (
        "Win32Window::Size size(1280, 720);",
        f"Win32Window::Size size({WINDOW_WIDTH}, {WINDOW_HEIGHT});",
    ),
    Path("linux") / "my_application.cc": (
        "gtk_window_set_default_size(window, 1280, 720);",
        f"gtk_window_set_default_size(window, {WINDOW_WIDTH}, {WINDOW_HEIGHT});",
    ),
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--version",
        required=True,
        help="Version de Flet cuya plantilla oficial se usara (ej. 1.1.2).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(".flet-build-template"),
        help="Carpeta de trabajo para la plantilla descargada.",
    )
    args = parser.parse_args()

    if args.output.exists():
        shutil.rmtree(args.output)
    args.output.mkdir(parents=True)

    zip_path = args.output / "flet-build-template.zip"
    url = TEMPLATE_URL.format(version=args.version)
    print(f"Descargando plantilla de Flet: {url}")
    urllib.request.urlretrieve(url, zip_path)

    extracted = args.output / "extracted"
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(extracted)

    template_root = extracted / "build"
    if not (template_root / "cookiecutter.json").is_file():
        print(
            f"La plantilla no tiene el formato esperado: {template_root}",
            file=sys.stderr,
        )
        return 1

    control_root = template_root / "{{cookiecutter.out_dir}}"
    for relative_path, (old, new) in REPLACEMENTS.items():
        path = control_root / relative_path
        if not path.is_file():
            print(f"No se encontro el archivo esperado: {path}", file=sys.stderr)
            return 1

        text = path.read_text(encoding="utf-8")
        if old not in text:
            print(
                f"No se encontro el tamano por defecto en {path}",
                file=sys.stderr,
            )
            return 1

        path.write_text(text.replace(old, new, 1), encoding="utf-8")
        print(f"Parcheado {relative_path}: {WINDOW_WIDTH}x{WINDOW_HEIGHT}")

    print(f"Plantilla lista en: {template_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
