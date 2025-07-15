# mkv_transcoder/transcoder.py

import subprocess
import os
import logging

import shutil

class Transcoder:
    def cleanup(self):
        """Removes the temporary directories and all their contents."""
        for d in [self.temp_dir, self.ram_temp_dir]:
            if d and os.path.isdir(d):
                self.logger.info(f"Cleaning up temporary directory: {d}")
                try:
                    shutil.rmtree(d)
                    self.logger.info(f"Directory {d} cleaned up successfully.")
                except OSError as e:
                    self.logger.error(f"Error cleaning up temp directory {d}: {e}")
    """Handles the execution of the transcoding pipeline."""

    def __init__(self, job, temp_dir):
        from . import config  # Import here to avoid circular dependency issues
        self.job = job
        self.temp_dir = temp_dir  # Persistent temp dir for large files
        self.ram_temp_dir = None   # RAM-based temp dir for small files
        self.input_path = job['input_path']
        self.base_name = os.path.splitext(os.path.basename(self.input_path))[0]
        # Define a log path within the temp directory for this job's logs
        self.log_path = os.path.join(self.temp_dir, f"{self.base_name}_transcode.log")

        # Create a unique subdirectory in the RAM disk for this job
        if os.path.isdir(config.RAM_TEMP_DIR):
            # Use the same unique name as the persistent temp dir for consistency
            self.ram_temp_dir = os.path.join(config.RAM_TEMP_DIR, os.path.basename(self.temp_dir))
            os.makedirs(self.ram_temp_dir, exist_ok=True)
        else:
            # Fallback to the persistent temp dir if RAM disk is not available
            self.ram_temp_dir = self.temp_dir

        # Ensure the log directory exists
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)

        # Set up logging for this specific job
        self.logger = logging.getLogger(f"transcoder_{self.base_name}")
        self.logger.setLevel(logging.INFO)
        
        # Avoid adding handlers multiple times if the logger is reused
        if not self.logger.handlers:
            # File handler for the job-specific log
            fh = logging.FileHandler(self.log_path)
            fh.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
            self.logger.addHandler(fh)

            # Stream handler to also print to console
            sh = logging.StreamHandler()
            sh.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
            self.logger.addHandler(sh)

    def _run_command(self, command, shell=False):
        """Runs a command and logs its output."""
        # The command to log is always the string representation
        cmd_for_logging = command if shell else ' '.join(command)
        self.logger.info(f"Executing: {cmd_for_logging}")

        # The command for execution is a list for shell=False, and a string for shell=True
        cmd_for_execution = command

        try:
            process = subprocess.run(
                cmd_for_execution,
                capture_output=True,
                text=True,
                shell=shell,
                check=True  # Raise CalledProcessError on non-zero exit codes
            )
            if process.stdout:
                self.logger.info(process.stdout)
            if process.stderr:
                self.logger.warning(process.stderr)
            return process
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Error running command: {cmd_for_logging}")
            self.logger.error(f"Return Code: {e.returncode}")
            self.logger.error(f"Stdout: {e.stdout}")
            self.logger.error(f"Stderr: {e.stderr}")
            raise RuntimeError(f"Command failed: {cmd_for_logging}") from e
        except FileNotFoundError as e:
            self.logger.error(f"Command not found: {e.filename}. Please ensure it is installed and in your PATH.")
            raise RuntimeError(f"Command not found: {e.filename}") from e

    def run_pipeline(self):
        """Executes the full transcoding pipeline."""
        from . import config
        self.logger.info(f"Starting transcoding for {self.input_path}")
        
        try:
            # Define intermediate file paths
            p7_hevc = os.path.join(self.temp_dir, f"{self.base_name}_p7.hevc")
            p81_hevc = os.path.join(self.temp_dir, f"{self.base_name}_p81.hevc")
            rpu_bin = os.path.join(self.ram_temp_dir, "rpu_81.bin")  # Use RAM disk
            final_video_hevc = os.path.join(self.temp_dir, f"{self.base_name}_final_video.hevc")
            dolby_hevc = os.path.join(self.temp_dir, f"{self.base_name}_dolby.hevc")
            # The final file will be placed alongside the original file.
            output_dir = os.path.dirname(self.input_path)
            final_output_path = os.path.join(output_dir, f"{self.base_name}_final.mkv")
            chapters_file = os.path.join(self.ram_temp_dir, "chapters.txt")  # Use RAM disk

            # Step 1: Extract HEVC Stream
            self.logger.info("Step 1: Extracting HEVC stream...")
            cmd1 = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", self.input_path, "-map", "0:v:0", "-c", "copy", p7_hevc]
            self._run_command(cmd1)

            # Step 2: Convert to Profile 8.1
            self.logger.info("Step 2: Converting to Profile 8.1...")
            cmd2_str = f"ffmpeg -hide_banner -loglevel error -i {p7_hevc} -c:v copy -bsf:v hevc_mp4toannexb -f hevc - | dovi_tool -m 2 convert --discard - -o {p81_hevc}"
            self._run_command(cmd2_str, shell=True)

            # Step 3: Extract RPU
            self.logger.info("Step 3: Extracting RPU...")
            cmd3 = ["dovi_tool", "extract-rpu", p81_hevc, "-o", rpu_bin]
            self._run_command(cmd3)

            # Step 4: Re-encode Video
            self.logger.info("Step 4: Re-encoding video...")
            cmd4 = [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", p81_hevc, "-an", "-sn", "-dn",
                "-c:v", "libx265", "-preset", "slow", "-crf", "20.5", "-tune", "fastdecode",
                "-g", "240", "-keyint_min", "24", "-x265-params", "pools=1:wpp=1",
                final_video_hevc
            ]
            self._run_command(cmd4)

            # Step 5: Inject Profile 5 RPU
            self.logger.info("Step 5: Injecting Profile 5 RPU...")
            cmd5 = ["dovi_tool", "inject-rpu", "-i", final_video_hevc, "--rpu-in", rpu_bin, "-o", dolby_hevc]
            self._run_command(cmd5)

            # Step 6: Extract Chapters from original file
            self.logger.info("Step 6: Extracting chapters...")
            cmd6 = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", self.input_path, "-f", "ffmetadata", chapters_file]
            self._run_command(cmd6)

            # Step 7: Final Remux with mkvmerge
            self.logger.info("Step 7: Remuxing final MKV with mkvmerge...")
            # This command mirrors the logic from the shell script, taking specific tracks.
            # You may need to adjust --audio-tracks and --subtitle-tracks if they vary.
            cmd7 = [
                "mkvmerge", "-o", final_output_path,
                "--language", "0:eng", "--default-track", "0:yes", dolby_hevc,
                "--language", "0:eng", "--audio-tracks", "1,2,3,4",
                "--subtitle-tracks", "6",
                "--chapters", chapters_file, "--no-video", self.input_path
            ]
            self._run_command(cmd7)

            self.logger.info(f"Successfully transcoded {self.input_path} to {final_output_path}")
            return final_output_path

        except Exception as e:
            self.logger.error(f"Pipeline failed for {self.input_path}. Error: {e}")
            raise
