# Distributed MKV Transcoding Pipeline

This project provides a distributed pipeline for transcoding 4K Blu-ray MKV files with Dolby Vision Profile 7 to Profile 8.1, re-encoding them, and remuxing them into a final file. The system is designed to run on a Proxmox host, with a central orchestrator, a file share for media, and multiple transcoder VMs.

## Architecture

-   **Scanner Role**: One of the VMs takes on the role of the scanner by running the `scanner.py` script. This scans the media library on the Unraid fileshare for `.mkv` files and adds them to the central job queue. This only needs to be run once to populate the queue, or whenever you add new media.
-   **Unraid Fileshare**: A central storage location (e.g., a NAS VM) that hosts the original media, the final transcoded files, and the shared project data (job queue, logs).
-   **Transcoder VMs**: Multiple worker VMs (Ubuntu 22.04) that pull jobs from the queue, perform the heavy lifting of transcoding, and save the final output back to the fileshare.

## Project Structure

-   `mkv_transcoder/`: The main Python package.
    -   `config.py`: Centralized configuration for file paths, credentials, and VM settings. Supports environment variable overrides.
    -   `job_queue.py`: Manages the shared `job_queue.json` with file-based locking.
    -   `transcoder.py`: The core transcoding logic that runs on the worker VMs. It replicates the steps from `mkv_converter.sh`.
-   `scanner.py`: A script to scan the media library and populate the job queue.
-   `worker.py`: The main script for the transcoder VMs. It polls the queue, claims jobs, and executes the transcoding pipeline.
-   `requirements.txt`: Python package dependencies.
-   `README.md`: This file.

## Setup and Installation

These steps need to be performed on your **Ubuntu 22.04 transcoder VMs**.

### 1. Install System Dependencies

Install `ffmpeg` and `mkvtoolnix` (which provides `mkvmerge`):

```bash
sudo apt update
sudo apt install -y ffmpeg mkvtoolnix
```

### 2. Install `dovi_tool`

Download the latest `dovi_tool` binary from the official releases page:

```bash
# Check for the latest version and update the URL if needed
DOVI_TOOL_VERSION="1.6.3"
wget "https://github.com/quietvoid/dovi_tool/releases/download/${DOVI_TOOL_VERSION}/dovi_tool-${DOVI_TOOL_VERSION}-x86_64-unknown-linux-musl.tar.gz"

tar -xvf dovi_tool-*.tar.gz
sudo mv dovi_tool /usr/local/bin/
rm dovi_tool-*.tar.gz
```

### 3. Clone the Project and Install Python Dependencies

Clone this repository to a location accessible by the scanner and workers (e.g., on the Unraid share itself or on each machine).

```bash
# On a machine with access to the project files:
git clone <your-repo-url>
cd mkv_transcoder_project
pip install -r requirements.txt
```

### 4. Configure Your Environment

All configuration is handled in `mkv_transcoder/config.py`. You can either edit the file directly or override the settings with environment variables for better security and flexibility.

-   **`UNRAID_...`**: Set the correct IP, share name, and credentials for your fileshare.
-   **`SHARED_DIR`**: This is the path where the `job_queue.json` and logs will be stored. It must be accessible by the scanner and all workers.
-   **`TEMP_DIR_BASE`**: The base directory on each worker VM for storing large intermediate HEVC files (e.g., `/var/tmp/mkv_transcoder`). **Ensure this directory exists and has sufficient space (e.g., >100GB).**
-   **`RAM_TEMP_DIR`**: The RAM-based directory (`/dev/shm` on Linux) for storing small temporary files.

## Advanced Configuration and Reference

### Environment Variables

- `UNRAID_USERNAME`: Username for Unraid share access (default: `cjblank`)
- `UNRAID_PASSWORD`: Password for Unraid share access (default: see `config.py`)
- `UNRAID_HOST`: IP address of Unraid server (default: `10.50.50.218`)
- `UNRAID_SHARE_NAME`: Name of the Unraid share (default: `UNRAID_SHARE`)
- `UNRAID_MOUNT_PATH`: Base mount path for Unraid share (default: `/mnt/unraid`)
- `MKV_SHARED_DIR`: Path to shared directory for job queue and logs (default: `<project_root>/shared_data`)
- `TRANSCODER_RAM_DIR`: RAM-based temp directory (default: `/dev/shm`)

### Advanced Configuration

