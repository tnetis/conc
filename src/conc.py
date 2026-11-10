import flet as ft
import subprocess
import os
import inspect
import asyncio
from collections import deque
from pathlib import Path
import threading
import json
import re
import random
import string
import time
from datetime import datetime

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
VIDEO_EXTS = ('.mp4', '.mov', '.mkv', '.avi', '.webm', '.m4v')
IMAGE_EXTS = ('.jpg', '.jpeg', '.png', '.webp')
AUDIO_EXTS = ('.mp3', '.wav', '.m4a', '.opus', '.flac', '.aac', '.ogg')

TIME_RE = re.compile(r'^(?:\d{1,2}:)?\d{1,2}:\d{2}(?:\.\d+)?$|^\d+(?:\.\d+)?$')
TIME_TOKEN = r'\d+(?::\d{1,2}){0,2}(?:\.\d+)?'
CREATION_FLAGS = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
MONO_FONT = "Consolas" if os.name == "nt" else "monospace"

RENAME_LOG_NAME = "rename_log.txt"
PROTECTED_FILES = {RENAME_LOG_NAME, "reporte_compresion.txt"}

FFMPEG_PRESETS = [
    "ultrafast", "superfast", "veryfast", "faster",
    "fast", "medium", "slow", "slower"
]

ACCENT_COLOR_OPTIONS = [
    ("amber400", "Ámbar (Original)"),
    ("blue400", "Azul"),
    ("green400", "Verde"),
    ("red400", "Rojo"),
    ("purple400", "Morado"),
    ("pink400", "Rosa"),
    ("teal400", "Verde Azulado"),
    ("orange400", "Naranja"),
]
VALID_ACCENTS = {key for key, _ in ACCENT_COLOR_OPTIONS}

ROTATION_FILTERS = {
    'r90': 'transpose=1',
    'r-90': 'transpose=2',
    'r180': 'transpose=1,transpose=1',
}
ROTATION_LABELS = {
    'r90': 'rotar 90° (horario)',
    'r-90': 'rotar 90° (antihorario)',
    'r180': 'rotar 180°',
}
ROT_ALIASES = {
    '90': 'r90',
    '-90': 'r-90',
    '180': 'r180',
    '-180': 'r180',
    '270': 'r-90',
    '-270': 'r90',
}


def time_to_seconds(t: str) -> float:
    parts = [float(p) for p in t.strip().split(':')]
    while len(parts) < 3:
        parts.insert(0, 0.0)
    h, m, s = parts
    return h * 3600 + m * 60 + s


