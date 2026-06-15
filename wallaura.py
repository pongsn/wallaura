#!/usr/bin/env python3
"""wallaura — sync keyboard LED zones with the current KDE Plasma wallpaper.

Daemon mode (default):
  Listens for wallpaperChanged D-Bus signals from PlasmaShell, analyzes the
  wallpaper by splitting it into 4 equal vertical strips, extracts a dominant
  color per strip using median-cut quantization, scales each color by the
  configured brightness, and applies the 4 colors via `legionaura static`.

CLI modes:
  wallaura --set-brightness <value>   write brightness to config and exit
                                       value: 0.0–1.0  or  0–100  or  0–100%
  wallaura --get-brightness           print current brightness and exit
"""

import json
import os
import subprocess
import logging
import sys
from pathlib import Path
from urllib.parse import urlparse, unquote

import dbus
import dbus.mainloop.glib
from gi.repository import GLib
from PIL import Image

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)-7s  %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger('wallaura')

LEGIONAURA = 'legionaura'
SCREEN = 0
DEFAULT_BRIGHTNESS = 2 / 3          # ≈ 0.667
CONFIG_PATH = Path.home() / '.config' / 'wallaura' / 'config.json'

# Module-level state updated by the daemon loop
_brightness: float = DEFAULT_BRIGHTNESS
_config_mtime: float = 0.0
_last_colors: list[tuple[int, int, int]] | None = None


# ---------------------------------------------------------------------------
# Config persistence
# ---------------------------------------------------------------------------

def load_config() -> None:
    """Read config.json and update the module-level brightness."""
    global _brightness, _config_mtime
    try:
        data = json.loads(CONFIG_PATH.read_text())
        _brightness = float(data.get('brightness', DEFAULT_BRIGHTNESS))
        _brightness = max(0.0, min(1.0, _brightness))
        _config_mtime = CONFIG_PATH.stat().st_mtime
        log.debug('Config loaded: brightness=%.3f', _brightness)
    except FileNotFoundError:
        pass  # use default — no file yet
    except Exception as exc:
        log.warning('Could not read config: %s', exc)


