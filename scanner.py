# scanner.py

import os
import argparse
import sys

# Add project root to the Python path to allow importing 'mkv_transcoder'
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)

from mkv_transcoder.job_queue import JobQueue
from mkv_transcoder import config

def scan_and_add_jobs(directory_path, job_queue):
    """
    Scans a directory for .mkv files and adds them to the job queue if not already present.
    """
    print(f"Scanning directory: {directory_path}")
    if not os.path.isdir(directory_path):
        print(f"Error: Directory not found or not accessible: {directory_path}")
        return

    # Get all file paths that are already in the queue to avoid duplicates
    existing_paths = job_queue.get_all_file_paths()
    added_count = 0

    for root, _, files in os.walk(directory_path):
        for file in files:
            if file.lower().endswith('.mkv') and '_DV_P8' not in file:
                full_path = os.path.join(root, file)
                if full_path not in existing_paths:
                    job_queue.add_job(full_path)
                    print(f"Added job for: {full_path}")
                    added_count += 1
    
    print(f"Scan complete. Added {added_count} new jobs.")

def add_test_file(job_queue):
    """
    Adds the specific test file to the queue if it's not already there.
    """
    print(f"Attempting to add test file: {config.TEST_FILE_PATH}")
    if job_queue.add_job(config.TEST_FILE_PATH):
        print("Successfully added test file job.")
    else:
        print("Test file job already exists in the queue. No action taken.")

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
        scan_and_add_jobs(config.VIDEO_LANDING_POINT, job_queue)

if __name__ == "__main__":
    main()
