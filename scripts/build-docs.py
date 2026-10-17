#!/usr/bin/env python3
"""Build the Material for MkDocs site from only the permitted public pages."""
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("docs_validator", ROOT / "scripts/validate-docs.py")
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)

# MkDocs needs the site entry point at index.md; the repository source is README.md.
HOMEPAGE = {"README.md": "index.md"}


def prepare(root=ROOT):
    errors = validator.validate(root)
    if errors:
        raise ValueError("\n".join(errors))
    stage = root / ".build" / "site-src"
    if (root / ".build").is_symlink() or stage.is_symlink():
        raise ValueError("Unsafe symlink at build directory")
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    for name in validator.public_pages(root):
        dest = stage / HOMEPAGE.get(name, name)
        if dest.is_symlink() or dest.exists() and dest.is_dir():
            raise ValueError(f"Unsafe stage destination: {dest}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / name, dest)
    return stage


def build(root=ROOT):
    prepare(root)
    output = root / "public"
    if output.is_symlink():
        raise ValueError("Unsafe output symlink")
    if output.exists():
        shutil.rmtree(output)
    subprocess.run([sys.executable, "-m", "mkdocs", "build", "--strict",
                    "--config-file", str(root / "mkdocs.yml")], check=True)
    for private in ("AGENTS.md", "update-config.json", "scripts", "tests", "docs-internal"):
        if (output / private).exists():
            raise ValueError(f"Unexpected private build artifact: {private}")


if __name__ == "__main__":
    build()
