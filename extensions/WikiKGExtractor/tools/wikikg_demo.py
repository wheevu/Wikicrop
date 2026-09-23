"""Start a self-contained WikiKGExtractor browser demo in MediaWiki Docker.

The demo uses the committed synthetic rice fixture, a local SQLite wiki, the
real Special:WikiKGExtractor page, and the production Cytoscape renderer.
Gemini and Neo4j stay disabled.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Sequence


EXTENSION_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = EXTENSION_ROOT.parents[1]
BASE_COMPOSE_FILE = REPOSITORY_ROOT / "docker-compose.yml"
DEMO_COMPOSE_FILE = EXTENSION_ROOT / "demo" / "docker-compose.visual-demo.yml"
RAW_FIXTURE = EXTENSION_ROOT / "tests" / "fixtures" / "raw-data.rules.json"
LOCAL_SETTINGS = REPOSITORY_ROOT / "LocalSettings.php"
VENDOR_AUTOLOAD = REPOSITORY_ROOT / "vendor" / "autoload.php"
ENV_FILE = REPOSITORY_ROOT / ".env"
CACHE_DIRECTORY = REPOSITORY_ROOT / "cache" / "sqlite"
MINIMUM_COMPOSE_VERSION = (2, 24, 4)
DEFAULT_PORT = 8088
DEMO_USER = "Admin"
DEMO_PASSWORD = "dockerpass"
CONFIG_BEGIN = "# BEGIN WikiKGExtractor visual demo"
CONFIG_END = "# END WikiKGExtractor visual demo"
CONFIG_BLOCK = f"""{CONFIG_BEGIN}
wfLoadExtension( 'WikiKGExtractor' );
$wgWikiKGExtractorEnableKG = true;
$wgWikiKGExtractorPythonCommand = '/usr/local/bin/python3';
$wgWikiKGExtractorGeminiApiKey = '';
$wgWikiKGExtractorPushToNeo4j = false;
{CONFIG_END}
"""


class DemoError(RuntimeError):
    """A failure with a concrete recovery step for the demo user."""


def project_name_for_checkout(checkout_path: Path) -> str:
    checkout = checkout_path.resolve()
    identity = hashlib.sha256(os.fsencode(checkout)).hexdigest()[:16]
    return f"wikicrop-kg-demo-{identity}"


PROJECT_NAME = project_name_for_checkout(REPOSITORY_ROOT)


def parse_compose_version(version_text: str) -> tuple[int, int, int]:
    match = re.fullmatch(
        r"\s*v?(\d+)\.(\d+)\.(\d+)(?:[-+][0-9A-Za-z.-]+)?\s*",
        version_text,
    )
    if match is None:
        raise DemoError(
            f"Could not parse Docker Compose version: {version_text.strip()!r}."
        )
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def ensure_supported_compose_version(version_text: str) -> None:
    version = parse_compose_version(version_text)
    if version < MINIMUM_COMPOSE_VERSION:
        found = ".".join(str(part) for part in version)
        required = ".".join(str(part) for part in MINIMUM_COMPOSE_VERSION)
        raise DemoError(
            f"Docker Compose {required} or newer is required; found {found}. "
            "Upgrade Docker Compose and try again."
        )


def compose_command(*arguments: str) -> list[str]:
    return [
        "docker",
        "compose",
        "-p",
        PROJECT_NAME,
        "-f",
        str(BASE_COMPOSE_FILE),
        "-f",
        str(DEMO_COMPOSE_FILE),
        *arguments,
    ]


def command_environment(port: int) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "MW_DOCKER_PORT": str(port),
            "MEDIAWIKI_USER": DEMO_USER,
            "MEDIAWIKI_PASSWORD": DEMO_PASSWORD,
            "XDEBUG_CONFIG": "",
            "XDEBUG_ENABLE": "false",
            "XHPROF_ENABLE": "false",
        }
    )
    if hasattr(os, "getuid"):
        environment["MW_DOCKER_UID"] = str(os.getuid())
        environment["MW_DOCKER_GID"] = str(os.getgid())
    else:
        environment["MW_DOCKER_UID"] = "1000"
        environment["MW_DOCKER_GID"] = "1000"
    return environment


def run_checked(
    command: Sequence[str],
    *,
    environment: dict[str, str],
    input_text: str | None = None,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command),
            cwd=REPOSITORY_ROOT,
            env=environment,
            input=input_text,
            text=True,
            check=True,
            capture_output=capture_output,
        )
    except subprocess.CalledProcessError as error:
        detail = ""
        if capture_output:
            detail = (error.stderr or error.stdout or "").strip()
        suffix = f"\n{detail}" if detail else ""
        raise DemoError(
            f"Command failed: {' '.join(command)}{suffix}"
        ) from error


def ensure_prerequisites(environment: dict[str, str]) -> None:
    if shutil.which("docker") is None:
        raise DemoError("Docker is not installed. Install Docker Desktop and try again.")
    version = run_checked(
        ["docker", "compose", "version", "--short"],
        environment=environment,
        capture_output=True,
    )
    ensure_supported_compose_version(version.stdout.strip())
    result = subprocess.run(
        ["docker", "info"],
        cwd=REPOSITORY_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise DemoError("Docker is not running. Start Docker Desktop and try again.")
    for path in (BASE_COMPOSE_FILE, DEMO_COMPOSE_FILE, RAW_FIXTURE):
        if not path.is_file():
            raise DemoError(f"Required demo file is missing: {path}")


def ensure_env_file() -> None:
    if ENV_FILE.exists():
        return
    uid = str(os.getuid()) if hasattr(os, "getuid") else "1000"
    gid = str(os.getgid()) if hasattr(os, "getgid") else "1000"
    ENV_FILE.write_text(
        "\n".join(
            [
                f"MW_DOCKER_UID={uid}",
                f"MW_DOCKER_GID={gid}",
                f"MW_DOCKER_PORT={DEFAULT_PORT}",
                f"MEDIAWIKI_USER={DEMO_USER}",
                f"MEDIAWIKI_PASSWORD={DEMO_PASSWORD}",
                "XDEBUG_CONFIG=",
                "XDEBUG_ENABLE=false",
                "XHPROF_ENABLE=false",
                "",
            ]
        ),
        encoding="utf-8",
    )


def ensure_runtime_directories() -> None:
    CACHE_DIRECTORY.mkdir(parents=True, exist_ok=True)


def assert_local_settings_safe() -> None:
    if not LOCAL_SETTINGS.exists():
        return
    text = LOCAL_SETTINGS.read_text(encoding="utf-8")
    if CONFIG_BEGIN not in text or CONFIG_END not in text:
        raise DemoError(
            "LocalSettings.php already exists and was not created for this demo. "
            "Use a clean clone so the demo cannot alter an existing wiki configuration."
        )


def install_php_dependencies(environment: dict[str, str]) -> None:
    if VENDOR_AUTOLOAD.is_file():
        return
    print("[demo] Installing PHP dependencies...")
    run_checked(
        compose_command(
            "exec",
            "-T",
            "mediawiki",
            "composer",
            "install",
            "--no-interaction",
        ),
        environment=environment,
    )


def install_mediawiki(environment: dict[str, str]) -> None:
    if LOCAL_SETTINGS.is_file():
        return
    print("[demo] Installing the local SQLite wiki...")
    run_checked(
        compose_command(
            "exec",
            "-T",
            "mediawiki",
            "/bin/bash",
            "/docker/install.sh",
        ),
        environment=environment,
    )
    if not LOCAL_SETTINGS.is_file():
        raise DemoError("MediaWiki finished without creating LocalSettings.php.")


def configure_extension() -> None:
    text = LOCAL_SETTINGS.read_text(encoding="utf-8")
    has_begin = CONFIG_BEGIN in text
    has_end = CONFIG_END in text
    if has_begin != has_end:
        raise DemoError(
            "LocalSettings.php contains an incomplete WikiKGExtractor demo block."
        )
    if has_begin:
        start = text.index(CONFIG_BEGIN)
        end = text.index(CONFIG_END, start) + len(CONFIG_END)
        updated = text[:start] + CONFIG_BLOCK.rstrip() + text[end:]
    else:
        updated = text.rstrip() + "\n\n" + CONFIG_BLOCK
    LOCAL_SETTINGS.write_text(updated.rstrip() + "\n", encoding="utf-8")


def load_demo_pages() -> dict[str, str]:
    fixture = json.loads(RAW_FIXTURE.read_text(encoding="utf-8"))
    records = fixture.get("pages", [])
    if not isinstance(records, list):
        raise DemoError("The synthetic fixture does not contain a pages list.")

    accepted = [
        page
        for page in records
        if isinstance(page, dict) and page.get("kind") in {"species", "variety"}
    ]
    varieties = [page for page in accepted if page.get("kind") == "variety"]
    pages: dict[str, str] = {}
    for page in accepted:
        title = str(page.get("title", "")).strip()
        text = str(page.get("text", "")).strip()
        if not title or not text:
            raise DemoError("Every accepted synthetic page needs a title and text.")
        if page.get("kind") == "species":
            text = "{{InfoPlant1}}\n" + text
            links = "\n".join(
                f"* [[{str(variety['title']).replace('_', ' ')}]]"
                for variety in varieties
            )
            text += f"\n\n== Danh sách giống ==\n{links}"
        pages[title.replace("_", " ")] = text + "\n"
    if not pages or not varieties:
        raise DemoError("The synthetic fixture needs a species and at least one variety.")
    return pages


def read_page(title: str, environment: dict[str, str]) -> str | None:
    result = subprocess.run(
        compose_command(
            "exec",
            "-T",
            "mediawiki",
            "php",
            "maintenance/run.php",
            "getText",
            title,
        ),
        cwd=REPOSITORY_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    return result.stdout if result.returncode == 0 else None


def seed_demo_pages(environment: dict[str, str]) -> None:
    print("[demo] Loading the synthetic rice pages...")
    for title, text in load_demo_pages().items():
        current = read_page(title, environment)
        if current is not None and current.rstrip() == text.rstrip():
            continue
        run_checked(
            compose_command(
                "exec",
                "-T",
                "mediawiki",
                "php",
                "maintenance/run.php",
                "edit",
                title,
                "--user",
                DEMO_USER,
                "--summary",
                "Load WikiKGExtractor visual demo page",
                "--no-rc",
            ),
            environment=environment,
            input_text=text,
        )


def wait_for_wiki(port: int, timeout_seconds: int = 60) -> None:
    url = f"http://127.0.0.1:{port}/w/index.php/Main_Page"
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                if response.status < 500:
                    return
        except (urllib.error.URLError, TimeoutError):
            pass
        time.sleep(1)
    raise DemoError(f"MediaWiki did not become ready within {timeout_seconds} seconds.")


def start_demo(port: int) -> None:
    environment = command_environment(port)
    ensure_prerequisites(environment)
    assert_local_settings_safe()
    ensure_env_file()
    ensure_runtime_directories()

    print("[demo] Building the MediaWiki image with Python 3...")
    run_checked(compose_command("build", "mediawiki"), environment=environment)
    print("[demo] Starting MediaWiki on the local machine...")
    run_checked(
        compose_command("up", "-d", "mediawiki", "mediawiki-web"),
        environment=environment,
    )
    install_php_dependencies(environment)
    install_mediawiki(environment)
    configure_extension()
    seed_demo_pages(environment)
    wait_for_wiki(port)

    url = f"http://localhost:{port}/w/index.php/Special:WikiKGExtractor"
    print("\nWikiKGExtractor visual demo is ready.")
    print(f"URL: {url}")
    print(f"Login: {DEMO_USER} / {DEMO_PASSWORD}")
    print("After login, enter Lúa, select 'Tạo Knowledge Graph', and run it.")
    print("Gemini and Neo4j are disabled. All generated claims remain pending.")


def stop_demo(port: int) -> None:
    environment = command_environment(port)
    ensure_prerequisites(environment)
    run_checked(compose_command("down"), environment=environment)
    print("WikiKGExtractor demo containers stopped.")
    print("The ignored SQLite database and LocalSettings.php were kept for the next run.")


def show_status(port: int) -> None:
    environment = command_environment(port)
    ensure_prerequisites(environment)
    run_checked(compose_command("ps"), environment=environment)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the real WikiKGExtractor visualization in local MediaWiki.",
        epilog=(
            "Examples:\n"
            "  python3 extensions/WikiKGExtractor/tools/wikikg_demo.py start\n"
            "  python3 extensions/WikiKGExtractor/tools/wikikg_demo.py status\n"
            "  python3 extensions/WikiKGExtractor/tools/wikikg_demo.py stop"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("action", choices=("start", "status", "stop"))
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Host port for MediaWiki (default: {DEFAULT_PORT}).",
    )
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    return args


def main() -> int:
    args = parse_args()
    try:
        if args.action == "start":
            start_demo(args.port)
        elif args.action == "status":
            show_status(args.port)
        else:
            stop_demo(args.port)
    except DemoError as error:
        print(f"Demo failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
