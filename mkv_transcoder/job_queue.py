# mkv_transcoder/job_queue.py

import json
import fcntl
import time
from datetime import datetime
from . import config

class JobQueue:
    def add_job(self, input_path):
        """Add a new job to the queue if it doesn't already exist."""
        self._lock()
        try:
            try:
                with open(self.queue_path, 'r+') as f:
                    jobs = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                jobs = []

            # Check if a job with this input path already exists
            if any(job['input_path'] == input_path for job in jobs):
                return False # Job already exists

            new_job = {
                'input_path': input_path,
                'status': 'pending',
                'output_path': None,
                'assigned_to': None,
                'created_at': datetime.utcnow().isoformat() + 'Z',
                'updated_at': datetime.utcnow().isoformat() + 'Z',
            }
            jobs.append(new_job)

            with open(self.queue_path, 'w') as f:
                json.dump(jobs, f, indent=2)
            
            return True # Job was added
        finally:
            self._unlock()
    """Manages the job queue with file-based locking."""

    def __init__(self, queue_path=config.JOB_QUEUE_PATH, lock_path=config.LOCK_FILE_PATH):
        self.queue_path = queue_path
        self.lock_path = lock_path

    def _lock(self):
        """Acquire an exclusive lock."""
        self.lockfile = open(self.lock_path, 'w')
        fcntl.flock(self.lockfile, fcntl.LOCK_EX)

    def _unlock(self):
        """Release the lock."""
        fcntl.flock(self.lockfile, fcntl.LOCK_UN)
        self.lockfile.close()

    def get_next_job(self, worker_id):
        """Find and claim the next available job."""
        self._lock()
        try:
            with open(self.queue_path, 'r+') as f:
                jobs = json.load(f)
                for job in jobs:
                    if job['status'] == 'pending':
                        job['status'] = 'in_progress'
                        job['assigned_to'] = worker_id
                        job['updated_at'] = datetime.utcnow().isoformat() + 'Z'
                        
                        # Rewind and write the updated jobs list back to the file
                        f.seek(0)
                        json.dump(jobs, f, indent=2)
                        f.truncate()
                        return job
            return None
        finally:
            self._unlock()

    def update_job_status(self, input_path, status, output_path=None):
        """Update the status of a job."""
        self._lock()
        try:
            with open(self.queue_path, 'r+') as f:
                jobs = json.load(f)
                for job in jobs:
                    if job['input_path'] == input_path:
                        job['status'] = status
                        job['updated_at'] = datetime.utcnow().isoformat() + 'Z'
                        if output_path:
                            job['output_path'] = output_path
                        
                        f.seek(0)
                        json.dump(jobs, f, indent=2)
                        f.truncate()
                        break
        finally:
            self._unlock()
