#!/usr/bin/env python3
"""
Check for updated container image tags in a vars file.

Parses a YAML vars file, finds variables ending in _image_tag that have
skopeo commands in their comments, runs those commands to get the latest
available tags, and compares them to the current values.

If no file is specified, presents an interactive list of inventory files.

A tag can be held back by appending a hold marker after the skopeo command:

    foo_image_tag: v1.2.3  # skopeo list-tags ... | jq ...  # hold: <reason>
    foo_image_tag: v1.2.3  # skopeo list-tags ... | jq ...  # hold: <url>

A plain-text reason reports the tag as held and never suggests an update. A
URL points at an upstream compose file; the tag follows whatever that file
pins for the same image, so an update is suggested only when upstream moves.
In a raw.githubusercontent.com URL, {latest_release} is replaced with the
repository's latest GitHub release tag, and {some_variable} is replaced with
that variable's current value from the same file, so a sidecar can follow the
compose file of the release actually deployed:

    # hold: https://raw.githubusercontent.com/o/r/v{app_image_tag}/compose.yml

Versions that aren't image tags (a binary or plugin release) use a
github-release marker instead of a skopeo command, and are compared with the
repository's latest GitHub release:

    foo_version: v1.2.3  # github-release owner/repo

--drift compares every *_image_tag and *_version pinned in more than one
inventory file and reports the ones that differ, without any network calls.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

# ANSI color codes
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
RESET = "\033[0m"
BOLD = "\033[1m"

# Manifest media types that represent a multi-platform index rather than a
# single image. A child entry with one of these types means the index is
# nested, which podman cannot pull.
INDEX_MEDIA_TYPES = {
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
}

# Trailing marker that holds a tag back; see the module docstring.
HOLD_PATTERN = re.compile(r"\s+#\s*hold:\s*(.+?)\s*$")

# A plain "name: value" line, used to resolve {variable} placeholders
SCALAR_PATTERN = re.compile(r"""^(\w+):\s*["']?([^"'#\s]+)["']?\s*(?:#.*)?$""")

# Variables whose values are pinned versions, for --drift
PINNED_PATTERN = re.compile(r"^\w+_(image_tag|version)$")


def parse_scalar_vars(file_path: Path) -> dict[str, str]:
    """Return every top-level "name: value" scalar in the file."""
    values = {}
    with open(file_path, "r") as f:
        for line in f:
            match = SCALAR_PATTERN.match(line.rstrip("\n"))
            if match:
                values[match.group(1)] = match.group(2)
    return values


def parse_image_tag_lines(file_path: Path) -> list[dict]:
    """
    Parse the YAML file and extract image tag variables with their skopeo commands.

    Returns a list of dicts with keys:
    - variable: the variable name
    - current_value: the current tag value
    - skopeo_command: the full skopeo | jq command from the comment
    - image_ref: the docker://... image reference from the skopeo command
    - hold: the hold reason or upstream URL, or None if the tag isn't held
    - line_number: 1-based line number in the file
    """
    results = []

    # Pattern to match lines like:
    # variable_image_tag: "value"  # skopeo list-tags ... | jq ...
    # variable_image_tag: value  # skopeo list-tags ... | jq ...
    pattern = re.compile(
        r"^(\w+_image_tag):\s*"  # Variable name ending in _image_tag
        r'["\']?([^"\'#\s]+)["\']?\s*'  # Value (quoted or unquoted)
        r"#\s*(skopeo\s+list-tags\s+.+)$"  # Comment with skopeo command
    )

    # variable_version: v1.2.3  # github-release owner/repo
    release_pattern = re.compile(
        r"^(\w+_version):\s*"
        r'["\']?([^"\'#\s]+)["\']?\s*'
        r"#\s*github-release\s+([\w.-]+/[\w.-]+)(.*)$"
    )

    with open(file_path, "r") as f:
        for line_number, line in enumerate(f, 1):
            release_match = release_pattern.match(line.strip())
            if release_match:
                hold_match = HOLD_PATTERN.search(release_match.group(4))
                results.append(
                    {
                        "variable": release_match.group(1),
                        "current_value": release_match.group(2),
                        "skopeo_command": None,
                        "github_repo": release_match.group(3),
                        "image_ref": None,
                        "hold": hold_match.group(1) if hold_match else None,
                        "line_number": line_number,
                    }
                )
                continue

            match = pattern.match(line.strip())
            if match:
                command = match.group(3)
                hold = None
                hold_match = HOLD_PATTERN.search(command)
                if hold_match:
                    hold = hold_match.group(1)
                    command = command[: hold_match.start()]
                ref_match = re.search(r"docker://[^\s|'\"]+", command)
                results.append(
                    {
                        "variable": match.group(1),
                        "current_value": match.group(2),
                        "skopeo_command": command,
                        "github_repo": None,
                        "image_ref": ref_match.group(0) if ref_match else None,
                        "hold": hold,
                        "line_number": line_number,
                    }
                )

    return results


def run_skopeo_command(command: str, timeout: int = 30) -> list[str] | None:
    """
    Run a skopeo command and return the list of tags.

    Returns None if the command fails.
    """
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            return None

        # Split output into lines and filter empty ones
        tags = [tag.strip() for tag in result.stdout.strip().split("\n") if tag.strip()]
        return tags

    except subprocess.TimeoutExpired:
        return None
    except Exception:
        return None


def normalize_image_name(ref: str) -> str:
    """
    Reduce an image reference to a comparable name without its tag.

    "docker://docker.io/library/elasticsearch" and "elasticsearch:7.17.27"
    both become "elasticsearch".
    """
    name = ref.removeprefix("docker://").split("@", 1)[0]
    last_slash = name.rfind("/")
    colon = name.rfind(":")
    if colon > last_slash:
        name = name[:colon]
    name = name.removeprefix("docker.io/").removeprefix("index.docker.io/")
    return name.removeprefix("library/")


def get_latest_release(repo: str, timeout: int = 30) -> str:
    """Return the tag of a GitHub repository's latest (non-prerelease) release."""
    request = urllib.request.Request(f"https://api.github.com/repos/{repo}/releases/latest")
    # Unauthenticated calls are limited to 60 an hour; use a token when there is one
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)["tag_name"]


