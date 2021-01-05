import os
import platform
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, Sequence, Tuple, cast

from .environment import ParsedEnvironment
from .logger import log
from .util import (Architecture, BuildOptions, BuildSelector, NonPlatformWheelError,
                   download, get_build_verbosity_extra_flags, get_pip_script,
                   install_certifi_script, prepare_command, wrap_text)
from .typing import PathOrStr


def call(args: Sequence[PathOrStr], env: Optional[Dict[str, str]] = None, cwd: Optional[str] = None, shell: bool = False) -> int:
    # print the command executing for the logs
    if shell:
        print(f'+ {args}')
    else:
        print('+ ' + ' '.join(shlex.quote(str(a)) for a in args))

    return subprocess.check_call(args, env=env, cwd=cwd, shell=shell)


def get_macos_version() -> Tuple[int, int]:
    '''
    Returns the macOS major/minor version, as a tuple, e.g. (10, 15) or (11, 0)

    These tuples can be used in comparisons, e.g.
        (10, 14) <= (11, 0) == True
        (11, 2) <= (11, 0) != True
    '''
    version_str, _, _ = platform.mac_ver()
    version = tuple(map(int, version_str.split(".")[:2]))
    return cast(Tuple[int, int], version)


class PythonConfiguration(NamedTuple):
    version: str
    identifier: str
    url: str


def get_python_configurations(build_selector: BuildSelector,
                              architectures: List[Architecture]) -> List[PythonConfiguration]:
    python_configurations = [
        # CPython
        PythonConfiguration(version='2.7', identifier='cp27-macosx_x86_64', url='https://www.python.org/ftp/python/2.7.18/python-2.7.18-macosx10.9.pkg'),
        PythonConfiguration(version='3.5', identifier='cp35-macosx_x86_64', url='https://www.python.org/ftp/python/3.5.4/python-3.5.4-macosx10.6.pkg'),
        PythonConfiguration(version='3.6', identifier='cp36-macosx_x86_64', url='https://www.python.org/ftp/python/3.6.8/python-3.6.8-macosx10.9.pkg'),
        PythonConfiguration(version='3.7', identifier='cp37-macosx_x86_64', url='https://www.python.org/ftp/python/3.7.9/python-3.7.9-macosx10.9.pkg'),
        PythonConfiguration(version='3.8', identifier='cp38-macosx_x86_64', url='https://www.python.org/ftp/python/3.8.7/python-3.8.7-macosx10.9.pkg'),
        PythonConfiguration(version='3.9', identifier='cp39-macosx_x86_64', url='https://www.python.org/ftp/python/3.9.1/python-3.9.1-macos11.0.pkg'),
        PythonConfiguration(version='3.9', identifier='cp39-macosx_universal2', url='https://www.python.org/ftp/python/3.9.1/python-3.9.1-macos11.0.pkg'),
        PythonConfiguration(version='3.9', identifier='cp39-macosx_arm64', url='https://www.python.org/ftp/python/3.9.1/python-3.9.1-macos11.0.pkg'),
        # PyPy
        PythonConfiguration(version='2.7', identifier='pp27-macosx_x86_64', url='https://downloads.python.org/pypy/pypy2.7-v7.3.3-osx64.tar.bz2'),
        PythonConfiguration(version='3.6', identifier='pp36-macosx_x86_64', url='https://downloads.python.org/pypy/pypy3.6-v7.3.3-osx64.tar.bz2'),
        PythonConfiguration(version='3.7', identifier='pp37-macosx_x86_64', url='https://downloads.python.org/pypy/pypy3.7-v7.3.3-osx64.tar.bz2'),
    ]

    # filter out configs that don't match any of the selected architectures
    python_configurations = [c for c in python_configurations
                             if any(c.identifier.endswith(a.value) for a in architectures)]

    # skip builds as required by BUILD/SKIP
    python_configurations = [c for c in python_configurations if build_selector(c.identifier)]

    if get_macos_version() >= (11, 0):
        if any(c.identifier.startswith('pp') for c in python_configurations):
            # pypy doesn't work on macOS 11 yet
            # See https://foss.heptapod.net/pypy/pypy/-/issues/3314
            log.warning(wrap_text('''
                PyPy is currently unsupported when building on macOS 11. To build macOS PyPy wheels,
                build on an older OS, such as macOS 10.15. To silence this warning, deselect PyPy by
                adding "pp*-macosx*" to your CIBW_SKIP option.
            '''))
            python_configurations = [c for c in python_configurations if not c.identifier.startswith('pp')]

        if any(c.identifier.startswith('cp35') for c in python_configurations):
            # CPython 3.5 doesn't work on macOS 11
            log.warning(wrap_text('''
                CPython 3.5 is unsupported when building on macOS 11. To build CPython 3.5 wheels,
                build on an older OS, such as macOS 10.15. To silence this warning, deselect CPython
                3.5 by adding "cp35-macosx_x86_64" to your CIBW_SKIP option.
            '''))
            python_configurations = [c for c in python_configurations if not c.identifier.startswith('cp35')]

    return python_configurations


