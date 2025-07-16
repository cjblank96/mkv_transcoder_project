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
        self.original_input_path = input_path
        self.base_filename = os.path.splitext(os.path.basename(input_path))[0]

        # A single, unique staging directory for all this job's local files
        self.job_staging_dir = os.path.join(config.STAGING_DIR, f"{self.base_filename}_{self.job_id}")
        os.makedirs(self.job_staging_dir, exist_ok=True)

        # Define paths for all files, which will now be local to the staging directory
        self.local_source_path = os.path.join(self.job_staging_dir, os.path.basename(self.original_input_path))
        self.p7_video_path = os.path.join(self.job_staging_dir, 'video_p7.hevc')
        self.p8_video_path = os.path.join(self.job_staging_dir, 'video_p8.hevc')
        self.rpu_path = os.path.join(self.job_staging_dir, 'rpu.bin')
        self.reencoded_video_path = os.path.join(self.job_staging_dir, 'video_reencoded.hevc')
        self.final_video_with_rpu_path = os.path.join(self.job_staging_dir, 'video_final_with_rpu.hevc')
        
        # Define local and final (network) output paths
        output_filename = f"{self.base_filename}_DV_P8.mkv"
        self.local_output_path = os.path.join(self.job_staging_dir, output_filename)
        self.output_path = os.path.join(os.path.dirname(self.original_input_path), output_filename)

        # Setup logging
        log_dir = os.path.join(config.LOG_DIR, 'transcoding_logs')
        os.makedirs(log_dir, exist_ok=True)
        self.log_file = os.path.join(log_dir, f"{self.base_filename}_{self.job_id}.log")
        self.logger = self._setup_logger()
        self._log_initial_paths()

    def _log_initial_paths(self):
        self.logger.info(f"Job {self.job_id} initialized.")
        self.logger.info(f"Original input file: {self.original_input_path}")
        self.logger.info(f"Final output file: {self.output_path}")
        self.logger.info(f"Local job staging directory: {self.job_staging_dir}")
        self._log_disk_space(config.STAGING_DIR, "Local Staging SSD")

    def _log_disk_space(self, path, description):
        try:
            total, used, free = shutil.disk_usage(path)
            self.logger.info(f"Disk space for {description} ({path}):")
            self.logger.info(f"  - Total: {total / (1024**3):.2f} GB")
            self.logger.info(f"  - Used: {used / (1024**3):.2f} GB")
            self.logger.info(f"  - Free: {free / (1024**3):.2f} GB")
        except FileNotFoundError:
            self.logger.error(f"Path not found for disk space check: {path}")
        except Exception as e:
            self.logger.error(f"Could not check disk space for {path}: {e}")

    def _setup_logger(self):
        logger = logging.getLogger(f"transcoder_{self.job_id}")
        logger.setLevel(logging.INFO)
        if not logger.handlers:
            fh = logging.FileHandler(self.log_file)
            formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
            fh.setFormatter(formatter)
            logger.addHandler(fh)
        return logger

    def _copy_with_progress(self, src, dst, description):
        self.logger.info(f"{description}: from {src} to {dst}")
        print(f"- {description}...")
        try:
            file_size = os.path.getsize(src)
            with open(src, 'rb') as fsrc, open(dst, 'wb') as fdst, tqdm(
                total=file_size, unit='B', unit_scale=True, unit_divisor=1024, desc=description, bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]'
            ) as pbar:
                while True:
                    buf = fsrc.read(1024 * 1024)
                    if not buf:
                        break
                    fdst.write(buf)
                    pbar.update(len(buf))
            self.logger.info("Copy successful.")
            print(f"\n- {description} successful.")
            return True
        except Exception as e:
            self.logger.error(f"Failed to copy {src} to {dst}: {e}", exc_info=True)
            print(f"\n- {description} failed. Check logs.")
            return False

    def _move_final_file(self, src, dst):
        self.logger.info(f"Moving final file from {src} to {dst}")
        print(f"- Moving final file to destination...")
        try:
            shutil.move(src, dst)
            self.logger.info("Move successful.")
            print("  Done.")
            return True
        except Exception as e:
            self.logger.error(f"Failed to move {src} to {dst}: {e}", exc_info=True)
            print("  Failed. Check logs.")
            return False

    def _run_command(self, command, step_name):
        self.logger.info(f"{step_name}...")
        self.logger.info(f"Executing command: {' '.join(command)}")
        print(f"- {step_name}...")
        try:
            process = subprocess.run(command, check=True, capture_output=True, text=True, encoding='utf-8')
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
        self.logger.info(f"Getting total frame count from metadata for {video_file}...")
        command = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=nb_frames", "-of", "default=nokey=1:noprint_wrappers=1", video_file]
        self.logger.info(f"Executing command: {' '.join(command)}")
        try:
            result = subprocess.run(command, capture_output=True, text=True, check=True)
            output = result.stdout.strip()
            if output.isdigit() and int(output) > 0:
                total_frames = int(output)
                self.logger.info(f"Successfully found {total_frames} frames from metadata.")
                return total_frames
            else:
                self.logger.warning("nb_frames not found in metadata, falling back to counting frames.")
                command = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries", "stream=nb_read_frames", "-of", "default=nokey=1:noprint_wrappers=1", video_file]
                self.logger.info(f"Executing fallback command: {' '.join(command)}")
                result = subprocess.run(command, capture_output=True, text=True, check=True)
                total_frames = int(result.stdout.strip())
                self.logger.info(f"Successfully counted {total_frames} frames.")
                return total_frames
        except subprocess.CalledProcessError as e:
            self.logger.error(f"ffprobe command failed to get total frames for {video_file}.")
            self.logger.error(f"Exit code: {e.returncode}")
            self.logger.error(f"Stderr: {e.stderr}")
            return None
        except (ValueError, IndexError) as e:
            self.logger.error(f"Failed to parse frame count from ffprobe output: {e}")
            return None

    def _run_ffmpeg_with_progress(self, command, total_frames):
        self.logger.info("Re-encoding HEVC stream with progress...")
        self.logger.info(f"Executing command: {' '.join(command)}")
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

    def _run_dovi_tool_with_progress(self, command, step_name):
        self.logger.info(f"{step_name}...")
        self.logger.info(f"Executing command: {' '.join(command)}")
        print(f"- {step_name}...")
        process = subprocess.Popen(command, stderr=subprocess.PIPE, universal_newlines=True, encoding='utf-8')
        progress_bar = tqdm(total=100, unit='%', desc=step_name, bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]')
        for line in process.stderr:
            if 'INFO' in line and '%' in line:
                try:
                    percentage = int(line.strip().split(' ')[-1].replace('%', ''))
                    progress_bar.update(percentage - progress_bar.n)
                except (ValueError, IndexError):
                    pass
            self.logger.debug(line.strip())
        progress_bar.close()
        process.wait()
        if process.returncode != 0:
            self.logger.error(f"{step_name} failed with exit code {process.returncode}")
            print(f"\n- {step_name} failed. Check logs.")
            return False
        self.logger.info(f"{step_name} successful.")
        print(f"\n- {step_name} successful.")
        return True

    def transcode(self):
        print(f"\nStarting job {self.job_id} for: {os.path.basename(self.original_input_path)}")

        # Step 0: Copy source file to local staging directory
        if not self._copy_with_progress(self.original_input_path, self.local_source_path, "Copying source file locally"):
            return False

        # Step 0.5: Get total frame count from local source file metadata
        total_frames = self._get_total_frames(self.local_source_path)
        if not total_frames:
            return False

        # Step 1: Extract Profile 7 HEVC stream (from local copy)
        cmd1 = ["ffmpeg", "-i", self.local_source_path, "-map", "0:v:0", "-c", "copy", "-y", self.p7_video_path]
        if not self._run_command(cmd1, "Extracting Profile 7 HEVC stream"):
            return False

        # Step 2: Convert P7 to P8.1
        cmd2 = ["dovi_tool", "-m", "2", "convert", "--discard", "-i", self.p7_video_path, "-o", self.p8_video_path]
        if not self._run_dovi_tool_with_progress(cmd2, "Converting P7 to P8.1"):
            return False

        # Step 3: Extract RPU from P8.1 stream
        cmd3 = ["dovi_tool", "extract-rpu", "-i", self.p8_video_path, "-o", self.rpu_path]
        if not self._run_command(cmd3, "Extracting RPU from P8.1 stream"):
            return False

        # Step 4: Re-encode video
        cmd4 = ["ffmpeg", "-fflags", "+genpts", "-i", self.p8_video_path, "-an", "-sn", "-dn", "-c:v", "libx265", "-preset", "medium", "-crf", "18", "-threads", "10", "-x265-params", "pools=4", "-y", self.reencoded_video_path]
        if not self._run_ffmpeg_with_progress(cmd4, total_frames):
            return False

        # Step 5: Inject RPU into re-encoded video
        cmd5 = ["dovi_tool", "inject-rpu", "-i", self.reencoded_video_path, "--rpu-in", self.rpu_path, "-o", self.final_video_with_rpu_path]
        if not self._run_dovi_tool_with_progress(cmd5, "Injecting RPU into re-encoded video"):
            return False

        # Step 6: Remux final MKV (using local source for audio/subs)
        cmd6 = ["mkvmerge", "-o", self.local_output_path, "--language", "0:eng", self.final_video_with_rpu_path, "--no-video", self.local_source_path]
        if not self._run_command(cmd6, "Remuxing final MKV"):
            return False

        # Step 7: Move final file to its destination on the network share
        if not self._move_final_file(self.local_output_path, self.output_path):
            return False

        print(f"\nSuccessfully transcoded {os.path.basename(self.original_input_path)} to {self.output_path}")
        return True

    def cleanup(self):
        """Cleans up the entire local job staging directory."""
        self.logger.info(f"Cleaning up job staging directory: {self.job_staging_dir}")
        try:
            if os.path.exists(self.job_staging_dir):
                shutil.rmtree(self.job_staging_dir)
        except OSError as e:
            self.logger.error(f"Error removing staging directory {self.job_staging_dir}: {e}")