def hold_url_variables(url: str) -> list[str]:
    """Names of the {variable} placeholders in a hold URL."""
    return [name for name in re.findall(r"\{(\w+)\}", url) if name != "latest_release"]


def resolve_hold_url(url: str, variables: dict[str, str] | None = None, timeout: int = 30) -> str:
    """Substitute {latest_release} and {variable} placeholders in a hold URL."""
    for name in hold_url_variables(url):
        if not variables or name not in variables:
            raise ValueError(f"{{{name}}} is not set in this file")
        url = url.replace(f"{{{name}}}", variables[name])

    if "{latest_release}" not in url:
        return url

    repo_match = re.match(r"https://raw\.githubusercontent\.com/([^/]+)/([^/]+)/", url)
    if not repo_match:
        raise ValueError("{latest_release} only works in raw.githubusercontent.com URLs")

    owner, repo = repo_match.groups()
    return url.replace("{latest_release}", get_latest_release(f"{owner}/{repo}", timeout=timeout))


def get_upstream_tag(
    url: str, image_ref: str, variables: dict[str, str] | None = None, timeout: int = 30
) -> str:
    """
    Return the tag an upstream compose file pins for image_ref.

    Raises ValueError if the file can't be read or doesn't pin the image.
    """
    try:
        resolved_url = resolve_hold_url(url, variables, timeout=timeout)
        with urllib.request.urlopen(resolved_url, timeout=timeout) as response:
            content = response.read().decode()
    except (urllib.error.URLError, OSError, KeyError, json.JSONDecodeError) as e:
        raise ValueError(f"could not read upstream file: {e}") from e

    wanted = normalize_image_name(image_ref)
    for line in content.splitlines():
        image_match = re.match(r"\s*image:\s*[\"']?([^\s\"'#]+)", line)
        if not image_match:
            continue
        image = image_match.group(1)
        if normalize_image_name(image) != wanted:
            continue

        tag = image.split("@", 1)[0].rpartition(":")[2]
        # Compose files often use ${VERSION:-default}; take the default.
        default_match = re.fullmatch(r"\$\{[^:}]+:?-([^}]+)\}", tag)
        if default_match:
            tag = default_match.group(1)
        if tag and "/" not in tag and "$" not in tag:
            return tag

    raise ValueError(f"upstream file does not pin {wanted}")


