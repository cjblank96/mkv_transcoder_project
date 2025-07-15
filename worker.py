# worker.py

import time
import socket
import os
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
            
            # Create a unique temporary directory for this job to avoid conflicts
            job_basename = os.path.splitext(os.path.basename(job['input_path']))[0]
            temp_dir = os.path.join(config.TEMP_DIR_BASE, f"{job_basename}_{int(time.time())}")
            os.makedirs(temp_dir, exist_ok=True)

            transcoder = None  # Ensure transcoder is defined before the try block
            try:
                transcoder = Transcoder(job, temp_dir)
                final_output_path = transcoder.run_pipeline()
                job_queue.update_job_status(job['input_path'], 'done', final_output_path)
                print(f"Finished job: {job['input_path']}")
            except Exception as e:
                print(f"Failed job: {job['input_path']}. Error: {e}")
                job_queue.update_job_status(job['input_path'], 'failed')
            finally:
                # Always clean up the temporary directory
                if transcoder:
                    transcoder.cleanup()

        else:
            # No pending jobs, wait before polling again
            time.sleep(10)

if __name__ == "__main__":
    main()
