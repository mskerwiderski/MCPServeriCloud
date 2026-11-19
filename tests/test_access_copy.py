"""icloud_mcp/access/ and icloud_mcp/mcp_oauth/ are copies of MyCommons building
blocks. Locally (MyCommons cloned next to this repo) the copy must match it; in
CI, where the private MyCommons repo is not available, the check is skipped."""
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SYNC = REPO.parent / "MyCommons" / "sync.sh"


@pytest.mark.skipif(not SYNC.is_file(), reason="MyCommons is not cloned next to this repo")
@pytest.mark.parametrize("block", ["access", "mcp_oauth"])
def test_the_copy_matches_mycommons(block):
    r = subprocess.run([str(SYNC), "--check", block, str(REPO / "icloud_mcp")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