def fmt_seconds(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def parse_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() not in ("false", "0", "", "no", "off")
    return bool(v)


def get_unique_path(folder: str, filename: str, reserved: set | None = None) -> str:
    reserved = reserved if reserved is not None else set()
    candidate = os.path.join(folder, filename)
    stem, ext = os.path.splitext(filename)
    i = 1
    while os.path.exists(candidate) or candidate in reserved:
        candidate = os.path.join(folder, f"{stem}_{i}{ext}")
        i += 1
    reserved.add(candidate)
    return candidate


def write_rename_log(folder: str, title: str, pairs: list):
    if not pairs:
        return
    log_path = os.path.join(folder, RENAME_LOG_NAME)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"# {title} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        for old, new in pairs:
            f.write(f"{old} -> {new}\n")
        f.write("\n")


# ---------------------------------------------------------------------------
# Multi-Cut: parser y emparejamiento
# ---------------------------------------------------------------------------
LINE_ID_RE = re.compile(
    r'^[\[\(]?\s*(?P<id>\d+)(?!\d|:\d)\s*[\]\)]?\s*[:\-–—]?\s*(?P<rest>.*)$'
)
ROT_RE = re.compile(
    r'(?<![A-Za-z0-9])r\s*(?P<deg>-?\s*(?:90|180|270))(?!\d)',
    re.IGNORECASE
)
RANGE_RE = re.compile(
    r'(?<![\d:.])(?P<start>' + TIME_TOKEN + r')\s*[-–—]\s*(?P<end>' + TIME_TOKEN + r')(?![\d:])'
)


def parse_multi_cut_lines(txt: str):
    jobs, invalid, ignored = [], [], []

    for raw in txt.splitlines():
        line = raw.strip()
        if not line:
            continue

        m = LINE_ID_RE.match(line)
        if not m:
            ignored.append(line)
            continue

        vid = m.group('id')
        rest = m.group('rest')

        rotation = None
        rm = ROT_RE.search(rest)
        if rm:
            key = re.sub(r'\s+', '', rm.group('deg'))
            rotation = ROT_ALIASES.get(key)
            rest = rest[:rm.start()] + ' ' + rest[rm.end():]

        start = end = None
        tm = RANGE_RE.search(rest)
        if tm:
            start, end = tm.group('start'), tm.group('end')
            if time_to_seconds(end) <= time_to_seconds(start):
                invalid.append(line)
                continue

        if not (start or rotation):
            ignored.append(line)
            continue

        jobs.append({
            'id': vid,
            'start': start,
            'end': end,
            'rotation': rotation,
            'line': line,
        })

    return jobs, invalid, ignored


def _norm_id(s: str) -> str:
    return s.lstrip('0') or '0'


def find_video_matches(vid: str, files: list) -> list:
    target = _norm_id(vid)
    exact, suffix = [], []

    for f in files:
        blocks = re.findall(r'\d+', os.path.splitext(f)[0])
        if not blocks:
            continue

        if any(_norm_id(b) == target for b in blocks):
            exact.append(f)
        else:
            last = blocks[-1]
            if len(last) > 4 and _norm_id(last[-4:]) == target:
                suffix.append(f)

    return exact if exact else suffix


def build_multi_cut_plan(jobs: list, video_files: list):
    plan, missing, ambiguous = [], [], []

    for job in jobs:
        matches = find_video_matches(job['id'], video_files)
        if len(matches) == 1:
            plan.append((job, matches[0]))
        elif not matches:
            missing.append(job)
        else:
            ambiguous.append((job, matches))

    return plan, missing, ambiguous


def describe_job(job: dict) -> str:
    parts = []
    if job['start'] and job['end']:
        parts.append(f"{job['start']} - {job['end']}")
    if job['rotation']:
        parts.append(ROTATION_LABELS.get(job['rotation'], job['rotation']))
    return "  ".join(parts)


class AppState:
    def __init__(self):
        self.visual_concat_path = None
        self.is_visual_concat_video = False
        self.audio_path = None
        self.video_for_cut_path = None
        self.videos_folder = None
        self.multi_cut_folder = None
        self.rename_folder = None

        self.ffmpeg_path = "ffmpeg"
        self.crf = 25
        self.preset = "medium"
        self.gen_report = True
        self.accent_color = "amber400"
        self.ffmpeg_checked = None
        self.ffmpeg_version = None


async def main(page: ft.Page):
    page.title = "conc"
    page.theme_mode = ft.ThemeMode.DARK
    page.window.width = 430
    page.window.height = 460
    page.bgcolor = "black"

    ui_loop = asyncio.get_running_loop()

    try:
        res = page.window.center()
        if inspect.isawaitable(res):
            await res
    except Exception:
        pass

    state = AppState()

    prefs = ft.SharedPreferences()
    file_picker = None

    def register_service(svc):
        if hasattr(page, 'services'):
            try:
                page.services.append(svc)
                return
            except Exception:
                pass
        page.overlay.append(svc)

    register_service(prefs)

    def get_file_picker():
        # Se crea en el primer uso: en Flet 1.x el servicio se registra solo
        # al construirse dentro del contexto de la página.
        nonlocal file_picker
        if file_picker is None:
            file_picker = ft.FilePicker()
        return file_picker

    def safe_update():
        def _do():
            try:
                page.update()
            except Exception:
                pass

        try:
            ui_loop.call_soon_threadsafe(_do)
        except Exception:
            pass

    def open_modal(ctl):
        def _do():
            try:
                # Flet 1.x
                if hasattr(page, "show_dialog"):
                    page.show_dialog(ctl)
                # Flet 0.28.x
                elif hasattr(page, "open"):
                    page.open(ctl)
                # Flet 0.27.x y anteriores
                elif isinstance(ctl, ft.SnackBar):
                    page.snack_bar = ctl
                    ctl.open = True
                    page.update()
                else:
                    page.dialog = ctl
                    ctl.open = True
                    page.update()
            except Exception:
                pass
        try:
            ui_loop.call_soon_threadsafe(_do)
        except Exception:
            pass

    def close_modal(ctl):
        def _do():
            try:
                # Flet 1.x: cierra el diálogo superior
                if hasattr(page, "pop_dialog"):
                    page.pop_dialog()
                # Flet 0.28.x
                elif hasattr(page, "close"):
                    page.close(ctl)
                else:
                    ctl.open = False
                    page.update()
            except Exception:
                pass
        try:
            ui_loop.call_soon_threadsafe(_do)
        except Exception:
            pass

    # ---------------- Carga de ajustes guardados ----------------
    async def load_setting(key, default, caster=None):
        try:
            if await prefs.contains_key(key):
                value = await prefs.get(key)
                if value is None:
                    return default
                return caster(value) if caster else value
        except Exception:
            pass
        return default

    state.ffmpeg_path = await load_setting("concat_ffmpeg_path", "ffmpeg", str) or "ffmpeg"
    state.crf = await load_setting("concat_crf", 25, lambda v: int(float(v)))
    state.crf = min(30, max(18, state.crf))

    state.preset = await load_setting("concat_preset", "medium", str) or "medium"
    if state.preset not in FFMPEG_PRESETS:
        state.preset = "medium"

    state.gen_report = await load_setting("concat_gen_report", True, parse_bool)

    state.accent_color = await load_setting("concat_accent_color", "amber400", str) or "amber400"
    if state.accent_color not in VALID_ACCENTS:
        state.accent_color = "amber400"

    progress_ring = ft.ProgressRing(visible=False, width=14, height=14)
    status_text = ft.Text(visible=False, text_align=ft.TextAlign.CENTER, expand=True)

    cancel_btn = ft.IconButton(
        icon=ft.Icons.STOP_CIRCLE,
        icon_color=ft.Colors.RED_400,
        tooltip="Cancelar proceso actual",
        on_click=lambda e: processor.cancel(),
    )

    bottom_indicator = ft.Row(
        [progress_ring, status_text, cancel_btn],
        alignment=ft.MainAxisAlignment.CENTER,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        visible=False,
        spacing=10,
        expand=True,
    )

    def show_snackbar(message, color=None):
        snack = ft.SnackBar(
            content=ft.Text(message),
            bgcolor=color or state.accent_color,
            show_close_icon=True
        )
        open_modal(snack)

    def show_error_dialog(title: str, message: str):
        dlg = ft.AlertDialog(
            title=ft.Text(title, color=ft.Colors.RED_400, weight=ft.FontWeight.BOLD),
            content=ft.Container(
                content=ft.Column(
                    [ft.Text(message, selectable=True, font_family=MONO_FONT, size=12)],
                    scroll=ft.ScrollMode.ALWAYS
                ),
                width=400,
                height=200,
                padding=10
            ),
            actions=[ft.TextButton("Cerrar", on_click=lambda e: close_modal(dlg))],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        open_modal(dlg)

    # ---------------------------------------------------------------------------
    # Procesador FFmpeg
    # ---------------------------------------------------------------------------
    class FFmpegProcessor:
        def __init__(self, page_ref, status_text_ref, progress_ring_ref, bottom_ind_ref,
                     snackbar_func, error_dialog_func):
            self.page = page_ref
            self.status_text = status_text_ref
            self.progress_ring = progress_ring_ref
            self.bottom_indicator = bottom_ind_ref
            self.show_snackbar = snackbar_func
            self.show_error_dialog = error_dialog_func
            self.creationflags = CREATION_FLAGS
            self._lock = threading.Lock()
            self.busy = False
            self.current_process = None
            self._cancelled = False

        def try_begin(self) -> bool:
            with self._lock:
                if self.busy:
                    return False
                self.busy = True
                self._cancelled = False
                return True

        def end(self):
            with self._lock:
                self.busy = False

        def cancel(self):
            with self._lock:
                if not self.busy or self._cancelled:
                    return
                self._cancelled = True
                proc = self.current_process

            if proc is None or proc.poll() is not None:
                return

            try:
                proc.terminate()
            except Exception:
                pass

            def _ensure_dead():
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                except Exception:
                    pass

            threading.Thread(target=_ensure_dead, daemon=True).start()

        def _show_progress(self, message):
            self.progress_ring.visible = True
            self.status_text.value = message
            self.status_text.visible = True
            self.bottom_indicator.visible = True
            safe_update()

        def _hide_progress(self):
            self.progress_ring.visible = False
            self.status_text.visible = False
            self.bottom_indicator.visible = False
            safe_update()

        def get_ffprobe_path(self):
            ext = ".exe" if os.name == "nt" else ""
            dirname = os.path.dirname(state.ffmpeg_path)
            if dirname:
                candidate = os.path.join(dirname, f"ffprobe{ext}")
                if os.path.exists(candidate):
                    return candidate
            return "ffprobe"

        def get_duration(self, path):
            if not path or not os.path.exists(path):
                return None

            cmd = [
                self.get_ffprobe_path(),
                '-v', 'quiet',
                '-print_format', 'json',
                '-show_format',
                path
            ]

            try:
                result = subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding='utf-8',
                    errors='replace',
                    timeout=60,
                    creationflags=self.creationflags
                )
                info = json.loads(result.stdout or "{}")
                return float(info['format']['duration'])
            except Exception:
                return None

        @staticmethod
        def _parse_progress_time(line_lower: str):
            m = re.search(r'time=(\d+):(\d+):(\d+(?:\.\d+)?)', line_lower)
            if not m:
                return None
            return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))

        def _run_command_in_thread(
            self,
            cmd,
            success_message,
            error_prefix,
            on_complete=None,
            total_items=1,
            current_item_idx=0,
            probe_path=None,
            expected_seconds=None,
            cleanup_path=None,
            on_result=None
        ):
            def target():
                counter = f"({current_item_idx + 1}/{total_items})"
                process = None
                fatal = False

                def report(status, detail=""):
                    if on_result:
                        try:
                            on_result(status, detail)
                        except Exception as ex:
                            self.show_error_dialog("Error Inesperado", str(ex))
                        return

                    if status == "ok":
                        self.show_snackbar(success_message)
                    elif status == "cancelled":
                        self.show_snackbar(
                            "Proceso cancelado por el usuario.",
                            color=ft.Colors.ORANGE
                        )
                    else:
                        self.show_error_dialog(f"Error FFmpeg: {error_prefix}", detail)

                def aborted() -> bool:
                    if self._cancelled:
                        report("cancelled")
                        return True
                    return False

                try:
                    if aborted():
                        return

                    self._show_progress(f"{error_prefix} {counter}...")

                    total_seconds = expected_seconds
                    if total_seconds is None and probe_path:
                        if aborted():
                            return
                        total_seconds = self.get_duration(probe_path)

                    if aborted():
                        return

                    process = subprocess.Popen(
                        cmd,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                        text=True,
                        encoding='utf-8',
                        errors='replace',
                        bufsize=1,
                        creationflags=self.creationflags
                    )

                    with self._lock:
                        self.current_process = process

                    if self._cancelled:
                        try:
                            process.terminate()
                        except Exception:
                            pass

                    full_error_log = deque(maxlen=80)
                    last_update = 0.0

                    try:
                        for line in process.stderr:
                            line_lower = line.lower()
                            is_progress = (
                                "time=" in line_lower
                                and ("frame=" in line_lower or "size=" in line_lower)
                            )

                            if not is_progress:
                                full_error_log.append(line)
                                continue

                            now = time.monotonic()
                            if now - last_update >= 0.3:
                                cur = self._parse_progress_time(line_lower)
                                if cur is not None:
                                    txt = f"{error_prefix} {counter}: {fmt_seconds(cur)}"
                                    if total_seconds and total_seconds > 0:
                                        pct = min(99.9, cur / total_seconds * 100)
                                        txt += f" ({pct:.0f}%)"
                                    self.status_text.value = txt
                                    safe_update()
                                last_update = now
                    except Exception:
                        pass

                    process.wait()

                    with self._lock:
                        if self.current_process is process:
                            self.current_process = None

                    if self._cancelled:
                        report("cancelled")
                    elif process.returncode == 0:
                        report("ok")
                    else:
                        log = "".join(full_error_log)
                        report(
                            "error",
                            log or f"FFmpeg terminó con código {process.returncode}"
                        )

                except FileNotFoundError:
                    fatal = True
                    state.ffmpeg_checked = False
                    self.show_error_dialog(
                        "FFmpeg no encontrado",
                        f"No se pudo ejecutar '{cmd[0]}'. Configura la ruta en Ajustes."
                    )
                except Exception as ex:
                    fatal = True
                    self.show_error_dialog("Error Inesperado", str(ex))
                finally:
                    with self._lock:
                        if process is not None and self.current_process is process:
                            self.current_process = None

                    is_last = current_item_idx + 1 >= total_items
                    cancelled = self._cancelled
                    failed = fatal or cancelled or (
                        process is not None and process.returncode not in (0, None)
                    )

                    if cleanup_path and failed:
                        try:
                            os.remove(cleanup_path)
                        except OSError:
                            pass

                    should_cleanup = is_last or cancelled or fatal or (on_complete is None)

                    if on_complete and not cancelled and not fatal:
                        try:
                            on_complete()
                        except Exception as ex:
                            self.show_error_dialog("Error Inesperado", str(ex))
                            should_cleanup = True

                    if should_cleanup:
                        try:
                            self._hide_progress()
                        finally:
                            self.end()

            threading.Thread(target=target, daemon=True).start()

    processor = FFmpegProcessor(
        page,
        status_text,
        progress_ring,
        bottom_indicator,
        show_snackbar,
        show_error_dialog
    )

    # ---------------------------------------------------------------------------
    # Utilidades FFmpeg
    # ---------------------------------------------------------------------------
    def check_ffmpeg_installed():
        if state.ffmpeg_checked is None:
            try:
                result = subprocess.run(
                    [state.ffmpeg_path, '-version'],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding='utf-8',
                    errors='replace',
                    timeout=15,
                    creationflags=CREATION_FLAGS
                )
                state.ffmpeg_checked = result.returncode == 0
                m = re.search(r'ffmpeg version\s+n?(\d+)\.(\d+)', result.stdout or "")
                state.ffmpeg_version = (int(m.group(1)), int(m.group(2))) if m else None
            except Exception:
                state.ffmpeg_checked = False
        return state.ffmpeg_checked

    def require_ffmpeg() -> bool:
        if not check_ffmpeg_installed():
            show_error_dialog(
                "Dependencia faltante",
                f"FFmpeg no está instalado o no se encuentra en '{state.ffmpeg_path}'. "
                "Ve a Ajustes para configurarlo."
            )
            return False
        return True

    def require_idle() -> bool:
        if not processor.try_begin():
            show_snackbar(
                "Ya hay un proceso en curso. Espera o cancélalo.",
                color=ft.Colors.ORANGE
            )
            return False
        return True

    def fps_passthrough_args():
        if state.ffmpeg_version is not None and state.ffmpeg_version < (5, 1):
            return ['-vsync', 'passthrough']
        return ['-fps_mode', 'passthrough']

    def build_cut_cmd(src: str, out: str, start: str | None, end: str | None, rotation: str | None):
        cmd = [state.ffmpeg_path, '-y', '-hide_banner']
        start_s = time_to_seconds(start) if start else 0.0
        expected = None

        if start:
            cmd.extend(['-ss', f"{start_s:.3f}"])

        cmd.extend(['-i', src])

        if end:
            expected = time_to_seconds(end) - start_s
            cmd.extend(['-t', f"{expected:.3f}"])

        cmd.extend(['-map', '0:v?', '-map', '0:a?'])

        if rotation:
            vf = ROTATION_FILTERS.get(rotation)
            if vf is None:
                raise ValueError(f"Rotación no soportada: {rotation}")

            out_ext = Path(out).suffix.lower()

            if out_ext == '.webm':
                cmd.extend([
                    '-vf', vf,
                    '-c:v', 'libvpx-vp9',
                    '-crf', str(state.crf),
                    '-b:v', '0',
                    '-pix_fmt', 'yuv420p',
                    '-c:a', 'libopus'
                ])
            else:
                cmd.extend([
                    '-vf', vf,
                    '-c:v', 'libx264',
                    '-crf', str(state.crf),
                    '-preset', state.preset,
                    '-pix_fmt', 'yuv420p',
                    '-c:a', 'copy'
                ])
        else:
            cmd.extend(['-c', 'copy', '-avoid_negative_ts', 'make_zero'])

        cmd.append(out)
        return cmd, expected

    # ---------------------------------------------------------------------------
    # Selectores
    # ---------------------------------------------------------------------------
    async def pick_file(title: str, exts=None):
        kwargs = {"dialog_title": title}
        if exts:
            kwargs["file_type"] = ft.FilePickerFileType.CUSTOM
            kwargs["allowed_extensions"] = [x.lstrip('.') for x in exts]

        files = await get_file_picker().pick_files(**kwargs)
        if not files:
            return None

        path = files[0].path
        if not path:
            show_snackbar("No se pudo obtener la ruta del archivo.", color=ft.Colors.ORANGE)
            return None
        return path

    async def pick_dir(title: str):
        return await get_file_picker().get_directory_path(dialog_title=title)

    async def pick_visual_concat(e):
        path = await pick_file("Select Image or Video", IMAGE_EXTS + VIDEO_EXTS)
        if path:
            state.visual_concat_path = path
            visual_concat_path_text.value = Path(path).name
            state.is_visual_concat_video = Path(path).suffix.lower() in VIDEO_EXTS
            page.update()

    async def pick_audio(e):
        path = await pick_file("Select audio", AUDIO_EXTS)
        if path:
            state.audio_path = path
            audio_path_text.value = Path(path).name
            page.update()

    async def pick_folder(e):
        path = await pick_dir("Select videos folder")
        if path:
            state.videos_folder = path
            folder_path_text.value = f"Folder: {Path(path).name}"
            page.update()

    async def pick_multi_cut_folder(e):
        path = await pick_dir("Select folder for Multi-Cut")
        if path:
            state.multi_cut_folder = path
            multi_cut_folder_path_text.value = f"Folder: {Path(path).name}"
            page.update()

    async def pick_video_for_cut(e):
        path = await pick_file("Select video to cut", VIDEO_EXTS)
        if path:
            state.video_for_cut_path = path
            video_cut_path_text.value = f"Video: {Path(path).name}"
            page.update()

    async def pick_rename_folder(e):
        path = await pick_dir("Select folder to rename files")
        if path:
            state.rename_folder = path
            rename_folder_path_text.value = f"Folder: {Path(path).name}"
            page.update()

    async def pick_ffmpeg_exe(e):
        path = await pick_file("Select ffmpeg executable")
        if path:
            state.ffmpeg_path = path
            state.ffmpeg_checked = None
            state.ffmpeg_version = None

            try:
                await prefs.set("concat_ffmpeg_path", state.ffmpeg_path)
            except Exception as ex:
                show_error_dialog("No se pudo guardar la ruta", str(ex))

            ffmpeg_path_text.value = state.ffmpeg_path
            page.update()

    # ---------------------------------------------------------------------------
    # COMPRESS
    # ---------------------------------------------------------------------------
    def list_videos_to_compress(folder: str):
        all_files = [
            f for f in os.listdir(folder)
            if os.path.isfile(os.path.join(folder, f))
        ]

        video_files = [
            f for f in all_files
            if f.lower().endswith(VIDEO_EXTS)
        ]

        stems_lower = {Path(f).stem.lower() for f in video_files}
        pending = []

        for f in sorted(video_files):
            stem = Path(f).stem
            stem_l = stem.lower()

            if stem_l.startswith('r') and len(stem_l) > 1 and stem_l[1:] in stems_lower:
                continue

            if f"r{stem_l}" in stems_lower:
                continue

            pending.append(f)

        return pending

    def compress_videos(e):
        acquired = False
        try:
            if not state.videos_folder or not os.path.isdir(state.videos_folder):
                show_snackbar("Please select a folder first!", color=ft.Colors.ORANGE)
                return

            if not require_ffmpeg():
                return

            video_files = list_videos_to_compress(state.videos_folder)
            if not video_files:
                show_snackbar(
                    "No new videos found (already compressed?)",
                    color=ft.Colors.ORANGE
                )
                return

            if not require_idle():
                return
            acquired = True

            total_files = len(video_files)
            folder = state.videos_folder
            crf, preset, gen_report = state.crf, state.preset, state.gen_report

            report_data = [
                f"REPORTE DE COMPRESIÓN - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                f"CRF: {crf} | Preset: {preset}",
                "-" * 50
            ]
            reserved = set()

            def process_next(index):
                if index < total_files:
                    filename = video_files[index]
                    in_path = os.path.join(folder, filename)
                    out_path = get_unique_path(
                        folder,
                        f"r{Path(filename).stem}.mp4",
                        reserved
                    )

                    cmd = [
                        state.ffmpeg_path, '-y', '-hide_banner',
                        '-i', in_path,
                        '-c:v', 'libx264',
                        '-crf', str(crf),
                        '-preset', preset,
                        '-c:a', 'aac',
                        *fps_passthrough_args(),
                        '-movflags', '+faststart',
                        out_path
                    ]

                    def on_done():
                        if gen_report:
                            dur_orig = processor.get_duration(in_path)
                            dur_comp = processor.get_duration(out_path)
                            size_orig = os.path.getsize(in_path) if os.path.exists(in_path) else None
                            size_comp = os.path.getsize(out_path) if os.path.exists(out_path) else None

                            if dur_orig is None or dur_comp is None:
                                status = "ERROR"
                            elif abs(dur_orig - dur_comp) <= 0.6:
                                status = "OK"
                            else:
                                status = f"ALERTA (Dif: {abs(dur_orig - dur_comp):.2f}s)"

                            orig_str = f"{dur_orig:.1f}s" if dur_orig is not None else "N/A"
                            comp_str = f"{dur_comp:.1f}s" if dur_comp is not None else "N/A"

                            if size_orig is not None and size_comp is not None:
                                size_orig_mb = size_orig / (1024 * 1024)
                                size_comp_mb = size_comp / (1024 * 1024)
                                pct = (1 - size_comp / size_orig) * 100 if size_orig > 0 else 0
                                word = "Reducción" if pct >= 0 else "Aumento"
                                size_line = (
                                    f"   TAMAÑO: {size_orig_mb:.2f} MB -> "
                                    f"{size_comp_mb:.2f} MB ({word}: {abs(pct):.1f}%)"
                                )
                            else:
                                size_line = "   TAMAÑO: N/A"

                            report_data.append(
                                f"ARCHIVO: {filename}\n"
                                f"   RESULTADO: {status} "
                                f"(Orig: {orig_str} | Comp: {comp_str})\n"
                                f"{size_line}\n"
                            )

                        process_next(index + 1)

                    processor._run_command_in_thread(
                        cmd,
                        f"Compressed: {filename}",
                        "Compressing",
                        on_complete=on_done,
                        total_items=total_files,
                        current_item_idx=index,
                        probe_path=in_path,
                        cleanup_path=out_path
                    )
                else:
                    if gen_report:
                        try:
                            report_path = os.path.join(folder, "reporte_compresion.txt")
                            with open(report_path, "w", encoding="utf-8") as f:
                                f.write("\n".join(report_data))
                            show_snackbar("All compressions completed! Report generated.")
                        except OSError as ex:
                            show_error_dialog("No se pudo escribir el reporte", str(ex))
                    else:
                        show_snackbar("All compressions completed!")

            process_next(0)
        except Exception as ex:
            if acquired:
                processor.end()
            show_error_dialog("Error Compresión", str(ex))

    # ---------------------------------------------------------------------------
    # COMBINE
    # ---------------------------------------------------------------------------
    def combine_media(e):
        acquired = False
        try:
            if not state.visual_concat_path or not state.audio_path:
                show_snackbar("Please select visual and audio first!", color=ft.Colors.ORANGE)
                return

            if not os.path.exists(state.visual_concat_path) or not os.path.exists(state.audio_path):
                show_snackbar("Alguno de los archivos seleccionados ya no existe.", color=ft.Colors.ORANGE)
                return

            if not require_ffmpeg():
                return

            audio_duration = processor.get_duration(state.audio_path)
            if audio_duration is None or audio_duration <= 0:
                show_error_dialog("Error", "No se pudo leer la duración del audio seleccionado.")
                return

            if not require_idle():
                return
            acquired = True

            output_name = Path(state.audio_path).with_suffix('.mp4').name
            output_file = Path(
                get_unique_path(
                    str(Path(state.audio_path).parent),
                    output_name
                )
            )

            cmd = [state.ffmpeg_path, '-y', '-hide_banner']

            if state.is_visual_concat_video:
                cmd.extend(['-stream_loop', '-1', '-i', state.visual_concat_path])
            else:
                cmd.extend(['-loop', '1', '-framerate', '1', '-i', state.visual_concat_path])

            cmd.extend(['-i', state.audio_path, '-map', '0:v:0', '-map', '1:a:0'])

            if state.is_visual_concat_video:
                cmd.extend([
                    '-c:v', 'libx264',
                    '-preset', 'veryfast',
                    '-crf', '26'
                ])
            else:
                cmd.extend([
                    '-c:v', 'libx264',
                    '-tune', 'stillimage',
                    '-preset', 'veryfast',
                    '-crf', '26',
                    '-r', '1'
                ])

            cmd.extend([
                '-vf',
                'scale=1920:1080:force_original_aspect_ratio=decrease,'
                'pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=black',
                '-pix_fmt', 'yuv420p',
                '-c:a', 'aac',
                '-shortest',
                '-t', f"{audio_duration:.3f}",
                '-movflags', '+faststart',
                str(output_file)
            ])

            processor._run_command_in_thread(
                cmd,
                f"Video created: {output_file.name}",
                "Combining Media",
                expected_seconds=audio_duration,
                cleanup_path=str(output_file)
            )
        except Exception as ex:
            if acquired:
                processor.end()
            show_error_dialog("Error Combine", str(ex))

    # ---------------------------------------------------------------------------
    # CUT
    # ---------------------------------------------------------------------------
    def cut_video(e):
        acquired = False
        try:
            start_val = (start_time_field.value or "").strip()
            end_val = (end_time_field.value or "").strip()

            if not state.video_for_cut_path or not start_val or not end_val:
                show_snackbar("Please enter all details!", color=ft.Colors.ORANGE)
                return

            if not os.path.exists(state.video_for_cut_path):
                show_snackbar("El video seleccionado ya no existe.", color=ft.Colors.ORANGE)
                return

            if not TIME_RE.match(start_val) or not TIME_RE.match(end_val):
                show_snackbar("Formato de tiempo inválido. Usa HH:MM:SS o MM:SS", color=ft.Colors.ORANGE)
                return

            if time_to_seconds(end_val) <= time_to_seconds(start_val):
                show_snackbar("El tiempo final debe ser mayor al inicial", color=ft.Colors.ORANGE)
                return

            if not require_ffmpeg():
                return

            if not require_idle():
                return
            acquired = True

            src = Path(state.video_for_cut_path)
            output_path = get_unique_path(
                str(src.parent),
                f"rf_{src.stem}{src.suffix}"
            )

            cmd, expected = build_cut_cmd(
                str(src),
                output_path,
                start_val,
                end_val,
                None
            )

            processor._run_command_in_thread(
                cmd,
                f"Video cut: {Path(output_path).name}",
                "Video Cutting",
                expected_seconds=expected,
                cleanup_path=output_path
            )
        except Exception as ex:
            if acquired:
                processor.end()
            show_error_dialog("Error Cut", str(ex))

    # ---------------------------------------------------------------------------
    # MULTI-CUT
    # ---------------------------------------------------------------------------
    def show_multi_cut_preview(total, plan_lines, warning_lines, on_confirm):
        dlg = None

        def confirm(ev):
            close_modal(dlg)
            on_confirm()

        def cancel(ev):
            close_modal(dlg)

        controls = [
            ft.Text(f"Se procesarán {total} video(s):", weight=ft.FontWeight.BOLD)
        ]
        controls += [
            ft.Text(line, font_family=MONO_FONT, size=12, selectable=True)
            for line in plan_lines
        ]

        if warning_lines:
            controls.append(ft.Divider())
            controls.append(
                ft.Text("Avisos:", weight=ft.FontWeight.BOLD, color=ft.Colors.ORANGE)
            )
            controls += [
                ft.Text(line, size=12, color=ft.Colors.ORANGE_300, selectable=True)
                for line in warning_lines
            ]

        dlg = ft.AlertDialog(
            title=ft.Text("Multi-Cut: confirmar", color=state.accent_color),
            content=ft.Container(
                content=ft.Column(controls, scroll=ft.ScrollMode.ALWAYS, spacing=4),
                width=400,
                height=300,
                padding=10
            ),
            actions=[
                ft.TextButton("Cancelar", on_click=cancel),
                ft.FilledButton(
                    "Procesar",
                    on_click=confirm,
                    style=ft.ButtonStyle(bgcolor=state.accent_color, color="black")
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        open_modal(dlg)

    def process_multi_cuts(e):
        try:
            txt = multi_cut_input_field.value or ""
            folder = state.multi_cut_folder

            if not folder or not txt.strip():
                show_snackbar("Please select a folder and enter instructions!", color=ft.Colors.ORANGE)
                return

            if not os.path.isdir(folder):
                show_snackbar("La carpeta seleccionada ya no existe.", color=ft.Colors.ORANGE)
                return

            if processor.busy:
                show_snackbar("Ya hay un proceso en curso. Espera o cancélalo.", color=ft.Colors.ORANGE)
                return

            if not require_ffmpeg():
                return

            jobs, invalid, ignored = parse_multi_cut_lines(txt)

            warnings = []
            for line in ignored:
                warnings.append(f"• No reconocida (se omite): «{line}»")
            for line in invalid:
                warnings.append(f"• Tiempo inválido (fin ≤ inicio): «{line}»")

            if not jobs:
                show_error_dialog(
                    "Multi-Cut",
                    "No se reconocieron instrucciones válidas.\n\n"
                    "Formato: ID INICIO - FIN [r90 | r-90 | r180]\n"
                    "Ejemplos:\n  4632 00:04 - 00:35\n  2739 r90\n"
                    "  1510 01:10 - 01:40 r-90"
                    + ("\n\n" + "\n".join(warnings) if warnings else "")
                )
                return

            video_files = sorted(
                f for f in os.listdir(folder)
                if f.lower().endswith(VIDEO_EXTS)
                and not f.lower().startswith('c_')
                and os.path.isfile(os.path.join(folder, f))
            )

            if not video_files:
                show_error_dialog("Multi-Cut", "No hay videos en la carpeta seleccionada.")
                return

            plan, missing, ambiguous = build_multi_cut_plan(jobs, video_files)

            if not plan and not ambiguous:
                plan = list(zip(jobs, video_files))
                missing = jobs[len(plan):]
                warnings.insert(
                    0,
                    "• Ningún ID coincidió con los nombres de archivo: se emparejan "
                    "por orden alfabético. Revisa que sea lo que quieres."
                )

            for job in missing:
                warnings.append(f"• Sin video para el ID {job['id']} (se omite)")
            for job, matches in ambiguous:
                warnings.append(
                    f"• El ID {job['id']} coincide con varios archivos "
                    f"({', '.join(matches)}); se omite"
                )

            if not plan:
                show_error_dialog("Multi-Cut", "No hay nada que procesar.\n\n" + "\n".join(warnings))
                return

            commands = []
            plan_lines = []
            reserved = set()

            for job, video in plan:
                src = os.path.join(folder, video)
                out = get_unique_path(folder, f"c_{video}", reserved)
                cmd, expected = build_cut_cmd(
                    src,
                    out,
                    job['start'],
                    job['end'],
                    job['rotation']
                )
                commands.append({
                    'cmd': cmd,
                    'out': out,
                    'expected': expected,
                    'probe': None if expected else src,
                })
                plan_lines.append(f"{job['id']} → {video}\n    {describe_job(job)}")

            total = len(commands)
            results = []

            def show_summary():
                ok = sum(1 for r in results if r['status'] == 'ok')
                failed = [r for r in results if r['status'] == 'error']
                cancelled = any(r['status'] == 'cancelled' for r in results)

                head = f"Completados: {ok}/{total}"
                if cancelled:
                    head = "Proceso cancelado. " + head

                if failed:
                    parts = [head, ""]
                    for r in failed:
                        tail = "\n".join(r['detail'].strip().splitlines()[-6:])
                        parts.append(f"✗ {r['name']}\n{tail}\n")
                    show_error_dialog(f"Multi-Cut: {len(failed)} error(es)", "\n".join(parts))
                elif cancelled:
                    show_snackbar(head, color=ft.Colors.ORANGE)
                else:
                    show_snackbar(f"Multi-Cut completado: {ok} video(s)")

            def run_multi(idx):
                if idx >= total:
                    show_summary()
                    return

                job = commands[idx]
                out_name = Path(job['out']).name

                def on_result(status, detail="", name=out_name):
                    results.append({'name': name, 'status': status, 'detail': detail})
                    if status == 'cancelled':
                        show_summary()

                processor._run_command_in_thread(
                    job['cmd'],
                    f"Procesado: {out_name}",
                    "Multi-Cutting",
                    on_complete=lambda: run_multi(idx + 1),
                    total_items=total,
                    current_item_idx=idx,
                    probe_path=job['probe'],
                    expected_seconds=job['expected'],
                    cleanup_path=job['out'],
                    on_result=on_result
                )

            def start_batch():
                if not require_idle():
                    return
                try:
                    run_multi(0)
                except Exception as ex:
                    processor.end()
                    show_error_dialog("Error en Multi-Cut", str(ex))

            show_multi_cut_preview(total, plan_lines, warnings, start_batch)

        except Exception as ex:
            show_error_dialog("Error en Multi-Cut", str(ex))

    # ---------------------------------------------------------------------------
    # RENAME
    # ---------------------------------------------------------------------------
    def iter_rename_targets(folder: str):
        for filename in sorted(os.listdir(folder)):
            if filename.startswith('.') or filename in PROTECTED_FILES:
                continue

            path = os.path.join(folder, filename)
            if os.path.isfile(path):
                yield filename, path

    def do_random_rename(folder: str, length: int):
        try:
            if not os.path.isdir(folder):
                show_snackbar("La carpeta seleccionada ya no existe.", color=ft.Colors.ORANGE)
                return

            errors, pairs = [], []
            alphabet = string.ascii_letters + string.digits
            reserved = set()

            try:
                for filename, old_path in list(iter_rename_targets(folder)):
                    ext = os.path.splitext(filename)[1]
                    new_filename = ''.join(random.choices(alphabet, k=length)) + ext
                    new_path = get_unique_path(folder, new_filename, reserved)

                    try:
                        os.rename(old_path, new_path)
                        pairs.append((filename, os.path.basename(new_path)))
                    except OSError as ex:
                        errors.append(f"{filename}: {ex}")
            finally:
                try:
                    write_rename_log(folder, "Randomize", pairs)
                except OSError as ex:
                    errors.append(f"No se pudo escribir {RENAME_LOG_NAME}: {ex}")

            if errors:
                show_error_dialog("Errores al renombrar", "\n".join(errors))

            if pairs:
                show_snackbar(f"Renamed {len(pairs)} files! Log: {RENAME_LOG_NAME}")
            elif not errors:
                show_snackbar("No files were renamed.", color=ft.Colors.ORANGE)

        except Exception as ex:
            show_error_dialog("Error Random Rename", str(ex))

    def process_random_rename(e):
        try:
            folder = state.rename_folder

            if not folder or not os.path.isdir(folder):
                show_snackbar("Please select a folder first!", color=ft.Colors.ORANGE)
                return

            length_val = (rename_length_field.value or "8").strip()

            try:
                length = int(length_val)
                if length < 1 or length > 200:
                    raise ValueError()
            except ValueError:
                show_snackbar("Error: la longitud debe ser un número entero positivo", color=ft.Colors.ORANGE)
                return

            targets = [name for name, _ in iter_rename_targets(folder)]
            if not targets:
                show_snackbar("No files to rename.", color=ft.Colors.ORANGE)
                return

            dlg = None

            def confirm(ev):
                close_modal(dlg)
                do_random_rename(folder, length)

            def cancel(ev):
                close_modal(dlg)

            sample_lines = [f"• {n}" for n in targets[:5]]
            if len(targets) > 5:
                sample_lines.append(f"… y {len(targets) - 5} más")

            dlg = ft.AlertDialog(
                title=ft.Text("¿Renombrar al azar?", color=ft.Colors.RED_400, weight=ft.FontWeight.BOLD),
                content=ft.Container(
                    content=ft.Column(
                        [
                            ft.Text(
                                f"Se renombrarán {len(targets)} archivo(s) de "
                                f"«{Path(folder).name}» con nombres aleatorios de "
                                f"{length} caracteres (se conserva la extensión)."
                            ),
                            ft.Text(
                                "Afecta a TODOS los archivos de la carpeta, no solo a videos.",
                                size=12,
                                color=ft.Colors.ORANGE_300
                            ),
                            ft.Text(
                                f"El mapeo antiguo → nuevo se guardará en {RENAME_LOG_NAME} dentro de la carpeta.",
                                size=12,
                                color=ft.Colors.GREY_400
                            ),
                            ft.Divider(),
                            *[
                                ft.Text(line, font_family=MONO_FONT, size=12)
                                for line in sample_lines
                            ],
                        ],
                        scroll=ft.ScrollMode.AUTO,
                        spacing=6
                    ),
                    width=400,
                    height=280,
                    padding=10
                ),
                actions=[
                    ft.TextButton("Cancelar", on_click=cancel),
                    ft.FilledButton(
                        "Renombrar",
                        on_click=confirm,
                        style=ft.ButtonStyle(bgcolor=ft.Colors.RED_400, color="black")
                    ),
                ],
                actions_alignment=ft.MainAxisAlignment.END,
            )
            open_modal(dlg)

        except Exception as ex:
            show_error_dialog("Error Random Rename", str(ex))

    def process_find_replace(e):
        try:
            find_text = find_text_field.value or ""

            if not state.rename_folder or not find_text.strip():
                show_snackbar("Please select a folder and enter text to find!", color=ft.Colors.ORANGE)
                return

            if not os.path.isdir(state.rename_folder):
                show_snackbar("La carpeta seleccionada ya no existe.", color=ft.Colors.ORANGE)
                return

            replace_text = replace_text_field.value or ""

            if any(ch in replace_text for ch in '/\\'):
                show_snackbar("El texto de reemplazo no puede contener / ni \\", color=ft.Colors.ORANGE)
                return

            folder = state.rename_folder
            errors, pairs = [], []
            reserved = set()

            try:
                for filename, old_path in list(iter_rename_targets(folder)):
                    name, ext = os.path.splitext(filename)

                    if find_text not in name:
                        continue

                    new_name = name.replace(find_text, replace_text).strip()

                    if not new_name:
                        errors.append(f"{filename}: el nombre resultante quedaría vacío")
                        continue

                    new_filename = new_name + ext

                    if new_filename == filename:
                        continue

                    new_path = get_unique_path(folder, new_filename, reserved)

                    try:
                        os.rename(old_path, new_path)
                        pairs.append((filename, os.path.basename(new_path)))
                    except OSError as ex:
                        errors.append(f"{filename}: {ex}")
            finally:
                try:
                    write_rename_log(folder, f"Find/Replace '{find_text}' -> '{replace_text}'", pairs)
                except OSError as ex:
                    errors.append(f"No se pudo escribir {RENAME_LOG_NAME}: {ex}")

            if errors:
                show_error_dialog("Errores al renombrar", "\n".join(errors))

            if pairs:
                show_snackbar(f"Updated {len(pairs)} files! Log: {RENAME_LOG_NAME}")
            elif not errors:
                show_snackbar("No files were renamed.", color=ft.Colors.ORANGE)

        except Exception as ex:
            show_error_dialog("Error Find Replace", str(ex))

    # ---------------------------------------------------------------------------
    # Ajustes
    # ---------------------------------------------------------------------------
    def settings_summary():
        return (
            f"⚡ CRF: {state.crf} | Preset: {state.preset} | "
            f"Reporte: {'Sí' if state.gen_report else 'No'}"
        )

    def update_theme_colors():
        page.theme = ft.Theme(color_scheme_seed=state.accent_color)
        tab_bar.label_color = state.accent_color
        tab_bar.indicator_color = state.accent_color
        settings_btn.icon_color = state.accent_color
        compress_settings_text.color = state.accent_color

        for btn in accent_buttons:
            btn.style = ft.ButtonStyle(bgcolor=state.accent_color, color="black")

    def sync_settings_controls():
        ffmpeg_path_text.value = state.ffmpeg_path
        crf_slider.value = state.crf
        crf_text.value = f"CRF (Calidad): {int(state.crf)}"
        preset_dropdown.value = state.preset
        gen_report_switch.value = state.gen_report
        color_dropdown.value = state.accent_color

    def open_settings_dialog(e):
        sync_settings_controls()
        dlg = None

        async def save_settings(ev):
            state.crf = int(crf_slider.value)

            state.preset = preset_dropdown.value or "medium"
            if state.preset not in FFMPEG_PRESETS:
                state.preset = "medium"

            state.gen_report = bool(gen_report_switch.value)

            state.accent_color = color_dropdown.value or "amber400"
            if state.accent_color not in VALID_ACCENTS:
                state.accent_color = "amber400"

            try:
                await prefs.set("concat_crf", state.crf)
                await prefs.set("concat_preset", state.preset)
                await prefs.set("concat_gen_report", state.gen_report)
                await prefs.set("concat_accent_color", state.accent_color)
            except Exception as ex:
                show_error_dialog("No se pudieron guardar los ajustes", str(ex))

            update_theme_colors()
            compress_settings_text.value = settings_summary()
            close_modal(dlg)
            show_snackbar("Ajustes guardados correctamente")
            page.update()

        async def clear_config(ev):
            for key in (
                "concat_ffmpeg_path",
                "concat_crf",
                "concat_preset",
                "concat_gen_report",
                "concat_accent_color"
            ):
                try:
                    await prefs.remove(key)
                except Exception:
                    pass

            state.ffmpeg_path = "ffmpeg"
            state.crf = 25
            state.preset = "medium"
            state.gen_report = True
            state.accent_color = "amber400"
            state.ffmpeg_checked = None
            state.ffmpeg_version = None

            sync_settings_controls()
            update_theme_colors()
            compress_settings_text.value = settings_summary()
            close_modal(dlg)
            show_snackbar("Configuración borrada. Se restauraron los valores por defecto.", color="red400")
            page.update()

        def cancel(ev):
            close_modal(dlg)

        dlg = ft.AlertDialog(
            title=ft.Text("Ajustes", color=state.accent_color),
            content=ft.Container(
                content=ft.Column(
                    [
                        ft.Text("Ruta FFmpeg", weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE),
                        ft.Text(
                            "Por defecto usa el del sistema. Si falla, selecciona el ejecutable manualmente.",
                            size=11,
                            color=ft.Colors.GREY_400
                        ),
                        ffmpeg_path_text,
                        ft.FilledButton("Seleccionar ffmpeg", on_click=pick_ffmpeg_exe, icon=ft.Icons.FIND_IN_PAGE),
                        ft.Divider(),
                        ft.Text("Compresión", weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE),
                        crf_text,
                        crf_slider,
                        ft.Text("Preset (Velocidad):", size=13),
                        preset_dropdown,
                        ft.Divider(),
                        gen_report_switch,
                        ft.Divider(),
                        ft.Text("Apariencia", weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE),
                        ft.Text("Color de acento de la app:", size=13),
                        color_dropdown,
                        ft.Divider(),
                        ft.FilledButton(
                            "Borrar configuración guardada",
                            on_click=clear_config,
                            icon=ft.Icons.DELETE_FOREVER,
                            style=ft.ButtonStyle(bgcolor=ft.Colors.RED_400, color="black")
                        ),
                    ],
                    tight=True,
                    scroll=ft.ScrollMode.ALWAYS,
                    width=320
                ),
                padding=10,
                height=400
            ),
            actions=[
                ft.TextButton("Cancelar", on_click=cancel),
                ft.FilledButton(
                    "Guardar",
                    on_click=save_settings,
                    style=ft.ButtonStyle(bgcolor=state.accent_color, color="black")
                ),
            ]
        )
        open_modal(dlg)

    # ---------------------------------------------------------------------------
    # Controles
    # ---------------------------------------------------------------------------
    visual_concat_path_text = ft.Text("No visual selected", color=ft.Colors.GREY_300)
    audio_path_text = ft.Text("No audio selected", color=ft.Colors.GREY_300)
    folder_path_text = ft.Text("No folder selected", color=ft.Colors.GREY_300)
    video_cut_path_text = ft.Text("No video selected", color=ft.Colors.GREY_300)
    multi_cut_folder_path_text = ft.Text("No folder selected", color=ft.Colors.GREY_300)
    rename_folder_path_text = ft.Text("No folder selected", color=ft.Colors.GREY_300)

    ffmpeg_path_text = ft.Text(
        state.ffmpeg_path,
        color=ft.Colors.GREY_300,
        max_lines=2,
        selectable=True,
        width=300
    )

    crf_text = ft.Text(f"CRF (Calidad): {int(state.crf)}", size=13)

    def update_crf_label(e):
        crf_text.value = f"CRF (Calidad): {int(crf_slider.value)}"
        crf_text.update()

    crf_slider = ft.Slider(
        min=18,
        max=30,
        divisions=12,
        label="{value}",
        value=state.crf,
        width=300,
        on_change=update_crf_label
    )

    preset_dropdown = ft.Dropdown(
        options=[
            ft.dropdown.Option(key=p, text=p)
            for p in FFMPEG_PRESETS
        ],
        value=state.preset,
        width=200
    )

    gen_report_switch = ft.Switch(
        label="Generar reporte de compresión",
        value=state.gen_report
    )

    color_options = [
        ft.dropdown.Option(key=key, text=text)
        for key, text in ACCENT_COLOR_OPTIONS
    ]

    color_dropdown = ft.Dropdown(
        options=color_options,
        value=state.accent_color,
        width=200
    )

    start_time_field = ft.TextField(label="Start Time (HH:MM:SS)", hint_text="00:00:00", width=200)
    end_time_field = ft.TextField(label="End Time (HH:MM:SS)", hint_text="00:00:00", width=200)

    rename_length_field = ft.TextField(
        label="Length",
        value="8",
        width=100,
        keyboard_type=ft.KeyboardType.NUMBER,
        text_align=ft.TextAlign.CENTER
    )

    find_text_field = ft.TextField(label="Find (e.g. VID_)", hint_text="text to remove", width=160)
    replace_text_field = ft.TextField(label="Replace (Blank to delete)", hint_text="new text", width=200)

    multi_cut_input_field = ft.TextField(
        label="Instructions (ID START - END [ROT])",
        multiline=True,
        height=150,
        hint_text="4632 00:04 - 00:35\n2739 r90\n1510 01:10 - 01:40 r-90",
        border=ft.NoInputBorder(),
        bgcolor="transparent",
        color=ft.Colors.WHITE
    )

    multi_cut_container = ft.Container(
        content=ft.Column([multi_cut_input_field], height=150),
        width=350,
        height=165,
        bgcolor=ft.Colors.with_opacity(0.1, ft.Colors.GREY),
        border=ft.Border.all(1, ft.Colors.GREY),
        border_radius=5,
        padding=5
    )

    compress_settings_text = ft.Text(settings_summary(), size=11, color=state.accent_color)

    combine_btn = ft.FilledButton("Combine", on_click=combine_media, icon=ft.Icons.MERGE_TYPE)
    compress_btn = ft.FilledButton("Compress", on_click=compress_videos, icon=ft.Icons.COMPRESS)
    cut_btn = ft.FilledButton("Cut Video", on_click=cut_video, icon=ft.Icons.CONTENT_CUT)
    multi_cut_btn = ft.FilledButton("Process All Cuts", on_click=process_multi_cuts, icon=ft.Icons.PLAYLIST_PLAY)
    rename_random_btn = ft.FilledButton("Randomize All", on_click=process_random_rename, icon=ft.Icons.SHUFFLE)
    rename_find_btn = ft.FilledButton("Process Text", on_click=process_find_replace, icon=ft.Icons.FIND_REPLACE)

    accent_buttons = [
        combine_btn,
        compress_btn,
        cut_btn,
        multi_cut_btn,
        rename_random_btn,
        rename_find_btn,
    ]

    center = ft.CrossAxisAlignment.CENTER

    def build_combine_content():
        return ft.Column(
            [
                ft.Text("Combine Visual with Audio", size=25, weight=ft.FontWeight.BOLD),
                ft.Divider(),
                ft.FilledButton(
                    "Select Visual (Image/Video)",
                    on_click=pick_visual_concat,
                    style=ft.ButtonStyle(bgcolor=ft.Colors.PURPLE_400, color="black"),
                    icon=ft.Icons.IMAGE_SEARCH
                ),
                visual_concat_path_text,
                ft.FilledButton(
                    "Select Audio",
                    on_click=pick_audio,
                    icon=ft.Icons.AUDIO_FILE,
                    style=ft.ButtonStyle(bgcolor=ft.Colors.DEEP_PURPLE_400, color="black")
                ),
                audio_path_text,
                combine_btn,
            ],
            horizontal_alignment=center,
            scroll=ft.ScrollMode.AUTO
        )

    def build_compress_content():
        return ft.Column(
            [
                ft.Text("Compress Videos", size=24, weight=ft.FontWeight.BOLD),
                ft.Divider(),
                ft.FilledButton(
                    "Select Folder",
                    on_click=pick_folder,
                    icon=ft.Icons.FOLDER,
                    style=ft.ButtonStyle(bgcolor=ft.Colors.INDIGO_400, color="black")
                ),
                folder_path_text,
                compress_settings_text,
                compress_btn,
            ],
            horizontal_alignment=center,
            scroll=ft.ScrollMode.AUTO
        )

    def build_cut_content():
        return ft.Column(
            [
                ft.Text("Cut Video", size=25, weight=ft.FontWeight.BOLD),
                ft.Divider(),
                ft.FilledButton(
                    "Select Video",
                    on_click=pick_video_for_cut,
                    icon=ft.Icons.VIDEO_FILE,
                    style=ft.ButtonStyle(bgcolor=ft.Colors.ORANGE_400, color="black")
                ),
                video_cut_path_text,
                ft.Row([start_time_field, end_time_field], alignment=ft.MainAxisAlignment.CENTER, wrap=True),
                cut_btn,
            ],
            horizontal_alignment=center,
            scroll=ft.ScrollMode.AUTO
        )

    def build_multi_cut_content():
        return ft.Column(
            [
                ft.Text("Multi-Cut", size=25, weight=ft.FontWeight.BOLD),
                ft.Divider(),
                ft.FilledButton(
                    "Select Videos Folder",
                    on_click=pick_multi_cut_folder,
                    icon=ft.Icons.FOLDER_SPECIAL,
                    style=ft.ButtonStyle(bgcolor=ft.Colors.CYAN_400, color="black")
                ),
                multi_cut_folder_path_text,
                multi_cut_container,
                multi_cut_btn,
            ],
            horizontal_alignment=center,
            scroll=ft.ScrollMode.AUTO
        )

    def build_rename_content():
        return ft.Column(
            [
                ft.Text("File Renamer", size=25, weight=ft.FontWeight.BOLD),
                ft.Divider(),
                ft.FilledButton(
                    "Select Folder",
                    on_click=pick_rename_folder,
                    icon=ft.Icons.FOLDER_OPEN,
                    style=ft.ButtonStyle(bgcolor=ft.Colors.BLUE_GREY_400, color="black")
                ),
                rename_folder_path_text,
                ft.Divider(height=10, color=ft.Colors.TRANSPARENT),
                ft.Text("1. Randomize File Names", size=16, color=ft.Colors.WHITE_70),
                ft.Row([rename_length_field, rename_random_btn], alignment=ft.MainAxisAlignment.CENTER),
                ft.Divider(height=10, color=ft.Colors.TRANSPARENT),
                ft.Text("2. Find & Replace", size=16, color=ft.Colors.WHITE_70),
                ft.Row([find_text_field, replace_text_field], alignment=ft.MainAxisAlignment.CENTER, wrap=True),
                rename_find_btn,
            ],
            horizontal_alignment=center,
            scroll=ft.ScrollMode.AUTO
        )

    tab_builders = [
        build_combine_content,
        build_compress_content,
        build_cut_content,
        build_multi_cut_content,
        build_rename_content,
    ]

    tab_bar = ft.TabBar(
        scrollable=True,
        label_color=state.accent_color,
        unselected_label_color=ft.Colors.GREY,
        indicator_color=state.accent_color,
        tabs=[
            ft.Tab(label="Combine", icon=ft.Icons.MERGE_TYPE),
            ft.Tab(label="Compress", icon=ft.Icons.COMPRESS),
            ft.Tab(label="Cut", icon=ft.Icons.CONTENT_CUT),
            ft.Tab(label="Multi-Cut", icon=ft.Icons.PLAYLIST_PLAY),
            ft.Tab(label="Rename", icon=ft.Icons.EDIT_NOTE),
        ],
    )

    tab_slots = [ft.Container(padding=18) for _ in range(5)]
    tab_slots[0].content = tab_builders[0]()

    def ensure_tab_content(index: int):
        slot = tab_slots[index]
        if slot.content is None:
            slot.content = tab_builders[index]()
            slot.update()

    def on_tab_click(e):
        # Se construye antes de que Tabs cambie de página para no mostrar
        # un hueco vacío durante la transición.
        try:
            ensure_tab_content(int(e.data))
        except (TypeError, ValueError, IndexError):
            pass

    def on_tab_change(e):
        try:
            ensure_tab_content(int(e.control.selected_index))
        except (TypeError, ValueError, IndexError):
            pass

    tab_bar.on_click = on_tab_click

    tab_view = ft.TabBarView(
        expand=True,
        controls=tab_slots,
    )

    main_tabs = ft.Tabs(
        length=5,
        selected_index=0,
        animation_duration=0,
        on_change=on_tab_change,
        expand=True,
        content=ft.Column([tab_bar, tab_view], expand=True, spacing=0),
    )

    settings_btn = ft.IconButton(
        icon=ft.Icons.SETTINGS,
        icon_color=state.accent_color,
        on_click=open_settings_dialog,
        tooltip="Ajustes"
    )

    update_theme_colors()

    bottom_row = ft.Row(
        [bottom_indicator, settings_btn],
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        spacing=0
    )

    page.add(main_tabs, bottom_row)


if __name__ == "__main__":
    ft.run(main)