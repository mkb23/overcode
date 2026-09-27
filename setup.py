"""Build hook: ship the markdown docs inside the package (#503).

Everything else is in pyproject.toml. The docs live at the repo root (the
GitHub docs and the README link there), so build_py copies docs/**/*.md
into the wheel as overcode/docs. Installs then carry the docs for their own
version, with no network needed to read them: overcode runs on restricted
corporate networks, and the overagent answers from these files. MANIFEST.in
puts docs/ in the sdist, which `python -m build` builds the wheel from.
"""

import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py

DOCS = Path(__file__).resolve().parent / "docs"


class build_py_with_docs(build_py):
    def run(self):
        super().run()
        if not DOCS.is_dir():
            return
        target = Path(self.build_lib) / "overcode" / "docs"
        for src in DOCS.rglob("*.md"):
            dst = target / src.relative_to(DOCS)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)


setup(cmdclass={"build_py": build_py_with_docs})
