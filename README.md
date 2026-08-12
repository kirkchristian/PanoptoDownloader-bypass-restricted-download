# Panopto Downloader

Download multiple authorized Panopto videos from a folder page through an
interactive Firefox session.

This fork updates the original downloader for current versions of Selenium and
Panopto. It signs in through Firefox, discovers the videos visible in a Panopto
folder, resolves each current podcast stream from the authenticated browser
session, and saves the videos as MP4 files.

## Features

- Downloads every video found on a Panopto `Sessions/List.aspx` folder page
- Supports protected folders through normal interactive sign-in
- Uses a dedicated Firefox profile so the login can persist between runs
- Can process multiple folders without closing Firefox or signing in again
- Supports one-video tests and resuming from a selected video number
- Produces Windows-safe filenames and never overwrites an existing file
- Restricts requests and redirects to HTTPS
- Removes incomplete downloads and avoids printing temporary signed media URLs
- Uses modern Selenium APIs and current Panopto list-page selectors

## Requirements

- Windows 10 or Windows 11
- Python 3.10 or newer
- Mozilla Firefox
- `curl.exe` (included with current versions of Windows)
- The following Python packages:
  - `selenium`
  - `tqdm`

Recent Selenium versions normally manage GeckoDriver automatically. If Firefox
does not open, install a current GeckoDriver and make sure it is available on
your `PATH`.

## Installation

### 1. Download or clone the repository

```powershell
git clone https://github.com/kirkchristian/PanoptoDownloader-bypass-restricted-download.git
Set-Location .\PanoptoDownloader-bypass-restricted-download
```

Alternatively, download the repository as a ZIP from GitHub, extract it, open
PowerShell, and change to the extracted directory.

### 2. Create a virtual environment

The following command keeps the environment outside OneDrive and separate from
the repository:

```powershell
py -m venv "$env:LOCALAPPDATA\PanoptoDownloader-venv"
```

### 3. Install the dependencies

```powershell
& "$env:LOCALAPPDATA\PanoptoDownloader-venv\Scripts\python.exe" -m pip install --upgrade pip
& "$env:LOCALAPPDATA\PanoptoDownloader-venv\Scripts\python.exe" -m pip install selenium tqdm
```

### 4. Optional syntax check

```powershell
& "$env:LOCALAPPDATA\PanoptoDownloader-venv\Scripts\python.exe" -m py_compile .\PanoptoDownloader.py
```

No output means the syntax check passed.

## Finding the correct Panopto URL

Open the folder containing the videos and copy its folder-page URL. The expected
format contains:

```text
/Panopto/Pages/Sessions/List.aspx#folderID=...
```

A single-video URL containing `Pages/Viewer.aspx?id=...` is not a folder URL and
will not provide the complete list of videos.

## Usage

### Download one folder

```powershell
$folderUrl = Read-Host "Paste the Panopto folder URL"
& "$env:LOCALAPPDATA\PanoptoDownloader-venv\Scripts\python.exe" .\PanoptoDownloader.py $folderUrl
```

When prompted:

1. Sign in through the Firefox window opened by the script.
2. Wait until the requested Panopto folder is visible.
3. Return to PowerShell and press Enter.
4. Leave Firefox open until the download finishes.

By default, videos are saved to:

```text
C:\Users\YOUR-USERNAME\Downloads\Panopto
```

### Download multiple folders without signing in again

Use `--stay-open`:

```powershell
$folderUrl = Read-Host "Paste the first Panopto folder URL"
& "$env:LOCALAPPDATA\PanoptoDownloader-venv\Scripts\python.exe" .\PanoptoDownloader.py $folderUrl --stay-open
```

After each folder finishes, PowerShell displays:

```text
Paste the next Panopto folder URL, or press Enter to finish:
```

Paste the next folder URL and press Enter. The same Firefox window and login are
reused. Press Enter without pasting a URL when all folders are finished.

### Test only the first video

Testing one video before a large download is recommended:

```powershell
& "$env:LOCALAPPDATA\PanoptoDownloader-venv\Scripts\python.exe" .\PanoptoDownloader.py $folderUrl --limit 1
```

### Resume from a particular video

If the script reports that video 7 failed, restart at video 7:

