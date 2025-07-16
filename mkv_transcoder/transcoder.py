import os
import subprocess
import logging
import shutil
import re
from tqdm import tqdm

from . import config

class Transcoder:
    def __init__(self, job_id, input_path):
        self.job_id = job_id
        self.input_path = input_path
        self.base_filename = os.path.splitext(os.path.basename(input_path))[0]

        # Create unique directories for this job's intermediate files
        self.temp_dir_name = f"{self.base_filename}_{self.job_id}"
        self.staging_dir = os.path.join(config.STAGING_DIR, self.temp_dir_name)
        self.ram_temp_dir = os.path.join(config.RAM_TEMP_DIR, self.temp_dir_name)
        os.makedirs(self.staging_dir, exist_ok=True)
        os.makedirs(self.ram_temp_dir, exist_ok=True)

        # Define paths for intermediate and final files
        self.p7_video_path = os.path.join(self.staging_dir, 'video_p7.hevc')
        self.p8_video_path = os.path.join(self.staging_dir, 'video_p8.hevc')
        self.reencoded_video_path = os.path.join(self.staging_dir, 'video_reencoded.hevc')
        self.final_video_with_rpu_path = os.path.join(self.staging_dir, 'video_final_with_rpu.hevc')

        # Small RPU file goes to the RAM disk for performance
        self.rpu_path = os.path.join(self.ram_temp_dir, 'rpu.bin')

        # Define final output path
        output_filename = f"{self.base_filename}_DV_P8.mkv"
        self.output_path = os.path.join(os.path.dirname(self.input_path), output_filename)

        # Setup logging
        log_dir = os.path.join(config.LOG_DIR, 'transcoding_logs')
        os.makedirs(log_dir, exist_ok=True)
        self.log_file = os.path.join(log_dir, f"{self.base_filename}_{self.job_id}.log")
        self.logger = self._setup_logger()

    def _setup_logger(self):
        logger = logging.getLogger(f"transcoder_{self.job_id}")
        logger.setLevel(logging.INFO)
        
        if not logger.handlers:
            fh = logging.FileHandler(self.log_file)
            formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
            fh.setFormatter(formatter)
            logger.addHandler(fh)
        return logger

    def _run_command(self, command, step_name):
        self.logger.info(f"{step_name}...")
        print(f"- {step_name}...")
        try:
            process = subprocess.run(
                command, check=True, capture_output=True, text=True, encoding='utf-8'
            )
            self.logger.info(f"{step_name} successful.")
            self.logger.debug(f"Stdout: {process.stdout}")
            return True
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Command failed: {' '.join(command)}")
            self.logger.error(f"Exit code: {e.returncode}")
            self.logger.error(f"Stderr: {e.stderr}")
            print(f"- {step_name} failed. Check logs for details.")
            return False

    def _get_total_frames(self, video_file):
        command = [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-count_frames", "-show_entries", "stream=nb_read_frames",
            "-of", "default=nokey=1:noprint_wrappers=1", video_file
        ]
        try:
            result = subprocess.run(command, capture_output=True, text=True, check=True)
            return int(result.stdout.strip())
        except (subprocess.CalledProcessError, ValueError) as e:
            self.logger.error(f"Failed to get total frames for {video_file}: {e}")
            return None

    def _run_ffmpeg_with_progress(self, command, total_frames):
        self.logger.info("Re-encoding HEVC stream with progress...")
        print("- Re-encoding HEVC stream (this may take a while)...")
        process = subprocess.Popen(command, stderr=subprocess.PIPE, universal_newlines=True, encoding='utf-8')
        
        progress_bar = tqdm(total=total_frames, unit='frames', desc="Re-encoding", bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}{postfix}]')
        
        for line in process.stderr:
            match = re.search(r'frame=\s*(\d+)', line)
            if match:
                frames_done = int(match.group(1))
                progress_bar.update(frames_done - progress_bar.n)
        
        progress_bar.close()
        process.wait()
        
        if process.returncode != 0:
            self.logger.error(f"ffmpeg re-encoding failed with exit code {process.returncode}")
            print("\n- Re-encoding failed. Check logs.")
            return False
        
        self.logger.info("Re-encoding successful.")
        print("\n- Re-encoding successful.")
        return True

    def transcode(self):
        print(f"\nStarting job {self.job_id} for: {os.path.basename(self.input_path)}")

        # Step 1: Extract Profile 7 HEVC stream
        cmd1 = ["ffmpeg", "-i", self.input_path, "-map", "0:v:0", "-c", "copy", "-y", self.p7_video_path]
        if not self._run_command(cmd1, "Extracting Profile 7 HEVC stream"):
            return False

        # Step 2: Convert P7 to P8.1
        cmd2 = ["dovi_tool", "-m", "2", "convert", "--discard", "-i", self.p7_video_path, "-o", self.p8_video_path]
        if not self._run_command(cmd2, "Converting P7 to P8.1"):
            return False

        # Step 3: Extract RPU from P8.1 stream
        cmd3 = ["dovi_tool", "extract-rpu", "-i", self.p8_video_path, "-o", self.rpu_path]
        if not self._run_command(cmd3, "Extracting RPU from P8.1 stream"):
            return False

        # Step 4: Re-encode video
        total_frames = self._get_total_frames(self.p8_video_path)
        if not total_frames:
            return False
        cmd4 = [
            "ffmpeg", "-fflags", "+genpts", "-i", self.p8_video_path, "-an", "-sn", "-dn",
            "-c:v", "libx265", "-preset", "medium", "-crf", "18","-threads", "4", "-x265-params", "pools=4", "-y", self.reencoded_video_path
        ]
        if not self._run_ffmpeg_with_progress(cmd4, total_frames):
            return False

        # Step 5: Inject RPU into re-encoded video
        cmd5 = ["dovi_tool", "inject-rpu", "-i", self.reencoded_video_path, "--rpu-in", self.rpu_path, "-o", self.final_video_with_rpu_path]
        if not self._run_command(cmd5, "Injecting RPU into re-encoded video"):
            return False

        # Step 6: Remux final MKV
        cmd6 = [
            "mkvmerge", "-o", self.output_path, 
            "--language", "0:eng", self.final_video_with_rpu_path, 
            "--no-video", self.input_path
        ]
        if not self._run_command(cmd6, "Remuxing final MKV"):
            return False

        print(f"\nSuccessfully transcoded {os.path.basename(self.input_path)} to {self.output_path}")
        return True

    def cleanup(self):
        """Cleans up all temporary files and directories created by the transcoder."""
        self.logger.info(f"Cleaning up staging directory: {self.staging_dir}")
        try:
            if os.path.exists(self.staging_dir):
                shutil.rmtree(self.staging_dir)
        except OSError as e:
            self.logger.error(f"Error removing staging directory {self.staging_dir}: {e}")

        self.logger.info(f"Cleaning up RAM disk directory: {self.ram_temp_dir}")
        try:
            if os.path.exists(self.ram_temp_dir):
                shutil.rmtree(self.ram_temp_dir)
        except OSError as e:
            self.logger.error(f"Error removing RAM temp directory {self.ram_temp_dir}: {e}")
