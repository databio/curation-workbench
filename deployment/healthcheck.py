"""Container healthcheck: 200 (ok) and 503 (degraded) both mean alive.

ONE definition, referenced by both the Dockerfile HEALTHCHECK and the stack's
healthcheck.test (cloud-management/gcp/compute/stacks/curate.yml). A degraded
check must not crash-loop the only replica: Swarm would kill and restart it
forever and nginx would 502 the whole service. Degradation is reported in the
/health body, not enforced here.
"""

import sys
import urllib.error
import urllib.request

try:
    code = urllib.request.urlopen("http://localhost:8134/health", timeout=4).status
except urllib.error.HTTPError as e:
    code = e.code
except Exception:
    code = 0

sys.exit(0 if code in (200, 503) else 1)
