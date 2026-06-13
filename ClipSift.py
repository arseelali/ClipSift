#!/usr/bin/env python3
"""
Fast media review and rename tool.

Usage:
    python3 ClipSift.py

Controls:
    Enter: rename to the text box value, or skip if the box is blank
    Escape: quit
    Primary modifier + Z: undo last rename
    Primary modifier + A/D: previous/next file
    Primary modifier + O: open the current file in the default app
    Primary modifier + S: copy the current filename into the text box
    Primary modifier + F: search the file list
    Primary modifier + P: play/pause video preview
    Up arrow: recall previous nonblank filename input
    Click a file in the right-side list to jump to it
"""

from __future__ import annotations

import argparse
import json
import queue
import os
import platform
import re
import signal
import shutil
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox, ttk


IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".bmp",
    ".tif",
    ".tiff",
    ".heic",
    ".heif",
    ".webp",
    ".raw",
    ".cr2",
    ".nef",
    ".arw",
    ".dng",
}

VIDEO_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".m4v",
    ".avi",
    ".mkv",
    ".webm",
    ".wmv",
    ".flv",
    ".mpeg",
    ".mpg",
    ".3gp",
    ".mts",
    ".m2ts",
}

MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS
NO_VIDEO_FRAME = object()
SESSION_PATH = Path.home() / ".ClipSift.json"
LEGACY_SESSION_PATH = Path.home() / ".videoRenameTool.json"
APP_DIR = Path(__file__).resolve().parent


def app_base_dir() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return APP_DIR


BASE_DIR = app_base_dir()


def first_existing_dir(candidates: list[Path]) -> Path:
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def packaged_contents_dir() -> Path | None:
    if not getattr(sys, "frozen", False):
        return None
    executable = Path(sys.executable).resolve()
    for parent in executable.parents:
        if parent.name == "Contents":
            return parent
    return None


CONTENTS_DIR = packaged_contents_dir()
ASSET_DIR = first_existing_dir(
    [
        BASE_DIR / "assets",
        APP_DIR / "assets",
        *(([CONTENTS_DIR / "Resources" / "assets"] if CONTENTS_DIR else [])),
    ]
)


def bundled_binary_name(name: str) -> str:
    return name


def executable_matches(base_name: str) -> list[str]:
    return [base_name, f"{base_name}*"]


def media_binary(name: str) -> str | None:
    packaged_candidates: list[Path] = []
    if CONTENTS_DIR:
        packaged_candidates.extend(
            [
                CONTENTS_DIR / "Frameworks" / "bin" / bundled_binary_name(name),
                CONTENTS_DIR / "Resources" / "bin" / bundled_binary_name(name),
            ]
        )
    candidates = [
        *packaged_candidates,
        BASE_DIR / "bin" / bundled_binary_name(name),
        APP_DIR / "bin" / bundled_binary_name(name),
        Path("/opt/homebrew/bin") / name,
        Path("/usr/local/bin") / name,
        Path("/usr/bin") / name,
    ]
    for candidate in candidates:
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
        if candidate.parent.exists():
            for pattern in executable_matches(name):
                for match in sorted(candidate.parent.glob(pattern)):
                    if match.is_file() and os.access(match, os.X_OK):
                        return str(match)
    return shutil.which(name)


def system_name() -> str:
    return platform.system()


def subprocess_window_options() -> dict[str, int]:
    return {}


def primary_modifier() -> str:
    return "Command"


def reveal_label() -> str:
    return "Reveal in Finder"


def macos_appearance() -> str:
    if system_name() != "Darwin":
        return "light"
    try:
        result = subprocess.run(
            ["defaults", "read", "-g", "AppleInterfaceStyle"],
            capture_output=True,
            text=True,
            timeout=1,
        )
        return "dark" if result.stdout.strip().lower() == "dark" else "light"
    except Exception:
        return "light"


def shortcut_defaults() -> dict[str, str]:
    modifier = primary_modifier()
    return {
        "accept": "Enter",
        "quit": "Escape",
        "open_folder": f"{modifier}-Shift-O",
        "reload_folder": f"{modifier}-Shift-R",
        "undo_rename": f"{modifier}-Z",
        "open_current": f"{modifier}-O",
        "fullscreen_media": f"{modifier}-F",
        "previous_file": f"{modifier}-A",
        "next_file": f"{modifier}-D",
        "use_current_name": f"{modifier}-S",
        "focus_search": f"{modifier}-Shift-F",
        "focus_rename": f"{modifier}-R",
        "toggle_playback": f"{modifier}-P",
        "toggle_mute": f"{modifier}-M",
        "previous_input": "Up",
    }


def shortcut_action_labels() -> dict[str, str]:
    return {
        "accept": "Rename / Skip",
        "quit": "Quit",
        "open_folder": "Open Folder",
        "reload_folder": "Reload Folder",
        "undo_rename": "Undo Rename",
        "open_current": "Open Current Media",
        "fullscreen_media": "Fullscreen Media",
        "previous_file": "Previous File",
        "next_file": "Next File",
        "use_current_name": "Use Current Filename",
        "focus_search": "Focus Search",
        "focus_rename": "Focus Rename Box",
        "toggle_playback": "Play/Pause Preview",
        "toggle_mute": "Toggle Video Mute",
        "previous_input": "Previous Filename Input",
    }


def default_fps_by_speed() -> dict[str, int]:
    return {
        "1x": 24,
        "2x": 24,
        "4x": 20,
        "8x": 15,
    }


def sanitize_fps_by_speed(value: object) -> dict[str, int]:
    defaults = default_fps_by_speed()
    if not isinstance(value, dict):
        return defaults

    sanitized = defaults.copy()
    for speed in defaults:
        try:
            fps = int(value.get(speed, defaults[speed]))
        except (TypeError, ValueError):
            fps = defaults[speed]
        sanitized[speed] = max(1, min(60, fps))
    return sanitized


def normalize_shortcut(value: str) -> str:
    aliases = {
        "cmd": "Command",
        "command": "Command",
        "option": "Alt",
        "alt": "Alt",
        "shift": "Shift",
        "return": "Enter",
        "enter": "Enter",
        "esc": "Escape",
        "escape": "Escape",
        "space": "Space",
        "up": "Up",
        "down": "Down",
        "left": "Left",
        "right": "Right",
    }
    parts = [part.strip() for part in re.split(r"[-+]", value.strip()) if part.strip()]
    normalized: list[str] = []
    for part in parts:
        lower = part.lower()
        if lower in aliases:
            token = aliases[lower]
        elif len(part) == 1:
            token = part.upper()
        else:
            token = part[:1].upper() + part[1:]
        if token not in normalized:
            normalized.append(token)
    return "-".join(normalized)


def shortcut_to_sequences(shortcut: str) -> list[str]:
    shortcut = normalize_shortcut(shortcut)
    if not shortcut:
        return []

    key_aliases = {
        "Enter": "Return",
        "Escape": "Escape",
        "Space": "space",
        "Up": "Up",
        "Down": "Down",
        "Left": "Left",
        "Right": "Right",
    }
    parts = shortcut.split("-")
    key = parts[-1]
    modifiers = parts[:-1]
    tk_key = key_aliases.get(key, key)
    sequences = [f"<{'-'.join(modifiers + [tk_key])}>"]
    if len(key) == 1 and key.isalpha():
        lower = f"<{'-'.join(modifiers + [key.lower()])}>"
        upper = f"<{'-'.join(modifiers + [key.upper()])}>"
        sequences = [lower, upper]
    return sequences


def event_to_shortcut(event: tk.Event) -> str | None:
    key = event.keysym
    if key in {"Shift_L", "Shift_R", "Command", "Meta_L", "Meta_R", "Alt_L", "Alt_R"}:
        return None

    parts: list[str] = []
    state = int(getattr(event, "state", 0))
    if state & 0x1:
        parts.append("Shift")
    if state & 0x8:
        parts.append("Alt")
    if state & 0x10 or state & 0x80:
        parts.append("Command")

    key_aliases = {
        "Return": "Enter",
        "Escape": "Escape",
        "space": "Space",
        "Up": "Up",
        "Down": "Down",
        "Left": "Left",
        "Right": "Right",
    }
    key_name = key_aliases.get(key, key.upper() if len(key) == 1 else key)
    parts.append(key_name)
    return normalize_shortcut("-".join(parts))


