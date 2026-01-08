import os

# ---------------------------------------------------------
# CONSTANTS (The "Protocol")
# ---------------------------------------------------------
REDIS_PREFIX = "ISOAUTOMATE:"
WORKERS_SET = f"{REDIS_PREFIX}workers"

# File System Paths
SCREENSHOT_FOLDER = "screenshots"
ASSERTION_FOLDER = os.path.join(SCREENSHOT_FOLDER, "failures")

# ---------------------------------------------------------
# NO DEFAULTS (Force user configuration)
# ---------------------------------------------------------
# We leave these as None so the Client can check if they were provided
DEFAULT_REDIS_HOST = None
DEFAULT_REDIS_PORT = None
DEFAULT_REDIS_PASSWORD = None
DEFAULT_REDIS_DB = 0