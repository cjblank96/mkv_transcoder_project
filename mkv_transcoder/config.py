# mkv_transcoder/config.py

import os

# Get the project root directory (the parent of the 'mkv_transcoder' package directory)
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

# --- Unraid Fileshare Configuration ---
UNRAID_USERNAME = os.getenv("UNRAID_USERNAME", "cjblank")
UNRAID_PASSWORD = os.getenv("UNRAID_PASSWORD", "C8Z0FHrh*@f5!q*eyu#f")
UNRAID_HOST = os.getenv("UNRAID_HOST", "10.50.50.218")
UNRAID_SHARE_NAME = os.getenv("UNRAID_SHARE_NAME", "UNRAID_SHARE")

# Base path for the mounted Unraid share on the Linux VMs.
# This should be the location where '//{UNRAID_HOST}/{UNRAID_SHARE_NAME}' is mounted.
UNRAID_MOUNT_PATH = os.getenv("UNRAID_MOUNT_PATH", "/mnt/unraid")

# --- Directory Configuration ---

# Shared directory for job queue and logs on the local machine or a shared mount
SHARED_DIR = os.getenv("MKV_SHARED_DIR", os.path.join(PROJECT_ROOT, "shared_data"))

# Directories for video files on the Unraid share, accessed via the mount point
VIDEO_LANDING_POINT = os.path.join(UNRAID_MOUNT_PATH, "landing_point/Video")
TEST_FILE_PATH = os.path.join(UNRAID_MOUNT_PATH, "landing_point/Test/test_file_dune.mkv")

# --- Job Queue and Worker Settings ---

# Job queue settings (local to the orchestrator/scanner)
JOB_QUEUE_PATH = os.path.join(SHARED_DIR, "job_queue.json")
LOCK_FILE_PATH = os.path.join(SHARED_DIR, "job_queue.lock")

# Directory for logs
LOG_DIR = os.path.join(SHARED_DIR, "logs")

# Base directory for temporary files on the transcoder VMs
# Each VM should have this path available and have sufficient space (e.g., 500GB).
TEMP_DIR_BASE = os.getenv("TRANSCODER_TEMP_DIR", "/var/tmp/mkv_transcoder")

# RAM-based temp directory for small, frequently accessed files on Linux VMs
# This leverages system memory to speed up I/O for non-video files.
RAM_TEMP_DIR = os.getenv("TRANSCODER_RAM_DIR", "/dev/shm")

# Worker settings
STALE_JOB_THRESHOLD_HOURS = 2
TRANSCODER_VMS = [f"10.50.50.11{i}" for i in range(4)] # 110-113
