#!/usr/bin/env python3
"""
Build ClipSift macOS packages.

Examples:
    python3 build.py
    python3 build.py --target app
    python3 build.py --target installer
    python3 build.py --target macos-installer
    python3 build.py --target all
    python3 build.py --install-deps

Notes:
    ClipSift packaging is macOS-only.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import plistlib
import platform
import shutil
import subprocess
import sys
import venv
from pathlib import Path


APP_NAME = "ClipSift"
APP_IDENTIFIER = "local.clipsift"
APP_VERSION = "1.0.0"

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "ClipSift.py"
ASSETS = ROOT / "assets"
INSTALLER_ASSETS = ASSETS / "installer"
MAC_ICON = ASSETS / "ClipSift.icns"
DMG_BACKGROUND = INSTALLER_ASSETS / "dmg-background.png"
BUILD_DIR = ROOT / "build"
DIST_DIR = BUILD_DIR / "dist"
WORK_DIR = BUILD_DIR / "work"
SPEC_DIR = BUILD_DIR / "spec"
VENV_DIR = BUILD_DIR / ".venv"
INSTALLER_DIR = BUILD_DIR / "installer"
DMG_WINDOW_WIDTH = 660
DMG_WINDOW_HEIGHT = 420


def current_platform() -> str:
    return "macos" if platform.system() == "Darwin" else "other"


def run(command: list[str], *, dry_run: bool = False) -> None:
    print("+", " ".join(command))
    if not dry_run:
        subprocess.run(command, check=True)


def run_capture(command: list[str], *, dry_run: bool = False) -> bytes:
    print("+", " ".join(command))
    if dry_run:
        return b""
    return subprocess.run(command, check=True, capture_output=True).stdout


def ensure_files() -> None:
    required = [
        SOURCE,
        ASSETS,
        MAC_ICON,
        ASSETS / "ClipSift-light.png",
        ASSETS / "ClipSift-dark.png",
        DMG_BACKGROUND,
    ]
    missing = [path for path in required if not path.exists()]
    if missing:
        joined = "\n".join(str(path) for path in missing)
        raise SystemExit(f"Missing required build asset(s):\n{joined}")


def pyinstaller_module_exists(python: Path | str) -> bool:
    command = [
        str(python),
        "-c",
        "import importlib.util; raise SystemExit(0 if importlib.util.find_spec('PyInstaller') else 1)",
    ]
    return subprocess.run(command).returncode == 0


def pyinstaller_python(install_deps: bool, dry_run: bool) -> Path | str:
    if importlib.util.find_spec("PyInstaller"):
        return sys.executable

    venv_python = VENV_DIR / "bin" / "python"
    if venv_python.exists() and pyinstaller_module_exists(venv_python):
        return venv_python

    if not install_deps:
        raise SystemExit(
            "PyInstaller is not installed.\n"
            "Run this once to create a local build environment:\n"
            "    python3 build.py --install-deps\n"
        )

    if not dry_run:
        print(f"Creating build virtualenv: {VENV_DIR}")
        venv.EnvBuilder(with_pip=True, clear=False).create(VENV_DIR)
    run([str(venv_python), "-m", "pip", "install", "--upgrade", "pip", "pyinstaller"], dry_run=dry_run)
    return venv_python


def clean() -> None:
    for path in [DIST_DIR, WORK_DIR, SPEC_DIR, INSTALLER_DIR]:
        if path.exists():
            print(f"Removing {path}")
            shutil.rmtree(path)


def add_data_arg() -> str:
    return f"{ASSETS}{os.pathsep}assets"


def media_tool_paths() -> list[Path]:
    paths: list[Path] = []
    for name in ["ffmpeg", "ffplay"]:
        found = shutil.which(name)
        if found:
            paths.append(Path(found))
    return paths


def add_binary_args() -> list[str]:
    args: list[str] = []
    for path in media_tool_paths():
        args.extend(["--add-binary", f"{path}{os.pathsep}bin"])
    return args


def base_pyinstaller_command(python: Path | str) -> list[str]:
    return [
        str(python),
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--name",
        APP_NAME,
        "--add-data",
        add_data_arg(),
        "--distpath",
        str(DIST_DIR),
        "--workpath",
        str(WORK_DIR),
        "--specpath",
        str(SPEC_DIR),
    ] + add_binary_args()


def patch_macos_plist(app_path: Path) -> None:
    plist_path = app_path / "Contents" / "Info.plist"
    if not plist_path.exists():
        return

    with plist_path.open("rb") as handle:
        plist = plistlib.load(handle)

    plist.update(
        {
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": APP_NAME,
            "CFBundleIdentifier": APP_IDENTIFIER,
            "CFBundleShortVersionString": APP_VERSION,
            "CFBundleVersion": APP_VERSION,
            "CFBundleIconFile": APP_NAME,
            "NSHighResolutionCapable": True,
        }
    )

    with plist_path.open("wb") as handle:
        plistlib.dump(plist, handle)


def ad_hoc_codesign(app_path: Path, dry_run: bool) -> None:
    if not shutil.which("codesign"):
        return
    try:
        run(["codesign", "--force", "--deep", "--sign", "-", str(app_path)], dry_run=dry_run)
    except subprocess.CalledProcessError:
        print("Warning: ad-hoc codesign failed. The app was still built.")


def build_macos_app(python: Path | str, dry_run: bool) -> Path:
    if current_platform() != "macos":
        raise SystemExit("ClipSift packaging is macOS-only. Build the .app on macOS.")

    command = base_pyinstaller_command(python) + [
        "--osx-bundle-identifier",
        APP_IDENTIFIER,
        "--icon",
        str(MAC_ICON),
        str(SOURCE),
    ]
    run(command, dry_run=dry_run)

    app_path = DIST_DIR / f"{APP_NAME}.app"
    if not dry_run:
        patch_macos_plist(app_path)
        ad_hoc_codesign(app_path, dry_run=False)
    return app_path


def prepare_dmg_root(app_path: Path, dmg_root: Path) -> None:
    if dmg_root.exists():
        shutil.rmtree(dmg_root)
    dmg_root.mkdir(parents=True)
    shutil.copytree(app_path, dmg_root / app_path.name, symlinks=True)

    background_dir = dmg_root / ".background"
    background_dir.mkdir()
    shutil.copy2(DMG_BACKGROUND, background_dir / DMG_BACKGROUND.name)

    applications_link = dmg_root / "Applications"
    if applications_link.exists() or applications_link.is_symlink():
        applications_link.unlink()
    applications_link.symlink_to("/Applications")


def attach_dmg(dmg_path: Path, mount_path: Path) -> Path:
    if mount_path.exists():
        shutil.rmtree(mount_path)
    mount_path.mkdir(parents=True)
    output = run_capture(
        [
            "hdiutil",
            "attach",
            str(dmg_path),
            "-readwrite",
            "-noverify",
            "-noautoopen",
            "-mountpoint",
            str(mount_path),
            "-plist",
        ]
    )
    plist = plistlib.loads(output)
    for entity in plist.get("system-entities", []):
        mounted = entity.get("mount-point")
        if mounted:
            return Path(mounted)
    return mount_path


def detach_dmg(mount_path: Path) -> None:
    try:
        run(["hdiutil", "detach", str(mount_path)])
    finally:
        if mount_path.exists():
            shutil.rmtree(mount_path, ignore_errors=True)


def clean_dmg_mount_metadata(mount_path: Path) -> None:
    for name in [".fseventsd", ".Spotlight-V100", ".Trashes"]:
        path = mount_path / name
        if path.exists():
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)


def apply_dmg_finder_layout(mount_path: Path) -> None:
    if not shutil.which("osascript"):
        print("Warning: osascript is not available. The DMG will not get a custom Finder layout.")
        return

    background_path = mount_path / ".background" / DMG_BACKGROUND.name
    script = f"""
