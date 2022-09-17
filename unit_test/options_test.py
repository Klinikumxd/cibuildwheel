from __future__ import annotations

import os
import platform as platform_module
import textwrap
from pathlib import Path

import pytest

from cibuildwheel.__main__ import get_build_identifiers
from cibuildwheel.bashlex_eval import local_environment_executor
from cibuildwheel.environment import parse_environment
from cibuildwheel.options import Options, _get_pinned_container_images

from .utils import get_default_command_line_arguments

PYPROJECT_1 = """
[tool.cibuildwheel]
build = ["cp38*", "cp37*"]
environment = {FOO="BAR"}

test-command = "pyproject"

manylinux-x86_64-image = "manylinux1"

environment-pass = ["EXAMPLE_ENV"]

[tool.cibuildwheel.macos]
test-requires = "else"

[[tool.cibuildwheel.overrides]]
select = "cp37*"
test-command = "pyproject-override"
manylinux-x86_64-image = "manylinux2014"
"""


def test_options_1(tmp_path, monkeypatch):
    with tmp_path.joinpath("pyproject.toml").open("w") as f:
        f.write(PYPROJECT_1)

    args = get_default_command_line_arguments()
    args.package_dir = tmp_path

    monkeypatch.setattr(platform_module, "machine", lambda: "x86_64")

    options = Options(platform="linux", command_line_arguments=args)

    identifiers = get_build_identifiers(
        platform="linux",
        build_selector=options.globals.build_selector,
        architectures=options.globals.architectures,
    )

    override_display = """\
test_command: 'pyproject'
  cp37-manylinux_x86_64: 'pyproject-override'"""

    print(options.summary(identifiers))

    assert override_display in options.summary(identifiers)

    default_build_options = options.build_options(identifier=None)

    assert default_build_options.environment == parse_environment("FOO=BAR")

    all_pinned_container_images = _get_pinned_container_images()
    pinned_x86_64_container_image = all_pinned_container_images["x86_64"]

    local = options.build_options("cp38-manylinux_x86_64")
    assert local.manylinux_images is not None
    assert local.test_command == "pyproject"
    assert local.manylinux_images["x86_64"] == pinned_x86_64_container_image["manylinux1"]

    local = options.build_options("cp37-manylinux_x86_64")
    assert local.manylinux_images is not None
    assert local.test_command == "pyproject-override"
    assert local.manylinux_images["x86_64"] == pinned_x86_64_container_image["manylinux2014"]


def test_passthrough(tmp_path, monkeypatch):
    with tmp_path.joinpath("pyproject.toml").open("w") as f:
        f.write(PYPROJECT_1)

    args = get_default_command_line_arguments()
    args.package_dir = tmp_path

    monkeypatch.setattr(platform_module, "machine", lambda: "x86_64")
    monkeypatch.setenv("EXAMPLE_ENV", "ONE")

    options = Options(platform="linux", command_line_arguments=args)

    default_build_options = options.build_options(identifier=None)

    assert default_build_options.environment.as_dictionary(prev_environment={}) == {
        "FOO": "BAR",
        "EXAMPLE_ENV": "ONE",
    }


@pytest.mark.parametrize(
    "env_var_value",
    [
        "normal value",
        '"value wrapped in quotes"',
        "an unclosed single-quote: '",
        'an unclosed double-quote: "',
        "string\nwith\ncarriage\nreturns\n",
        "a trailing backslash \\",
    ],
)
def test_passthrough_evil(tmp_path, monkeypatch, env_var_value):
    args = get_default_command_line_arguments()
    args.package_dir = tmp_path

    monkeypatch.setattr(platform_module, "machine", lambda: "x86_64")
    monkeypatch.setenv("CIBW_ENVIRONMENT_PASS_LINUX", "ENV_VAR")
    options = Options(platform="linux", command_line_arguments=args)

    monkeypatch.setenv("ENV_VAR", env_var_value)
    parsed_environment = options.build_options(identifier=None).environment
    assert parsed_environment.as_dictionary(prev_environment={}) == {"ENV_VAR": env_var_value}


@pytest.mark.parametrize(
    "env_var_value",
    [
        "normal value",
        '"value wrapped in quotes"',
        'an unclosed double-quote: "',
        "string\nwith\ncarriage\nreturns\n",
        "a trailing backslash \\",
    ],
)
def test_toml_environment_evil(tmp_path, monkeypatch, env_var_value):
    args = get_default_command_line_arguments()
    args.package_dir = tmp_path

    tmp_path.joinpath("pyproject.toml").write_text(
        textwrap.dedent(
            f"""\
            [tool.cibuildwheel.environment]
            EXAMPLE='''{env_var_value}'''
            """
        )
    )

    options = Options(platform="linux", command_line_arguments=args)
    parsed_environment = options.build_options(identifier=None).environment
    assert parsed_environment.as_dictionary(prev_environment={}) == {"EXAMPLE": env_var_value}


@pytest.mark.parametrize(
    "toml_assignment,result_value",
    [
        ('TEST_VAR="simple_value"', "simple_value"),
        # spaces
        ('TEST_VAR="simple value"', "simple value"),
        # env var
        ('TEST_VAR="$PARAM"', "spam"),
        ('TEST_VAR="$PARAM $PARAM"', "spam spam"),
        # env var extension
        ('TEST_VAR="before:$PARAM:after"', "before:spam:after"),
        # env var extension with spaces
        ('TEST_VAR="before $PARAM after"', "before spam after"),
    ],
)
def test_toml_environment_quoting(tmp_path: Path, toml_assignment, result_value):
    args = get_default_command_line_arguments()
    args.package_dir = tmp_path

    tmp_path.joinpath("pyproject.toml").write_text(
        textwrap.dedent(
            f"""\
            [tool.cibuildwheel.environment]
            {toml_assignment}
            """
        )
    )

    options = Options(platform="linux", command_line_arguments=args)
    parsed_environment = options.build_options(identifier=None).environment
    environment_values = parsed_environment.as_dictionary(
        prev_environment={**os.environ, "PARAM": "spam"},
        executor=local_environment_executor,
    )

    assert environment_values["TEST_VAR"] == result_value
