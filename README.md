# ClipSift

ClipSift is a macOS desktop app for quickly reviewing folders of photos and videos and renaming files one by one. It is built for media cleanup workflows where opening a full editor or file manager preview is too slow.

Source: [github.com/arseelali/ClipSift](https://github.com/arseelali/ClipSift/)

## Features

- Open a media folder from the app menu or toolbar.
- Preview photos normally and play video previews in the app.
- Rename the current file by entering a new filename without the extension.
- Leave the rename box blank to skip a file.
- Jump to any file with the built-in file list and search box.
- Use video speed controls from `1x` to `8x`.
- Configure frame rate per video speed in Settings.
- Toggle video mute for the session.
- Fullscreen the current media preview.
- Undo the last rename.
- Ignore hidden files and macOS `._` metadata files.
- Build a macOS `.app` and drag-to-Applications `.dmg` installer.

## Supported Media

Photos:

```text
.jpg .jpeg .png .gif .bmp .tif .tiff .heic .heif .webp .raw .cr2 .nef .arw .dng
```

Videos:

```text
.mp4 .mov .m4v .avi .mkv .webm .wmv .flv .mpeg .mpg .3gp .mts .m2ts
```

## Running From Source

Requirements:

- macOS
- Python 3
- Tkinter, included with most standard Python installers
- `ffmpeg` for video previews
- `ffplay` for video audio, optional

Run the app:

```bash
python3 ClipSift.py
```

## Common Shortcuts

| Shortcut | Action |
| --- | --- |
| `Enter` | Rename or skip |
| `Command-O` | Open current media |
| `Command-A` | Previous file |
| `Command-D` | Next file |
| `Command-S` | Use current filename in the rename box |
| `Command-F` | Fullscreen media |
| `Command-Shift-F` | Focus the search box |
| `Command-R` | Focus the rename box |
| `Command-P` | Play or pause video preview |
| `Command-M` | Toggle mute |
| `Command-Z` | Undo rename |
| `Up` | Recall previous nonblank filename input |

Shortcuts can be customized from the app menu.

## Building

See [BUILD.md](BUILD.md) for full build details.

Build the app bundle:

```bash
python3 build.py --install-deps --clean
```

Build the macOS DMG installer:

```bash
python3 build.py --target macos-installer --install-deps --clean
```

Build both outputs:

```bash
python3 build.py --target all --install-deps --clean
```

## Website

The static showcase site lives in [web](web/). It can be opened directly from `web/index.html` or published with a static host such as GitHub Pages.

The hero mockup uses [web/assets/sample-mockup.png](web/assets/sample-mockup.png), a replaceable 1920x1080 placeholder image.

## Notes

ClipSift stores settings and session state in `~/.ClipSift.json`.

If video previews do not work in a packaged app, rebuild from a Mac where `ffmpeg` and `ffplay` are installed and visible in the terminal:

```bash
which ffmpeg ffplay
python3 build.py --target all --install-deps --clean
```
