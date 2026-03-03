#!/usr/bin/env python3
"""
Test GME -> YAML -> GME -> YAML roundtrip for GME files.

This script verifies that the build command can correctly reconstruct GME files
from exported YAML by comparing the re-exported YAML content.

Usage:
    python test_roundtrip.py [--gme-path PATH] [--stop-on-fail] [--keep-temp]
                             [--diff]

Options:
    --gme-path PATH   GME file or directory (or set GME_PATH env var)
    --stop-on-fail    Stop at first failure and show details
    --keep-temp       Keep temp directory on failure for debugging
    --diff            Show detailed YAML diff on failure
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RoundtripResult:
    """Result of a roundtrip test."""

    success: bool
    message: str
    temp_dir: Path | None


def run_cmd(args: list[str], capture: bool = True) -> subprocess.CompletedProcess:
    """Run a command and return the result."""
    return subprocess.run(
        args,
        capture_output=capture,
        text=True,
    )


def extract_error(stderr: str) -> str:
    """Extract the last error line from stderr (skip traceback)."""
    if not stderr:
        return "(no error message)"
    lines = stderr.strip().split("\n")
    # Find last non-empty line (usually the actual error message)
    for line in reversed(lines):
        line = line.strip()
        if line and not line.startswith("File "):
            return line
    return lines[-1] if lines else "(no error message)"


def test_roundtrip(gme_path: Path, keep_temp: bool = False) -> RoundtripResult:
    """
    Test roundtrip for a single GME file using CLI commands.

    Steps:
    1. Export original GME to YAML + media
    2. Build new GME from YAML
    3. Export rebuilt GME to YAML (no media)
    4. Compare the two YAML files

    Returns a RoundtripResult with all comparison details.
    """
    tmpdir = None
    try:
        name = gme_path.stem

        # Create temp directory
        tmpdir = Path(tempfile.mkdtemp(prefix=f"roundtrip_{name}_"))

        # Step 1: Export original GME to YAML + media
        result = run_cmd([
            "tiptoi-tools",
            str(gme_path),
            "export",
            "--name",
            name,
            "-f",
            str(tmpdir),
        ])
        if result.returncode != 0:
            return RoundtripResult(
                success=False,
                message=f"Export failed: {extract_error(result.stderr)}",
                temp_dir=tmpdir if keep_temp else None,
            )

        yaml_path = tmpdir / f"{name}.yaml"
        if not yaml_path.exists():
            return RoundtripResult(
                success=False,
                message=f"Export did not create {yaml_path.name}",
                temp_dir=tmpdir if keep_temp else None,
            )

        # Step 2: Build new GME from YAML
        rebuilt_gme = tmpdir / f"{name}_rebuilt.gme"
        result = run_cmd([
            "tiptoi-tools",
            str(yaml_path),
            "build",
            "-f",
            str(rebuilt_gme),
        ])
        if result.returncode != 0:
            return RoundtripResult(
                success=False,
                message=f"Build failed: {extract_error(result.stderr)}",
                temp_dir=tmpdir if keep_temp else None,
            )

        if not rebuilt_gme.exists():
            return RoundtripResult(
                success=False,
                message="Build did not create GME file",
                temp_dir=tmpdir if keep_temp else None,
            )

        # Step 3: Export rebuilt GME to YAML (no media)
        # Export to subdirectory with same name so media-path matches
        rebuilt_dir = tmpdir / "rebuilt"
        rebuilt_dir.mkdir()
        result = run_cmd([
            "tiptoi-tools",
            str(rebuilt_gme),
            "export",
            "--no-media",
            "--name",
            name,
            "-f",
            str(rebuilt_dir),
        ])
        rebuilt_yaml = rebuilt_dir / f"{name}.yaml"
        if result.returncode != 0:
            return RoundtripResult(
                success=False,
                message=f"Re-export failed: {extract_error(result.stderr)}",
                temp_dir=tmpdir if keep_temp else None,
            )

        if not rebuilt_yaml.exists():
            return RoundtripResult(
                success=False,
                message="Re-export did not create YAML file",
                temp_dir=tmpdir if keep_temp else None,
            )

        # Step 4: Compare YAML files
        diff_result = run_cmd(["diff", "-q", str(yaml_path), str(rebuilt_yaml)])

        if diff_result.returncode != 0:
            # Get actual diff for debugging
            diff_detail = run_cmd(["diff", str(yaml_path), str(rebuilt_yaml)])
            diff_lines = len(diff_detail.stdout.strip().split("\n"))
            result = RoundtripResult(
                success=False,
                message=f"YAML differs ({diff_lines} diff lines)",
                temp_dir=tmpdir if keep_temp else None,
            )
            if not keep_temp:
                shutil.rmtree(tmpdir)
            return result

        # Success - clean up
        shutil.rmtree(tmpdir)
        return RoundtripResult(success=True, message="OK", temp_dir=None)

    except Exception as e:
        return RoundtripResult(
            success=False,
            message=f"Error: {type(e).__name__}: {e}",
            temp_dir=tmpdir if (tmpdir and keep_temp) else None,
        )


def show_yaml_diff(temp_dir: Path, name: str) -> None:
    """Show diff between original and rebuilt YAML."""
    if not temp_dir or not temp_dir.exists():
        print("  (temp directory not available for diff)")
        return

    yaml_path = temp_dir / f"{name}.yaml"
    rebuilt_yaml = temp_dir / "rebuilt" / f"{name}.yaml"

    if not yaml_path.exists() or not rebuilt_yaml.exists():
        print("  (YAML files not available for diff)")
        return

    result = subprocess.run(
        ["diff", "-u", str(yaml_path), str(rebuilt_yaml)],
        capture_output=True,
        text=True,
    )
    if result.stdout:
        lines = result.stdout.split("\n")[:50]
        for line in lines:
            print(f"  {line}")
        if len(result.stdout.split("\n")) > 50:
            print("  ... (truncated)")
    else:
        print("  (no diff output)")


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


def main():
    parser = argparse.ArgumentParser(
        description="Test GME -> YAML -> GME -> YAML roundtrip"
    )
    parser.add_argument(
        "--gme-path", help="GME file or directory (or set GME_PATH env var)"
    )
    parser.add_argument(
        "--stop-on-fail", action="store_true", help="Stop at first failure"
    )
    parser.add_argument(
        "--keep-temp", action="store_true", help="Keep temp directory on failure"
    )
    parser.add_argument(
        "--diff", action="store_true", help="Show detailed YAML diff on failure"
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

    # Collect GME files
    if gme_path.is_file():
        gme_files = [gme_path]
    else:
        gme_files = sorted(gme_path.glob("*.gme"))

    if not gme_files:
        print(f"No GME files found in {gme_path}", file=sys.stderr)
        sys.exit(1)

    print(f"GME path: {gme_path}")
    print(f"Testing {len(gme_files)} GME file(s)...")
    print()

    passed = 0
    failed = 0
    failures = []
    keep_temp = args.keep_temp or args.stop_on_fail or args.diff

    for i, gme_file in enumerate(gme_files):
        result = test_roundtrip(gme_file, keep_temp)

        if result.success:
            passed += 1
            print(f"[{i + 1}/{len(gme_files)}] PASS {gme_file.name}")
        else:
            failed += 1
            failures.append((gme_file, result))
            print(f"[{i + 1}/{len(gme_files)}] FAIL {gme_file.name}: {result.message}")

            if args.stop_on_fail:
                print("\nStopping at first failure.")

                if args.diff and result.temp_dir:
                    print("\nYAML diff:")
                    show_yaml_diff(result.temp_dir, gme_file.stem)

                if result.temp_dir:
                    print(f"\nTemp directory kept at: {result.temp_dir}")
                sys.exit(1)

    # Print summary
    print()
    print("=" * 60)
    print(f"Results: {passed}/{len(gme_files)} passed ({failed} failed)")
    print("=" * 60)

    if failures:
        print("\nFailed files:")
        for gme_file, result in failures[:20]:
            if result.temp_dir:
                print(f"  {gme_file.name}: {result.message} -> {result.temp_dir}")
            else:
                print(f"  {gme_file.name}: {result.message}")

            if args.diff and result.temp_dir:
                show_yaml_diff(result.temp_dir, gme_file.stem)

        if len(failures) > 20:
            print(f"  ... and {len(failures) - 20} more")

    # Exit with error code if any failures
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