def natural_key(path: Path) -> list[object]:
    parts = re.split(r"(\d+)", path.name.lower())
    return [int(part) if part.isdigit() else part for part in parts]


def is_hidden_or_metadata_file(path: Path, root: Path) -> bool:
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        parts = path.parts
    return any(part.startswith(".") for part in parts)


def find_media(folder: Path, recursive: bool) -> list[Path]:
    iterator = folder.rglob("*") if recursive else folder.iterdir()
    return sorted(
        (
            path
            for path in iterator
            if path.is_file()
            and not is_hidden_or_metadata_file(path, folder)
            and path.suffix.lower() in MEDIA_EXTENSIONS
        ),
        key=natural_key,
    )


def clean_new_name(value: str, old_suffix: str) -> str:
    value = value.strip()
    if value.lower().endswith(old_suffix.lower()):
        value = value[: -len(old_suffix)]
    value = value.strip()
    if "/" in value or "\x00" in value:
        raise ValueError("File names cannot contain '/' or null characters.")
    return value


def next_available_path(target: Path) -> Path:
    if not target.exists():
        return target

    for number in range(2, 10000):
        candidate = target.with_name(f"{target.stem} {number}{target.suffix}")
        if not candidate.exists():
            return candidate

    raise FileExistsError(f"Could not find an available name based on {target.name}.")


def load_session() -> dict[str, object]:
    session_path = SESSION_PATH if SESSION_PATH.exists() else LEGACY_SESSION_PATH
    try:
        with session_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_session(data: dict[str, object]) -> None:
    try:
        with SESSION_PATH.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
    except Exception:
        pass


