import os, pytest
import utils

def test():
    project_dir = os.path.dirname(__file__)

    if utils.platform != 'linux':
        pytest.skip('the docker test is only relevant to the linux build')

    # build the wheels
    # CFLAGS environment veriable is ecessary to fail on 'malloc_info' (on manylinux1) during compilation/linking,
    # rather than when dynamically loading the Python 
    utils.cibuildwheel_run(project_dir, add_env={
        'CIBW_ENVIRONMENT': 'CFLAGS="$CFLAGS -Werror=implicit-function-declaration"',
    })
    
    # also check that we got the right wheels
    expected_wheels = utils.expected_wheels('spam', '0.1.0', manylinux_versions={'2010_x86_64'})
    actual_wheels = os.listdir('wheelhouse')
    assert set(actual_wheels) == set(expected_wheels)
