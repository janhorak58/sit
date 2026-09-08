import importlib.util
from pathlib import Path

import pytest
from packaging.version import InvalidVersion


MODULE_PATH = Path(__file__).parents[1] / "pyannote_version.py"
spec = importlib.util.spec_from_file_location("pyannote_version", MODULE_PATH)
version = importlib.util.module_from_spec(spec)
spec.loader.exec_module(version)


def test_python_prereleases_work_for_runtime_and_checkpoint(capsys):
    version.check_version("torch", "2.3.1", "2.8.0a0+34c6371d24")
    version.check_version("torch", "2.8.0a0+34c6371d24", "2.8.0.dev20250801+nv.25.8")
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    "checkpoint,action",
    [("3.0.0", "upgrade"), ("1.9.0", "revert"), ("2.9.0", "upgrade")],
)
def test_incompatible_versions_still_warn(checkpoint, action, capsys):
    version.check_version("torch", checkpoint, "2.8.0a0+34c6371d24")
    assert action in capsys.readouterr().out


def test_invalid_versions_are_not_silently_accepted():
    with pytest.raises(InvalidVersion):
        version.check_version("torch", "2.3.1", "not-a-version")
