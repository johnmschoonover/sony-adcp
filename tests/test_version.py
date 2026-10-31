from importlib.metadata import version

import pyadcp


def test_version_matches_package_metadata() -> None:
    assert pyadcp.__version__ == version("pyadcp")
