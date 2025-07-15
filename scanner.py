# scanner.py

import os
import argparse
import sys

# Add project root to the Python path to allow importing 'mkv_transcoder'
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)

from mkv_transcoder.job_queue import JobQueue
from mkv_transcoder import config

def scan_directory(directory_path, job_queue):
    """
    Scans a directory for .mkv files and adds them to the job queue.
    """
    print(f"Scanning directory: {directory_path}")
    # A basic check to see if the path seems accessible.
    if not os.path.isdir(directory_path):
        print(f"Error: Directory not found or not accessible: {directory_path}")
        print("Please ensure the Unraid share is mounted and the path is correct.")
        print("Example mount command for macOS/Linux:")
        print(f"  sudo mount -t cifs {config.UNRAID_BASE_PATH} /mnt/unraid -o username={config.UNRAID_USERNAME},password='<your_password>'")
        return

    added_count = 0
    for root, _, files in os.walk(directory_path):
        for file in files:
            if file.lower().endswith('.mkv'):
                full_path = os.path.join(root, file)
                # Avoid adding already transcoded files back to the queue
                if not full_path.endswith('_final.mkv'):
                    if job_queue.add_job(full_path):
                        print(f"Added job for: {full_path}")
                        added_count += 1
                    else:
                        # This is not an error, just informational
                        pass

    print(f"Scan complete. Added {added_count} new jobs.")

def add_test_file(job_queue):
    """
    Adds the specific test file to the queue for a dry run.
    """
    print(f"Adding test file: {config.TEST_FILE_PATH}")
    if job_queue.add_job(config.TEST_FILE_PATH):
        print("Successfully added test file job.")
    else:
        print("Test file job already exists in the queue.")

def main():
    """
    Main function to parse arguments and run the scanner.
    """
    parser = argparse.ArgumentParser(description="Scan for MKV files and add them to the transcoding queue.")
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help="Add only the test file to the queue for a dry run."
    )
    parser.add_argument(
        '--full-scan',
        action='store_true',
        help=f"Perform a full scan of the media directory ({config.VIDEO_LANDING_POINT})."
    )

    args = parser.parse_args()

    job_queue = JobQueue()

    if not args.dry_run and not args.full_scan:
        parser.print_help()
        print("\nError: Please specify either --dry-run or --full-scan.")
        return

    if args.dry_run:
        add_test_file(job_queue)

    if args.full_scan:
        scan_directory(config.VIDEO_LANDING_POINT, job_queue)

if __name__ == "__main__":
    main()
