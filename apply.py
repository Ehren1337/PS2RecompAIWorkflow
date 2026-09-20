"""Apply the reviewed source-only patch to a pinned PS2Recomp checkout.

Python standard library and Git only. No downloads, builds, game data, or backups.
"""
import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def git(target, *args):
    return subprocess.run(
        ["git", "-C", str(target), *args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, default=ROOT / "PS2Recomp")
    parser.add_argument("--check", action="store_true", help="validate without changing files")
    args = parser.parse_args()
    target = args.target.resolve()
    expected = (ROOT / "upstream-revision.txt").read_text().strip()
    head = git(target, "rev-parse", "HEAD")
    if head.returncode or head.stdout.strip() != expected:
        raise RuntimeError(f"Target must be a PS2Recomp checkout at {expected}: {target}")
    top = git(target, "rev-parse", "--show-toplevel")
    if top.returncode or Path(top.stdout.strip()).resolve() != target:
        raise RuntimeError("Target must be the repository root")
    patch = str(ROOT / "patches" / "ps2recomp-workflow.patch")
    check = git(target, "apply", "--check", patch)
    if check.returncode:
        reverse = git(target, "apply", "--reverse", "--check", patch)
        if reverse.returncode == 0:
            print("Patch is already present; no files changed.")
            return
        raise RuntimeError("Patch conflicts; no files changed.\n" + check.stderr)
    status = git(target, "status", "--porcelain", "--untracked-files=no")
    if status.returncode or status.stdout.strip():
        raise RuntimeError("Tracked files have local changes. Use a clean checkout; no files changed.")
    if args.check:
        print("Pinned revision and patch check passed; no files changed.")
        return
    result = git(target, "apply", patch)
    if result.returncode:
        raise RuntimeError(result.stderr)
    print("Applied source patch. Continue with WORKFLOW.md to analyze your own game.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
