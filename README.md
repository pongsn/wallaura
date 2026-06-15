# wallaura

Automatically syncs your Lenovo Legion / LOQ keyboard LED zone colors with your KDE Plasma desktop wallpaper.

When the wallpaper changes — whether you change it manually or a slideshow rotates — wallaura analyzes the image, extracts a dominant color from each of its four horizontal sections, and applies those colors to the keyboard's four LED zones in real time.

---

## How it works

1. **Detects wallpaper changes** by listening to the `org.kde.PlasmaShell.wallpaperChanged` D-Bus signal emitted by KDE Plasma. This covers both manual changes and automatic slideshow rotations.
2. **Resolves the image file** via the PlasmaShell D-Bus API. KDE wallpaper packages (directories like `MilkyWay/`) are handled automatically by picking the highest-resolution image inside `contents/images/`.
3. **Splits the image** into four equal vertical strips corresponding to the keyboard's four LED zones (left → right).
4. **Extracts a dominant color** from each strip using median-cut quantization (via Pillow), selecting the color that covers the most pixels rather than a blended average.
5. **Scales the colors** by the configured brightness factor (default 67%) and sends them to the keyboard via `legionaura static <c1> <c2> <c3> <c4> --brightness 2`.

---

## Requirements

- **KDE Plasma** desktop (Wayland or X11)
- **[LegionAura](https://github.com/nivedck/LegionAura)** — the `legionaura` CLI must be installed and working
- **Python 3.10+**
- Python packages: `dbus-python`, `PyGObject`, `Pillow`

On Arch-based systems:

```bash
sudo pacman -S python-dbus python-gobject python-pillow
```

On Debian/Ubuntu:

```bash
sudo apt install python3-dbus python3-gi python3-pil
```

---

## Installation

### 1. Clone the repository

```bash
git clone <repo-url> ~/tst/wallaura
cd ~/tst/wallaura
```

### 2. Make the script executable

```bash
chmod +x wallaura.py
```

### 3. Add to PATH

```bash
ln -sf ~/tst/wallaura/wallaura.py ~/.local/bin/wallaura
```

Verify it is found:

```bash
wallaura --get-brightness
```

### 4. Install the systemd user service

```bash
cp wallaura.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now wallaura.service
```

The service starts automatically on graphical login.

---

## Usage

### Daemon

The daemon is managed via systemctl:

```bash
systemctl --user start wallaura      # start
systemctl --user stop wallaura       # stop
systemctl --user restart wallaura    # restart (pick up code changes)
systemctl --user status wallaura     # show running status
journalctl --user -u wallaura -f     # live log output
```

### Brightness

wallaura applies software brightness scaling to the extracted colors, giving smooth control across the full 0–100% range.

The default brightness is **67%** (approximately two-thirds of maximum).

```bash
wallaura --set-brightness 80%        # set to 80%
wallaura --set-brightness 0.5        # set to 50%  (decimal form)
wallaura --set-brightness 50         # set to 50%  (plain integer)
wallaura --get-brightness            # print current brightness
```

The brightness setting is saved to `~/.config/wallaura/config.json`. The running daemon detects the change within five seconds and re-applies the current wallpaper colors at the new brightness — no restart needed.

---

## Configuration

Settings are stored in `~/.config/wallaura/config.json`:

```json
{
  "brightness": 0.6667
}
```

This file can be edited by hand. The daemon polls it every five seconds and reloads it automatically when it changes.

---

## Troubleshooting

**`legionaura` command not found**
Install LegionAura and ensure the `legionaura` binary is in your PATH. See [LegionAura's README](https://github.com/nivedck/LegionAura) for installation instructions.

**Colors are not applied / keyboard does not respond**
Run `legionaura static ff0000 ff0000 ff0000 ff0000` manually. If that fails, the issue is with LegionAura or USB permissions, not wallaura.

**Slideshow changes are not detected**
Ensure slideshow is configured through KDE's desktop settings (right-click desktop → Configure Desktop). wallaura relies on the `wallpaperChanged` D-Bus signal from PlasmaShell, which is only emitted when Plasma manages the slideshow.

**Service fails to start**
Check the logs with `journalctl --user -u wallaura -f`. The most common cause is a missing Python package; install the dependencies listed in the Requirements section.

---

## License

MIT