SYMLINKS_DIR = Path('/tmp/cibw_bin')


def make_symlinks(installation_bin_path: Path, python_executable: str, pip_executable: str) -> None:
    assert (installation_bin_path / python_executable).exists()

    # Python bin folders on Mac don't symlink `python3` to `python`, and neither
    # does PyPy for `pypy` or `pypy3`, so we do that so `python` and `pip` always
    # point to the active configuration.
    if SYMLINKS_DIR.exists():
        shutil.rmtree(SYMLINKS_DIR)
    SYMLINKS_DIR.mkdir(parents=True)

    (SYMLINKS_DIR / 'python').symlink_to(installation_bin_path / python_executable)
    (SYMLINKS_DIR / 'python-config').symlink_to(installation_bin_path / (python_executable + '-config'))
    (SYMLINKS_DIR / 'pip').symlink_to(installation_bin_path / pip_executable)


def install_cpython(version: str, url: str) -> Path:
    installed_system_packages = subprocess.check_output(['pkgutil', '--pkgs'], universal_newlines=True).splitlines()

    # if this version of python isn't installed, get it from python.org and install
    python_package_identifier = f'org.python.Python.PythonFramework-{version}'
    python_executable = 'python3' if version[0] == '3' else 'python'
    installation_bin_path = Path(f'/Library/Frameworks/Python.framework/Versions/{version}/bin')

    if python_package_identifier not in installed_system_packages:
        # download the pkg
        download(url, Path('/tmp/Python.pkg'))
        # install
        call(['sudo', 'installer', '-pkg', '/tmp/Python.pkg', '-target', '/'])
        # patch open ssl
        if version == '3.5':
            open_ssl_patch_url = f'https://github.com/mayeut/patch-macos-python-openssl/releases/download/v1.1.1h/patch-macos-python-{version}-openssl-v1.1.1h.tar.gz'
            download(open_ssl_patch_url, Path('/tmp/python-patch.tar.gz'))
            call(['sudo', 'tar', '-C', f'/Library/Frameworks/Python.framework/Versions/{version}/', '-xmf', '/tmp/python-patch.tar.gz'])

        call(["sudo", str(installation_bin_path/python_executable), str(install_certifi_script)])

    pip_executable = 'pip3' if version[0] == '3' else 'pip'
    make_symlinks(installation_bin_path, python_executable, pip_executable)

    return installation_bin_path


def install_pypy(version: str, url: str) -> Path:
    pypy_tar_bz2 = url.rsplit('/', 1)[-1]
    extension = ".tar.bz2"
    assert pypy_tar_bz2.endswith(extension)
    pypy_base_filename = pypy_tar_bz2[:-len(extension)]
    installation_path = Path('/tmp') / pypy_base_filename
    if not installation_path.exists():
        downloaded_tar_bz2 = Path("/tmp") / pypy_tar_bz2
        download(url, downloaded_tar_bz2)
        call(['tar', '-C', '/tmp', '-xf', downloaded_tar_bz2])
        # Patch PyPy to make sure headers get installed into a venv
        patch_version = '_27' if version == '2.7' else ''
        patch_path = Path(__file__).absolute().parent / 'resources' / f'pypy_venv{patch_version}.patch'
        call(['patch', '--force', '-p1', '-d', installation_path, '-i', patch_path])

    installation_bin_path = installation_path / 'bin'
    python_executable = 'pypy3' if version[0] == '3' else 'pypy'
    pip_executable = 'pip3' if version[0] == '3' else 'pip'
    make_symlinks(installation_bin_path, python_executable, pip_executable)

    return installation_bin_path


