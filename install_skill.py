#!/usr/bin/env python3
"""Install the moct skill into the selected client's own discovery directory."""

import argparse
import os
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parent
CORE = ROOT / "plugins/moct/skills/moct"
CLIENTS = {"deepseek": ("DSH_HOME", ".dsh"), "kimi": ("KIMI_CODE_HOME", ".kimi-code")}
FILES = {
    "SKILL.md": ROOT / "clients/moct/SKILL.md",
    "references/moct.md": CORE / "references/moct.md",
    "references/implementation-handoff.md": CORE / "references/implementation-handoff.md",
    "scripts/lessons.py": CORE / "scripts/lessons.py",
}


def install(target, project=None):
    variable, subdir = CLIENTS[target]
    if project is None:
        configured = os.environ.get(variable)
        client_root = Path(configured).expanduser() if configured else Path.home() / subdir
    else:
        project = Path(project).expanduser().resolve(strict=True)
        if not project.is_dir():
            raise NotADirectoryError(project)
        client_root = project / subdir
    skills = client_root / "skills"
    dest = skills / "moct"
    contents = {name: source.read_bytes() for name, source in FILES.items()}
    skills.mkdir(parents=True, exist_ok=True)
    if not dest.exists() and not dest.is_symlink():
        with tempfile.TemporaryDirectory(prefix=".moct-install-", dir=skills) as stage:
            bundle = Path(stage) / "moct"
            for name, data in contents.items():
                path = bundle / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            try:
                bundle.rename(dest)
                return dest, "installed"
            except OSError:
                if not dest.is_dir():
                    raise
    if dest.is_dir() and not dest.is_symlink():
        entries = list(dest.rglob("*"))
        files = {p.relative_to(dest).as_posix() for p in entries if p.is_file()}
        if (not any(p.is_symlink() for p in entries) and files == set(contents) and
                all((dest / name).read_bytes() == data for name, data in contents.items())):
            return dest, "already installed"
    raise FileExistsError(f"В {dest} уже есть другая версия или пользовательские файлы.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", choices=CLIENTS)
    parser.add_argument("--project", type=Path, help="Install in this project's client folder")
    args = parser.parse_args()
    try:
        path, status = install(args.target, args.project)
    except OSError as exc:
        print(f"moct: {exc}", file=sys.stderr)
        return 1
    print(f"{args.target}: {status} — {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
