# Análisis técnico de `conc.py`

## Resumen

`conc` es una aplicación de escritorio de un solo archivo (1.962 líneas) construida con **Flet** y **FFmpeg**. La interfaz tiene cinco pestañas: combinar, comprimir, cortar, multi-cut y renombrar. Todo el trabajo pesado se ejecuta en un hilo de fondo y la UI se actualiza desde el hilo principal mediante `call_soon_threadsafe`.

El script mezclaba APIs de Flet 0.28 y de Flet 1.x. Para poder compilarlo y ejecutarlo con **Flet 1.1.2** se corrigieron cuatro incompatibilidades que impedían arrancar la app o que hacían que los diálogos no aparecieran.

## Arquitectura

| Parte | Responsabilidad |
| --- | --- |
| `AppState` | Estado de la sesión: rutas, CRF, preset, color, versión de FFmpeg. |
| `FFmpegProcessor` | Ejecuta comandos con `subprocess.Popen`, lee el progreso de `stderr`, calcula duración con `ffprobe`, cancela procesos y limpia salidas parciales. |
| `parse_multi_cut_lines` | Parser de instrucciones `ID INICIO - FIN [rotación]`. |
| `build_multi_cut_plan` | Empareja IDs numéricos con nombres de archivo; detecta ambigüedades y ausencias. |
| Funciones de pestaña | Validan entradas, construyen comandos FFmpeg y encadenan lotes. |
| Utilidades de renombrado | Generan nombres aleatorios o aplican buscar/reemplazar, con log de reversión. |

### Flujo de un proceso

1. El handler valida rutas, tiempos y dependencias.
2. `require_idle()` bloquea el procesador para evitar dos lotes simultáneos.
3. Se lanza un `threading.Thread` con el comando FFmpeg.
4. El hilo lee `stderr`, actualiza la barra de progreso y usa `safe_update()` para tocar la UI en el event loop.
5. Al terminar, se muestra un snackbar, un diálogo de error o se encadena el siguiente archivo.
6. Si el usuario cancela, se hace `terminate()` y, tras 3 segundos, `kill()`.

## Hallazgos y correcciones aplicadas

### 1. Diálogos y snackbars (crítico)

El código comprobaba `hasattr(page, "open")` y, si no existía, usaba `page.dialog` / `page.snack_bar`. En Flet 1.x **no existen ninguna de las tres APIs**: los diálogos y snackbars fallaban silenciosamente porque la excepción se capturaba con `pass`.

Corrección: usar primero `page.show_dialog(ctl)` y `page.pop_dialog()`, manteniendo `page.open()` y `page.close()` como compatibilidad con Flet 0.28.

### 2. `ft.border.all` eliminado

En Flet 1.x los helpers de módulo pasaron a ser classmethods. `ft.border.all(1, ...)` lanzaba `AttributeError` al construir la pestaña Multi-Cut y la app no arrancaba.

Corrección: `ft.Border.all(1, ft.Colors.GREY)`.

### 3. Constantes de color renombradas

`ft.Colors.WHITE70` pasó a `ft.Colors.WHITE_70` en Flet 1.x. El cambio afectaba a dos textos de la pestaña Rename y provocaba otro `AttributeError` al arrancar.

Corrección: `ft.Colors.WHITE_70`.

### 4. `InputBorder.NONE` obsoleto

Flet 1.x marca `ft.InputBorder.NONE` como obsoleto para 1.3. Se sustituyó por `ft.NoInputBorder()`.

### 5. `ft.dropdown.Option`

Se mantiene porque Flet 1.1.2 exporta `Option = DropdownOption` como alias. La comprobación de API de CI valida que siga existiendo.

## Riesgos y limitaciones

### FFmpeg no está incluido

El paquete generado no contiene `ffmpeg.exe` ni `ffmpeg` para Linux. El usuario debe instalarlo o seleccionar la ruta manualmente. Es la limitación más importante para una distribución “todo en uno”. Opciones futuras:

- incluir binarios estáticos de FFmpeg en el artefacto (aumenta bastante el tamaño);
- descargarlos en el primer arranque;
- detectar una carpeta `bin/` junto al ejecutable.

### Renombrado destructivo

`Randomize All` renombra **todos los archivos** de la carpeta, incluidos documentos e imágenes. La confirmación y `rename_log.txt` reducen el riesgo, pero no hay deshacer automático. Sería recomendable limitar el renombrado a extensiones multimedia o pedir una segunda confirmación escribiendo el nombre de la carpeta.

### Heurística de compresión

`list_videos_to_compress` evita recomprimir cuando encuentra un original y su versión `r<nombre>`. Casos límite:

- si se borra el original, el archivo `r...` puede volver a comprimirse;
- un archivo original que ya empiece por `r` puede confundirse con una versión comprimida.

Un manifiesto (por ejemplo un JSON con hash/fecha) sería más robusto que el prefijo `r`.

### Fallback de Multi-Cut

Si ningún ID coincide, el código empareja las instrucciones con los vídeos por orden alfabético y solo muestra un aviso. Es cómodo, pero peligroso si el usuario se equivoca de carpeta. La vista previa lo mitiga; una opción sería exigir confirmación explícita para ese modo.

### Modelo de un solo hilo de Flet 1.x

Flet 1.0 ejecuta los handlers síncronos en el event loop. Las operaciones pesadas ya están en hilos, pero `do_random_rename` y `process_find_replace` hacen E/S de archivos en el handler; con miles de archivos la UI puede congelarse unos segundos. Se podrían mover a un hilo con `page.run_thread()`.

Las mutaciones de controles desde el hilo de FFmpeg se hacen antes de programar `page.update()` en el loop. Funciona en la práctica, pero lo más estricto sería programar también las mutaciones dentro del `call_soon_threadsafe`.

### Combine a 1080p fijo

La pestaña Combine escala y rellena siempre a 1920×1080. Para material vertical u otras resoluciones sería mejor exponer la resolución de salida o conservar la del origen.

### Versión de FFmpeg

`fps_passthrough_args()` usa `-fps_mode` salvo que detecte una versión menor de 5.1. Si el regex de `ffmpeg -version` falla, asume una versión moderna. Una detección más defensiva evitaría fallos con builds antiguos.

### Calidad del código

- Un solo archivo de ~2.000 líneas con una clase y muchas funciones anidadas dentro de `main`; difícil de testear.
- Sin tests automatizados ni tipado estricto.
- Textos mezclados en español e inglés.
- Los errores de la UI se capturan con `pass` en varios sitios, lo que oculta fallos.

## CI/CD

`.github/workflows/build.yml`:

1. **check**: instala Flet 1.1.2, compila `src/conc.py` y ejecuta `scripts/check_flet_api.py`, que valida todos los símbolos `ft.*` usados.
2. **build**: matriz con `windows-latest` (Windows 10/11 x64) y `ubuntu-22.04` (Linux x86_64), usando `flet-dev/flet-build-action@v1`.
3. **release**: en tags `v*`, descarga los artefactos y crea/actualiza la release.

Windows 10 y 11 comparten el mismo binario x64. El artefacto Linux se compila en Ubuntu 22.04 para mantener una glibc más antigua y mejorar compatibilidad.

## Recomendaciones a corto plazo

1. Incluir o autodescargar FFmpeg.
2. Añadir un test de humo que arranque la app en modo web o con `xvfb` y abra cada pestaña.
3. Separar `conc.py` en módulos (`ui`, `ffmpeg`, `rename`, `config`).
4. Sustituir la heurística `r<nombre>` por un manifiesto.
5. Unificar idioma de la interfaz.