def setup_python(python_configuration: PythonConfiguration,
                 dependency_constraint_flags: Sequence[PathOrStr],
                 environment: ParsedEnvironment) -> Dict[str, str]:
    implementation_id = python_configuration.identifier.split("-")[0]
    log.step(f'Installing Python {implementation_id}...')

    if implementation_id.startswith('cp'):
        installation_bin_path = install_cpython(python_configuration.version, python_configuration.url)
    elif implementation_id.startswith('pp'):
        installation_bin_path = install_pypy(python_configuration.version, python_configuration.url)
    else:
        raise ValueError("Unknown Python implementation")

    log.step('Setting up build environment...')

    env = os.environ.copy()
    env['PATH'] = os.pathsep.join([
        str(SYMLINKS_DIR),
        str(installation_bin_path),
        env['PATH'],
    ])

    # Fix issue with site.py setting the wrong `sys.prefix`, `sys.exec_prefix`,
    # `sys.path`, ... for PyPy: https://foss.heptapod.net/pypy/pypy/issues/3175
    # Also fix an issue with the shebang of installed scripts inside the
    # testing virtualenv- see https://github.com/theacodes/nox/issues/44 and
    # https://github.com/pypa/virtualenv/issues/620
    # Also see https://github.com/python/cpython/pull/9516
    env.pop('__PYVENV_LAUNCHER__', None)
    env = environment.as_dictionary(prev_environment=env)

    # check what version we're on
    call(['which', 'python'], env=env)
    call(['python', '--version'], env=env)
    which_python = subprocess.check_output(['which', 'python'], env=env, universal_newlines=True).strip()
    if which_python != '/tmp/cibw_bin/python':
        print("cibuildwheel: python available on PATH doesn't match our installed instance. If you have modified PATH, ensure that you don't overwrite cibuildwheel's entry or insert python above it.", file=sys.stderr)
        exit(1)

    # install pip & wheel
    call(['python', get_pip_script, *dependency_constraint_flags], env=env, cwd="/tmp")
    assert (installation_bin_path / 'pip').exists()
    call(['which', 'pip'], env=env)
    call(['pip', '--version'], env=env)
    which_pip = subprocess.check_output(['which', 'pip'], env=env, universal_newlines=True).strip()
    if which_pip != '/tmp/cibw_bin/pip':
        print("cibuildwheel: pip available on PATH doesn't match our installed instance. If you have modified PATH, ensure that you don't overwrite cibuildwheel's entry or insert pip above it.", file=sys.stderr)
        exit(1)

    # Set MACOSX_DEPLOYMENT_TARGET to 10.9, if the user didn't set it.
    # CPython 3.5 defaults to 10.6, and pypy defaults to 10.7, causing
    # inconsistencies if it's left unset.
    env.setdefault('MACOSX_DEPLOYMENT_TARGET', '10.9')

    if python_configuration.version == '3.5':
        # Cross-compilation platform override - CPython 3.5 has an
        # i386/x86_64 version of Python, but we only want a x64_64 build
        env.setdefault('_PYTHON_HOST_PLATFORM', 'macosx-10.9-x86_64')
        # https://github.com/python/cpython/blob/a5ed2fe0eedefa1649aa93ee74a0bafc8e628a10/Lib/_osx_support.py#L260
        env.setdefault('ARCHFLAGS', '-arch x86_64')

    if python_configuration.version == '3.9':
        if python_configuration.identifier.endswith('x86_64'):
            env.setdefault('_PYTHON_HOST_PLATFORM', 'macosx-10.9-x86_64')
            env.setdefault('ARCHFLAGS', '-arch x86_64')
        elif python_configuration.identifier.endswith('arm64'):
            env.setdefault('_PYTHON_HOST_PLATFORM', 'macosx-11.0-arm64')
            env.setdefault('ARCHFLAGS', '-arch arm64')

    log.step('Installing build tools...')
    call(['pip', 'install', '--upgrade', 'setuptools', 'wheel', 'delocate', *dependency_constraint_flags], env=env)

    return env