def make_quicklook_thumbnail(path: Path, output_dir: Path, size: int) -> Path | None:
    qlmanage = shutil.which("qlmanage")
    if not qlmanage:
        return None

    before = set(output_dir.glob("*.png"))
    try:
        subprocess.run(
            [qlmanage, "-t", "-s", str(size), "-o", str(output_dir), str(path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception:
        return None

    after = set(output_dir.glob("*.png"))
    created = sorted(after - before, key=lambda p: p.stat().st_mtime, reverse=True)
    if created:
        return created[0]

    existing = sorted(after, key=lambda p: p.stat().st_mtime, reverse=True)
    return existing[0] if existing else None


def make_preview(path: Path, output_dir: Path, size: int) -> Path | None:
    output_dir.mkdir(parents=True, exist_ok=True)
    return make_quicklook_thumbnail(path, output_dir, size)


def ffmpeg_video_pipe_commands(ffmpeg: str, path: Path, vf: str, speed: int) -> list[list[str]]:
    base_output = [
        "-i",
        str(path),
        "-an",
        "-vf",
        vf,
        "-f",
        "image2pipe",
        "-vcodec",
        "ppm",
        "-",
    ]
    prefix = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin"]
    commands: list[list[str]] = []
    if speed > 1:
        commands.append(prefix + ["-readrate", str(speed), "-readrate_initial_burst", "0"] + base_output)
        commands.append(prefix + ["-readrate", str(speed)] + base_output)
    commands.append(prefix + base_output)
    return commands


def read_ppm_frame(stream) -> bytes | None:
    def read_token() -> bytes | None:
        token = bytearray()
        while True:
            char = stream.read(1)
            if not char:
                return None
            if char == b"#":
                stream.readline()
                continue
            if char.isspace():
                continue
            token.extend(char)
            break

        while True:
            char = stream.read(1)
            if not char:
                return None
            if char.isspace():
                break
            token.extend(char)

        return bytes(token)

    magic = read_token()
    if magic != b"P6":
        return None

    width_token = read_token()
    height_token = read_token()
    max_value_token = read_token()
    if not width_token or not height_token or not max_value_token:
        return None

    try:
        width = int(width_token)
        height = int(height_token)
        max_value = int(max_value_token)
    except ValueError:
        return None

    if width <= 0 or height <= 0 or max_value > 255:
        return None

    pixel_count = width * height * 3
    pixels = stream.read(pixel_count)
    if len(pixels) != pixel_count:
        return None

    header = f"P6\n{width} {height}\n{max_value}\n".encode("ascii")
    return header + pixels


class RenameTool:
    def __init__(
        self,
        root: tk.Tk,
        initial_speed: int,
        initial_folder: Path | None = None,
        recursive: bool = False,
        initial_index: int = 0,
        shortcut_overrides: dict[str, str] | None = None,
        muted: bool = True,
        resume_on_launch: bool = True,
        fps_by_speed: dict[str, int] | None = None,
    ) -> None:
        self.root = root
        self.files: list[Path] = []
        self.folder: Path | None = None
        self.base_dir = Path.home()
        self.index = 0
        self.filtered_indices: list[int] = []
        self.reviewed_indices: set[int] = set()
        self.renamed_indices: set[int] = set()
        self.undo_stack: list[tuple[int, Path, Path]] = []
        self.preview_token = 0
        self.preview_image: tk.PhotoImage | None = None
        self.video_queue: queue.Queue[bytes | None] | None = None
        self.video_after_id: str | None = None
        self.video_stop_event: threading.Event | None = None
        self.video_process: subprocess.Popen | None = None
        self.audio_process: subprocess.Popen | None = None
        self.video_paused = False
        self.video_current_path: Path | None = None
        self.video_current_token: int | None = None
        self.resize_after_id: str | None = None
        self.last_preview_size: tuple[int, int] = (0, 0)
        self.fps_by_speed = sanitize_fps_by_speed(fps_by_speed)
        self.name_history: list[str] = []
        self.history_position: int | None = None
        self.syncing_file_list = False
        self.current_icon_mode: str | None = None
        self.icon_image: tk.PhotoImage | None = None
        self.tempdir = tempfile.TemporaryDirectory(prefix="video-rename-tool-")
        self.temp_path = Path(self.tempdir.name)

        self.root.title("ClipSift")
        self.root.geometry("1120x780")
        self.root.minsize(760, 560)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.status_var = tk.StringVar()
        self.file_var = tk.StringVar()
        self.kind_var = tk.StringVar()
        self.speed_var = tk.StringVar(value=f"{initial_speed}x")
        self.search_var = tk.StringVar()
        self.recursive_var = tk.BooleanVar(value=recursive)
        self.muted_var = tk.BooleanVar(value=muted)
        self.resume_on_launch_var = tk.BooleanVar(value=resume_on_launch)
        self.playback_var = tk.StringVar(value="Pause")
        self.mute_var = tk.StringVar()
        self.shortcuts = shortcut_defaults()
        if shortcut_overrides:
            for action, shortcut in shortcut_overrides.items():
                if action in self.shortcuts and isinstance(shortcut, str):
                    self.shortcuts[action] = normalize_shortcut(shortcut)
            defaults = shortcut_defaults()
            if self.shortcuts.get("focus_search") == defaults["fullscreen_media"]:
                self.shortcuts["focus_search"] = defaults["focus_search"]
            if self.shortcuts.get("reload_folder") == defaults["focus_rename"]:
                self.shortcuts["reload_folder"] = defaults["reload_folder"]
        self.bound_shortcut_sequences: list[tuple[tk.Misc, str]] = []
        self.footer_reveal_button: ttk.Button | None = None

        self.build_menu()
        self.build_ui()
        self.bind_shortcuts()
        self.update_app_icon()
        self.search_var.trace_add("write", lambda *_args: self.apply_filter())

        if initial_folder:
            self.open_folder(initial_folder, initial_index=initial_index, show_errors=False)
        else:
            self.show_welcome()

    def shortcut(self, action: str) -> str:
        return self.shortcuts.get(action, shortcut_defaults().get(action, ""))

    def update_app_icon(self) -> None:
        mode = macos_appearance() if system_name() == "Darwin" else "light"
        if mode != self.current_icon_mode:
            icon_path = ASSET_DIR / ("ClipSift-dark.png" if mode == "dark" else "ClipSift-light.png")
            try:
                if icon_path.exists():
                    self.icon_image = tk.PhotoImage(file=str(icon_path))
                    self.root.iconphoto(True, self.icon_image)
                self.current_icon_mode = mode
            except tk.TclError:
                pass
        self.root.after(5000, self.update_app_icon)

    def build_menu(self) -> None:
        menubar = tk.Menu(self.root)

        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(
            label="Open Folder...",
            accelerator=self.shortcut("open_folder"),
            command=self.choose_folder,
        )
        file_menu.add_command(
            label="Reload Folder",
            accelerator=self.shortcut("reload_folder"),
            command=self.reload_folder,
        )
        file_menu.add_checkbutton(
            label="Include Subfolders",
            variable=self.recursive_var,
            command=self.reload_folder,
        )
        file_menu.add_separator()
        file_menu.add_command(label=reveal_label(), command=self.reveal_current)
        if system_name() != "Darwin":
            file_menu.add_command(label="Settings...", command=self.show_settings)
        file_menu.add_command(label="Quit", accelerator=self.shortcut("quit"), command=self.close)
        menubar.add_cascade(label="File", menu=file_menu)

        edit_menu = tk.Menu(menubar, tearoff=False)
        edit_menu.add_command(
            label="Undo Rename",
            accelerator=self.shortcut("undo_rename"),
            command=self.undo_last_rename,
        )
        edit_menu.add_command(
            label="Use Current Filename",
            accelerator=self.shortcut("use_current_name"),
            command=self.fill_current_name,
        )
        edit_menu.add_command(
            label="Rename / Skip",
            accelerator=self.shortcut("accept"),
            command=self.accept,
        )
        edit_menu.add_command(
            label="Focus Search",
            accelerator=self.shortcut("focus_search"),
            command=self.focus_search,
        )
        edit_menu.add_separator()
        edit_menu.add_command(
            label="Customize Keyboard Shortcuts...",
            command=self.show_shortcut_editor,
        )
        menubar.add_cascade(label="Edit", menu=edit_menu)

        navigate_menu = tk.Menu(menubar, tearoff=False)
        navigate_menu.add_command(
            label="Previous File",
            accelerator=self.shortcut("previous_file"),
            command=self.previous,
        )
        navigate_menu.add_command(
            label="Next File",
            accelerator=self.shortcut("next_file"),
            command=self.skip,
        )
        navigate_menu.add_command(
            label="Open Current Media",
            accelerator=self.shortcut("open_current"),
            command=self.open_current,
        )
        navigate_menu.add_command(
            label="Fullscreen Media",
            accelerator=self.shortcut("fullscreen_media"),
            command=self.fullscreen_media,
        )
        menubar.add_cascade(label="Navigate", menu=navigate_menu)

        playback_menu = tk.Menu(menubar, tearoff=False)
        playback_menu.add_command(
            label="Play/Pause",
            accelerator=self.shortcut("toggle_playback"),
            command=self.toggle_playback,
        )
        playback_menu.add_checkbutton(
            label="Mute Videos",
            accelerator=self.shortcut("toggle_mute"),
            variable=self.muted_var,
            command=self.on_mute_changed,
        )
        playback_menu.add_command(label="Restart Preview", command=self.restart_current_preview)
        speed_menu = tk.Menu(playback_menu, tearoff=False)
        for speed in ("1x", "2x", "4x", "8x"):
            speed_menu.add_radiobutton(
                label=speed,
                variable=self.speed_var,
                value=speed,
                command=self.on_speed_changed,
            )
        playback_menu.add_cascade(label="Speed", menu=speed_menu)
        menubar.add_cascade(label="Playback", menu=playback_menu)

        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(label="Keyboard Shortcuts", command=self.show_shortcuts_popup)
        if system_name() != "Darwin":
            help_menu.add_command(label="Settings...", command=self.show_settings)
        menubar.add_cascade(label="Help", menu=help_menu)

        self.root.config(menu=menubar)
        self.register_macos_app_menu_commands()

    def register_macos_app_menu_commands(self) -> None:
        if system_name() != "Darwin":
            return
        try:
            self.root.createcommand("tk::mac::ShowPreferences", self.show_settings)
            self.root.createcommand("tk::mac::Quit", self.close)
        except tk.TclError:
            pass

    def build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        header = ttk.Frame(self.root, padding=(14, 12, 14, 6))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)

        ttk.Label(header, textvariable=self.status_var, font=("", 13, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Button(header, text="Open Folder...", command=self.choose_folder).grid(
            row=0, column=1, sticky="e", padx=(8, 0)
        )
        ttk.Label(header, textvariable=self.kind_var).grid(row=0, column=2, sticky="e", padx=(10, 0))
        ttk.Label(header, textvariable=self.file_var, foreground="#555").grid(
            row=1, column=0, columnspan=3, sticky="ew", pady=(4, 0)
        )

        body = ttk.Frame(self.root, padding=(14, 8, 14, 8))
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)

        self.canvas = tk.Canvas(body, bg="#111111", highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")

        file_panel = ttk.Frame(body, padding=(10, 0, 0, 0))
        file_panel.grid(row=0, column=1, sticky="ns")
        file_panel.rowconfigure(2, weight=1)

        ttk.Label(file_panel, text="Files", font=("", 12, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 6)
        )
        self.search_entry = ttk.Entry(file_panel, textvariable=self.search_var)
        self.search_entry.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        self.file_list = tk.Listbox(
            file_panel,
            width=38,
            activestyle="dotbox",
            exportselection=False,
        )
        self.file_list.grid(row=2, column=0, sticky="ns")
        file_scrollbar = ttk.Scrollbar(file_panel, orient="vertical", command=self.file_list.yview)
        file_scrollbar.grid(row=2, column=1, sticky="ns")
        self.file_list.configure(yscrollcommand=file_scrollbar.set)
        self.populate_file_list()

        footer = ttk.Frame(self.root, padding=(14, 6, 14, 14))
        footer.grid(row=2, column=0, sticky="ew")
        footer.columnconfigure(1, weight=1)

        ttk.Label(footer, text="New name").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.entry = ttk.Entry(footer)
        self.entry.grid(row=0, column=1, sticky="ew")

        ttk.Label(footer, text="Speed").grid(row=0, column=2, sticky="w", padx=(8, 4))
        self.speed_combo = ttk.Combobox(
            footer,
            textvariable=self.speed_var,
            values=("1x", "2x", "4x", "8x"),
            width=5,
            state="readonly",
        )
        self.speed_combo.grid(row=0, column=3, sticky="w")
        self.speed_combo.bind("<<ComboboxSelected>>", lambda _event: self.on_speed_changed())

        self.play_pause_button = ttk.Button(
            footer,
            textvariable=self.playback_var,
            command=self.toggle_playback,
        )
        self.play_pause_button.grid(row=0, column=4, padx=(8, 0))
        ttk.Button(footer, text="Restart", command=self.restart_current_preview).grid(
            row=0, column=5, padx=(8, 0)
        )
        self.update_mute_text()
        ttk.Checkbutton(
            footer,
            textvariable=self.mute_var,
            variable=self.muted_var,
            command=self.on_mute_changed,
        ).grid(row=0, column=6, padx=(8, 0))

        ttk.Button(footer, text="Rename / Skip", command=self.accept).grid(
            row=0, column=7, padx=(8, 0)
        )
        ttk.Button(footer, text="Back", command=self.previous).grid(row=0, column=8, padx=(8, 0))
        ttk.Button(footer, text="Open", command=self.open_current).grid(
            row=0, column=9, padx=(8, 0)
        )
        self.footer_reveal_button = ttk.Button(footer, text=reveal_label(), command=self.reveal_current)
        self.footer_reveal_button.grid(row=0, column=10, padx=(8, 0))

        shortcuts_link = ttk.Label(
            footer,
            text="Keyboard Shortcuts",
            foreground="#0066cc",
            cursor="hand2",
        )
        shortcuts_link.grid(row=1, column=0, columnspan=11, sticky="w", pady=(8, 0))
        shortcuts_link.bind("<Button-1>", lambda _event: self.show_shortcuts_popup())
        shortcuts_link.bind(
            "<Enter>",
            lambda _event: shortcuts_link.configure(font=("", 13, "underline")),
        )
        shortcuts_link.bind(
            "<Leave>",
            lambda _event: shortcuts_link.configure(font=("", 13)),
        )

    def shortcut_handlers(self) -> dict[str, Callable[[], object]]:
        return {
            "accept": self.accept,
            "quit": self.close,
            "open_folder": self.choose_folder,
            "reload_folder": self.reload_folder,
            "undo_rename": self.undo_last_rename,
            "open_current": self.open_current,
            "fullscreen_media": self.fullscreen_media,
            "previous_file": self.previous,
            "next_file": self.skip,
            "use_current_name": self.fill_current_name,
            "focus_search": self.focus_search,
            "focus_rename": self.focus_rename,
            "toggle_playback": self.toggle_playback,
            "toggle_mute": self.toggle_mute,
            "previous_input": lambda: self.recall_previous_name(None),
        }

    def bind_shortcuts(self) -> None:
        for widget, sequence in self.bound_shortcut_sequences:
            try:
                widget.unbind(sequence)
            except tk.TclError:
                pass
        self.bound_shortcut_sequences.clear()

        handlers = self.shortcut_handlers()
        for action, handler in handlers.items():
            widget: tk.Misc = self.entry if action == "previous_input" else self.root
            for sequence in shortcut_to_sequences(self.shortcut(action)):
                widget.bind(sequence, lambda _event, callback=handler: self.run_shortcut(callback))
                self.bound_shortcut_sequences.append((widget, sequence))

        self.file_list.bind("<<ListboxSelect>>", self.jump_to_selected_file)
        self.canvas.bind("<Configure>", self.on_canvas_resize)

    def run_shortcut(self, callback: Callable[[], object]) -> str:
        callback()
        return "break"

    def show_shortcuts_popup(self) -> None:
        popup = tk.Toplevel(self.root)
        popup.title("Keyboard Shortcuts")
        popup.transient(self.root)
        popup.resizable(False, False)

        content = ttk.Frame(popup, padding=14)
        content.grid(row=0, column=0, sticky="nsew")

        rows = (
            (self.shortcut("accept"), "rename, or skip when blank"),
            (self.shortcut("previous_input"), "use previous filename input"),
            (self.shortcut("undo_rename"), "undo last rename"),
            (self.shortcut("use_current_name"), "put current filename in the text box"),
            (self.shortcut("previous_file"), "previous file"),
            (self.shortcut("next_file"), "next file"),
            (self.shortcut("open_current"), "open current media"),
            (self.shortcut("fullscreen_media"), "fullscreen media"),
            (self.shortcut("open_folder"), "open a folder"),
            (self.shortcut("focus_search"), "search the file list"),
            (self.shortcut("focus_rename"), "focus the rename box"),
            (self.shortcut("toggle_playback"), "play/pause video preview"),
            (self.shortcut("toggle_mute"), "toggle video mute for this session"),
            (self.shortcut("quit"), "quit"),
        )

        ttk.Label(content, text="Keyboard Shortcuts", font=("", 13, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8)
        )
        for row_index, (key, action) in enumerate(rows, start=1):
            ttk.Label(content, text=key, font=("", 12, "bold")).grid(
                row=row_index, column=0, sticky="w", padx=(0, 14), pady=2
            )
            ttk.Label(content, text=action).grid(row=row_index, column=1, sticky="w", pady=2)

        ttk.Label(
            content,
            text="Blank names skip. Extensions are preserved. Use 1x for normal video speed.",
            foreground="#666",
        ).grid(row=len(rows) + 1, column=0, columnspan=2, sticky="w", pady=(10, 0))

        ttk.Button(content, text="Close", command=popup.destroy).grid(
            row=len(rows) + 2, column=0, columnspan=2, sticky="e", pady=(12, 0)
        )

        popup.bind("<Escape>", lambda _event: popup.destroy())
        popup.update_idletasks()

        root_x = self.root.winfo_rootx()
        root_y = self.root.winfo_rooty()
        root_width = self.root.winfo_width()
        root_height = self.root.winfo_height()
        popup_width = popup.winfo_width()
        popup_height = popup.winfo_height()
        x = root_x + max(0, (root_width - popup_width) // 2)
        y = root_y + max(0, (root_height - popup_height) // 2)
        popup.geometry(f"+{x}+{y}")
        popup.focus_set()

    def show_shortcut_editor(self) -> None:
        self.show_settings()

    def show_settings(self) -> None:
        popup = tk.Toplevel(self.root)
        popup.title("Settings")
        popup.transient(self.root)
        popup.resizable(False, False)

        content = ttk.Frame(popup, padding=14)
        content.grid(row=0, column=0, sticky="nsew")
        content.columnconfigure(1, weight=1)

        ttk.Label(content, text="Settings", font=("", 13, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 10)
        )

        speed_var = tk.StringVar(value=self.speed_var.get())
        recursive_var = tk.BooleanVar(value=self.recursive_var.get())
        muted_var = tk.BooleanVar(value=self.muted_var.get())
        resume_var = tk.BooleanVar(value=self.resume_on_launch_var.get())
        fps_vars = {
            speed: tk.StringVar(value=str(self.fps_by_speed[speed]))
            for speed in ("1x", "2x", "4x", "8x")
        }

        ttk.Label(content, text="Playback speed").grid(row=1, column=0, sticky="w", padx=(0, 12), pady=3)
        ttk.Combobox(
            content,
            textvariable=speed_var,
            values=("1x", "2x", "4x", "8x"),
            width=8,
            state="readonly",
        ).grid(row=1, column=1, sticky="w", pady=3)

        ttk.Checkbutton(
            content,
            text="Include media in subfolders",
            variable=recursive_var,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=3)
        ttk.Checkbutton(
            content,
            text="Mute video previews for this session",
            variable=muted_var,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=3)
        ttk.Checkbutton(
            content,
            text="Reopen last folder on launch",
            variable=resume_var,
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=3)

        ttk.Separator(content).grid(row=5, column=0, columnspan=2, sticky="ew", pady=(12, 10))
        ttk.Label(content, text="Preview Frame Rate", font=("", 12, "bold")).grid(
            row=6, column=0, columnspan=2, sticky="w", pady=(0, 6)
        )
        ttk.Label(
            content,
            text="Frames per second used while previewing videos at each speed.",
            foreground="#666",
        ).grid(row=7, column=0, columnspan=2, sticky="w", pady=(0, 8))
        for offset, speed in enumerate(("1x", "2x", "4x", "8x"), start=8):
            ttk.Label(content, text=f"{speed} FPS").grid(
                row=offset, column=0, sticky="w", padx=(0, 12), pady=3
            )
            ttk.Spinbox(
                content,
                from_=1,
                to=60,
                textvariable=fps_vars[speed],
                width=8,
            ).grid(row=offset, column=1, sticky="w", pady=3)

        ttk.Separator(content).grid(row=12, column=0, columnspan=2, sticky="ew", pady=(12, 10))
        ttk.Label(content, text="Keyboard Shortcuts", font=("", 12, "bold")).grid(
            row=13, column=0, columnspan=2, sticky="w", pady=(0, 6)
        )
        ttk.Label(
            content,
            text="Click a field and press the shortcut you want to use.",
            foreground="#666",
        ).grid(row=14, column=0, columnspan=2, sticky="w", pady=(0, 10))

        action_order = (
            "accept",
            "previous_input",
            "undo_rename",
            "use_current_name",
            "previous_file",
            "next_file",
            "open_current",
            "fullscreen_media",
            "open_folder",
            "reload_folder",
            "focus_search",
            "focus_rename",
            "toggle_playback",
            "toggle_mute",
            "quit",
        )
        labels = shortcut_action_labels()
        shortcut_vars: dict[str, tk.StringVar] = {}

        def capture_shortcut(event: tk.Event, action: str) -> str:
            shortcut = event_to_shortcut(event)
            if shortcut:
                shortcut_vars[action].set(shortcut)
            return "break"

        for row_index, action in enumerate(action_order, start=15):
            ttk.Label(content, text=labels[action]).grid(
                row=row_index, column=0, sticky="w", padx=(0, 12), pady=3
            )
            var = tk.StringVar(value=self.shortcut(action))
            shortcut_vars[action] = var
            entry = ttk.Entry(content, textvariable=var, width=24)
            entry.grid(row=row_index, column=1, sticky="ew", pady=3)
            entry.bind("<FocusIn>", lambda event: event.widget.select_range(0, tk.END))
            entry.bind("<KeyPress>", lambda event, key=action: capture_shortcut(event, key))

        button_row = len(action_order) + 15

        def reset_defaults() -> None:
            defaults = shortcut_defaults()
            for action, var in shortcut_vars.items():
                var.set(defaults[action])
            for speed, fps in default_fps_by_speed().items():
                fps_vars[speed].set(str(fps))

        def save_settings() -> None:
            new_shortcuts: dict[str, str] = {}
            used: dict[str, str] = {}
            for action, var in shortcut_vars.items():
                shortcut = normalize_shortcut(var.get())
                if not shortcut:
                    messagebox.showerror(
                        "Customize Shortcuts",
                        f"{labels[action]} needs a shortcut.",
                        parent=popup,
                    )
                    return
                if shortcut in used:
                    messagebox.showerror(
                        "Customize Shortcuts",
                        f"{shortcut} is assigned to both {labels[used[shortcut]]} and {labels[action]}.",
                        parent=popup,
                    )
                    return
                used[shortcut] = action
                new_shortcuts[action] = shortcut

            old_recursive = self.recursive_var.get()
            new_fps_by_speed: dict[str, int] = {}
            for speed, var in fps_vars.items():
                try:
                    fps = int(var.get())
                except ValueError:
                    messagebox.showerror(
                        "Settings",
                        f"{speed} FPS must be a whole number from 1 to 60.",
                        parent=popup,
                    )
                    return
                if not 1 <= fps <= 60:
                    messagebox.showerror(
                        "Settings",
                        f"{speed} FPS must be between 1 and 60.",
                        parent=popup,
                    )
                    return
                new_fps_by_speed[speed] = fps

            self.speed_var.set(speed_var.get())
            self.fps_by_speed = new_fps_by_speed
            self.recursive_var.set(recursive_var.get())
            self.muted_var.set(muted_var.get())
            self.resume_on_launch_var.set(resume_var.get())
            self.on_mute_changed(rebuild_menu=False)
            self.shortcuts.update(new_shortcuts)
            self.bind_shortcuts()
            self.build_menu()
            if old_recursive != self.recursive_var.get() and self.folder:
                self.reload_folder()
            else:
                self.restart_current_preview()
            self.save_progress()
            popup.destroy()

        ttk.Button(content, text="Reset Defaults", command=reset_defaults).grid(
            row=button_row, column=0, sticky="w", pady=(12, 0)
        )
        actions = ttk.Frame(content)
        actions.grid(row=button_row, column=1, sticky="e", pady=(12, 0))
        ttk.Button(actions, text="Cancel", command=popup.destroy).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(actions, text="Save", command=save_settings).grid(row=0, column=1)

        popup.bind("<Escape>", lambda _event: popup.destroy())
        popup.update_idletasks()
        root_x = self.root.winfo_rootx()
        root_y = self.root.winfo_rooty()
        root_width = self.root.winfo_width()
        root_height = self.root.winfo_height()
        popup_width = popup.winfo_width()
        popup_height = popup.winfo_height()
        x = root_x + max(0, (root_width - popup_width) // 2)
        y = root_y + max(0, (root_height - popup_height) // 2)
        popup.geometry(f"+{x}+{y}")
        popup.focus_set()

    def fill_current_name(self) -> str:
        current = self.current_file()
        if current is None:
            return "break"

        self.entry.delete(0, tk.END)
        self.entry.insert(0, current.stem)
        self.entry.icursor(tk.END)
        self.entry.focus_set()
        self.history_position = None
        return "break"

    def choose_folder(self) -> str:
        initial_dir = str(self.folder or Path.home())
        selected = filedialog.askdirectory(parent=self.root, initialdir=initial_dir)
        if selected:
            self.open_folder(Path(selected), initial_index=0, show_errors=True)
        return "break"

    def open_folder(self, folder: Path, initial_index: int = 0, show_errors: bool = True) -> bool:
        folder = folder.expanduser().resolve()
        if not folder.exists() or not folder.is_dir():
            if show_errors:
                messagebox.showerror("Open Folder", f"Not a folder:\n{folder}", parent=self.root)
            self.show_welcome()
            return False

        files = find_media(folder, self.recursive_var.get())
        if not files:
            if show_errors:
                messagebox.showinfo("Open Folder", f"No supported media files found in:\n{folder}", parent=self.root)
            self.folder = folder
            self.files = []
            self.index = 0
            self.show_welcome(f"No supported media files found in {folder}")
            self.save_progress()
            return False

        self.stop_video_preview()
        self.folder = folder
        self.files = files
        self.base_dir = folder
        self.index = max(0, min(initial_index, len(files) - 1))
        self.filtered_indices = []
        self.reviewed_indices.clear()
        self.renamed_indices.clear()
        self.undo_stack.clear()
        self.search_var.set("")
        self.apply_filter()
        self.show_current()
        self.save_progress()
        return True

    def reload_folder(self) -> str:
        if not self.folder:
            self.choose_folder()
            return "break"

        current = self.current_file()
        files = find_media(self.folder, self.recursive_var.get())
        self.files = files
        if current and current in files:
            self.index = files.index(current)
        else:
            self.index = min(self.index, max(0, len(files) - 1))
        self.reviewed_indices.clear()
        self.renamed_indices.clear()
        self.undo_stack.clear()
        self.apply_filter()
        if files:
            self.show_current()
        else:
            self.show_welcome(f"No supported media files found in {self.folder}")
        self.save_progress()
        return "break"

    def save_progress(self) -> None:
        data: dict[str, object] = {
            "folder": str(self.folder) if self.folder else "",
            "index": self.index,
            "recursive": self.recursive_var.get(),
            "speed": self.speed_var.get(),
            "fps_by_speed": self.fps_by_speed,
            "muted": self.muted_var.get(),
            "resume_on_launch": self.resume_on_launch_var.get(),
            "shortcuts": self.shortcuts,
            "shortcut_platform": system_name(),
        }
        save_session(data)

    def show_welcome(self, message: str | None = None) -> None:
        self.stop_video_preview()
        self.preview_image = None
        self.status_var.set("Open a folder")
        self.file_var.set(message or "Use File > Open Folder or the Open Folder button to choose media.")
        self.kind_var.set("")
        self.entry.delete(0, tk.END)
        self.populate_file_list()
        self.canvas.delete("all")
        self.canvas.create_text(
            self.canvas.winfo_width() // 2,
            self.canvas.winfo_height() // 2,
            text="Open Folder",
            fill="white",
            font=("", 24, "bold"),
        )
        self.canvas.create_text(
            self.canvas.winfo_width() // 2,
            self.canvas.winfo_height() // 2 + 38,
            text="Choose a folder from the menu bar or the Open Folder button.",
            fill="#cccccc",
            font=("", 15),
        )

    def focus_search(self) -> str:
        self.search_entry.focus_force()
        self.search_entry.select_range(0, tk.END)
        return "break"

    def focus_rename(self) -> str:
        self.entry.focus_force()
        self.entry.select_range(0, tk.END)
        return "break"

    def file_list_label(self, index: int, path: Path) -> str:
        try:
            display_path = path.relative_to(self.base_dir)
        except ValueError:
            display_path = path.name
        markers: list[str] = []
        if index in self.renamed_indices:
            markers.append("[renamed]")
        elif index in self.reviewed_indices:
            markers.append("[done]")
        marker_text = " ".join(markers)
        marker_text = f" {marker_text}" if marker_text else ""
        return f"{index + 1:>3}. {display_path}{marker_text}"

    def matches_filter(self, path: Path) -> bool:
        query = self.search_var.get().strip().lower()
        if not query:
            return True
        return query in path.name.lower() or query in str(path).lower()

    def apply_filter(self) -> None:
        self.filtered_indices = [
            index for index, path in enumerate(self.files) if self.matches_filter(path)
        ]
        self.populate_file_list()
        self.sync_file_list_selection()

    def populate_file_list(self) -> None:
        self.syncing_file_list = True
        try:
            self.file_list.delete(0, tk.END)
            for index in self.filtered_indices:
                path = self.files[index]
                self.file_list.insert(tk.END, self.file_list_label(index, path))
        finally:
            self.syncing_file_list = False

    def sync_file_list_selection(self) -> None:
        if not hasattr(self, "file_list"):
            return

        self.syncing_file_list = True
        try:
            self.file_list.selection_clear(0, tk.END)
            if 0 <= self.index < len(self.files) and self.index in self.filtered_indices:
                row = self.filtered_indices.index(self.index)
                self.file_list.selection_set(row)
                self.file_list.activate(row)
                self.file_list.see(row)
        finally:
            self.syncing_file_list = False

    def update_file_list_row(self, index: int) -> None:
        if not (0 <= index < len(self.files)):
            return

        if index not in self.filtered_indices:
            self.apply_filter()
            return

        row = self.filtered_indices.index(index)
        selected = self.file_list.curselection()
        self.syncing_file_list = True
        try:
            self.file_list.delete(row)
            self.file_list.insert(row, self.file_list_label(index, self.files[index]))
            self.file_list.selection_clear(0, tk.END)
            for selected_index in selected:
                if 0 <= selected_index < self.file_list.size():
                    self.file_list.selection_set(selected_index)
            if 0 <= self.index < len(self.files) and self.index in self.filtered_indices:
                active_row = self.filtered_indices.index(self.index)
                self.file_list.activate(active_row)
                self.file_list.see(active_row)
        finally:
            self.syncing_file_list = False

    def jump_to_selected_file(self, _event: tk.Event) -> None:
        if self.syncing_file_list:
            return

        selected = self.file_list.curselection()
        if not selected:
            return

        selected_row = selected[0]
        if selected_row >= len(self.filtered_indices):
            return
        new_index = self.filtered_indices[selected_row]
        if new_index == self.index:
            self.entry.focus_set()
            return

        self.index = new_index
        self.show_current()

    def recall_previous_name(self, _event: tk.Event) -> str:
        if not self.name_history:
            return "break"

        if self.history_position is None:
            self.history_position = len(self.name_history) - 1
        else:
            self.history_position = max(0, self.history_position - 1)

        self.entry.delete(0, tk.END)
        self.entry.insert(0, self.name_history[self.history_position])
        self.entry.icursor(tk.END)
        return "break"

    def current_file(self) -> Path | None:
        if 0 <= self.index < len(self.files):
            return self.files[self.index]
        return None

    def show_current(self) -> None:
        current = self.current_file()
        if current is None:
            self.status_var.set("Done")
            self.file_var.set(f"Reviewed {len(self.files)} file(s).")
            self.kind_var.set("")
            self.entry.delete(0, tk.END)
            self.sync_file_list_selection()
            self.canvas.delete("all")
            self.canvas.create_text(
                self.canvas.winfo_width() // 2,
                self.canvas.winfo_height() // 2,
                text="All media reviewed",
                fill="white",
                font=("", 24, "bold"),
            )
            self.save_progress()
            return

        self.status_var.set(f"{self.index + 1} of {len(self.files)}")
        self.file_var.set(str(current))
        media_type = "Video preview" if current.suffix.lower() in VIDEO_EXTENSIONS else "Photo"
        self.kind_var.set(media_type)
        self.entry.delete(0, tk.END)
        self.history_position = None
        self.entry.focus_set()
        self.sync_file_list_selection()
        self.load_preview_async(current)
        self.save_progress()

    def load_preview_async(self, path: Path) -> None:
        self.stop_video_preview()
        self.preview_token += 1
        token = self.preview_token
        self.preview_image = None
        self.video_current_path = None
        self.video_current_token = None
        self.video_paused = False
        self.playback_var.set("Pause")
        self.last_preview_size = (
            max(1, self.canvas.winfo_width()),
            max(1, self.canvas.winfo_height()),
        )
        self.canvas.delete("all")
        self.canvas.create_text(
            self.canvas.winfo_width() // 2,
            self.canvas.winfo_height() // 2,
            text="Loading preview...",
            fill="white",
            font=("", 18),
        )

        if path.suffix.lower() in VIDEO_EXTENSIONS and media_binary("ffmpeg"):
            self.start_video_preview(path, token)
            return

        output_dir = self.temp_path / str(token)
        canvas_width = max(320, self.canvas.winfo_width() - 20)
        canvas_height = max(240, self.canvas.winfo_height() - 20)
        size = max(320, min(1600, max(canvas_width, canvas_height)))

        def worker() -> None:
            preview_path = make_preview(path, output_dir, size)
            self.root.after(0, lambda: self.set_preview(token, preview_path))

        threading.Thread(target=worker, daemon=True).start()

    def restart_current_preview(self) -> None:
        current = self.current_file()
        if current:
            self.load_preview_async(current)
        return "break"

    def on_canvas_resize(self, _event: tk.Event) -> None:
        self.draw_preview()
        current = self.current_file()
        if not current:
            return

        size = (max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height()))
        old_width, old_height = self.last_preview_size
        if old_width and old_height and abs(size[0] - old_width) < 80 and abs(size[1] - old_height) < 80:
            return

        if self.resize_after_id:
            try:
                self.root.after_cancel(self.resize_after_id)
            except tk.TclError:
                pass
        self.resize_after_id = self.root.after(350, self.restart_preview_after_resize)

    def restart_preview_after_resize(self) -> None:
        self.resize_after_id = None
        current = self.current_file()
        if not current:
            return
        self.last_preview_size = (
            max(1, self.canvas.winfo_width()),
            max(1, self.canvas.winfo_height()),
        )
        self.load_preview_async(current)

    def on_speed_changed(self) -> str:
        self.save_progress()
        return self.restart_current_preview()

    def toggle_playback(self) -> str:
        current = self.current_file()
        if not current or current.suffix.lower() not in VIDEO_EXTENSIONS:
            return "break"

        self.video_paused = not self.video_paused
        self.playback_var.set("Play" if self.video_paused else "Pause")
        if self.video_paused:
            self.pause_audio_preview()
        else:
            self.resume_audio_preview()
        return "break"

    def update_mute_text(self) -> None:
        if hasattr(self, "mute_var"):
            self.mute_var.set("Muted" if self.muted_var.get() else "Sound")

    def on_mute_changed(self, rebuild_menu: bool = True) -> str:
        self.update_mute_text()
        current = self.current_file()
        if self.muted_var.get():
            self.stop_audio_preview()
        elif current and current.suffix.lower() in VIDEO_EXTENSIONS:
            self.start_audio_preview(current)
            if self.video_paused:
                self.pause_audio_preview()
        if rebuild_menu:
            self.build_menu()
        self.save_progress()
        return "break"

    def toggle_mute(self) -> str:
        self.muted_var.set(not self.muted_var.get())
        return self.on_mute_changed()

    def video_speed(self) -> int:
        value = self.speed_var.get().removesuffix("x")
        try:
            speed = int(value)
        except ValueError:
            speed = 4
        return max(1, min(8, speed))

    def video_frame_rate(self) -> int:
        return self.fps_by_speed.get(self.speed_var.get(), default_fps_by_speed()["4x"])

    def start_video_preview(self, path: Path, token: int) -> None:
        frame_queue: queue.Queue[bytes | None] = queue.Queue(maxsize=3)
        stop_event = threading.Event()
        self.video_queue = frame_queue
        self.video_stop_event = stop_event
        self.video_current_path = path
        self.video_current_token = token

        canvas_width = max(640, self.canvas.winfo_width())
        canvas_height = max(360, self.canvas.winfo_height())
        max_width = min(1280, max(320, canvas_width - 20))
        max_height = min(900, max(240, canvas_height - 20))
        display_fps = self.video_frame_rate()
        speed = self.video_speed()

        def put_frame(frame: bytes | None) -> None:
            try:
                if frame_queue.full():
                    frame_queue.get_nowait()
                frame_queue.put_nowait(frame)
            except queue.Full:
                pass
            except queue.Empty:
                pass

        def worker() -> None:
            ffmpeg = media_binary("ffmpeg")
            if not ffmpeg:
                put_frame(None)
                return

            vf = (
                f"fps={display_fps},"
                f"scale=w={max_width}:h={max_height}:force_original_aspect_ratio=decrease"
            )

            while not stop_event.is_set():
                saw_frame = False
                for command in ffmpeg_video_pipe_commands(ffmpeg, path, vf, speed):
                    if stop_event.is_set():
                        break
                    try:
                        process = subprocess.Popen(
                            command,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL,
                            **subprocess_window_options(),
                        )
                    except OSError:
                        continue
                    self.video_process = process

                    try:
                        assert process.stdout is not None
                        while not stop_event.is_set():
                            frame = read_ppm_frame(process.stdout)
                            if frame is None:
                                break
                            saw_frame = True
                            put_frame(frame)
                    finally:
                        if process.poll() is None:
                            process.terminate()
                            try:
                                process.wait(timeout=1)
                            except subprocess.TimeoutExpired:
                                process.kill()
                        if self.video_process is process:
                            self.video_process = None

                    if saw_frame:
                        break

                if not saw_frame:
                    break

            put_frame(None)

        threading.Thread(target=worker, daemon=True).start()
        self.start_audio_preview(path)
        self.schedule_video_frame(token, display_fps)

    def audio_filter_for_speed(self) -> str | None:
        speed = self.video_speed()
        if speed <= 1:
            return None
        filters: list[str] = []
        remaining = speed
        while remaining > 1:
            step = 2 if remaining >= 2 else remaining
            filters.append(f"atempo={step:g}")
            remaining /= step
        return ",".join(filters)

    def start_audio_preview(self, path: Path) -> None:
        self.stop_audio_preview()
        if self.muted_var.get():
            return

        ffplay = media_binary("ffplay")
        if not ffplay:
            self.muted_var.set(True)
            self.update_mute_text()
            return

        command = [
            ffplay,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nodisp",
            "-autoexit",
            "-vn",
            "-loop",
            "0",
        ]
        audio_filter = self.audio_filter_for_speed()
        if audio_filter:
            command.extend(["-af", audio_filter])
        command.append(str(path))

        try:
            self.audio_process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                **subprocess_window_options(),
            )
        except OSError:
            self.audio_process = None

    def stop_audio_preview(self) -> None:
        if self.audio_process and self.audio_process.poll() is None:
            self.audio_process.terminate()
            try:
                self.audio_process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.audio_process.kill()
        self.audio_process = None

    def pause_audio_preview(self) -> None:
        if not self.audio_process or self.audio_process.poll() is not None:
            return
        try:
            os.kill(self.audio_process.pid, signal.SIGSTOP)
        except OSError:
            pass

    def resume_audio_preview(self) -> None:
        if self.audio_process and self.audio_process.poll() is None:
            try:
                os.kill(self.audio_process.pid, signal.SIGCONT)
            except OSError:
                pass
            return

        current = self.current_file()
        if current and current.suffix.lower() in VIDEO_EXTENSIONS and not self.muted_var.get():
            self.start_audio_preview(current)

    def schedule_video_frame(self, token: int, display_fps: int) -> None:
        delay_ms = max(15, int(1000 / display_fps))
        self.video_after_id = self.root.after(delay_ms, lambda: self.show_video_frame(token, display_fps))

    def show_video_frame(self, token: int, display_fps: int) -> None:
        if token != self.preview_token or not self.video_queue:
            return

        if self.video_paused:
            self.schedule_video_frame(token, display_fps)
            return

        frame: bytes | None | object = NO_VIDEO_FRAME
        try:
            while True:
                frame = self.video_queue.get_nowait()
        except queue.Empty:
            pass

        if frame is NO_VIDEO_FRAME:
            self.schedule_video_frame(token, display_fps)
            return

        if frame is None:
            if self.video_current_path:
                self.load_static_fallback_async(
                    self.video_current_path,
                    token,
                    "Video preview unavailable. Showing thumbnail fallback.",
                )
            return

        try:
            self.preview_image = tk.PhotoImage(data=frame, format="PPM")
            self.draw_preview()
        except tk.TclError:
            self.canvas.delete("all")
            self.canvas.create_text(
                self.canvas.winfo_width() // 2,
                self.canvas.winfo_height() // 2,
                text=f"Video preview could not be displayed. Press {self.shortcut('open_current')} to open the file.",
                fill="white",
                font=("", 18),
            )
            return

        self.schedule_video_frame(token, display_fps)

    def load_static_fallback_async(self, path: Path, token: int, message: str) -> None:
        output_dir = self.temp_path / f"fallback-{token}"
        canvas_width = max(320, self.canvas.winfo_width() - 20)
        canvas_height = max(240, self.canvas.winfo_height() - 20)
        size = max(320, min(1600, max(canvas_width, canvas_height)))

        def worker() -> None:
            preview_path = make_preview(path, output_dir, size)
            self.root.after(0, lambda: self.set_preview(token, preview_path, message))

        threading.Thread(target=worker, daemon=True).start()

    def stop_video_preview(self) -> None:
        self.stop_audio_preview()
        if self.video_after_id:
            try:
                self.root.after_cancel(self.video_after_id)
            except tk.TclError:
                pass
            self.video_after_id = None

        if self.video_stop_event:
            self.video_stop_event.set()
            self.video_stop_event = None

        if self.video_process and self.video_process.poll() is None:
            self.video_process.terminate()
        self.video_process = None
        self.video_queue = None

    def set_preview(self, token: int, preview_path: Path | None, note: str | None = None) -> None:
        if token != self.preview_token:
            return

        self.canvas.delete("all")
        if not preview_path:
            self.canvas.create_text(
                self.canvas.winfo_width() // 2,
                self.canvas.winfo_height() // 2,
                text=f"Preview unavailable. Press {self.shortcut('open_current')} to open the file.",
                fill="white",
                font=("", 18),
            )
            return

        try:
            self.preview_image = tk.PhotoImage(file=str(preview_path))
        except tk.TclError:
            self.preview_image = None
            self.canvas.create_text(
                self.canvas.winfo_width() // 2,
                self.canvas.winfo_height() // 2,
                text=f"Preview could not be loaded. Press {self.shortcut('open_current')} to open the file.",
                fill="white",
                font=("", 18),
            )
            return

        self.draw_preview()
        if note:
            self.canvas.create_text(
                12,
                12,
                text=note,
                fill="#eeeeee",
                anchor="nw",
                font=("", 13),
            )

    def draw_preview(self) -> None:
        self.canvas.delete("all")
        if not self.preview_image:
            return

        canvas_width = max(1, self.canvas.winfo_width())
        canvas_height = max(1, self.canvas.winfo_height())
        image_width = self.preview_image.width()
        image_height = self.preview_image.height()

        self.canvas.create_image(
            canvas_width // 2,
            canvas_height // 2,
            image=self.preview_image,
            anchor="center",
        )

        if image_width > canvas_width or image_height > canvas_height:
            self.canvas.create_text(
                canvas_width - 12,
                canvas_height - 12,
                text=f"{image_width} x {image_height}",
                fill="#cccccc",
                anchor="se",
            )

    def accept(self) -> None:
        current = self.current_file()
        if current is None:
            self.choose_folder()
            return

        raw_value = self.entry.get()
        try:
            new_stem = clean_new_name(raw_value, current.suffix)
        except ValueError as error:
            messagebox.showerror("Invalid name", str(error), parent=self.root)
            return

        if new_stem:
            if not self.name_history or self.name_history[-1] != new_stem:
                self.name_history.append(new_stem)
            self.history_position = None

            target = current.with_name(new_stem + current.suffix)
            if target != current:
                if target.exists():
                    try:
                        suggested = next_available_path(target)
                    except FileExistsError as error:
                        messagebox.showerror("Name already exists", str(error), parent=self.root)
                        return
                    use_suggested = messagebox.askyesno(
                        "Name already exists",
                        f"{target.name} already exists.\n\nUse this available name instead?\n{suggested.name}",
                        parent=self.root,
                    )
                    if not use_suggested:
                        return
                    target = suggested
                try:
                    current.rename(target)
                except OSError as error:
                    messagebox.showerror("Rename failed", str(error), parent=self.root)
                    return
                self.undo_stack.append((self.index, current, target))
                self.files[self.index] = target
                self.renamed_indices.add(self.index)
                self.update_file_list_row(self.index)

        self.reviewed_indices.add(self.index)
        self.update_file_list_row(self.index)
        self.index += 1
        self.show_current()

    def skip(self) -> None:
        self.entry.delete(0, tk.END)
        self.accept()

    def previous(self) -> None:
        if self.index > 0:
            self.index -= 1
            self.show_current()
        return "break"

    def undo_last_rename(self) -> str:
        if not self.undo_stack:
            return "break"

        index, old_path, new_path = self.undo_stack[-1]
        if not new_path.exists():
            messagebox.showerror(
                "Undo Rename",
                f"Cannot undo because this file no longer exists:\n{new_path}",
                parent=self.root,
            )
            return "break"
        if old_path.exists():
            messagebox.showerror(
                "Undo Rename",
                f"Cannot undo because the original name already exists:\n{old_path}",
                parent=self.root,
            )
            return "break"

        try:
            new_path.rename(old_path)
        except OSError as error:
            messagebox.showerror("Undo Rename", str(error), parent=self.root)
            return "break"

        self.undo_stack.pop()
        if 0 <= index < len(self.files):
            self.files[index] = old_path
            self.renamed_indices.discard(index)
            self.update_file_list_row(index)
            self.index = index
            self.show_current()
        self.save_progress()
        return "break"

    def open_current(self) -> None:
        current = self.current_file()
        if current is None:
            return
        try:
            subprocess.Popen(["open", str(current)])
        except Exception as error:
            messagebox.showerror("Open failed", str(error), parent=self.root)

    def fullscreen_media(self) -> str:
        current = self.current_file()
        if current is None:
            return "break"

        popup = tk.Toplevel(self.root)
        popup.title(current.name)
        popup.configure(bg="#000000")
        popup.attributes("-fullscreen", True)
        popup.focus_set()

        canvas = tk.Canvas(popup, bg="#000000", highlightthickness=0)
        canvas.pack(fill="both", expand=True)

        token = self.preview_token
        stop_event = threading.Event()
        frame_queue: queue.Queue[bytes | None] = queue.Queue(maxsize=3)
        frame_image: tk.PhotoImage | None = None
        process: subprocess.Popen | None = None
        after_id: str | None = None

        def draw_photo(image: tk.PhotoImage) -> None:
            canvas.delete("all")
            canvas.create_image(
                max(1, canvas.winfo_width()) // 2,
                max(1, canvas.winfo_height()) // 2,
                image=image,
                anchor="center",
            )

        def close_fullscreen(_event: tk.Event | None = None) -> str:
            nonlocal process, after_id
            stop_event.set()
            if after_id:
                try:
                    popup.after_cancel(after_id)
                except tk.TclError:
                    pass
                after_id = None
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
            try:
                popup.destroy()
            except tk.TclError:
                pass
            return "break"

        def schedule_frame(display_fps: int) -> None:
            nonlocal after_id
            delay_ms = max(15, int(1000 / max(1, display_fps)))
            after_id = popup.after(delay_ms, lambda: show_frame(display_fps))

        def show_frame(display_fps: int) -> None:
            nonlocal frame_image
            if stop_event.is_set() or not popup.winfo_exists():
                return

            frame: bytes | None | object = NO_VIDEO_FRAME
            try:
                while True:
                    frame = frame_queue.get_nowait()
            except queue.Empty:
                pass

            if frame is NO_VIDEO_FRAME:
                schedule_frame(display_fps)
                return

            if frame is None:
                canvas.delete("all")
                canvas.create_text(
                    canvas.winfo_width() // 2,
                    canvas.winfo_height() // 2,
                    text="Fullscreen preview unavailable.",
                    fill="white",
                    font=("", 24, "bold"),
                )
                return

            try:
                frame_image = tk.PhotoImage(data=frame, format="PPM")
                draw_photo(frame_image)
            except tk.TclError:
                return

            schedule_frame(display_fps)

        popup.bind("<Escape>", close_fullscreen)
        for sequence in shortcut_to_sequences(self.shortcut("fullscreen_media")):
            popup.bind(sequence, close_fullscreen)

        popup.update_idletasks()
        width = max(640, popup.winfo_screenwidth())
        height = max(360, popup.winfo_screenheight())

        if current.suffix.lower() in VIDEO_EXTENSIONS and media_binary("ffmpeg"):
            display_fps = self.video_frame_rate()
            speed = self.video_speed()

            def worker() -> None:
                nonlocal process
                ffmpeg = media_binary("ffmpeg")
                if not ffmpeg:
                    frame_queue.put(None)
                    return

                vf = (
                    f"fps={display_fps},"
                    f"scale=w={width}:h={height}:force_original_aspect_ratio=decrease"
                )
                while not stop_event.is_set():
                    saw_frame = False
                    for command in ffmpeg_video_pipe_commands(ffmpeg, current, vf, speed):
                        if stop_event.is_set():
                            break
                        try:
                            process = subprocess.Popen(
                                command,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL,
                                **subprocess_window_options(),
                            )
                        except OSError:
                            continue
                        try:
                            assert process.stdout is not None
                            while not stop_event.is_set():
                                frame = read_ppm_frame(process.stdout)
                                if frame is None:
                                    break
                                saw_frame = True
                                if frame_queue.full():
                                    try:
                                        frame_queue.get_nowait()
                                    except queue.Empty:
                                        pass
                                frame_queue.put_nowait(frame)
                        finally:
                            if process.poll() is None:
                                process.terminate()
                                try:
                                    process.wait(timeout=1)
                                except subprocess.TimeoutExpired:
                                    process.kill()
                        if saw_frame:
                            break
                    if not saw_frame:
                        break
                if not stop_event.is_set():
                    frame_queue.put(None)

            threading.Thread(target=worker, daemon=True).start()
            schedule_frame(display_fps)
            return "break"

        output_dir = self.temp_path / f"fullscreen-{token}"
        size = max(width, height)

        def image_worker() -> None:
            preview_path = make_preview(current, output_dir, size)
            def set_image() -> None:
                nonlocal frame_image
                if stop_event.is_set() or not popup.winfo_exists():
                    return
                if not preview_path:
                    canvas.create_text(
                        canvas.winfo_width() // 2,
                        canvas.winfo_height() // 2,
                        text="Fullscreen preview unavailable.",
                        fill="white",
                        font=("", 24, "bold"),
                    )
                    return
                try:
                    frame_image = tk.PhotoImage(file=str(preview_path))
                    draw_photo(frame_image)
                except tk.TclError:
                    pass
            popup.after(0, set_image)

        threading.Thread(target=image_worker, daemon=True).start()
        return "break"

    def reveal_current(self) -> None:
        current = self.current_file()
        if current is None:
            return
        try:
            subprocess.Popen(["open", "-R", str(current)])
        except Exception as error:
            messagebox.showerror("Reveal failed", str(error), parent=self.root)

    def close(self) -> None:
        if self.resize_after_id:
            try:
                self.root.after_cancel(self.resize_after_id)
            except tk.TclError:
                pass
            self.resize_after_id = None
        self.stop_video_preview()
        try:
            self.tempdir.cleanup()
        finally:
            self.root.destroy()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Standalone app for reviewing and renaming photos/videos.")
    parser.add_argument(
        "--speed",
        type=int,
        choices=(1, 2, 4, 8),
        default=4,
        help="Initial video preview speed. Use 1 for normal speed. Default: 4",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Include media files in subfolders too",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Start without reopening the last folder",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    session = load_session() if not args.no_resume else {}
    resume_on_launch = session.get("resume_on_launch") if isinstance(session.get("resume_on_launch"), bool) else True
    initial_folder_text = session.get("folder") if isinstance(session.get("folder"), str) else ""
    initial_folder = Path(initial_folder_text) if resume_on_launch and initial_folder_text else None
    initial_index = session.get("index") if isinstance(session.get("index"), int) else 0
    session_recursive = session.get("recursive") if isinstance(session.get("recursive"), bool) else args.recursive
    session_speed = session.get("speed") if isinstance(session.get("speed"), str) else f"{args.speed}x"
    session_fps_by_speed = sanitize_fps_by_speed(session.get("fps_by_speed"))
    session_muted = session.get("muted") if isinstance(session.get("muted"), bool) else True
    session_platform = session.get("shortcut_platform") if isinstance(session.get("shortcut_platform"), str) else ""
    session_shortcuts = (
        session.get("shortcuts")
        if session_platform == system_name() and isinstance(session.get("shortcuts"), dict)
        else None
    )
    try:
        initial_speed = int(session_speed.removesuffix("x"))
    except ValueError:
        initial_speed = args.speed

    root = tk.Tk()
    RenameTool(
        root,
        initial_speed=initial_speed,
        initial_folder=initial_folder,
        recursive=session_recursive,
        initial_index=initial_index,
        shortcut_overrides=session_shortcuts,
        muted=session_muted,
        resume_on_launch=resume_on_launch,
        fps_by_speed=session_fps_by_speed,
    )
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
