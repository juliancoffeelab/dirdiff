import pytest

pytestmark = pytest.mark.git


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if item.path.parent.name == "integration":
            item.add_marker(pytest.mark.git)
