import sys
import tomllib
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

ROOT = Path(__file__).resolve().parents[1]
CI_YML = ROOT / ".github" / "workflows" / "ci.yml"
PYPROJECT = ROOT / "pyproject.toml"


class InfraStructureTests(unittest.TestCase):
    def test_ci_workflow_is_valid_yaml(self):
        data = yaml.safe_load(CI_YML.read_text(encoding="utf-8"))
        self.assertIn("jobs", data)
        self.assertIn("test", data["jobs"])
        self.assertIn("quality-gate", data["jobs"])
        versions = data["jobs"]["test"]["strategy"]["matrix"]["python-version"]
        self.assertIn("3.11", versions)
        self.assertIn("3.12", versions)

    def test_anthropic_is_optional_dependency_only(self):
        data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
        default_deps = " ".join(data["project"]["dependencies"]).lower()
        self.assertNotIn("anthropic", default_deps)
        llm_extra = " ".join(data["project"]["optional-dependencies"]["llm"]).lower()
        self.assertIn("anthropic", llm_extra)


if __name__ == "__main__":
    unittest.main()
