# conc

[![Build conc](https://github.com/tnetis/conc/actions/workflows/build.yml/badge.svg)](https://github.com/tnetis/conc/actions/workflows/build.yml)

Aplicación de escritorio para **combinar, comprimir, cortar y renombrar** archivos multimedia. Está escrita en Python con [Flet](https://flet.dev/) y usa **FFmpeg** para todo el procesamiento de audio y vídeo.

## Descargas

Los binarios se generan automáticamente en GitHub Actions:

1. Abre la pestaña [**Actions**](https://github.com/tnetis/conc/actions/workflows/build.yml).
2. Entra en la ejecución más reciente de **Build conc**.
3. Descarga uno de los artefactos:

| Plataforma | Artefacto |
| --- | --- |
| Windows 10 / Windows 11 (x64) | `conc-windows-x86_64.zip` |
| Linux (x86_64) | `conc-linux-x86_64.tar.gz` |

Windows 10 y Windows 11 usan **el mismo ejecutable x64**. No se compila para macOS.

También se publica una release con los dos archivos cuando se sube un tag `v*` (por ejemplo `v0.1.0`).

## Requisitos

- **FFmpeg 5.1 o superior**, incluyendo `ffprobe`.
- Windows 10 (1809+) o Windows 11, 64 bits.
- Linux x86_64 con GTK 3 y glibc 2.35 o superior (el artefacto se compila en Ubuntu 22.04).

FFmpeg **no va incluido** en el paquete; la app lo busca en el `PATH` o en la ruta que indiques en **Ajustes**.

Instalación de FFmpeg:

```powershell
# Windows (winget)
winget install --id Gyan.FFmpeg -e
```

```bash
# Debian / Ubuntu
sudo apt update && sudo apt install ffmpeg
```

Si `ffmpeg` y `ffprobe` no están en el `PATH`, abre el botón de ajustes de conc y selecciona el ejecutable de FFmpeg. `ffprobe` debe estar en la misma carpeta o también en el `PATH`.

## Uso

### Windows

1. Descarga `conc-windows-x86_64.zip`.
2. Extrae el ZIP completo (no ejecutes el `.exe` desde dentro del ZIP).
3. Abre `conc.exe`.

### Linux

```bash
tar -xzf conc-linux-x86_64.tar.gz
cd conc
chmod +x conc
./conc
```

La primera vez, comprueba la ruta de FFmpeg en **Ajustes**.

## Funciones

| Pestaña | Qué hace |
| --- | --- |
| **Combine** | Une una imagen o vídeo con una pista de audio y genera un MP4 1920×1080 ajustado a la duración del audio. |
| **Compress** | Comprime todos los vídeos de una carpeta a `r<nombre>.mp4` con H.264, CRF y preset configurables. Genera un reporte opcional. |
| **Cut** | Corta un vídeo entre dos tiempos (`HH:MM:SS`, `MM:SS` o segundos). Sin rotación usa copia directa de streams. |
| **Multi-Cut** | Procesa varias instrucciones `ID INICIO - FIN [r90/r-90/r180]` sobre los vídeos de una carpeta. Muestra una vista previa antes de ejecutar. |
| **Rename** | Renombra archivos al azar o hace buscar/reemplazar. Guarda el mapa de cambios en `rename_log.txt`. |

### Ajustes

- Ruta del ejecutable de FFmpeg.
- CRF (calidad) y preset de compresión.
- Activar/desactivar el reporte de compresión.
- Color de acento de la interfaz.

Los ajustes se guardan con `SharedPreferences`.

## Estructura del proyecto

```text
conc/
├── .github/workflows/build.yml   # CI: check + Windows/Linux + release
├── pyproject.toml                # metadatos y configuración de Flet
├── scripts/check_flet_api.py     # validación de API antes de compilar
├── src/
│   ├── assets/icon.png
│   └── conc.py                   # aplicación completa
└── README.md
```

## Compilación en GitHub Actions

No necesitas instalar Flet ni Flutter en tu PC. El workflow `Build conc` hace todo:

- **Check Python and Flet API**: instala Flet 1.1.2, compila `conc.py` y valida que todos los símbolos de Flet usados existan.
- **Windows 10/11 x86_64**: instala `flet[cli]`, descarga Flutter automáticamente y ejecuta `flet build windows` en `windows-latest`.
- **Linux x86_64**: instala las dependencias de sistema que publica `flet --version --json`, descarga Flutter y ejecuta `flet build linux` en `ubuntu-22.04`.
- **Publish GitHub Release**: solo en tags `v*`.

Los jobs de compilación empaquetan la salida con `scripts/archive.py` (ZIP en Windows, tar.gz en Linux) y suben un único archivo por plataforma. La primera ejecución tarda más porque Flutter se descarga dentro del runner.

El workflow se ejecuta al hacer push a `main`, en pull requests y manualmente con **Run workflow**.

Para publicar una versión:

```bash
git tag v0.1.0
git push origin v0.1.0
```

## Notas

- El renombrado aleatorio afecta a **todos los archivos** de la carpeta seleccionada, no solo a vídeos. La app pide confirmación y escribe `rename_log.txt` para poder revertir los cambios.
- En Multi-Cut, si ningún ID coincide con un nombre de archivo, la app avisa y empareja por orden alfabético. Revisa siempre la vista previa.
- La app no incluye FFmpeg por tamaño y licencia; se instala aparte.
