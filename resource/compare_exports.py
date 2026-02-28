#!/usr/bin/env python3
"""
Compare YAML exports from tttool and tiptoi-tools for GME files.

Usage:
    python compare_exports.py [--export] [--diff] [--tttool PATH] [--gme-path PATH]
                              [--yaml-dir DIR]

Options:
    --export          Re-export all files (default: only compare existing)
    --diff            Show detailed diffs for differing files
    --tttool PATH     Path to tttool binary (or set TTTOOL env var)
    --gme-path PATH   GME file or directory (or set GME_PATH env var)
    --yaml-dir DIR    Directory for YAML output (or set YAML_DIR env var)
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


def resolve_path(cli_path: str | None, env_var: str, name: str) -> Path | None:
    """Resolve file or directory from CLI arg or env var."""
    # 1. Command line argument takes priority
    if cli_path:
        path = Path(cli_path)
        if path.exists():
            return path
        print(f"Warning: specified {name} does not exist: {cli_path}", file=sys.stderr)

    # 2. Environment variable
    env_path = os.environ.get(env_var)
    if env_path:
        path = Path(env_path)
        if path.exists():
            return path
        print(
            f"Warning: {env_var} env var path does not exist: {env_path}",
            file=sys.stderr,
        )

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


def export_with_tiptoi_tools(gme_path: Path, yaml_path: Path, name: str) -> bool:
    """Export GME to YAML using tiptoi-tools."""
    try:
        subprocess.run(
            [
                "tiptoi-tools",
                str(gme_path),
                "export",
                "--no-media",
                "--name",
                name,
                yaml_path,
            ],
            capture_output=True,
            check=True,
        )
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
        "--gme-path", help="GME file or directory (or set GME_PATH env var)"
    )
    parser.add_argument(
        "--yaml-dir", help="Directory for YAML output (or set YAML_DIR env var)"
    )
    args = parser.parse_args()

    # Resolve GME path (file or directory)
    gme_path = resolve_path(args.gme_path, "GME_PATH", "GME path")
    if not gme_path:
        print(
            "Error: GME path not found. "
            "Provide --gme-path PATH or set GME_PATH env var.",
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
                "Error: YAML directory not specified. "
                "Provide --yaml-dir DIR or set YAML_DIR env var.",
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

    # Collect GME files
    if gme_path.is_file():
        gme_files = [gme_path]
    else:
        gme_files = sorted(gme_path.glob("*.gme"))

    if args.export:
        print(f"Using tttool: {tttool}")
        print(f"GME path: {gme_path}")
        print(f"YAML directory: {yaml_dir}")
        print(f"Exporting {len(gme_files)} GME file(s)...")
        for gme in gme_files:
            name = gme.stem
            target = yaml_dir / f"{name}_target.yaml"
            result_name = f"{name}_result"

            print(f"  {gme.name}...", end=" ", flush=True)
            t_ok = export_with_tttool(gme, target, tttool)
            r_ok = export_with_tiptoi_tools(gme, yaml_dir, result_name)
            t_status = "ok" if t_ok else "FAIL"
            r_status = "ok" if r_ok else "FAIL"
            print(f"tttool={t_status} tiptoi-tools={r_status}")

    # Compare all exported files
    print("\nComparing exports...")
    matches = 0
    diffs = []

    for gme in gme_files:
        name = gme.stem
        target = yaml_dir / f"{name}_target.yaml"
        result = yaml_dir / f"{name}_result.yaml"

        match, diff_output = compare_files(target, result)
        if match:
            matches += 1
        else:
            diffs.append((name, diff_output))

    total = len(gme_files)
    print(f"\nResults: {matches}/{total} files match ({total - matches} differ)")

    if diffs:
        print(f"\nFiles with differences: {', '.join(d[0] for d in diffs)}")

        if args.diff:
            for name, diff_output in diffs:
                print(f"\n{'=' * 60}")
                print(f"{name}.gme differences:")
                print(f"{'=' * 60}")
                # Show first 30 lines of diff
                lines = diff_output.split("\n")[:30]
                print("\n".join(lines))
                if len(diff_output.split("\n")) > 30:
                    print("... (truncated)")


if __name__ == "__main__":
    main()
