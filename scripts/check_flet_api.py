"""Comprueba que la API de Flet usada por conc.py exista en la versión instalada.

Se ejecuta en GitHub Actions antes de compilar. No arranca la interfaz: solo
valida imports y atributos (por ejemplo ft.Colors.WHITE_70 o ft.Border.all).
"""

from __future__ import annotations

import pathlib
import re
import sys

import flet as ft

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "conc.py"


def main() -> int:
    source = SOURCE.read_text(encoding="utf-8")
    errors: list[str] = []

    # ft.run, ft.Page, ft.Colors, ft.dropdown, ...
    for name in sorted(set(re.findall(r"\bft\.([A-Za-z_]\w*)", source))):
        if not hasattr(ft, name):
            errors.append(f"ft.{name} no existe en Flet {ft.__version__}")

    # ft.Border.all, ft.Colors.WHITE_70, ft.dropdown.Option, ...
    for parent, member in sorted(
        set(re.findall(r"\bft\.([A-Za-z_]\w*)\.([A-Za-z_]\w*)", source))
    ):
        obj = getattr(ft, parent, None)
        if obj is not None and not hasattr(obj, member):
            errors.append(
                f"ft.{parent}.{member} no existe en Flet {ft.__version__}"
            )

    for method in ("show_dialog", "pop_dialog"):
        if not hasattr(ft.Page, method):
            errors.append(f"ft.Page.{method} no existe en Flet {ft.__version__}")

    if errors:
        print("Errores de compatibilidad con Flet:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print(f"API de Flet OK (Flet {ft.__version__}, Python {sys.version.split()[0]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
