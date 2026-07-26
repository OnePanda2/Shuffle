# Smart VLC Randomizer

A lightweight, fully offline Windows companion for **VLC Media Player** that
delivers a genuinely uniform, unbiased shuffle across a large media library —
solving VLC's tendency to replay the same handful of files and ignore the rest.

It is **not** a media player. VLC does all decoding and playback; this app only
decides *what plays next* and manages per-folder watch history so recently seen
files don't repeat too soon.

## What it does

- Opens up to **6 "Player Slots"** — each an app-launched VLC window assigned to
  one genre folder. Arrange the windows however you like; the app never moves or
  resizes them.
- One core action per slot: **Next**. It evaluates what you were watching,
  updates history, then loads a *truly random* eligible file into that same
  window. **VLC's own Next button (and reaching the end of a clip) does the same
  thing** — the app detects it and jumps to a fresh random pick automatically.
- Watched files enter a **cooldown** (excluded for the next *N* Next-presses in
  that folder). Quickly-skipped files go to a per-folder **Review/Delete list**.
- Multiple slots can share the same folder (shared history, separate windows).
- Everything is local (`127.0.0.1`) and works with Wi-Fi off.
- A bold neo-brutalist look with a **dark / light toggle** (☾ / ☀ in the top bar);
  your choice is remembered between launches.

## Requirements

- Windows 10, VLC Media Player installed
- Python 3.10+ (only to run from source or to build the .exe — the built .exe
  needs neither Python nor the dependencies)

## Run — Option A: the standalone app (recommended, no terminal)

Double-click **`dist\SmartVLCRandomizer.exe`**. That's it — no Python required.
Pin it to the taskbar or make a desktop shortcut (right-click → *Send to →
Desktop*) for one-click launching.

To (re)build the .exe yourself:

```powershell
py -3.10 -m pip install pyinstaller
```
```powershell
build_exe.bat
```

## Run — Option B: from source

```powershell
py -3.10 -m pip install -r requirements.txt
```
```powershell
py -3.10 main.py
```

On first launch you'll be asked to add your genre folders (each is scanned
recursively for video and image files) and, if VLC isn't auto-detected, to point
the app at `vlc.exe`.

## Usage

1. **Add Slot** → a VLC window opens.
2. Pick a **folder** for the slot; a random file loads immediately.
3. Get a new random scene either way: press the app's **Next** button, **or**
   press **Next inside the VLC window** — both jump to a fresh random pick. When
   a clip reaches its end it also auto-advances.
4. **Folder Settings** adjusts, per folder: shuffle count *N* (default 10), skip
   threshold in seconds (default 60), and whether skipped files also enter the
   cooldown (default off).
5. **Review List** lets you permanently delete (to the Recycle Bin) or drop
   skipped files.
6. **Close Slot** closes that VLC window. Open and close slots from the app (use
   its *Close Slot* button rather than VLC's own window-close button).

## Testing

```powershell
py -3.10 -m pytest tests/ -q
```

## Documentation

See [`docs/DEVELOPER.md`](docs/DEVELOPER.md) for architecture, database schema,
the 8-step Next pipeline, and the module communication flow.
```
