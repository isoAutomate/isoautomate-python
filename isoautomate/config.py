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
# DEFAULTS (Fallback values if Env Vars are missing)
# ---------------------------------------------------------
DEFAULT_REDIS_HOST = "localhost"
DEFAULT_REDIS_PORT = 6379
DEFAULT_REDIS_PASSWORD = None
DEFAULT_REDIS_DB = 0