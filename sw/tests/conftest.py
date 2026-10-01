import pytest

from mathaccel.driver.device import connect


def pytest_addoption(parser):
    parser.addoption("--port", default=None, help="serial port of the board for -m hw tests")


@pytest.fixture
def dev(request):
    """Simulator by default; the real board when --port is given."""
    with connect(request.config.getoption("--port")) as d:
        yield d
