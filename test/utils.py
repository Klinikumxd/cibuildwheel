'''
Utility functions used by the cibuildwheel tests.

This file is added to the PYTHONPATH in the test runner at bin/run_test.py.
'''

import os
import platform as pm
import shutil
import subprocess
import sys
from contextlib import contextmanager
from tempfile import mkdtemp

IS_WINDOWS_RUNNING_ON_TRAVIS = os.environ.get('TRAVIS_OS_NAME') == 'windows'


# Python 2 does not have a tempfile.TemporaryDirectory context manager
@contextmanager
def TemporaryDirectoryIfNone(path):
    _path = path or mkdtemp()
    try:
        yield _path
    finally:
        if path is None:
            shutil.rmtree(_path)


def cibuildwheel_get_build_identifiers(project_path, env=None):
    '''
    Returns the list of build identifiers that cibuildwheel will try to build
    for the current platform.
    '''
    cmd_output = subprocess.check_output(
        [sys.executable, '-m', 'cibuildwheel', '--print-build-identifiers', str(project_path)],
        universal_newlines=True,
        env=env,
    )

    lines = cmd_output.strip().split('\n')

    # ignore warning lines in output
    return [l for l in lines if l != '' and not l.startswith('Warning ')]


def cibuildwheel_run(project_path, package_dir='.', env=None, add_env=None, output_dir=None):
    '''
    Runs cibuildwheel as a subprocess, building the project at project_path.

    Uses the current Python interpreter.

    :param project_path: path of the project to be built.
    :param package_dir: path of the package to be built. Can be absolute, or
    relative to project_path.
    :param env: full environment to be used, os.environ if None
    :param add_env: environment used to update env
    :param output_dir: directory where wheels are saved. If None, a temporary
    directory will be used for the duration of the command.
    :return: list of built wheels (file names).
    '''
    if env is None:
        env = os.environ.copy()
        # If present in the host environment, remove the MACOSX_DEPLOYMENT_TARGET for consistency
        env.pop('MACOSX_DEPLOYMENT_TARGET', None)

    if add_env is not None:
        env.update(add_env)

    with TemporaryDirectoryIfNone(output_dir) as _output_dir:
        subprocess.check_call(
            [sys.executable, '-m', 'cibuildwheel', '--output-dir', str(_output_dir), str(package_dir)],
            env=env,
            cwd=project_path,
        )
        wheels = os.listdir(_output_dir)
    return wheels


def expected_wheels(package_name, package_version, manylinux_versions=None,
                    macosx_deployment_target='10.9', machine_arch=None):
    '''
    Returns a list of expected wheels from a run of cibuildwheel.
    '''
    # per PEP 425 (https://www.python.org/dev/peps/pep-0425/), wheel files shall have name of the form
    # {distribution}-{version}(-{build tag})?-{python tag}-{abi tag}-{platform tag}.whl
    # {python tag} and {abi tag} are closely related to the python interpreter used to build the wheel
    # so we'll merge them below as python_abi_tag

    if machine_arch is None:
        machine_arch = pm.machine()

    if manylinux_versions is None:
        if machine_arch == 'x86_64':
            manylinux_versions = ['manylinux1', 'manylinux2010']
        else:
            manylinux_versions = ['manylinux2014']

    python_abi_tags = ['cp35-cp35m', 'cp36-cp36m', 'cp37-cp37m', 'cp38-cp38', 'cp39-cp39']

    if machine_arch in ['x86_64', 'AMD64', 'x86']:
        python_abi_tags += ['cp27-cp27m', 'pp27-pypy_73', 'pp36-pypy36_pp73', 'pp37-pypy37_pp73']

        if platform == 'linux':
            python_abi_tags.append('cp27-cp27mu')  # python 2.7 has 2 different ABI on manylinux

    if platform == 'macos' and get_macos_version() >= (11, 0):
        # CPython 3.5 doesn't work on macOS 11.
        python_abi_tags.remove('cp35-cp35m')
        # pypy not supported on macOS 11.
        python_abi_tags = [t for t in python_abi_tags if not t.startswith('pp')]

    wheels = []

    for python_abi_tag in python_abi_tags:
        platform_tags = []

        if platform == 'linux':
            architectures = [machine_arch]

            if machine_arch == 'x86_64' and python_abi_tag.startswith('cp'):
                architectures.append('i686')

            platform_tags = [
                f'{manylinux_version}_{architecture}'
                for architecture in architectures
                for manylinux_version in manylinux_versions
            ]

        elif platform == 'windows':
            if python_abi_tag.startswith('cp'):
                platform_tags = ['win32', 'win_amd64']
            else:
                platform_tags = ['win32']

        elif platform == 'macos':
            if python_abi_tag == 'cp39-cp39':
                if machine_arch == 'x86_64':
                    platform_tags = [
                        f'macosx_{macosx_deployment_target.replace(".", "_")}_x86_64',
                    ]
                elif machine_arch == 'arm64':
                    platform_tags = [
                        f'macosx_{macosx_deployment_target.replace(".", "_")}_universal2.macosx_11_0_universal2',
                        # macosx_deployment_target is ignored on arm64, because arm64 isn't supported on
                        # macOS earlier than 11.0
                        'macosx_11_0_arm64',
                    ]
            else:
                platform_tags = [
                    f'macosx_{macosx_deployment_target.replace(".", "_")}_x86_64',
                ]

        else:
            raise Exception('unsupported platform')

        for platform_tag in platform_tags:
            wheels.append(f'{package_name}-{package_version}-{python_abi_tag}-{platform_tag}.whl')

    if IS_WINDOWS_RUNNING_ON_TRAVIS:
        # Python 2.7 isn't supported on Travis.
        wheels = [w for w in wheels if '-cp27-' not in w and '-pp2' not in w]

    return wheels


def get_macos_version():
    '''
    Returns the macOS major/minor version, as a tuple, e.g. (10, 15) or (11, 0)

    These tuples can be used in comparisons, e.g.
        (10, 14) <= (11, 0) == True
        (11, 2) <= (11, 0) != True
    '''
    version_str, _, _ = pm.mac_ver()
    return tuple(map(int, version_str.split(".")[:2]))


platform = None

if 'CIBW_PLATFORM' in os.environ:
    platform = os.environ['CIBW_PLATFORM']
elif sys.platform.startswith('linux'):
    platform = 'linux'
elif sys.platform.startswith('darwin'):
    platform = 'macos'
elif sys.platform in ['win32', 'cygwin']:
    platform = 'windows'
else:
    raise Exception('Unsupported platform')
