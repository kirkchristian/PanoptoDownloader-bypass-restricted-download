import argparse
import os
import re
import shutil
import subprocess
import time
import unicodedata
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from tqdm import tqdm


WINDOWS_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL"}
WINDOWS_RESERVED_NAMES.update(f"COM{i}" for i in range(1, 10))
WINDOWS_RESERVED_NAMES.update(f"LPT{i}" for i in range(1, 10))

VIDEO_ID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

LIST_SELECTORS = (
    "#listViewContainer tbody > tr a.detail-title",
    "#detailsTable tbody > tr a.detail-title",
    "#thumbnailGrid > li a.detail-title",
    'a[href*="Pages/Viewer.aspx?id="]',
)

# Run the same authenticated DeliveryInfo request used by the current
# Panopto-Video-DL browser userscript. The signed media URL is returned to
# Python but is never printed.
DELIVERY_INFO_SCRIPT = r"""
const videoId = arguments[0];
const done = arguments[arguments.length - 1];
const body = new URLSearchParams({
    deliveryId: videoId,
    isEmbed: "true",
    responseType: "json"
}).toString();

fetch("/Panopto/Pages/Viewer/DeliveryInfo.aspx", {
    method: "POST",
    credentials: "same-origin",
    headers: {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"
    },
    body: body
})
.then(async (response) => {
    const text = await response.text();
    if (!response.ok) {
        throw new Error(`DeliveryInfo returned HTTP ${response.status}`);
    }
    try {
        return JSON.parse(text);
    } catch (error) {
        throw new Error("DeliveryInfo returned an unexpected response");
    }
})
.then((data) => {
    if (data && data.ErrorCode) {
        done({
            ok: false,
            error: data.ErrorMessage || `Panopto error ${data.ErrorCode}`
        });
        return;
    }

    const streamUrl = data?.Delivery?.PodcastStreams?.[0]?.StreamUrl;
    if (!streamUrl) {
        done({
            ok: false,
            error: "Panopto did not return a podcast stream for this video"
        });
        return;
    }

    done({ok: true, url: streamUrl});
})
.catch((error) => {
    done({ok: false, error: String(error.message || error)});
});
"""


def positive_integer(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a whole number") from exc
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def add_max_results(url: str) -> str:
    """Add or replace Panopto's maxResults query parameter."""
    parts = urlsplit(url)
    if parts.scheme.lower() != "https" or not parts.hostname:
        raise ValueError("The Panopto URL must be a complete HTTPS URL.")
    if parts.username or parts.password:
        raise ValueError("The Panopto URL must not contain embedded credentials.")

    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() != "maxresults"
    ]
    query.append(("maxResults", "250"))
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )


def safe_filename(title: str, fallback: str) -> str:
    """Create a Windows-safe filename stem from a Panopto title."""
    title = unicodedata.normalize("NFKC", title)
    title = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", title)
    title = re.sub(r"\s+", " ", title).strip(" .")
    title = title[:180].rstrip(" .") or fallback

    if title.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES:
        title = f"_{title}"

    return title


def available_path(directory: Path, stem: str) -> Path:
    """Return a new MP4 path without overwriting an existing file."""
    target = directory / f"{stem}.mp4"
    counter = 2
    while target.exists():
        target = directory / f"{stem} ({counter}).mp4"
        counter += 1
    return target


