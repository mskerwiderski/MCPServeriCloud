__version__ = "0.1.1"

# Sign-in and the iCloud connection through MyAuth come from the MyCommons
# building block `access` (icloud_mcp/access/ is a copy, see MyCommons/sync.sh).
from .access import myauth as _myauth

_myauth.configure(app="mcp-icloud", platform="iCloud")
