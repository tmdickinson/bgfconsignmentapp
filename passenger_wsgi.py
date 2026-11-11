"""Entry point for cPanel's "Setup Python App" (Phusion Passenger). See docs/DEPLOYMENT.md."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bgf.wsgi import application  # noqa: E402, F401
