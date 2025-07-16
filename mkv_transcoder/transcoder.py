import logging
import os
import re
import shutil
import subprocess
from tqdm import tqdm

from . import config

class Transcoder:
    def __init__(self, job_id, input_path):
        self.job_id = job_id
        self.input_path = input_path
        self.base_name = os.path.splitext(os.path.basename(input_path))[0]

        # Persistent temp dir for large files
        self.persistent_temp_dir = os.path.join(config.TEMP_DIR_BASE, f"{self.base_name}_{self.job_id}")
        # RAM-based temp dir for small files
        self.ram_temp_dir = os.path.join(config.RAM_TEMP_DIR, f"{self.base_name}_{self.job_id}")

        self.hevc_file = os.path.join(self.persistent_temp_dir, "track1.hevc")
        self.rpu_file = os.path.join(self.ram_temp_dir, "rpu_81.bin")
        self.chapters_file = os.path.join(self.ram_temp_dir, "chapters.txt")

        self.final_output_dir = os.path.dirname(self.input_path)
        self.final_output_file = os.path.join(self.final_output_dir, f"{self.base_name}_DV_P8.mkv")

        os.makedirs(self.persistent_temp_dir, exist_ok=True)
        os.makedirs(self.ram_temp_dir, exist_ok=True)

        # Set up file-based logging for this specific job
        self.logger = logging.getLogger(f"transcoder_{self.job_id}")
        self.logger.setLevel(logging.INFO)
        
        if not self.logger.handlers:
            log_file = os.path.join(self.persistent_temp_dir, f"{self.base_name}_transcode.log")
            fh = logging.FileHandler(log_file)
            fh.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
            self.logger.addHandler(fh)

    def _run_command(self, command, description):
        print(f"- {description}...")
        try:
            # For simple commands, we don't need to see their output unless there's an error.
            subprocess.run(command, check=True, capture_output=True, text=True)
            print("  Done.")
            return True
        except subprocess.CalledProcessError as e:
            print("  Failed.")
            self.logger.error(f"Failed: {description}")
            self.logger.error(f"Command failed: {' '.join(command)}")
            self.logger.error(f"Exit code: {e.returncode}")
            self.logger.error(f"Stderr: {e.stderr}")
            return False

    def _get_total_frames(self):
        command = [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-count_frames", "-show_entries", "stream=nb_read_frames",
            "-of", "default=nokey=1:noprint_wrappers=1", self.input_path
        ]
        try:
            result = subprocess.run(command, check=True, capture_output=True, text=True)
            return int(result.stdout.strip())
        except (subprocess.CalledProcessError, ValueError) as e:
            self.logger.error(f"Could not get total frames for {self.input_path}: {e}")
            return None

    def _run_ffmpeg_with_progress(self, command, total_frames):
        print(f"\n- Re-encoding video with progress...")
        process = subprocess.Popen(
            command, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.STDOUT, 
            universal_newlines=True, 
            text=True,
            errors='ignore' # Ignore potential decoding errors from ffmpeg progress
        )
        
        pbar = tqdm(total=total_frames, unit=' frames', ncols=80, bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]')
        
        frame_regex = re.compile(r"frame=\s*(\d+)")

        for line in iter(process.stdout.readline, ''):
            self.logger.debug(f"ffmpeg: {line.strip()}")
            match = frame_regex.search(line)
            if match:
                current_frame = int(match.group(1))
                pbar.update(current_frame - pbar.n) # Update to the absolute frame number
        
        pbar.close()
        process.wait()
        
        if process.returncode != 0:
            self.logger.error(f"ffmpeg command failed with exit code {process.returncode}")
            print("  Re-encoding failed. See log for details.")
            return False
        
        print("  Done.")
        return True

    def transcode(self):
        print(f"\nStarting job {self.job_id} for: {os.path.basename(self.input_path)}")

        # Define paths for intermediate files
        p7_hevc_file = os.path.join(self.persistent_temp_dir, "video_p7.hevc")
        p81_hevc_file = os.path.join(self.persistent_temp_dir, "video_p81.hevc")
        rpu_file = os.path.join(self.ram_temp_dir, "rpu.bin")
        reencoded_hevc_file = os.path.join(self.persistent_temp_dir, "video_final.hevc")
        final_video_with_rpu = os.path.join(self.persistent_temp_dir, "video_final_with_rpu.hevc")

        # Step 1: Extract Profile 7 HEVC stream from original MKV
        cmd1 = ["ffmpeg", "-i", self.input_path, "-map", "0:v:0", "-c", "copy", "-y", p7_hevc_file]
        if not self._run_command(cmd1, "Extracting Profile 7 HEVC stream"):
            return False

        # Step 2: Convert Profile 7 to Profile 8.1
        cmd2 = ["dovi_tool", "-m", "2", "convert", "--discard", "-i", p7_hevc_file, "-o", p81_hevc_file]
        if not self._run_command(cmd2, "Converting P7 to P8.1"):
            return False

        # Step 3: Extract RPU from the new Profile 8.1 stream
        cmd3 = ["dovi_tool", "extract-rpu", "-i", p81_hevc_file, "-o", rpu_file]
        if not self._run_command(cmd3, "Extracting RPU from P8.1 stream"):
            return False

        # Step 4: Re-encode the Profile 8.1 video (long step with progress)
        total_frames = self._get_total_frames()
        if not total_frames:
            return False
        cmd4 = [
            "ffmpeg", "-fflags", "+genpts", "-i", p81_hevc_file, "-an", "-sn", "-dn",
            "-c:v", "libx265", "-preset", "medium", "-crf", "18", "-y", reencoded_hevc_file
        ]
        if not self._run_ffmpeg_with_progress(cmd4, total_frames):
            return False

        # Step 5: Inject RPU into the re-encoded video
        cmd5 = ["dovi_tool", "inject-rpu", "-i", reencoded_hevc_file, "--rpu-in", rpu_file, "-o", final_video_with_rpu]
        if not self._run_command(cmd5, "Injecting RPU into final video"):
            return False

        # Step 6: Remux final MKV using mkvmerge
        # Note: This is a simplified mkvmerge command. A robust implementation would
        # dynamically detect audio and subtitle tracks from the source.
        cmd6 = [
            "mkvmerge", "-o", self.final_output_file, "--language", "0:eng", final_video_with_rpu,
            "--no-video", self.input_path
        ]
        if not self._run_command(cmd6, "Remuxing final MKV with mkvmerge"):
            return False

        print(f"\nJob {self.job_id} completed successfully.")
        print(f"Final file located at: {self.final_output_file}")
        return True

    def cleanup(self):
        """Removes the temporary directories and all their contents."""
        for d in [self.persistent_temp_dir, self.ram_temp_dir]:
            if d and os.path.isdir(d):
                self.logger.info(f"Cleaning up temporary directory: {d}")
                try:
                    shutil.rmtree(d)
                except OSError as e:
                    self.logger.error(f"Error cleaning up temp directory {d}: {e}")
