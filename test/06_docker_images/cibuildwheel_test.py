import subprocess, sys, os, pytest
from glob import glob
import utils

def test():
    project_dir = os.path.dirname(__file__)
    if not sys.platform.startswith('linux'):
        pytest.skip('the docker test is only relevant to the linux build')

    # set up the environment
    env = os.environ.copy()
    # change the docker images to use. The docker image is tested in setup.py 
    # during the build
    env.update({
        'CIBW_MANYLINUX1_X86_64_IMAGE': 'dockcross/manylinux-x64',
        'CIBW_MANYLINUX1_I686_IMAGE': 'dockcross/manylinux-x86',
    })

    # build the wheels
    subprocess.check_call([sys.executable, '-m', 'cibuildwheel', project_dir], env=env)
    
    # check that we got the right number of built wheels
    expected_identifiers = utils.cibuildwheel_get_build_identifiers(project_dir)
    built_wheels = glob('wheelhouse/*.whl')
    assert len(built_wheels) == len(expected_identifiers)
