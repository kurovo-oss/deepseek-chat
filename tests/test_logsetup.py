import logging
from logging.handlers import RotatingFileHandler

import pytest

from qqbot.logsetup import MAX_BYTES, setup_logging


@pytest.fixture(autouse=True)
def clean_handlers():
    yield
    logger = logging.getLogger("qqbot")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()


def test_creates_dir_and_writes(tmp_path):
    log_file = setup_logging(tmp_path / "logs", console=False)
    logging.getLogger("qqbot").info("hello world")

    assert log_file.exists()
    assert "hello world" in log_file.read_text(encoding="utf-8")


def test_idempotent_no_stacking(tmp_path):
    setup_logging(tmp_path, console=False)
    setup_logging(tmp_path, console=False)
    assert len(logging.getLogger("qqbot").handlers) == 1


def test_uses_rotating_handler(tmp_path):
    setup_logging(tmp_path, console=False)
    handler = logging.getLogger("qqbot").handlers[0]
    assert isinstance(handler, RotatingFileHandler)
    assert handler.maxBytes == MAX_BYTES


def test_does_not_touch_root_logger(tmp_path):
    root = logging.getLogger()
    before = list(root.handlers)
    setup_logging(tmp_path, console=False)
    assert list(root.handlers) == before


def test_console_handler_optional(tmp_path):
    setup_logging(tmp_path, console=True)
    assert len(logging.getLogger("qqbot").handlers) == 2


def test_stops_propagation(tmp_path):
    setup_logging(tmp_path, console=False)
    assert logging.getLogger("qqbot").propagate is False
