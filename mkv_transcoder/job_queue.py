# mkv_transcoder/job_queue.py

import json
import os
import time
import uuid
import fcntl
from . import config

class JobQueue:
    def __init__(self, queue_file=config.JOB_QUEUE_PATH):
        self.queue_file = queue_file
        # Ensure the queue file exists and has the correct structure
        if not os.path.exists(self.queue_file):
            os.makedirs(os.path.dirname(self.queue_file), exist_ok=True)
            with open(self.queue_file, 'w') as f:
                json.dump({'jobs': []}, f, indent=4)

    def _execute_with_lock(self, operation):
        """A robust, file-locking wrapper to perform operations on the job queue."""
        try:
            with open(self.queue_file, 'r+') as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                try:
                    # Read the current state
                    data = f.read()
                    if not data:
                        queue_data = {'jobs': []}
                    else:
                        queue_data = json.loads(data)
                    
                    # Legacy support: convert list to dict
                    if isinstance(queue_data, list):
                        queue_data = {'jobs': queue_data}

                    # Perform the requested operation
                    result = operation(queue_data)

                    # Write the modified state back to the file
                    f.seek(0)
                    f.truncate()
                    json.dump(queue_data, f, indent=4)
                    return result
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)
        except (IOError, json.JSONDecodeError) as e:
            print(f"ERROR: Could not access or parse job queue file at {self.queue_file}: {e}")
            # Attempt to reset the file to a clean state
            with open(self.queue_file, 'w') as f:
                json.dump({'jobs': []}, f, indent=4)
            return None

    def add_job(self, input_path):
        """Adds a new job to the queue if it doesn't already exist."""
        def _add_job_op(queue):
            if any(j.get('input_path') == input_path for j in queue['jobs']):
                return False # Job already exists
            
            new_job = {
                'id': str(uuid.uuid4()),
                'input_path': input_path,
                'status': 'pending',
                'worker_id': None,
                'output_path': None,
                'added_at': time.time()
            }
            queue['jobs'].append(new_job)
            return True
        return self._execute_with_lock(_add_job_op)

    def get_next_pending_job(self):
        """Finds the next pending job, marks it as 'running', and returns it."""
        def _get_and_update_op(queue):
            for job in queue['jobs']:
                if job.get('status') == 'pending':
                    job['status'] = 'running'
                    return job
            return None
        return self._execute_with_lock(_get_and_update_op)

    def update_job_status(self, job_id, status, output_path=None):
        """Updates the status and output path of a specific job by its ID."""
        def _update_job_op(queue):
            for job in queue['jobs']:
                if job.get('id') == job_id:
                    job['status'] = status
                    if output_path:
                        job['output_path'] = output_path
                    job['completed_at'] = time.time()
                    return True
            return False
        return self._execute_with_lock(_update_job_op)

    def get_all_file_paths(self):
        """Returns a set of all input_paths currently in the queue."""
        def _get_paths_op(queue):
            return {job.get('input_path') for job in queue.get('jobs', [])}
        return self._execute_with_lock(_get_paths_op)
