# worker.py

import logging
import os
import socket
import time
from mkv_transcoder.job_queue import JobQueue
from mkv_transcoder.transcoder import Transcoder
from mkv_transcoder import config

def main():
    worker_id = socket.gethostname()
    job_queue = JobQueue()
    print(f"Worker '{worker_id}' started. Polling for jobs...")

    while True:
        job = job_queue.get_next_job(worker_id)
        if job:
            print(f"Claimed job: {job['input_path']}")
            
            transcoder = Transcoder(job_id=job['id'], input_path=job['input_path'])
            try:
                success = transcoder.transcode()
                if success:
                    job_queue.update_job_status(job['id'], 'done', transcoder.final_output_file)
                    logging.info(f"Worker {worker_id} completed job {job['id']}.")
                else:
                    job_queue.update_job_status(job['id'], 'failed')
                    logging.error(f"Worker {worker_id} failed job {job['id']}. See transcoder log for details.")
            except Exception as e:
                logging.error(f"Worker {worker_id} CRASHED on job {job['id']}: {e}", exc_info=True)
                job_queue.update_job_status(job['id'], 'failed')
            finally:
                # Always clean up the temporary directory
                if transcoder:
                    transcoder.cleanup()

        else:
            # No pending jobs, wait before polling again
            time.sleep(10)

if __name__ == "__main__":
    main()
