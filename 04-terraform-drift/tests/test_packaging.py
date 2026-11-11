"""Release hygiene: one version, recorded in the changelog, with the console command wired up."""

import re

from plandrift import __version__

from .conftest import ROOT


def test_version_is_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__)


def test_changelog_top_entry_is_this_version():
    headings = re.findall(r"^## (\d+\.\d+\.\d+) \(\d{4}-\d{2}-\d{2}\)$", (ROOT / "CHANGELOG.md").read_text(), re.M)
    assert headings and headings[0] == __version__


def test_console_script_points_at_main():
    pyproject = (ROOT / "pyproject.toml").read_text()
    assert 'plandrift = "plandrift.cli:main"' in pyproject
    assert 'requires-python = ">=3.10"' in pyproject
    assert "dependencies = []" in pyproject


def test_action_installs_from_its_own_folder():
    action = (ROOT / "action.yml").read_text()
    assert '"$GITHUB_ACTION_PATH"' in action and "using: composite" in action
