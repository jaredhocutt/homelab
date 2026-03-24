#!/usr/bin/env python3
"""
Check for updated container image tags in apps.yml.

Parses the apps.yml file, finds variables ending in _image_tag that have
skopeo commands in their comments, runs those commands to get the latest
available tags, and compares them to the current values.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

# ANSI color codes
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
RESET = "\033[0m"
BOLD = "\033[1m"


def parse_image_tag_lines(file_path: Path) -> list[dict]:
    """
    Parse the YAML file and extract image tag variables with their skopeo commands.

    Returns a list of dicts with keys:
    - variable: the variable name
    - current_value: the current tag value
    - skopeo_command: the full skopeo | jq command from the comment
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

    with open(file_path, "r") as f:
        for line_number, line in enumerate(f, 1):
            match = pattern.match(line.strip())
            if match:
                results.append(
                    {
                        "variable": match.group(1),
                        "current_value": match.group(2),
                        "skopeo_command": match.group(3),
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
    for prefix in ("v", "version-v", "version-"):
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


def main():
    parser = argparse.ArgumentParser(
        description="Check for updated container image tags in apps.yml"
    )
    parser.add_argument(
        "file",
        nargs="?",
        default="inventory/host_vars/apps.yml",
        help="Path to the YAML file to check (default: inventory/host_vars/apps.yml)",
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
    file_path = Path(args.file)
    if not file_path.is_absolute():
        # Try relative to script location first, then current directory
        script_dir = Path(__file__).parent.parent
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

        if status == "update-available":
            updates_available += 1
            print(f"{YELLOW}update available{RESET}")
        elif status == "up-to-date":
            print(f"{GREEN}up-to-date{RESET}")
        else:
            print(f"{RED}unknown{RESET}")

        results.append(
            {
                **item,
                "latest_value": latest,
                "status": status,
                "all_tags": tags[-10:] if tags else [],  # Keep last 10 tags
            }
        )

    # Output results
    if args.json:
        import json

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

            if status == "up-to-date" and not args.updates_only:
                print(f"  {GREEN}✓{RESET} {variable}: {current}")
                print()
            elif status == "error" and not args.updates_only:
                print(f"  {RED}✗{RESET} {variable}: {current} (failed to check)")
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
                print(f"    Latest:  {GREEN}{latest}{RESET}")
                print()

        print(f"\n{BOLD}Summary:{RESET}")
        print(f"  Total checked: {len(results)}")
        print(f"  Up-to-date:    {GREEN}{len([r for r in results if r['status'] == 'up-to-date'])}{RESET}")
        print(f"  Updates:       {YELLOW}{updates_available}{RESET}")
        print(f"  Errors:        {RED}{len([r for r in results if r['status'] == 'error'])}{RESET}")

    sys.exit(0 if updates_available == 0 else 1)


if __name__ == "__main__":
    main()