def persistent_firefox_profile() -> Path:
    """Return a dedicated profile that can retain the Panopto login."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        profile = Path(local_app_data) / "PanoptoDownloader" / "FirefoxProfile"
    else:
        profile = Path.home() / ".panopto-downloader" / "firefox-profile"

    profile.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        profile.chmod(0o700)
    except OSError:
        # Windows ACLs, rather than POSIX mode bits, normally protect this path.
        pass
    return profile


def create_driver() -> webdriver.Firefox:
    """Start Firefox with a dedicated persistent Selenium profile."""
    options = webdriver.FirefoxOptions()
    options.add_argument("-profile")
    options.add_argument(str(persistent_firefox_profile()))

    driver = webdriver.Firefox(options=options)
    driver.implicitly_wait(10)
    driver.maximize_window()
    return driver


def find_video_elements(driver: webdriver.Firefox):
    """Use the first Panopto list-layout selector that returns results."""
    for selector in LIST_SELECTORS:
        elements = driver.find_elements(By.CSS_SELECTOR, selector)
        if elements:
            return elements
    return []


def collect_videos(driver: webdriver.Firefox, folder_url: str):
    """Collect unique Panopto video IDs and titles from the current folder."""
    previous_count = -1

    # Panopto may add sessions lazily while the list scrolls.
    for _ in range(20):
        elements = find_video_elements(driver)
        current_count = len(elements)
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(1)
        if current_count == previous_count:
            break
        previous_count = current_count

    elements = find_video_elements(driver)
    expected_host = (urlsplit(folder_url).hostname or "").casefold()
    videos = []
    seen_ids = set()

    for element in elements:
        href = element.get_attribute("href")
        if not href:
            continue

        parsed = urlsplit(href)
        actual_host = (parsed.hostname or "").casefold()
        if parsed.scheme.lower() != "https" or actual_host != expected_host:
            continue

        video_ids = parse_qs(parsed.query).get("id")
        if not video_ids:
            continue

        video_id = video_ids[0].strip()
        if not VIDEO_ID_PATTERN.fullmatch(video_id) or video_id in seen_ids:
            continue
        seen_ids.add(video_id)

        text_content = (element.get_attribute("textContent") or "").strip()
        title = (
            text_content
            or element.get_attribute("aria-label")
            or element.get_attribute("title")
            or f"video-{len(videos) + 1}"
        )
        title = re.sub(r"\s+", " ", title).strip()
        videos.append((video_id, title))

    return videos


def redact_urls(message: str) -> str:
    """Remove URLs before an error reaches the terminal."""
    return re.sub(r"https://\S+", "[URL redacted]", message, flags=re.IGNORECASE)


def resolve_stream_url(driver: webdriver.Firefox, video_id: str) -> str:
    """Resolve one signed podcast-stream URL inside authenticated Firefox."""
    driver.set_script_timeout(45)
    result = driver.execute_async_script(DELIVERY_INFO_SCRIPT, video_id)

    if not isinstance(result, dict) or not result.get("ok"):
        error = "Unknown DeliveryInfo error"
        if isinstance(result, dict) and result.get("error"):
            error = redact_urls(str(result["error"]))
        raise RuntimeError(error)

    stream_url = str(result.get("url", ""))
    parts = urlsplit(stream_url)
    if (
        parts.scheme.lower() != "https"
        or not parts.hostname
        or parts.username
        or parts.password
        or any(character in stream_url for character in "\r\n\x00")
    ):
        raise RuntimeError("Panopto returned an invalid or non-HTTPS media URL.")

    stream_path = parts.path.casefold()
    if stream_path.endswith(".m3u8") or re.search(r"\.panobf\d*$", stream_path):
        raise RuntimeError(
            "Panopto returned a segmented/obfuscated stream instead of a direct "
            "MP4. This script intentionally stopped rather than saving a broken file."
        )

    return stream_url


def download_video(
    url: str,
    target: Path,
    referer: str,
    user_agent: str,
) -> None:
    """Download a resolved HTTPS media URL without exposing it in errors."""
    curl_path = shutil.which("curl.exe") or shutil.which("curl")
    if not curl_path:
        raise RuntimeError("curl.exe was not found on this computer.")

    completed = subprocess.run(
        [
            curl_path,
            "--location",
            "--max-redirs",
            "5",
            "--fail",
            "--silent",
            "--show-error",
            "--remove-on-error",
            "--retry",
            "2",
            "--connect-timeout",
            "30",
            "--proto",
            "=https",
            "--proto-redir",
            "=https",
            "--user-agent",
            user_agent,
            "--referer",
            referer,
            "--output",
            str(target),
            "--",
            url,
        ],
        check=False,
        capture_output=True,
        text=True,
        errors="replace",
    )

    if completed.returncode != 0:
        target.unlink(missing_ok=True)
        detail = redact_urls(completed.stderr.strip())
        if detail:
            detail = detail.splitlines()[-1][:300]
            raise RuntimeError(
                f"curl failed with exit code {completed.returncode}: {detail}"
            )
        raise RuntimeError(f"curl failed with exit code {completed.returncode}.")

    if not target.exists() or target.stat().st_size == 0:
        target.unlink(missing_ok=True)
        raise RuntimeError("The server returned an empty file.")

    with target.open("rb") as downloaded_file:
        header = downloaded_file.read(256).lstrip().lower()
    if header.startswith(b"<!doctype html") or header.startswith(b"<html"):
        target.unlink(missing_ok=True)
        raise RuntimeError("The server returned a web page instead of a video file.")


def wait_for_page(driver: webdriver.Firefox) -> None:
    """Wait for the current page and its client-side content to settle."""
    WebDriverWait(driver, 30).until(
        lambda current_driver: current_driver.execute_script(
            "return document.readyState"
        )
        == "complete"
    )
    time.sleep(5)


def load_folder_videos(
    driver: webdriver.Firefox,
    folder_url: str,
    public: bool,
):
    """Open a folder, requesting interactive login only when necessary."""
    driver.get(folder_url)
    wait_for_page(driver)

    # A saved profile or the current --stay-open session may already be signed in.
    videos = collect_videos(driver, driver.current_url)
    if videos or public:
        return videos

    print("\nFirefox needs your Panopto login.")
    print("Sign in and wait until the requested Panopto folder is visible.")
    print("Then return here and press Enter.")
    input()

    driver.get(folder_url)
    wait_for_page(driver)
    return collect_videos(driver, driver.current_url)


def download_folder(
    driver: webdriver.Firefox,
    raw_folder_url: str,
    output_directory: Path,
    start_at: int,
    limit: int | None,
    public: bool,
) -> None:
    """Download the selected range from one Panopto folder."""
    folder_url = add_max_results(raw_folder_url)
    videos = load_folder_videos(driver, folder_url, public)

    if not videos:
        raise RuntimeError(
            "No Panopto video links were found. The folder may not be fully "
            "loaded, its layout may have changed, or your login may have expired."
        )

    if start_at > len(videos):
        raise ValueError(
            f"--start-at is {start_at}, but only {len(videos)} videos were found."
        )

    selected_videos = videos[start_at - 1 :]
    if limit is not None:
        selected_videos = selected_videos[:limit]

    print(f"Found {len(videos)} unique videos.")
    print(
        f"Selected {len(selected_videos)} video(s), starting at number {start_at}."
    )
    print(f"Saving to: {output_directory}\n")

    referer = driver.current_url
    user_agent = str(
        driver.execute_script("return navigator.userAgent;") or "Mozilla/5.0"
    )

    progress = tqdm(
        enumerate(selected_videos, start=start_at),
        total=len(selected_videos),
        desc="Downloading",
        unit="video",
    )

    for index, (video_id, title) in progress:
        stem = safe_filename(title, f"video-{index}")
        target = available_path(output_directory, stem)
        progress.set_postfix_str(target.name[:60])

        try:
            stream_url = resolve_stream_url(driver, video_id)
            download_video(stream_url, target, referer, user_agent)
        except Exception as exc:
            raise RuntimeError(
                f"Video {index} ({target.name}) failed: {redact_urls(str(exc))}"
            ) from None

    print(
        f"\nFinished {len(selected_videos)} video(s). "
        f"Videos are in: {output_directory}"
    )
    next_video = start_at + len(selected_videos)
    if limit is not None and next_video <= len(videos):
        print(f"The next video number is {next_video}.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download authorized videos from a Panopto folder or playlist."
    )
    parser.add_argument("url", metavar="URL", help="Panopto folder or playlist URL")
    parser.add_argument(
        "--public",
        action="store_true",
        help="Skip the interactive login step for a public folder",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path.home() / "Downloads" / "Panopto",
        help="Download directory (default: your Downloads/Panopto folder)",
    )
    parser.add_argument(
        "--start-at",
        type=positive_integer,
        default=1,
        metavar="NUMBER",
        help="Start with this video number (default: 1)",
    )
    parser.add_argument(
        "--limit",
        type=positive_integer,
        metavar="NUMBER",
        help="Download at most this many videos (use --limit 1 for testing)",
    )
    parser.add_argument(
        "--stay-open",
        action="store_true",
        help=(
            "After each folder, ask for another URL and reuse the same Firefox "
            "login; range options apply only to the first folder"
        ),
    )
    args = parser.parse_args()

    output_directory = args.output.expanduser().resolve()
    output_directory.mkdir(parents=True, exist_ok=True)

    current_url = args.url
    current_start = args.start_at
    current_limit = args.limit
    driver = create_driver()

    try:
        while True:
            download_folder(
                driver,
                current_url,
                output_directory,
                current_start,
                current_limit,
                args.public,
            )

            if not args.stay_open:
                break

            print("\nFirefox is staying open with the same login.")
            next_url = input(
                "Paste the next Panopto folder URL, or press Enter to finish: "
            ).strip()
            if not next_url:
                break

            current_url = next_url
            current_start = 1
            current_limit = None
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