def check_pullable(
    image_ref: str,
    tag: str,
    platform: str = "linux/amd64",
    timeout: int = 30,
) -> str | None:
    """
    Check that image_ref:tag resolves to an image manifest podman can pull.

    Returns None if the image looks fine, otherwise a short string describing
    the problem.

    This inspects the raw manifest rather than relying on `skopeo inspect`,
    because skopeo and podman disagree: skopeo transparently recurses into a
    nested image index, while podman's pull path expects an image manifest at
    the second level and fails with "Unexpectedly received a manifest list
    instead of a manifest for a single image". authentik 2026.8.x is published
    this way, so `skopeo inspect` reports it as perfectly healthy while
    `podman pull` cannot fetch it at all.

    Only the manifest is fetched, never any blobs.
    """
    target_os, _, target_arch = platform.partition("/")

    try:
        result = subprocess.run(
            ["skopeo", "inspect", "--raw", f"{image_ref}:{tag}"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        return "could not read manifest"

    if result.returncode != 0:
        return "could not read manifest"

    try:
        manifest = json.loads(result.stdout)
    except json.JSONDecodeError:
        return "could not parse manifest"

    entries = manifest.get("manifests")
    if not entries:
        # A plain single-platform image manifest; nothing to resolve.
        return None

    match = None
    for entry in entries:
        entry_platform = entry.get("platform") or {}
        if (
            entry_platform.get("os") == target_os
            and entry_platform.get("architecture") == target_arch
        ):
            match = entry
            break

    if match is None:
        # "unknown/unknown" entries are buildx attestations, not real platforms,
        # so leave them out of the list we show the user.
        available = sorted(
            f"{(e.get('platform') or {}).get('os')}/"
            f"{(e.get('platform') or {}).get('architecture')}"
            for e in entries
            if (e.get("platform") or {}).get("architecture") != "unknown"
        )
        return f"no {platform} image (found: {', '.join(available) or 'none'})"

    if match.get("mediaType") in INDEX_MEDIA_TYPES:
        return f"nested image index for {platform}; podman cannot pull it"

    return None


def parse_version(tag: str) -> tuple:
    """
    Parse a version string into a tuple for proper sorting.

    Handles formats like:
    - "16.11" -> (16, 11)
    - "v3.6.5" -> (3, 6, 5)
    - "2025.10.3" -> (2025, 10, 3)
    - "version-v3.13" -> (3, 13)
    - "8.18.0" -> (8, 18, 0)
    - "RELEASE.2023-12-23T07-19-11Z" -> kept as string (special case)
    """
    # Remove common prefixes
    cleaned = tag
    for prefix in ("version-v", "version-", "v"):
        if cleaned.lower().startswith(prefix):
            cleaned = cleaned[len(prefix):]
            break

    # Try to extract version numbers
    # Match sequences of digits separated by dots, dashes, or underscores
    version_match = re.match(r"^(\d+(?:[.\-_]\d+)*)", cleaned)

    if version_match:
        version_str = version_match.group(1)
        # Split on common separators and convert to integers
        parts = re.split(r"[.\-_]", version_str)
        try:
            return tuple(int(p) for p in parts)
        except ValueError:
            pass

    # Fallback: return a tuple that sorts after numbers but preserves string order
    return (float("inf"), tag)


def get_latest_tag(tags: list[str], current_value: str) -> str | None:
    """
    Determine the latest tag from the list using semantic version sorting.

    Properly sorts version numbers so that 16.11 > 16.9.
    """
    if not tags:
        return None

    # Sort tags by their parsed version, highest first
    sorted_tags = sorted(tags, key=parse_version, reverse=True)
    return sorted_tags[0]


def compare_versions(current: str, latest: str) -> str:
    """Return a status indicator for the version comparison."""
    if current == latest:
        return "up-to-date"
    else:
        return "update-available"


def check_drift(files: list[Path], root: Path) -> int:
    """Report pinned versions that differ between inventory files."""
    pins: dict[str, dict[str, str]] = {}
    for file_path in files:
        for name, value in parse_scalar_vars(file_path).items():
            if PINNED_PATTERN.match(name):
                pins.setdefault(name, {})[str(file_path.relative_to(root))] = value

    drifted = {
        name: by_file
        for name, by_file in sorted(pins.items())
        if len(by_file) > 1 and len(set(by_file.values())) > 1
    }
    shared = sum(1 for by_file in pins.values() if len(by_file) > 1)

    print(f"{BOLD}Pinned in more than one file: {shared}; differing: {len(drifted)}{RESET}\n")
    for name, by_file in drifted.items():
        print(f"  {YELLOW}≠{RESET} {BOLD}{name}{RESET}")
        for file_name, value in sorted(by_file.items()):
            print(f"    {file_name}: {value}")
        print()
    return len(drifted)


def main():
    parser = argparse.ArgumentParser(
        description="Check for updated container image tags in a vars file"
    )
    parser.add_argument(
        "file",
        nargs="?",
        help="Path to the YAML file to check (if omitted, presents a list of inventory files)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Timeout in seconds for each skopeo command (default: 30)",
    )
    parser.add_argument(
        "--updates-only",
        action="store_true",
        help="Only show variables that have updates available",
    )
    parser.add_argument(
        "--platform",
        default="linux/amd64",
        help="Platform the suggested tags must be pullable for (default: linux/amd64)",
    )
    parser.add_argument(
        "--no-pull-check",
        action="store_true",
        help="Skip verifying that suggested tags are actually pullable",
    )
    parser.add_argument(
        "--drift",
        action="store_true",
        help="Compare versions pinned in more than one inventory file (no network calls)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable colored output",
    )

    args = parser.parse_args()

    # Disable colors if requested
    if args.no_color:
        global GREEN, YELLOW, RED, CYAN, RESET, BOLD
        GREEN = YELLOW = RED = CYAN = RESET = BOLD = ""

    # Resolve file path
    script_dir = Path(__file__).parent.parent

    if args.drift:
        inventory_files = sorted(
            p for p in (script_dir / "inventory").rglob("*.yml") if p.name != "hosts.yml"
        )
        sys.exit(0 if check_drift(inventory_files, script_dir) == 0 else 1)

    if args.file is None:
        # Discover inventory vars files and let the user pick
        inventory_dir = script_dir / "inventory"
        candidates = sorted(
            p
            for p in inventory_dir.rglob("*.yml")
            if p.name != "hosts.yml"
        )
        if not candidates:
            print(f"{RED}Error: No YAML files found in {inventory_dir}{RESET}", file=sys.stderr)
            sys.exit(1)

        print(f"{BOLD}Select a vars file to check:{RESET}\n")
        for i, candidate in enumerate(candidates, 1):
            print(f"  {CYAN}{i}{RESET}) {candidate.relative_to(script_dir)}")
        print()

        try:
            choice = input(f"{BOLD}Enter number [1-{len(candidates)}]: {RESET}")
            idx = int(choice) - 1
            if idx < 0 or idx >= len(candidates):
                raise ValueError
            file_path = candidates[idx]
        except (ValueError, EOFError, KeyboardInterrupt):
            print(f"\n{RED}Invalid selection.{RESET}", file=sys.stderr)
            sys.exit(1)
    else:
        file_path = Path(args.file)
        if not file_path.is_absolute():
            # Try relative to script location first, then current directory
            if (script_dir / file_path).exists():
                file_path = script_dir / file_path
            elif not file_path.exists():
                print(f"{RED}Error: File not found: {file_path}{RESET}", file=sys.stderr)
                sys.exit(1)

        if not file_path.exists():
            print(f"{RED}Error: File not found: {file_path}{RESET}", file=sys.stderr)
            sys.exit(1)

    # Parse the file
    image_tags = parse_image_tag_lines(file_path)
    file_vars = parse_scalar_vars(file_path)

    if not image_tags:
        print(f"{YELLOW}No image tag variables with skopeo commands found.{RESET}")
        sys.exit(0)

    print(f"{BOLD}Checking {len(image_tags)} image tags...{RESET}\n")

    results = []
    updates_available = 0

    for item in image_tags:
        variable = item["variable"]
        current = item["current_value"]
        command = item["skopeo_command"]

        # Show progress
        print(f"  Checking {CYAN}{variable}{RESET}...", end=" ", flush=True)

        if item["github_repo"]:
            try:
                tags = [get_latest_release(item["github_repo"], timeout=args.timeout)]
            except (urllib.error.URLError, OSError, KeyError, json.JSONDecodeError):
                tags = None
        else:
            tags = run_skopeo_command(command, timeout=args.timeout)

        if tags is None:
            print(f"{RED}failed{RESET}")
            results.append(
                {
                    **item,
                    "latest_value": None,
                    "status": "error",
                    "all_tags": [],
                }
            )
            continue

        latest = get_latest_tag(tags, current)
        status = compare_versions(current, latest) if latest else "unknown"

        # A held tag never takes the registry's newest tag. One held to an
        # upstream URL follows that file's pin instead; any other hold stays put.
        hold = item["hold"]
        registry_latest = latest
        upstream_value = None
        if hold:
            if re.match(r"https?://", hold):
                try:
                    upstream_value = get_upstream_tag(
                        hold, item["image_ref"] or "", file_vars, timeout=args.timeout
                    )
                except ValueError as e:
                    print(f"{RED}failed ({e}){RESET}")
                    results.append(
                        {
                            **item,
                            "latest_value": None,
                            "registry_latest": registry_latest,
                            "status": "error",
                            "error": str(e),
                            "all_tags": tags[-10:],
                        }
                    )
                    continue
                latest = upstream_value
                status = "held" if upstream_value == current else "update-available"
            else:
                status = "held"

        # Only the tag we are about to suggest gets verified, so this costs one
        # extra manifest fetch per available update rather than per variable.
        pull_warning = None
        if status == "update-available":
            updates_available += 1
            if not args.no_pull_check and item["image_ref"]:
                pull_warning = check_pullable(
                    item["image_ref"],
                    latest,
                    platform=args.platform,
                    timeout=args.timeout,
                )
            if pull_warning:
                print(f"{YELLOW}update available{RESET} {RED}({pull_warning}){RESET}")
            else:
                print(f"{YELLOW}update available{RESET}")
        elif status == "up-to-date":
            print(f"{GREEN}up-to-date{RESET}")
        elif status == "held":
            print(f"{CYAN}held{RESET}")
        else:
            print(f"{RED}unknown{RESET}")

        results.append(
            {
                **item,
                "latest_value": latest,
                "registry_latest": registry_latest,
                "upstream_value": upstream_value,
                "status": status,
                "pull_warning": pull_warning,
                "all_tags": tags[-10:] if tags else [],  # Keep last 10 tags
            }
        )

    # A hold URL that follows another variable was resolved against that
    # variable's current value; if it's being bumped too, check again afterwards.
    updating = {r["variable"] for r in results if r["status"] == "update-available"}
    for r in results:
        if r.get("hold") and re.match(r"https?://", r["hold"]):
            followed = [name for name in hold_url_variables(r["hold"]) if name in updating]
            if followed:
                r["recheck_after"] = followed

    # Output results
    if args.json:
        # Filter if updates-only
        if args.updates_only:
            results = [r for r in results if r["status"] == "update-available"]
        print(json.dumps(results, indent=2))
    else:
        print(f"\n{BOLD}{'=' * 60}{RESET}")
        print(f"{BOLD}Results:{RESET}\n")

        for r in results:
            if args.updates_only and r["status"] != "update-available":
                continue

            variable = r["variable"]
            current = r["current_value"]

            if r["status"] == "up-to-date" and not args.updates_only:
                print(f"  {GREEN}✓{RESET} {variable}: {current}")
                print()
            elif r["status"] == "held" and not args.updates_only:
                if r.get("upstream_value"):
                    reason = "matches the upstream pin"
                    if r.get("recheck_after"):
                        reason += f"; re-check after updating {', '.join(r['recheck_after'])}"
                else:
                    reason = f"held: {r['hold']}"
                print(f"  {CYAN}⏸{RESET} {variable}: {current} ({reason}; newest in registry: {r['registry_latest']})")
                print()
            elif r["status"] == "error" and not args.updates_only:
                detail = r.get("error") or "failed to check"
                print(f"  {RED}✗{RESET} {variable}: {current} ({detail})")
                print()

        for r in results:
            if args.updates_only and r["status"] != "update-available":
                continue

            variable = r["variable"]
            current = r["current_value"]
            latest = r["latest_value"]
            status = r["status"]

            if status == "update-available":
                print(f"  {YELLOW}▶{RESET} {BOLD}{variable}{RESET}")
                print(f"    Current: {current}")
                if r.get("pull_warning"):
                    print(f"    Latest:  {RED}{latest}{RESET}")
                    print(f"    {RED}⚠ Not deployable: {r['pull_warning']}{RESET}")
                    print(f"    {RED}  Leave this one at {current}.{RESET}")
                else:
                    print(f"    Latest:  {GREEN}{latest}{RESET}")
                if r.get("upstream_value"):
                    print(f"    {CYAN}Held to upstream, which now pins {latest}: {r['hold']}{RESET}")
                if r.get("recheck_after"):
                    print(f"    {CYAN}Follows {', '.join(r['recheck_after'])}; re-check after updating it{RESET}")
                print()

        print(f"\n{BOLD}Summary:{RESET}")
        print(f"  Total checked: {len(results)}")
        print(f"  Up-to-date:    {GREEN}{len([r for r in results if r['status'] == 'up-to-date'])}{RESET}")
        print(f"  Updates:       {YELLOW}{updates_available}{RESET}")
        print(f"  Held:          {CYAN}{len([r for r in results if r['status'] == 'held'])}{RESET}")
        print(f"  Errors:        {RED}{len([r for r in results if r['status'] == 'error'])}{RESET}")

        not_deployable = [r for r in results if r.get("pull_warning")]
        if not_deployable:
            print(f"  Not deployable:{RED}{len(not_deployable)}{RESET}")
            print()
            for r in not_deployable:
                print(f"  {RED}⚠{RESET} {r['variable']}: {r['latest_value']} — {r['pull_warning']}")

    sys.exit(0 if updates_available == 0 else 1)


if __name__ == "__main__":
    main()
