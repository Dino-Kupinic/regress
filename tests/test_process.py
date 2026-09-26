import sys
import time
from pathlib import Path

import pytest

from regress.errors import ToolTimeout
from regress.process import run_command


def test_timeout_stops_the_command_and_keeps_its_output(tmp_path: Path):
    log = tmp_path / "logs/hang.log"
    started = time.monotonic()
    with pytest.raises(ToolTimeout) as error:
        run_command(
            [sys.executable, "-c", "print('started', flush=True); import time; time.sleep(60)"], tmp_path, 1, log
        )

    assert time.monotonic() - started < 10
    assert "timed out after 1s" in str(error.value)
    assert "started" in error.value.output
    # The log is written for a stopped command too, so a hang can be looked into afterwards.
    assert "started" in log.read_text() and "[stopped after 1s]" in log.read_text()
