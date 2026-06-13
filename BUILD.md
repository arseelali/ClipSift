# Building ClipSift

ClipSift packaging is macOS-only.

Build the app bundle:

```bash
python3 build.py --install-deps --clean
```

Build the macOS DMG installer:

```bash
python3 build.py --target macos-installer --install-deps --clean
```

Build both the `.app` and `.dmg`:

```bash
python3 build.py --target all --install-deps --clean
```

This creates:

```text
build/dist/ClipSift.app
build/dist/ClipSift-1.0.0-macOS.dmg
```

The build script uses:

- `assets/ClipSift.icns` for the macOS app icon
- `assets/ClipSift-light.png` and `assets/ClipSift-dark.png` as bundled runtime icons
- `assets/installer/dmg-background.png` for the macOS drag-to-Applications DMG background
- the full `assets/` folder as bundled runtime assets
- `ffmpeg` and `ffplay` as bundled `bin/` tools when they are available on the build machine
- `hdiutil` for macOS `.dmg` installers
- `osascript` for the styled Finder window layout when available

The macOS DMG build creates a styled Finder window with a background image and places `ClipSift.app` next to the `Applications` shortcut for drag-and-drop installation. The editable source art is in `assets/installer/dmg-background.source.svg`.

If video previews do not play in the packaged app, rebuild from a Mac where `ffmpeg` and `ffplay` are installed and visible in the terminal:

```bash
which ffmpeg ffplay
python3 build.py --target all --install-deps --clean
```
