# conftest.py — pytest configuration for backend tests
import asyncio
import pytest

# Use "auto" asyncio mode so every async test runs without manual event-loop
# management. Compatible with pytest-asyncio >= 0.21.
def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "asyncio: mark test as async (handled by pytest-asyncio)",
    )
