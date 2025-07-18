# worker.py

import logging
import socket
import time
import sys
import os
import argparse

# Add project root to the Python path
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)

from mkv_transcoder.job_queue import JobQueue
from mkv_transcoder.transcoder import Transcoder

# Basic logging configuration for the worker itself
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def main():
    parser = argparse.ArgumentParser(description="MKV Transcoder Worker")
    parser.add_argument('--force-rerun', type=int, metavar='STEP_INDEX', help='Force the job to re-run starting from the specified step index (1-8).')
    parser.add_argument('--job-id', type=str, help='(Optional) Job ID to force re-run. Required if --force-rerun is used.')
    parser.add_argument('--input-path', type=str, help='(Optional) Input path to force re-run. Required if --force-rerun is used and --job-id is not given.')
    args = parser.parse_args()

    worker_id = socket.gethostname()
    job_queue = JobQueue()
    logging.info(f"Worker '{worker_id}' started. Polling for jobs...")

    if args.force_rerun:
        if not args.job_id and not args.input_path:
            logging.error("--force-rerun requires either --job-id or --input-path to specify which job to reset.")
            sys.exit(1)

        # Try to forcibly reset the specified job
        reset_result = job_queue.force_reset_job_progress(
            job_id=args.job_id,
            input_path=args.input_path,
            from_step_index=args.force_rerun
        )
        if not reset_result:
            logging.error("No job found to force reset with given criteria.")
            sys.exit(1)
        logging.warning(f"--force-rerun flag detected. Forcibly resetting job (ID: {args.job_id}, Input: {args.input_path}) to start from step {args.force_rerun}.")

        # Claim and process ONLY the specified job
        def _find_job(queue):
            for job in queue['jobs']:
                if (args.job_id and job.get('id') == args.job_id) or (args.input_path and job.get('input_path') == args.input_path):
                    return job
            return None
        target_job = job_queue._execute_with_lock(_find_job)
        if not target_job:
            logging.error("Could not find job to process after reset.")
            sys.exit(1)
        # Set worker_id and claimed_at
        target_job['worker_id'] = worker_id
        target_job['claimed_at'] = time.time()
        # Save
        def _update_job(queue):
            for job in queue['jobs']:
                if job['id'] == target_job['id']:
                    job.update(target_job)
                    return True
            return False
        job_queue._execute_with_lock(_update_job)
        logging.info(f"Worker '{worker_id}' claimed job {target_job['id']} for file: {target_job['input_path']}")
        # Process the job as usual
        try:
            transcoder = Transcoder(target_job, worker_id)
            transcoder.run()
        except Exception as e:
            logging.error(f"Job {target_job['id']} failed during transcoding.")
        sys.exit(0)

    while True:
        job = job_queue.claim_next_available_job(worker_id)

        if not job:
            logging.info("No pending jobs found in the queue. Worker is shutting down.")
            break # Exit the loop if no jobs are available

        logging.info(f"Worker '{worker_id}' claimed job {job['id']} for file: {job['input_path']}")

        # If --force-rerun is used, reset the job progress before transcoding
        if args.force_rerun and not rerun_flag_applied:
            logging.warning(f"--force-rerun flag detected. Resetting job {job['id']} to start from step {args.force_rerun}.")
            job_queue.reset_job_progress(job['id'], args.force_rerun)
            # The job object is now stale, so we must refetch it to get the updated step statuses.
            # We can re-claim it, which is safe because we already have it marked as 'running'.
            job = job_queue.claim_next_available_job(worker_id)
            rerun_flag_applied = True

        transcoder = None
        success = False
        if job is None:
            logging.warning("No available job to process (job is None). Skipping processing.")
        else:
            try:
                transcoder = Transcoder(job=job, job_queue=job_queue)
                success = transcoder.transcode()

                if success:
                    logging.info(f"Job {job['id']} completed successfully.")
                    job_queue.update_job_status(job['id'], 'done', transcoder.output_path)
                else:
                    logging.error(f"Job {job['id']} failed during transcoding.")
                    job_queue.update_job_status(job['id'], 'failed')

            except BaseException as e:
                # Catching BaseException to handle KeyboardInterrupt and other critical errors
                job_id = job['id'] if job else 'UNKNOWN'
                if isinstance(e, KeyboardInterrupt):
                    logging.warning(f"Keyboard interrupt received. Marking job {job_id} as failed and exiting.")
                else:
                    logging.error(f"A critical error occurred while processing job {job_id}: {e}", exc_info=True)
                
                if job:
                    job_queue.update_job_status(job_id, 'failed')
                
                # Re-raise the exception to ensure the worker process terminates
                raise
            finally:
                # Cleanup only on success to allow for faster retries of failed jobs
                if transcoder and success:
                    transcoder.cleanup()

    logging.info(f"Worker '{worker_id}' finished all jobs and is now exiting.")

if __name__ == "__main__":
    main()