- `TEMP_DIR_BASE`: Base directory for large intermediate files (default: `/mnt/unraid/mkv_transcoder_project/temp`)
- `STAGING_DIR`: Platform-dependent staging directory
    - Windows: `H:\staging`
    - Linux: `/mnt/staging`
- `JOB_QUEUE_PATH`: Path to the job queue JSON (default: `$MKV_SHARED_DIR/job_queue.json`)
- `LOCK_FILE_PATH`: Path to the job queue lock file (default: `$MKV_SHARED_DIR/job_queue.lock`)
- `LOG_DIR`: Path to logs (default: `$MKV_SHARED_DIR/logs`)
- `STALE_JOB_THRESHOLD_HOURS`: (default: 2)
- `TRANSCODER_VMS`: List of VM IPs (default: `10.50.50.110-113`)

### Job Types and Pipeline Steps

- **Job Types:**
    - `dolby_vision`: Full pipeline (default for new jobs)
    - `standard`: Reduced steps (copy, get_metadata, reencode_x265, move_final)
- **Pipeline Steps:**
    - `copy_source`, `get_metadata`, `extract_p7`, `convert_p8`, `extract_rpu`, `reencode_x265`, `inject_rpu`, `remux_final`, `move_final`
    - `standard` jobs skip some steps

### File and Directory Structure

- `job_queue.json` and `job_queue.lock`: Located in `$MKV_SHARED_DIR`
- Logs: `$MKV_SHARED_DIR/logs` (per-job logs in `logs/transcoding_logs/`)
- Staging files: In `STAGING_DIR/<job_id>`
- Temp/intermediate files: In `TEMP_DIR_BASE`

### Logging

- Each job has a dedicated log file in `logs/transcoding_logs/`
- Logs include: job initialization, file paths, disk space, command execution, errors

### Dependencies

- **Python:** `tqdm`, `portalocker`
- **System:** `ffmpeg`, `mkvtoolnix` (`mkvmerge`), `dovi_tool`

### Limitations & Expectations

- All VMs must have access to the same shared file system
- Default paths are for Linux; Windows support is platform-aware
- Only `.mkv` files are supported for the pipeline
- Jobs are identified by UUID and input path

---

## How to Run the Pipeline

---

## Command-Line Flags

### `scanner.py`
- `--full-scan` &nbsp; &nbsp; Scan the entire media directory (`config.VIDEO_LANDING_POINT`) and add all MKV files to the job queue.
- `--dry-run` &nbsp; &nbsp; Add only the test file to the queue for a dry run.
- `--file PATH [PATH ...]` &nbsp; &nbsp; Add one or more specific MKV files to the queue by their full path.

> **Note:** These flags are mutually exclusive—use only one per invocation.

### `worker.py`
- `--force-rerun STEP_INDEX` &nbsp; &nbsp; Force the job to re-run starting from the specified step index (1-8).

---

## Handling Failed and Permanently Failed Jobs

- Each job tracks its retry count. If a job fails more than a set number of times (default: 3), it is marked as `failed_permanent` (permanently failed) and will not be picked up by workers again. This prevents infinite failure loops.
- The job queue and worker scripts are designed to skip jobs with `failed_permanent` status during normal operation.

---

## Admin Override: Forcibly Resetting Jobs

If you wish to re-run a job that has been marked as permanently failed (status `failed_permanent`), you can use the `--force-rerun` flag with `worker.py`:

```bash
python worker.py --force-rerun <STEP_INDEX>
```

- When this flag is used, the job's status and retry count are reset, even if it was previously marked as permanently failed. A message will be printed indicating that an admin override is occurring.
- This allows you to manually retry jobs that were previously locked out, while still protecting against runaway retries in normal unattended operation.

---

### Step 1: Scan for Media and Populate the Queue

On one of your worker VMs, run the `scanner.py` script. This will connect to the Unraid share, find `.mkv` files, and add them to `job_queue.json`.

**For a test run with a single file:**

```bash
python3 scanner.py --dry-run
```

**To scan your entire media library:**

```bash
python3 scanner.py --full-scan
```

### Step 2: Start the Workers

On each of your transcoder VMs, navigate to the project directory and start the worker script.

```bash
python3 worker.py
```

The worker will start polling the job queue. Once it finds a `pending` job, it will claim it, perform the transcoding, and update the job status. The process will repeat indefinitely, allowing you to add more jobs to the queue at any time.