def build(options: BuildOptions) -> None:
    allowed_archs = {Architecture.x86_64, Architecture.universal2, Architecture.arm64}
    if set(options.architectures) <= allowed_archs:
        raise ValueError(textwrap.dedent(f'''
            Invalid archs option {options.architectures}. macOS only supports
            these architectures: {', '.join(a.value for a in allowed_archs)}.
        '''))

    temp_dir = Path(tempfile.mkdtemp(prefix='cibuildwheel'))
    built_wheel_dir = temp_dir / 'built_wheel'
    repaired_wheel_dir = temp_dir / 'repaired_wheel'

    try:
        if options.before_all:
            log.step('Running before_all...')
            env = options.environment.as_dictionary(prev_environment=os.environ)
            before_all_prepared = prepare_command(options.before_all, project='.', package=options.package_dir)
            call([before_all_prepared], shell=True, env=env)

        python_configurations = get_python_configurations(options.build_selector, options.architectures)

        for config in python_configurations:
            log.build_start(config.identifier)

            dependency_constraint_flags: Sequence[PathOrStr] = []
            if options.dependency_constraints:
                dependency_constraint_flags = [
                    '-c', options.dependency_constraints.get_for_python_version(config.version)
                ]

            env = setup_python(config, dependency_constraint_flags, options.environment)

            if options.before_build:
                log.step('Running before_build...')
                before_build_prepared = prepare_command(options.before_build, project='.', package=options.package_dir)
                call(before_build_prepared, env=env, shell=True)

            log.step('Building wheel...')
            if built_wheel_dir.exists():
                shutil.rmtree(built_wheel_dir)
            built_wheel_dir.mkdir(parents=True)

            # Path.resolve() is needed. Without it pip wheel may try to fetch package from pypi.org
            # see https://github.com/joerick/cibuildwheel/pull/369
            call([
                'pip', 'wheel',
                options.package_dir.resolve(),
                '-w', built_wheel_dir,
                '--no-deps',
                *get_build_verbosity_extra_flags(options.build_verbosity)
            ], env=env)

            built_wheel = next(built_wheel_dir.glob('*.whl'))

            if repaired_wheel_dir.exists():
                shutil.rmtree(repaired_wheel_dir)
            repaired_wheel_dir.mkdir(parents=True)

            if built_wheel.name.endswith('none-any.whl'):
                raise NonPlatformWheelError()

            if options.repair_command:
                log.step('Repairing wheel...')

                if config.identifier.endswith('universal2'):
                    delocate_archs = 'x86_64,arm64'
                elif config.identifier.endswith('arm64'):
                    delocate_archs = 'arm64'
                else:
                    delocate_archs = 'x86_64'

                repair_command_prepared = prepare_command(
                    options.repair_command,
                    wheel=built_wheel,
                    dest_dir=repaired_wheel_dir,
                    delocate_archs=delocate_archs,
                )
                call(repair_command_prepared, env=env, shell=True)
            else:
                shutil.move(str(built_wheel), repaired_wheel_dir)

            repaired_wheel = next(repaired_wheel_dir.glob('*.whl'))

            if repaired_wheel.stem.endswith('_universal2'):
                # due to a bug in packaging/tags, this universal wheel isn't
                # installable on arm64. so we rename it to add a 11_0 platform
                # tag.
                # See https://github.com/pypa/packaging/pull/380 and
                # https://github.com/pypa/packaging/issues/379
                renamed_wheel = repaired_wheel.parent / f'{repaired_wheel.stem}.macosx_11_0_universal2.whl'
                repaired_wheel.rename(renamed_wheel)
                repaired_wheel = renamed_wheel

            log.step_end()

            machine_arch = platform.machine()
            testing_archs: List[str] = []

            if machine_arch == 'x86_64':
                if config.identifier.endswith('_arm64'):
                    log.warning(wrap_text('''
                        While arm64 wheels can be built on x86_64, they cannot be tested. The
                        ability to test the arm64 wheels will be added in a future release of
                        cibuildwheel, once Apple Silicon CI runners are widely available.
                    '''))
                    testing_archs = []
                elif config.identifier.endswith('_universal2'):
                    log.warning(wrap_text('''
                        While universal2 wheels can be built on x86_64, the arm64 part of them
                        cannot currently be tested. The ability to test the arm64 part of a
                        universal2 wheel will be added in a future release of cibuildwheel, once
                        Apple Silicon CI runners are widely available.
                    '''))
                    testing_archs = ['x86_64']
                else:
                    testing_archs = ['x86_64']
            elif machine_arch == 'arm64':
                if config.identifier.endswith('_x86_64'):
                    # testing using rosetta2 emulation
                    testing_archs = ['x86_64']
                elif config.identifier.endswith('_universal2'):
                    # testing the x86_64 using rosetta2 emulation
                    testing_archs = ['arm64', 'x86_64']
                else:
                    testing_archs = ['arm64']

            if options.test_command and len(testing_archs) > 0:
                for testing_arch in testing_archs:
                    log.step('Testing wheel...' if testing_arch == machine_arch else f'Testing wheel on {testing_arch}...')

                    # set up a virtual environment to install and test from, to make sure
                    # there are no dependencies that were pulled in at build time.
                    call(['pip', 'install', 'virtualenv', *dependency_constraint_flags], env=env)
                    venv_dir = Path(tempfile.mkdtemp())

                    arch_prefix = []
                    if testing_arch != machine_arch:
                        if machine_arch == 'arm64' and testing_arch == 'x86_64':
                            # rosetta2 will provide the emulation with just the arch prefix.
                            arch_prefix = ['arch', '-x86_64']
                        else:
                            raise RuntimeError("don't know how to emulate {testing_arch} on {machine_arch}")

                    # define a custom 'call' function that adds the arch prefix each time
                    def call_with_arch(args: Sequence[PathOrStr], **kwargs: Any) -> int:
                        if isinstance(args, str):
                            args = ' '.join(arch_prefix) + ' ' + args
                        else:
                            args = [*arch_prefix, *args]
                        return call(args, **kwargs)

                    # Use --no-download to ensure determinism by using seed libraries
                    # built into virtualenv
                    call_with_arch(['python', '-m', 'virtualenv', '--no-download', venv_dir], env=env)

                    virtualenv_env = env.copy()
                    virtualenv_env['PATH'] = os.pathsep.join([
                        str(venv_dir / 'bin'),
                        virtualenv_env['PATH'],
                    ])

                    # check that we are using the Python from the virtual environment
                    call_with_arch(['which', 'python'], env=virtualenv_env)

                    if options.before_test:
                        before_test_prepared = prepare_command(options.before_test, project='.', package=options.package_dir)
                        call_with_arch(before_test_prepared, env=virtualenv_env, shell=True)

                    # install the wheel
                    call_with_arch(['pip', 'install', str(repaired_wheel) + options.test_extras], env=virtualenv_env)

                    # test the wheel
                    if options.test_requires:
                        call_with_arch(['pip', 'install'] + options.test_requires, env=virtualenv_env)

                    # run the tests from $HOME, with an absolute path in the command
                    # (this ensures that Python runs the tests against the installed wheel
                    # and not the repo code)
                    test_command_prepared = prepare_command(
                        options.test_command,
                        project=Path('.').resolve(),
                        package=options.package_dir.resolve()
                    )
                    call_with_arch(test_command_prepared, cwd=os.environ['HOME'], env=virtualenv_env, shell=True)

                    # clean up
                    shutil.rmtree(venv_dir)

            # we're all done here; move it to output (overwrite existing)
            shutil.move(str(repaired_wheel), options.output_dir)
            log.build_end()
    except subprocess.CalledProcessError as error:
        log.step_end_with_error(f'Command {error.cmd} failed with code {error.returncode}. {error.stdout}')
        exit(1)