```powershell
& "$env:LOCALAPPDATA\PanoptoDownloader-venv\Scripts\python.exe" .\PanoptoDownloader.py $folderUrl --start-at 7
```

Video numbers correspond to the order found on the Panopto folder page.

### Choose another output directory

```powershell
& "$env:LOCALAPPDATA\PanoptoDownloader-venv\Scripts\python.exe" .\PanoptoDownloader.py $folderUrl --output "D:\Panopto Videos"
```

### Download a public folder

Use `--public` only when the folder does not require authentication:

```powershell
& "$env:LOCALAPPDATA\PanoptoDownloader-venv\Scripts\python.exe" .\PanoptoDownloader.py $folderUrl --public
```

## Command-line options

| Option | Description |
| --- | --- |
| `URL` | Required Panopto folder URL |
| `--stay-open` | Reuse the same Firefox session for additional folders |
| `--limit NUMBER` | Download at most the specified number of videos |
| `--start-at NUMBER` | Begin at the specified video number |
| `--output PATH` | Select a different download directory |
| `--public` | Skip the interactive sign-in step for a public folder |
| `-h`, `--help` | Display the built-in help |

When `--stay-open` is combined with `--limit` or `--start-at`, those range
options apply only to the first folder. Each additional folder starts from its
first video and downloads the complete folder.

## Firefox profile and login persistence

The script stores its dedicated Firefox profile at:

```text
%LOCALAPPDATA%\PanoptoDownloader\FirefoxProfile
```

This profile is separate from your normal Firefox profile. It may retain your
Panopto login between runs, but the institution can expire the session at any
time. If that happens, sign in again when the script prompts you.

The profile contains browser cookies. Do not upload, share, or commit that
folder to GitHub.

## Existing files

The downloader does not overwrite files. If a filename already exists, it adds
a number:

```text
Lecture.mp4
Lecture (2).mp4
Lecture (3).mp4
```

Check the output directory before restarting a complete folder if duplicate
downloads are not wanted.

## Troubleshooting

### `the following arguments are required: URL`

The URL variable is empty. Either paste the URL after this prompt:

```powershell
$folderUrl = Read-Host "Paste the Panopto folder URL"
```

Or assign it directly using single quotes:

```powershell
$folderUrl = 'https://your-school.hosted.panopto.com/Panopto/Pages/Sessions/List.aspx#folderID=...'
```

Do not put the URL inside the `Read-Host` prompt text. For example, this is
incorrect:

```powershell
$folderUrl = Read-Host "https://your-school.hosted.panopto.com/..."
```

### No Panopto video links were found

- Confirm that the URL contains `Pages/Sessions/List.aspx`.
- Confirm that the requested folder is visible after signing in.
- Wait for the folder to finish loading before pressing Enter.
- Confirm that the folder contains videos your account can view.

### Firefox says the profile is already in use

Close the Firefox window previously opened by the downloader, then rerun the
command. The dedicated downloader profile cannot be controlled by two script
processes simultaneously.

### A download fails partway through a folder

Rerun the script with `--start-at NUMBER`, using the failed video number shown
in the error. Panopto media URLs are temporary, so rerunning the script obtains
a new URL.

### Segmented or obfuscated stream error

The script supports direct podcast streams. It intentionally stops if Panopto
returns a segmented or obfuscated stream rather than a direct downloadable
file. It does not decrypt DRM-protected media.

## Security and privacy

- Credentials are entered only in the institution's Firefox sign-in page, not
  in PowerShell or the Python script.
- Temporary media URLs may contain access tokens. Do not share or publish them.
- The script validates HTTPS URLs, redacts media URLs from errors, and deletes
  empty, incomplete, or HTML responses instead of keeping them as MP4 files.

## Responsible use

Use this tool only for videos you are authorized to access and download. Follow
your institution's policies, the course's rules, applicable copyright law, and
Panopto's terms. Keep downloaded course material for permitted personal use and
do not redistribute it without authorization.

## Acknowledgments

This project modernizes
[Shamdan17/PanoptoDownloader](https://github.com/Shamdan17/PanoptoDownloader).
Its current stream-resolution approach is compatible with the method used by
the open-source
[Panopto-Video-DL browser userscript](https://github.com/Panopto-Video-DL/Panopto-Video-DL-browser).
