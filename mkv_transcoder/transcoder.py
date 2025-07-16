import os
import subprocess
import logging
import shutil
import re
import pty
import select
from tqdm import tqdm

from . import config

class Transcoder:
    def __init__(self, job, job_queue):
        self.job = job
        self.job_queue = job_queue
        self.job_id = job['id']
        self.original_input_path = job['input_path']
        self.base_filename = os.path.splitext(os.path.basename(self.original_input_path))[0]

        self.job_staging_dir = os.path.join(config.STAGING_DIR, self.job_id)
        os.makedirs(self.job_staging_dir, exist_ok=True)

        self.local_source_path = os.path.join(self.job_staging_dir, os.path.basename(self.original_input_path))

        self.p7_video_path = os.path.join(self.job_staging_dir, 'video_p7.hevc')
        self.p8_video_path = os.path.join(self.job_staging_dir, 'video_p8.hevc')
        self.rpu_path = os.path.join(self.job_staging_dir, 'rpu.bin')
        self.reencoded_video_path = os.path.join(self.job_staging_dir, 'video_reencoded.hevc')
        self.final_video_with_rpu_path = os.path.join(self.job_staging_dir, 'video_final_with_rpu.hevc')
        
        output_filename = f"{self.base_filename}_DV_P8.mkv"
        self.local_output_path = os.path.join(self.job_staging_dir, output_filename)
        self.output_path = os.path.join(os.path.dirname(self.original_input_path), output_filename)

        log_dir = os.path.join(config.LOG_DIR, 'transcoding_logs')
        os.makedirs(log_dir, exist_ok=True)
        self.log_file = os.path.join(log_dir, f"{self.job_id}.log")
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

    def _get_video_metadata(self, video_file):
        """Gets total frames and avg frame rate from video file."""
        total_frames = None
        frame_rate = None
        self.logger.info(f"Attempting to get video metadata from {video_file}")
        try:
            command = ["ffprobe", "-v", "quiet", "-show_format", "-show_streams", video_file]
            result = subprocess.run(command, capture_output=True, text=True, check=True)
            frame_rate_str = None
            for line in result.stdout.splitlines():
                if 'NUMBER_OF_FRAMES' in line:
                    parts = line.split('=')
                    if len(parts) == 2 and parts[1].isdigit():
                        total_frames = int(parts[1])
                elif 'avg_frame_rate' in line:
                    parts = line.split('=')
                    if len(parts) == 2:
                        frame_rate_str = parts[1]
            if frame_rate_str:
                num, den = frame_rate_str.split('/')
                if float(den) != 0:
                    frame_rate = float(num) / float(den)
        except (subprocess.CalledProcessError, ValueError, IndexError, ZeroDivisionError) as e:
            self.logger.warning(f"Could not get metadata via ffprobe tags: {e}")

        if not total_frames:
            self.logger.warning("Falling back to manually counting frames.")
            try:
                command = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries", "stream=nb_read_frames", "-of", "default=nokey=1:noprint_wrappers=1", video_file]
                result = subprocess.run(command, capture_output=True, text=True, check=True)
                total_frames = int(result.stdout.strip())
            except (subprocess.CalledProcessError, ValueError) as e:
                self.logger.error(f"Manual frame count failed: {e}")
                return None, None

        if total_frames and frame_rate:
            self.logger.info(f"Found {total_frames} frames at {frame_rate:.3f} fps.")
            return total_frames, frame_rate
        else:
            self.logger.error("Failed to determine total frames or frame rate.")
            return None, None

    def _run_ffmpeg_with_progress(self, command, total_frames, description):
        self.logger.info(f"Executing ffmpeg command: {' '.join(command)}")
        print(f"- {description}...")
        process = subprocess.Popen(command, stderr=subprocess.PIPE, universal_newlines=True, encoding='utf-8')
        pbar = tqdm(total=total_frames, unit='frames', desc=description, bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}{postfix}]')
        for line in process.stderr:
            match = re.search(r'frame=\s*(\d+)', line)
            if match:
                frames_done = int(match.group(1))
                pbar.update(frames_done - pbar.n)
        pbar.close()
        process.wait()
        if process.returncode != 0:
            self.logger.error(f"ffmpeg command failed with exit code {process.returncode}")
            print(f"\n- {description} failed. Check logs.")
            return False
        self.logger.info(f"Successfully completed: {description}")
        return True

    def _run_ffmpeg_copy_with_progress(self, command, total_frames, frame_rate, description):
        self.logger.info(f"Executing ffmpeg stream copy: {' '.join(command)}")
        print(f"- {description}...")
        progress_command = command[:1] + ['-progress', 'pipe:1', '-nostats'] + command[1:]
        process = subprocess.Popen(progress_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, bufsize=1)
        pbar = tqdm(total=total_frames, desc=description, unit="frame")
        last_frame = 0
        for line in iter(process.stdout.readline, ''):
            if 'out_time_us' in line:
                try:
                    out_time_us = int(line.strip().split('=')[1])
                    estimated_frame = int((out_time_us / 1_000_000) * frame_rate)
                    update_amount = estimated_frame - last_frame
                    if update_amount > 0:
                        pbar.update(update_amount)
                        last_frame = estimated_frame
                except (ValueError, IndexError):
                    continue
        if last_frame < total_frames:
            pbar.update(total_frames - last_frame)
        pbar.close()
        process.wait()
        if process.returncode != 0:
            self.logger.error(f"ffmpeg copy failed. Stderr: {process.stderr.read()}")
            print(f"\n- {description} failed. Check logs.")
            return False
        self.logger.info(f"Successfully completed: {description}")
        return True

    def _run_dovi_tool_with_native_progress(self, command, description):
        self.logger.info(f"Executing dovi_tool command: {' '.join(command)}")
        print(f"- {description}...")
        master, slave = pty.openpty()
        process = subprocess.Popen(command, stdout=slave, stderr=slave, close_fds=True, preexec_fn=os.setsid)
        os.close(slave)
        buffer = ""
        try:
            while process.poll() is None:
                r, _, _ = select.select([master], [], [], 0.1)
                if r:
                    try:
                        output = os.read(master, 1024).decode(errors='ignore')
                        if output:
                            print(output, end='', flush=True)
                            buffer += output
                            while '\n' in buffer:
                                line, buffer = buffer.split('\n', 1)
                                self.logger.info(line.strip())
                    except OSError:
                        break
            if buffer.strip():
                self.logger.info(buffer.strip())
        finally:
            os.close(master)

        if process.returncode != 0:
            self.logger.error(f"dovi_tool command failed with exit code {process.returncode}")
            print(f"\n- {description} failed. Check logs for details.")
            return False
        self.logger.info(f"Successfully completed: {description}")
        print()
        return True

    def transcode(self):
        print(f"\nStarting job {self.job_id} for: {os.path.basename(self.original_input_path)}")

        # This wrapper simplifies the check-run-update pattern for each step
        def run_step(step_name, description, file_to_check, function, *args, **kwargs):
            if self.job['steps'].get(step_name) == 'completed' and os.path.exists(file_to_check):
                self.logger.info(f"Step '{step_name}' already completed. Skipping.")
                print(f"- Skipping already completed step: {description}")
                return True
            
            success = function(*args, **kwargs)
            new_status = 'completed' if success else 'failed'
            self.job_queue.update_job_step_status(self.job_id, step_name, new_status)
            return success

        # Step 1: Copy source file locally
        if not run_step('copy_source', 'Copying source file locally', self.local_source_path, self._copy_source_file):
            return False

        # Get metadata (this is required for subsequent steps)
        total_frames, frame_rate = self._get_video_metadata(self.local_source_path)
        if not total_frames or not frame_rate:
            return False

        # Step 2: Extract P7 HEVC stream
        cmd1 = ["ffmpeg", "-i", self.local_source_path, "-map", "0:v:0", "-c", "copy", "-y", self.p7_video_path]
        if not run_step('extract_p7', 'Extracting Profile 7 HEVC stream', self.p7_video_path, self._run_ffmpeg_copy_with_progress, cmd1, total_frames, frame_rate, "Extracting Profile 7 HEVC stream"):
            return False

        # Step 3: Convert P7 to P8.1
        cmd2 = ["dovi_tool", "-m", "2", "convert", "--discard", "-i", self.p7_video_path, "-o", self.p8_video_path]
        if not run_step('convert_p8', 'Converting P7 to P8.1', self.p8_video_path, self._run_dovi_tool_with_native_progress, cmd2, "Converting P7 to P8.1"):
            return False

        # Step 4: Extract RPU from P8.1 stream
        cmd3 = ["dovi_tool", "extract-rpu", "-i", self.p8_video_path, "-o", self.rpu_path]
        if not run_step('extract_rpu', 'Extracting RPU from P8.1 stream', self.rpu_path, self._run_dovi_tool_with_native_progress, cmd3, "Extracting RPU from P8.1 stream"):
            return False

        # Step 5: Re-encode video
        cmd4 = ["ffmpeg", "-fflags", "+genpts", "-i", self.p8_video_path, "-an", "-sn", "-dn", "-c:v", "libx265", "-preset", "medium", "-crf", "18", "-threads", "9", "-x265-params", "pools=9", "-y", self.reencoded_video_path]
        if not run_step('reencode_x265', 'Re-encoding to x265', self.reencoded_video_path, self._run_ffmpeg_with_progress, cmd4, total_frames, "Re-encoding to x265"):
            return False

        # Step 6: Inject RPU into re-encoded video
        cmd5 = ["dovi_tool", "inject-rpu", "-i", self.reencoded_video_path, "--rpu-in", self.rpu_path, "-o", self.final_video_with_rpu_path]
        if not run_step('inject_rpu', 'Injecting RPU into re-encoded video', self.final_video_with_rpu_path, self._run_dovi_tool_with_native_progress, cmd5, "Injecting RPU into re-encoded video"):
            return False

        # Step 7: Remux final MKV
        cmd6 = ["mkvmerge", "-o", self.local_output_path, "--language", "0:eng", self.final_video_with_rpu_path, "--no-video", self.local_source_path]
        if not run_step('remux_final', 'Remuxing final MKV', self.local_output_path, self._run_command, cmd6, "Remuxing final MKV"):
            return False

        # Step 8: Move final file to destination
        if not run_step('move_final', 'Moving final file to destination', self.output_path, self._move_final_file, self.local_output_path, self.output_path):
            return False

        print(f"\nSuccessfully transcoded {os.path.basename(self.original_input_path)} to {self.output_path}")
        return True

    def _copy_source_file(self):
        try:
            source_size = os.path.getsize(self.original_input_path)
            # No need to check for existence here because the run_step wrapper does it
            if not self._copy_with_progress(self.original_input_path, self.local_source_path, "Copying source file locally"):
                return False
        except FileNotFoundError:
            self.logger.error(f"Source file not found: {self.original_input_path}")
            return False
        return True

    def cleanup(self):
        self.logger.info(f"Cleaning up job staging directory: {self.job_staging_dir}")
        try:
            if os.path.exists(self.job_staging_dir):
                shutil.rmtree(self.job_staging_dir)
        except OSError as e:
            self.logger.error(f"Error removing staging directory {self.job_staging_dir}: {e}")