tell application "Finder"
    set dmgFolder to (POSIX file "{mount_path}/" as alias)
    open dmgFolder
    delay 1
    set dmgWindow to container window of dmgFolder
    set current view of dmgWindow to icon view
    set toolbar visible of dmgWindow to false
    set statusbar visible of dmgWindow to false
    set the bounds of dmgWindow to {{100, 100, {100 + DMG_WINDOW_WIDTH}, {100 + DMG_WINDOW_HEIGHT}}}
    set viewOptions to the icon view options of dmgWindow
    set arrangement of viewOptions to not arranged
    set icon size of viewOptions to 96
    set background picture of viewOptions to (POSIX file "{background_path}" as alias)
    set position of item "{APP_NAME}.app" of dmgFolder to {{168, 210}}
    set position of item "Applications" of dmgFolder to {{492, 210}}
    delay 1
    close dmgWindow
end tell
"""
    print("+ osascript <dmg Finder layout>")
    try:
        subprocess.run(["osascript", "-"], input=script, text=True, check=True)
        subprocess.run(["sync"], check=True)
    except subprocess.CalledProcessError:
        print("Warning: Finder layout setup failed. The DMG will still be created without the styled window.")


def build_macos_dmg(app_path: Path, dry_run: bool) -> Path:
    if current_platform() != "macos":
        raise SystemExit("ClipSift packaging is macOS-only. Build the DMG on macOS.")
    if not shutil.which("hdiutil"):
        raise SystemExit("hdiutil is required to build the macOS DMG installer.")

    dmg_root = INSTALLER_DIR / "dmg-root"
    dmg_path = DIST_DIR / f"{APP_NAME}-{APP_VERSION}-macOS.dmg"
    rw_dmg_path = INSTALLER_DIR / f"{APP_NAME}-{APP_VERSION}-rw.dmg"
    mount_path = INSTALLER_DIR / "dmg-mount"

    if not dry_run:
        prepare_dmg_root(app_path, dmg_root)
        DIST_DIR.mkdir(parents=True, exist_ok=True)
        for path in [dmg_path, rw_dmg_path]:
            if path.exists():
                path.unlink()

    run(
        [
            "hdiutil",
            "create",
            "-volname",
            APP_NAME,
            "-srcfolder",
            str(dmg_root),
            "-fs",
            "HFS+",
            "-ov",
            "-format",
            "UDRW",
            str(rw_dmg_path),
        ],
        dry_run=dry_run,
    )

    if not dry_run:
        mounted = attach_dmg(rw_dmg_path, mount_path)
        try:
            apply_dmg_finder_layout(mounted)
            clean_dmg_mount_metadata(mounted)
        finally:
            detach_dmg(mounted)

    run(
        [
            "hdiutil",
            "convert",
            str(rw_dmg_path),
            "-format",
            "UDZO",
            "-imagekey",
            "zlib-level=9",
            "-o",
            str(dmg_path),
        ],
        dry_run=dry_run,
    )

    if not dry_run and rw_dmg_path.exists():
        rw_dmg_path.unlink()
    return dmg_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build ClipSift macOS app packages and installers.")
    parser.add_argument(
        "--target",
        choices=("auto", "app", "installer", "macos-installer", "dmg", "all"),
        default="auto",
        help="Package to build. Default: app.",
    )
    parser.add_argument(
        "--install-deps",
        action="store_true",
        help="Create/use build/.venv and install PyInstaller if needed.",
    )
    parser.add_argument("--clean", action="store_true", help="Remove build/dist, build/work, build/spec, and build/installer first.")
    parser.add_argument("--dry-run", action="store_true", help="Print build commands without running them.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ensure_files()

    if current_platform() != "macos":
        raise SystemExit("ClipSift packaging is macOS-only.")

    if args.clean:
        clean()

    python = pyinstaller_python(args.install_deps, args.dry_run)

    if args.target == "auto":
        targets = ["app"]
    elif args.target == "all":
        targets = ["app", "dmg"]
    elif args.target in {"installer", "macos-installer"}:
        targets = ["dmg"]
    else:
        targets = [args.target]

    outputs: list[Path] = []
    built_app: Path | None = None
    for target in targets:
        if target == "app":
            built_app = build_macos_app(python, args.dry_run)
            outputs.append(built_app)
        elif target == "dmg":
            built_app = built_app or build_macos_app(python, args.dry_run)
            if built_app not in outputs:
                outputs.append(built_app)
            outputs.append(build_macos_dmg(built_app, args.dry_run))

    print("\nBuild output:")
    for output in outputs:
        print(f"  {output}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
