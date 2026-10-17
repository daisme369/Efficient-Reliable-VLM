from __future__ import annotations

import ast
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "src/cxr_research"


class PackageTests(unittest.TestCase):
    def test_source_layout_and_entry_point(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())
        self.assertEqual(project["project"]["scripts"]["cxr-research"], "cxr_research.cli:main")
        self.assertEqual(project["tool"]["setuptools"]["packages"]["find"]["where"], ["src"])
        self.assertEqual(list((ROOT / "scripts/research").glob("*.py")), [])

    def test_package_imports_and_help_do_not_load_ml_dependencies(self):
        code = (
            "import sys; sys.path.insert(0, 'src'); import cxr_research; "
            "from cxr_research.cli import main; "
            "assert 'torch' not in sys.modules; assert 'transformers' not in sys.modules; "
            "main(['--help'])"
        )
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("prepare", result.stdout)
        self.assertIn("evaluate", result.stdout)

    def test_module_boundaries_and_no_import_cycles(self):
        forbidden = {
            "utils": {"augmentation", "preprocessing", "data", "models", "training", "evaluation", "metrics", "experiments", "cli"},
            "metrics": {"data", "models", "training", "evaluation", "experiments", "cli"},
            "models": {"data", "training", "evaluation", "experiments", "cli"},
            "augmentation": {"data", "models", "training", "evaluation", "cli"},
            "preprocessing": {"data", "models", "training", "evaluation", "cli"},
            "data": {"models", "training", "evaluation", "metrics", "cli"},
            "experiments": {"data", "models", "training", "evaluation", "cli"},
        }
        graph = {}
        for path in PACKAGE.rglob("*.py"):
            source = "cxr_research." + ".".join(path.relative_to(PACKAGE).with_suffix("").parts)
            area = path.relative_to(PACKAGE).parts[0]
            graph[source] = set()
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    self.assertEqual(node.level, 0, f"use explicit package imports: {path.name}")
                    modules = [module]
                elif isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                else:
                    continue
                for module in modules:
                    self.assertFalse(module.startswith("scripts"), source)
                    if module.startswith("cxr_research."):
                        target_area = module.split(".")[1]
                        self.assertNotIn(target_area, forbidden.get(area, set()), f"{source} imports {module}")
                        graph[source].add(module)
        visited, active = set(), set()
        def visit(module):
            self.assertNotIn(module, active, f"import cycle involving {module}")
            if module in visited:
                return
            active.add(module)
            for dependency in graph.get(module, ()):
                visit(dependency)
            active.remove(module)
            visited.add(module)
        for module in graph:
            visit(module)


if __name__ == "__main__":
    unittest.main()
