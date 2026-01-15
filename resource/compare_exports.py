#!/usr/bin/env python3
"""
Compare YAML exports from tttool and tiptoi-tools for all GME files.

Usage:
    python compare_exports.py [--export] [--diff] [--tttool PATH] [--gme-dir DIR] [--yaml-dir DIR]

Options:
    --export         Re-export all files (default: only compare existing)
    --diff           Show detailed diffs for differing files
    --tttool PATH    Path to tttool binary (or set TTTOOL env var)
    --gme-dir DIR    Directory containing GME files (or set GME_DIR env var)
    --yaml-dir DIR   Directory for YAML output (or set YAML_DIR env var)
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


def find_tttool(cli_path: str | None) -> Path | None:
    """Find tttool binary from CLI arg, env var, or PATH."""
    # 1. Command line argument takes priority
    if cli_path:
        path = Path(cli_path)
        if path.exists():
            return path
        print(
            f"Warning: specified tttool path does not exist: {cli_path}",
            file=sys.stderr,
        )

    # 2. Environment variable
    env_path = os.environ.get("TTTOOL")
    if env_path:
        path = Path(env_path)
        if path.exists():
            return path
        print(
            f"Warning: TTTOOL env var path does not exist: {env_path}", file=sys.stderr
        )

    # 3. Check if tttool is in PATH
    which_path = shutil.which("tttool")
    if which_path:
        return Path(which_path)

    return None


def resolve_dir(cli_path: str | None, env_var: str, name: str) -> Path | None:
    """Resolve directory from CLI arg or env var."""
    # 1. Command line argument takes priority
    if cli_path:
        path = Path(cli_path)
        if path.is_dir():
            return path
        print(f"Warning: specified {name} does not exist: {cli_path}", file=sys.stderr)

    # 2. Environment variable
    env_path = os.environ.get(env_var)
    if env_path:
        path = Path(env_path)
        if path.is_dir():
            return path
        print(
            f"Warning: {env_var} env var path does not exist: {env_path}",
            file=sys.stderr,
        )

    return None


def export_with_tttool(gme_path: Path, yaml_path: Path, tttool: Path) -> bool:
    """Export GME to YAML using tttool."""
    try:
        subprocess.run(
            [str(tttool), "export", str(gme_path)],
            capture_output=True,
            check=True,
        )
        # tttool writes yaml next to the gme file
        src = gme_path.with_suffix(".yaml")
        if src.exists():
            src.rename(yaml_path)
            return True
    except subprocess.CalledProcessError:
        pass
    return False


def export_with_tiptoi_tools(gme_path: Path, yaml_path: Path) -> bool:
    """Export GME to YAML using tiptoi-tools."""
    try:
        subprocess.run(
            ["tiptoi-tools", "export", str(gme_path)],
            capture_output=True,
            check=True,
        )
        # tiptoi-tools writes yaml next to the gme file
        src = gme_path.with_suffix(".yaml")
        if src.exists():
            src.rename(yaml_path)
            return True
    except subprocess.CalledProcessError:
        pass
    return False


def compare_files(target: Path, result: Path) -> tuple[bool, str]:
    """Compare two YAML files, return (match, diff_output)."""
    if not target.exists() or not result.exists():
        return False, "File missing"

    proc = subprocess.run(
        ["diff", str(target), str(result)],
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0, proc.stdout


def main():
    parser = argparse.ArgumentParser(
        description="Compare YAML exports from tttool and tiptoi-tools"
    )
    parser.add_argument("--export", action="store_true", help="Re-export all files")
    parser.add_argument("--diff", action="store_true", help="Show detailed diffs")
    parser.add_argument(
        "--tttool", help="Path to tttool binary (or set TTTOOL env var)"
    )
    parser.add_argument(
        "--gme-dir", help="Directory containing GME files (or set GME_DIR env var)"
    )
    parser.add_argument(
        "--yaml-dir", help="Directory for YAML output (or set YAML_DIR env var)"
    )
    args = parser.parse_args()

    # Resolve directories
    gme_dir = resolve_dir(args.gme_dir, "GME_DIR", "GME directory")
    if not gme_dir:
        print(
            "Error: GME directory not found. Provide --gme-dir DIR or set GME_DIR env var.",
            file=sys.stderr,
        )
        sys.exit(1)

    yaml_dir = resolve_dir(args.yaml_dir, "YAML_DIR", "YAML directory")
    if not yaml_dir:
        # Create yaml_dir if specified but doesn't exist yet
        if args.yaml_dir:
            yaml_dir = Path(args.yaml_dir)
            yaml_dir.mkdir(parents=True, exist_ok=True)
        elif os.environ.get("YAML_DIR"):
            yaml_dir = Path(os.environ["YAML_DIR"])
            yaml_dir.mkdir(parents=True, exist_ok=True)
        else:
            print(
                "Error: YAML directory not specified. Provide --yaml-dir DIR or set YAML_DIR env var.",
                file=sys.stderr,
            )
            sys.exit(1)

    # Find tttool
    tttool = find_tttool(args.tttool)
    if args.export and not tttool:
        print(
            "Error: tttool not found. Provide --tttool PATH or set TTTOOL env var.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Find all GME files
    gme_files = sorted(
        gme_dir.glob("book_*.gme"), key=lambda p: int(p.stem.split("_")[1])
    )

    if args.export:
        print(f"Using tttool: {tttool}")
        print(f"GME directory: {gme_dir}")
        print(f"YAML directory: {yaml_dir}")
        print(f"Exporting {len(gme_files)} GME files...")
        for gme in gme_files:
            idx = gme.stem.split("_")[1]
            target = yaml_dir / f"{idx}_target.yaml"
            result = yaml_dir / f"{idx}_result.yaml"

            print(f"  {gme.name}...", end=" ", flush=True)
            t_ok = export_with_tttool(gme, target, tttool)
            r_ok = export_with_tiptoi_tools(gme, result)
            print(
                f"tttool={'ok' if t_ok else 'FAIL'} tiptoi-tools={'ok' if r_ok else 'FAIL'}"
            )

    # Compare all exported files
    print("\nComparing exports...")
    matches = 0
    diffs = []

    for gme in gme_files:
        idx = gme.stem.split("_")[1]
        target = yaml_dir / f"{idx}_target.yaml"
        result = yaml_dir / f"{idx}_result.yaml"

        match, diff_output = compare_files(target, result)
        if match:
            matches += 1
        else:
            diffs.append((idx, diff_output))

    total = len(gme_files)
    print(f"\nResults: {matches}/{total} files match ({total - matches} differ)")

    if diffs:
        print(f"\nFiles with differences: {', '.join(d[0] for d in diffs)}")

        if args.diff:
            for idx, diff_output in diffs:
                print(f"\n{'=' * 60}")
                print(f"book_{idx}.gme differences:")
                print(f"{'=' * 60}")
                # Show first 30 lines of diff
                lines = diff_output.split("\n")[:30]
                print("\n".join(lines))
                if len(diff_output.split("\n")) > 30:
                    print("... (truncated)")


if __name__ == "__main__":
    main()