def save_config(brightness: float) -> None:
    """Write brightness to config.json, creating the directory if needed."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps({'brightness': round(brightness, 4)}, indent=2) + '\n')


def _parse_brightness(value: str) -> float:
    """Parse a user-supplied brightness value into a float in [0.0, 1.0]."""
    value = value.strip().rstrip('%')
    f = float(value)
    if f > 1.0:          # treat as percentage (0–100)
        f /= 100.0
    if not 0.0 <= f <= 1.0:
        raise ValueError(f'brightness must be between 0 and 100 (got {value})')
    return f


# ---------------------------------------------------------------------------
# Wallpaper path resolution
# ---------------------------------------------------------------------------

def _best_image_in_dir(directory: Path) -> Path | None:
    """Return the largest image inside a KDE wallpaper package or plain directory."""
    # KDE wallpaper package: <name>/contents/images/<res>.png
    package_images = directory / 'contents' / 'images'
    search_dir = package_images if package_images.is_dir() else directory

    IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.webp', '.avif'}
    candidates = [
        p for p in search_dir.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS
    ]
    return max(candidates, key=lambda p: p.stat().st_size) if candidates else None


def get_wallpaper_path() -> Path | None:
    """Return the filesystem path of the currently displayed wallpaper image."""
    try:
        bus = dbus.SessionBus()
        ps = bus.get_object('org.kde.plasmashell', '/PlasmaShell')
        iface = dbus.Interface(ps, 'org.kde.PlasmaShell')
        params = dict(iface.wallpaper(SCREEN))
        image_uri = str(params.get('Image', ''))
    except dbus.DBusException as exc:
        log.warning('D-Bus error getting wallpaper params: %s', exc)
        return None

    if not image_uri or image_uri == 'null':
        log.warning('wallpaper() returned empty Image field')
        return None

    path = Path(unquote(urlparse(image_uri).path))

    if path.is_file():
        return path
    if path.is_dir():
        resolved = _best_image_in_dir(path)
        if resolved:
            return resolved
        log.warning('No images found in wallpaper directory: %s', path)
    else:
        log.warning('Cannot resolve image path from URI: %s', image_uri)
    return None


# ---------------------------------------------------------------------------
# Image analysis: 4 vertical zones → dominant color each
# ---------------------------------------------------------------------------

def _dominant_color(region: Image.Image) -> tuple[int, int, int]:
    """Return the most visually prominent RGB color in an image region.

    Downscales the region, runs median-cut quantization to 5 colors, and picks
    the one covering the most pixels.
    """
    small = region.resize((100, 100), Image.LANCZOS).convert('RGB')

    NUM_COLORS = 5
    quantized = small.quantize(colors=NUM_COLORS, method=Image.Quantize.MEDIANCUT)
    palette = quantized.getpalette()[:NUM_COLORS * 3]

    counts = [0] * NUM_COLORS
    for idx in quantized.get_flattened_data():
        counts[idx] += 1

    best = counts.index(max(counts))
    return palette[best * 3], palette[best * 3 + 1], palette[best * 3 + 2]


def analyze_wallpaper(path: Path) -> list[tuple[int, int, int]]:
    """Split the wallpaper into 4 vertical strips and return one color per strip."""
    img = Image.open(path).convert('RGB')
    w, h = img.size
    zone_w = w // 4
    colors: list[tuple[int, int, int]] = []

    for zone in range(4):
        x0 = zone * zone_w
        x1 = w if zone == 3 else (zone + 1) * zone_w
        colors.append(_dominant_color(img.crop((x0, 0, x1, h))))

    return colors


# ---------------------------------------------------------------------------
# Apply colors to keyboard via legionaura CLI
# ---------------------------------------------------------------------------

def _scale(channel: int, factor: float) -> int:
    return round(channel * factor)


def apply_colors(colors: list[tuple[int, int, int]], brightness: float | None = None) -> None:
    """Scale colors by brightness and call `legionaura static <c1..c4> --brightness 2`.

    Hardware brightness is pinned to 2 (max) so that wallaura's software
    scaling has the full dynamic range.
    """
    if brightness is None:
        brightness = _brightness

    scaled = [
        (_scale(r, brightness), _scale(g, brightness), _scale(b, brightness))
        for r, g, b in colors
    ]
    hex_colors = [f'{r:02x}{g:02x}{b:02x}' for r, g, b in scaled]
    cmd = [LEGIONAURA, 'static'] + hex_colors + ['--brightness', '2']
    log.info('brightness=%.0f%%  zones=%s', brightness * 100, ' '.join(hex_colors))

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log.error('legionaura failed (exit %d): %s', result.returncode, result.stderr.strip())


# ---------------------------------------------------------------------------
# Main update cycle
# ---------------------------------------------------------------------------

def update() -> None:
    """Detect current wallpaper, analyze it, and push scaled colors to keyboard."""
    global _last_colors

    path = get_wallpaper_path()
    if path is None:
        log.warning('No wallpaper image resolved — skipping update')
        return

    log.info('Wallpaper: %s', path)
    try:
        colors = analyze_wallpaper(path)
        _last_colors = colors
        apply_colors(colors)
    except Exception as exc:
        log.error('Error processing %s: %s', path, exc)


def _reapply_with_current_brightness() -> None:
    """Re-apply last known colors with the updated brightness — no image re-read."""
    if _last_colors:
        apply_colors(_last_colors)
    else:
        update()


# ---------------------------------------------------------------------------
# Config file hot-reload (polled every 5 s via GLib timer)
# ---------------------------------------------------------------------------

def _poll_config() -> bool:
    """GLib timer callback: reload config if the file changed, then re-apply."""
    global _config_mtime
    try:
        mtime = CONFIG_PATH.stat().st_mtime
        if mtime != _config_mtime:
            log.info('Config changed — reloading brightness')
            load_config()
            _reapply_with_current_brightness()
    except FileNotFoundError:
        pass
    return True  # keep the timer alive


# ---------------------------------------------------------------------------
# D-Bus signal handler
# ---------------------------------------------------------------------------

def _on_wallpaper_changed(screen_num: int) -> None:
    log.info('wallpaperChanged signal (screen %s)', screen_num)
    update()


# ---------------------------------------------------------------------------
# Daemon entry point
# ---------------------------------------------------------------------------

def run_daemon() -> None:
    load_config()
    log.info('wallaura started  brightness=%.0f%%', _brightness * 100)

    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus()

    bus.add_signal_receiver(
        _on_wallpaper_changed,
        signal_name='wallpaperChanged',
        dbus_interface='org.kde.PlasmaShell',
        path='/PlasmaShell',
    )

    update()  # apply immediately on startup

    GLib.timeout_add_seconds(5, _poll_config)
    GLib.MainLoop().run()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    args = sys.argv[1:]

    if not args:
        run_daemon()
        return

    if args[0] == '--get-brightness':
        load_config()
        print(f'{_brightness * 100:.1f}%  ({_brightness:.4f})')
        return

    if args[0] == '--set-brightness':
        if len(args) < 2:
            print('Usage: wallaura --set-brightness <value>', file=sys.stderr)
            print('  value: 0–100, 0.0–1.0, or 0–100%', file=sys.stderr)
            sys.exit(1)
        try:
            brightness = _parse_brightness(args[1])
        except ValueError as exc:
            print(f'Error: {exc}', file=sys.stderr)
            sys.exit(1)

        save_config(brightness)
        print(f'Brightness set to {brightness * 100:.1f}%')
        print(f'(The running daemon picks this up within 5 seconds.)')
        return

    print(f'Unknown argument: {args[0]}', file=sys.stderr)
    print('Usage:', file=sys.stderr)
    print('  wallaura                          run as daemon', file=sys.stderr)
    print('  wallaura --set-brightness <val>   set brightness (0–100%)', file=sys.stderr)
    print('  wallaura --get-brightness         show current brightness', file=sys.stderr)
    sys.exit(1)


if __name__ == '__main__':
    main()
