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
  Pressing **Next always moves to a different file** — it never re-serves the one
  you were just watching.
- A **♥ Favorite** button on each slot (shown while a movie is loaded) adds the
  current movie to your **Favorites** — a permanent, built-in genre that plays
  only favorited movies. Favorited movies also get a gentle **priority** whenever
  their own genre plays. Click the filled heart again to un-favorite.
- Multiple slots can share the same folder (shared history, separate windows).
- **Cloud genres (new):** a genre can stream from an **Internet Archive** item instead
  of a local folder, so a huge library can live online for free instead of on your disk.
  See "Cloud genres" below.
- Local genres are fully offline (`127.0.0.1`) and work with Wi-Fi off. Cloud genres
  stream over the internet; the two work side by side.
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
6. **♥ Favorite**: while a movie is loaded, click the heart on its slot to add it
   to Favorites. Assign a slot to the **❤ Favorites** genre to play only
   favorited movies. Un-heart to remove.
7. **Close Slot** closes that VLC window. Open and close slots from the app (use
   its *Close Slot* button rather than VLC's own window-close button).

## Cloud genres (stream from Internet Archive)

Short on disk space? Put your **own or public-domain** movies on the Internet Archive
(free, no real size cap) and let a genre stream from there. VLC does the streaming; the
app just picks the next URL.

**1. Upload your movies** (once, per genre) with the free `ia` command-line tool. First
install it:

```powershell
py -3.10 -m pip install internetarchive
```

Then **`cd` into the folder that holds that genre's movies** and upload its contents. Doing
it from inside the folder is important — it keeps the movies' file names clean. If you
instead pass a full path like `ia upload my_movies_action "F:\Movies\Action\film.mp4"`,
Internet Archive stores the whole path as the file name and (if you point at a single
file) you upload only that one movie, so the genre can't shuffle.

```powershell
cd "F:\Movies\Action"
```
```powershell
ia upload my_movies_action * --metadata="mediatype:movies" --metadata="noindex:true"
```

- `my_movies_action` is the **item identifier** — pick something unique and non-obvious
  (it becomes part of the public URL). Use a separate item per genre, and put **all** of
  that genre's movies in it so there's something to shuffle between.
- `*` uploads every file in the folder; the app automatically ignores non-video files.
- `noindex:true` keeps the item **out of archive.org search** — it's reachable only by its
  direct link. (Note: unlisted is not the same as private — anyone you give a link to can
  open it, so keep your links to yourself.)
- Large uploads take as long as your connection allows and can run in the background. To
  add more movies later, run the same `ia upload` again (from inside the folder) with the
  same identifier, then hit **Rescan** in the app.

**2. Add a cloud genre in the app:** Settings → Add… → set **Type = Internet Archive** and
enter your item ID(s), comma-separated. That's it — the slot streams a random movie from
those items, with the same shuffle, cooldown, favorites, and Personal Algorithm as local
genres. **Rescan** re-fetches the item's file list after you upload more.

## Testing

```powershell
py -3.10 -m pytest tests/ -q
```

## Documentation

See [`docs/DEVELOPER.md`](docs/DEVELOPER.md) for architecture, database schema,
the 8-step Next pipeline, and the module communication flow.
```
