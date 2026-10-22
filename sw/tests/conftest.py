"""Provide the Device connection used by pytest's command-level tests.

I default to the software model so ordinary tests need no board. Supplying
--port opens that serial port or UDP address for tests using the dev fixture. Simulator-only
tests construct their own model and cannot be redirected to hardware here.
"""

import pytest

from mathaccel.driver.device import connect


def pytest_addoption(parser):
    """Register the optional serial port before pytest parses its arguments."""
    parser.addoption("--port", default=None,
                     help="board connection for the dev fixture: COM5, udp:192.168.10.2, or udpsim")


@pytest.fixture
def dev(request):
    """Give each test a connection and close it after the test finishes.

    yield lets pytest run the test inside the with block. Cleanup still runs
    if an assertion fails; a newly created simulator also has fresh memory.
    """
    with connect(request.config.getoption("--port")) as d:
        yield d
