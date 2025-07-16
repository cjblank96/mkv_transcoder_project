# mkv_transcoder/job_queue.py

import json
import os
import fcntl
import time
import uuid
from . import config

class JobQueue:
    def __init__(self, queue_file=config.JOB_QUEUE_PATH):
        self.queue_file = queue_file
        # Create the queue file with an empty list if it doesn't exist
        if not os.path.exists(self.queue_file):
            # Also create parent directory if it doesn't exist
            os.makedirs(os.path.dirname(self.queue_file), exist_ok=True)
            with open(self.queue_file, 'w') as f:
                json.dump([], f)

    def _execute_with_lock(self, operation):
        try:
            with open(self.queue_file, 'r+') as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                try:
                    data = f.read()
                    queue = json.loads(data) if data else []
                    result = operation(queue)
                    f.seek(0)
                    f.truncate()
                    json.dump(queue, f, indent=4)
                    return result
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)
        except (IOError, json.JSONDecodeError) as e:
            print(f"Error accessing queue file {self.queue_file}: {e}")
            # In case of corruption, reset the file
            with open(self.queue_file, 'w') as f:
                json.dump([], f)
            return None

    def add_job(self, input_path):
        def _add_job_op(queue):
            if any(job['input_path'] == input_path for job in queue):
                print(f"Job for {input_path} already exists. Skipping.")
                return None
            
            new_job = {
                'id': str(uuid.uuid4()),
                'input_path': input_path,
                'status': 'pending',
                'worker_id': None,
                'output_path': None,
                'added_at': time.time()
            }
            queue.append(new_job)
            print(f"Added job for {input_path}")
            return new_job
        
        return self._execute_with_lock(_add_job_op)

    def get_next_job(self, worker_id):
        def _get_next_job_op(queue):
            for job in queue:
                if job['status'] == 'pending':
                    job['status'] = 'claimed'
                    job['worker_id'] = worker_id
                    job['claimed_at'] = time.time()
                    return job
            return None
        
        return self._execute_with_lock(_get_next_job_op)

    def update_job_status(self, job_id, status, output_path=None):
        def _update_job_op(queue):
            for job in queue:
                if job['id'] == job_id:
                    job['status'] = status
                    if output_path:
                        job['output_path'] = output_path
                    job['completed_at'] = time.time()
                    return job
            print(f"Warning: Could not find job ID {job_id} to update status.")
            return None

        return self._execute_with_lock(_update_job_op)
