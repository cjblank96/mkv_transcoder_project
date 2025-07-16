# worker.py

import logging
import socket
import time
import sys
import os

# Add project root to the Python path
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)

from mkv_transcoder.job_queue import JobQueue
from mkv_transcoder.transcoder import Transcoder

# Basic logging configuration for the worker itself
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def main():
    worker_id = socket.gethostname()
    job_queue = JobQueue()
    logging.info(f"Worker '{worker_id}' started. Polling for jobs...")

    while True:
        job = job_queue.claim_next_available_job(worker_id)

        if not job:
            logging.info("No pending jobs found in the queue. Worker is shutting down.")
            break # Exit the loop if no jobs are available

        logging.info(f"Worker '{worker_id}' claimed job {job['id']} for file: {job['input_path']}")
        transcoder = None
        try:
            transcoder = Transcoder(job_id=job['id'], input_path=job['input_path'])
            success = transcoder.transcode()

            if success:
                logging.info(f"Job {job['id']} completed successfully.")
                job_queue.update_job_status(job['id'], 'done', transcoder.output_path)
            else:
                logging.error(f"Job {job['id']} failed during transcoding.")
                job_queue.update_job_status(job['id'], 'failed')

        except BaseException as e:
            # Catching BaseException to handle KeyboardInterrupt and other critical errors
            if isinstance(e, KeyboardInterrupt):
                logging.warning(f"Keyboard interrupt received. Marking job {job['id']} as failed and exiting.")
            else:
                logging.error(f"A critical error occurred while processing job {job['id']}: {e}", exc_info=True)
            
            if job:
                job_queue.update_job_status(job['id'], 'failed')
            
            # Re-raise the exception to ensure the worker process terminates
            raise
        finally:
            if transcoder:
                transcoder.cleanup()

    logging.info(f"Worker '{worker_id}' finished all jobs and is now exiting.")

if __name__ == "__main__":
    main()
