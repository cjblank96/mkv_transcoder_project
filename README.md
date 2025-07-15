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

## How to Run the Pipeline

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
