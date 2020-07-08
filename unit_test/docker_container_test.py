from pathlib import Path, PurePath
import platform
import subprocess
import textwrap
from uuid import uuid4

import pytest

from cibuildwheel.docker_container import DockerContainer

# for these tests we use manylinux2014 images, because they're available on
# multi architectures and include python3.8
pm = platform.machine()
if pm == "x86_64":
    DEFAULT_IMAGE = 'quay.io/pypa/manylinux2014_x86_64:2020-05-17-2f8ac3b'
elif pm == "aarch64":
    DEFAULT_IMAGE = 'quay.io/pypa/manylinux2014_aarch64:2020-05-17-2f8ac3b'
elif pm == "ppc64le":
    DEFAULT_IMAGE = 'quay.io/pypa/manylinux2014_ppc64le:2020-05-17-2f8ac3b'
elif pm == "s390x":
    DEFAULT_IMAGE = 'quay.io/pypa/manylinux2014_s390x:2020-05-17-2f8ac3b'


@pytest.mark.docker
def test_simple():
    with DockerContainer(DEFAULT_IMAGE) as container:
        assert container.call(['echo', 'hello'], capture_output=True) == 'hello\n'


@pytest.mark.docker
def test_no_lf():
    with DockerContainer(DEFAULT_IMAGE) as container:
        assert container.call(['printf', 'hello'], capture_output=True) == 'hello'


@pytest.mark.docker
def test_environment():
    with DockerContainer(DEFAULT_IMAGE) as container:
        assert container.call(['sh', '-c', 'echo $TEST_VAR'], env={'TEST_VAR': '1'}, capture_output=True) == '1\n'


@pytest.mark.docker
def test_container_removed():
    with DockerContainer(DEFAULT_IMAGE) as container:
        docker_containers_listing = subprocess.run('docker container ls', shell=True, check=True, stdout=subprocess.PIPE, universal_newlines=True).stdout
        assert container.name in docker_containers_listing
        old_container_name = container.name

    docker_containers_listing = subprocess.run('docker container ls', shell=True, check=True, stdout=subprocess.PIPE, universal_newlines=True).stdout
    assert old_container_name not in docker_containers_listing


@pytest.mark.docker
def test_large_environment():
    # max environment variable size is 128kB
    long_env_var_length = 127*1024
    large_environment = {
        'a': '0'*long_env_var_length,
        'b': '0'*long_env_var_length,
        'c': '0'*long_env_var_length,
        'd': '0'*long_env_var_length,
    }

    with DockerContainer(DEFAULT_IMAGE) as container:
        # check the length of d
        assert container.call(['sh', '-c', 'echo ${#d}'], env=large_environment, capture_output=True) == f'{long_env_var_length}\n'


@pytest.mark.docker
def test_binary_output():
    with DockerContainer(DEFAULT_IMAGE) as container:
        # note: the below embedded snippets are in python2

        # check that we can pass though arbitrary binary data without erroring
        container.call(['/usr/bin/python2', '-c', textwrap.dedent('''
            import sys
            sys.stdout.write(''.join(chr(n) for n in range(0, 256)))
        ''')])

        # check that we can capture arbitrary binary data
        output = container.call(['/usr/bin/python2', '-c', textwrap.dedent('''
            import sys
            sys.stdout.write(''.join(chr(n % 256) for n in range(0, 512)))
        ''')], capture_output=True)

        data = bytes(output, encoding='utf8', errors='surrogateescape')

        for i in range(0, 512):
            assert data[i] == i % 256

        # check that environment variables can carry binary data, except null characters
        # (https://www.gnu.org/software/libc/manual/html_node/Environment-Variables.html)
        binary_data = bytes(n for n in range(1, 256))
        binary_data_string = str(binary_data, encoding='utf8', errors='surrogateescape')
        output = container.call(
            ['python2', '-c', 'import os, sys; sys.stdout.write(os.environ["TEST_VAR"])'],
            env={'TEST_VAR': binary_data_string},
            capture_output=True,
        )
        assert output == binary_data_string


@pytest.mark.docker
def test_file_operations(tmp_path: Path):
    with DockerContainer(DEFAULT_IMAGE) as container:
        # test copying a file in
        test_binary_data = uuid4().bytes + uuid4().bytes + uuid4().bytes + uuid4().bytes

        original_test_file = tmp_path / 'test.dat'
        original_test_file.write_bytes(test_binary_data)

        dst_file = PurePath('/tmp/test.dat')

        container.copy_into(original_test_file, dst_file)

        output = container.call(['cat', dst_file], capture_output=True)
        assert test_binary_data == bytes(output, encoding='utf8', errors='surrogateescape')

        # test copying a dir in
        test_dir = tmp_path / 'test_dir'
        new_test_file = tmp_path / 'test-new.dat'
        container.copy_out(dst_file, new_test_file)

        assert original_test_file.read_bytes() == new_test_file.read_bytes()

        assert container.glob(PurePath('/tmp/*.dat')) == [dst_file]
