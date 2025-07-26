import os
import subprocess
import logging
import shutil
import re
import json
import time
import platform
import sys
import threading
from queue import Queue, Empty

from tqdm import tqdm

from . import config


class Transcoder:
    def __init__(self, job, job_queue):
        self.job = job
        self.job_queue = job_queue
        self.job_id = job['id']
        self.original_input_path = job['input_path'].strip('"')
        self.base_filename = os.path.splitext(os.path.basename(self.original_input_path))[0]

        self.job_staging_dir = os.path.join(config.STAGING_DIR, self.job_id)
        os.makedirs(self.job_staging_dir, exist_ok=True)

        self.local_source_path = os.path.join(self.job_staging_dir, os.path.basename(self.original_input_path))

        self.p7_video_path = os.path.join(self.job_staging_dir, 'video_p7.hevc')
        self.p8_video_path = os.path.join(self.job_staging_dir, 'video_p8.hevc')
        self.rpu_path = os.path.join(self.job_staging_dir, 'rpu.bin')
        self.reencoded_video_path = os.path.join(self.job_staging_dir, 'video_reencoded.hevc')
        self.final_video_with_rpu_path = os.path.join(self.job_staging_dir, 'video_final_with_rpu.hevc')

        self.total_frames = None
        self.frame_rate = None
        
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
            fh = logging.FileHandler(self.log_file, mode='a')  # APPEND mode
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
            process = subprocess.Popen(' '.join(command), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', shell=True)

            # Thread to consume stdout to prevent blocking
            def consume_stdout():
                with process.stdout:
                    for line in iter(process.stdout.readline, ''):
                        self.logger.debug(f"STDOUT: {line.strip()}")

            stdout_thread = threading.Thread(target=consume_stdout)
            stdout_thread.start()

            # Read stderr line by line and log/print it in real-time
            with process.stderr:
                for line in iter(process.stderr.readline, ''):
                    line = line.strip()
                    if line:
                        self.logger.error(line)
                        print(line, file=sys.stderr)

            stdout_thread.join()
            process.wait()

            if process.returncode != 0:
                self.logger.error(f"{step_name} failed with exit code {process.returncode}.")
                print(f"- {step_name} failed. Check logs for details.")
                return False

            self.logger.info(f"{step_name} successful.")
            print(f"- {step_name} successful.")
            return True
        except Exception as e:
            self.logger.error(f"An unexpected error occurred while running command: {' '.join(command)}", exc_info=True)
            print(f"- {step_name} failed with an unexpected error. Check logs.")
            return False

    def _get_video_metadata(self, video_path):
        """Gets video metadata using specific ffprobe commands and stores it in instance variables."""
        self.logger.info(f"Attempting to get video metadata from {video_path}")
        total_frames = None
        frame_rate = None

        # Method 1: Get avg_frame_rate
        try:
            command = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=avg_frame_rate", "-of", "default=noprint_wrappers=1:nokey=1", f'"{video_path}"']
            result = subprocess.run(' '.join(command), capture_output=True, text=True, check=True, shell=True)
            output = result.stdout.strip()
            if '/' in output:
                num, den = map(int, output.split('/'))
                frame_rate = num / den
                self.logger.info(f"Successfully got frame rate: {frame_rate:.3f} fps.")
        except (subprocess.CalledProcessError, ValueError, IndexError) as e:
            self.logger.warning(f"Could not get avg_frame_rate. Error: {e}")
            if isinstance(e, subprocess.CalledProcessError):
                self.logger.warning(f"ffprobe (frame rate) stdout: {e.stdout}")
                self.logger.warning(f"ffprobe (frame rate) stderr: {e.stderr}")

        # Method 2: Get frame count from tags
        for tag in ["NUMBER_OF_FRAMES-eng", "NUMBER_OF_FRAMES"]:
            try:
                command = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", f"stream_tags={tag}", "-of", "default=noprint_wrappers=1:nokey=1", f'"{video_path}"']
                result = subprocess.run(' '.join(command), capture_output=True, text=True, check=True, shell=True)
                output = result.stdout.strip()
                if output.isdigit():
                    total_frames = int(output)
                    self.logger.info(f"Successfully got frame count from tag '{tag}': {total_frames} frames.")
                    break # Exit loop on success
            except (subprocess.CalledProcessError, ValueError, IndexError) as e:
                self.logger.warning(f"Could not get frame count from tag '{tag}'. Error: {e}")
                if isinstance(e, subprocess.CalledProcessError):
                    self.logger.warning(f"ffprobe (tag: {tag}) stdout: {e.stdout}")
                    self.logger.warning(f"ffprobe (tag: {tag}) stderr: {e.stderr}")
        
        # Method 3: Fallback to counting frames manually if tags fail
        if not total_frames:
            self.logger.warning("Frame count tags failed. Falling back to manual frame counting.")
            try:
                command = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries", "stream=nb_read_frames", "-of", "default=noprint_wrappers=1:nokey=1", f'"{video_path}"']
                result = subprocess.run(' '.join(command), capture_output=True, text=True, check=True, shell=True)
                output = result.stdout.strip()
                if output.isdigit():
                    total_frames = int(output)
                    self.logger.info(f"Successfully counted frames manually: {total_frames} frames.")
            except (subprocess.CalledProcessError, ValueError, IndexError) as e:
                self.logger.error(f"Manual frame count failed. Error: {e}")
                if isinstance(e, subprocess.CalledProcessError):
                    self.logger.error(f"ffprobe (manual count) stdout: {e.stdout}")
                    self.logger.error(f"ffprobe (manual count) stderr: {e.stderr}")

        if total_frames and frame_rate:
            self.total_frames = total_frames
            self.frame_rate = frame_rate
            return True
        else:
            self.logger.error("Failed to determine total frames or frame rate after all methods.")
            return False

    def _run_ffmpeg_with_progress(self, command, total_frames, description):
        self.logger.info(f"Executing ffmpeg command: {' '.join(command)}")
        print(f"- {description}...")
        process = subprocess.Popen(' '.join(command), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', shell=True)
        pbar = tqdm(total=total_frames, unit='frames', desc=description, bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}{postfix}]')

        # Consume stdout in a thread to prevent blocking
        def consume_stdout():
            with process.stdout:
                for line in iter(process.stdout.readline, ''):
                    self.logger.debug(f"STDOUT: {line.strip()}")
        stdout_thread = threading.Thread(target=consume_stdout)
        stdout_thread.start()

        # Read stderr for progress and errors
        with process.stderr:
            for line in iter(process.stderr.readline, ''):
                line = line.strip()
                if not line:
                    continue
                match = re.search(r'frame=\s*(\d+)', line)
                if match:
                    frames_done = int(match.group(1))
                    pbar.update(frames_done - pbar.n)
                else:
                    # If it's not a progress line, log it as an error
                    self.logger.error(line)
                    print(line, file=sys.stderr)

        pbar.close()
        stdout_thread.join()
        process.wait()

        if process.returncode != 0:
            self.logger.error(f"{description} failed with exit code {process.returncode}.")
            print(f"\n- {description} failed. Check logs.")
            return False

        self.logger.info(f"Successfully completed: {description}")
        print(f"- {description} successful.")
        return True

    def _run_ffmpeg_copy_with_progress(self, command, total_frames, frame_rate, description):
        self.logger.info(f"Executing ffmpeg stream copy: {' '.join(command)}")
        print(f"- {description}...")
        progress_command = command[:1] + ['-progress', 'pipe:1', '-nostats'] + command[1:]

        process = subprocess.Popen(
            ' '.join(progress_command),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            bufsize=1,
            encoding='utf-8',
            shell=True
        )

        def _enqueue_output(pipe, queue):
            for line in iter(pipe.readline, ''):
                queue.put(line)
            pipe.close()

        stdout_queue = Queue()
        stderr_queue = Queue()

        stdout_thread = threading.Thread(target=_enqueue_output, args=(process.stdout, stdout_queue))
        stderr_thread = threading.Thread(target=_enqueue_output, args=(process.stderr, stderr_queue))

        stdout_thread.daemon = True
        stderr_thread.daemon = True
        stdout_thread.start()
        stderr_thread.start()

        pbar = tqdm(total=total_frames, desc=description, unit="frame")
        last_frame = 0
        error_lines = []

        while process.poll() is None:
            try:
                while True:
                    line = stdout_queue.get_nowait()
                    if 'frame=' in line:
                        try:
                            parts = line.strip().split('=')
                            if parts[0].strip() == 'frame':
                                frame_num = int(parts[1])
                                update_amount = frame_num - last_frame
                                if update_amount > 0:
                                    pbar.update(update_amount)
                                    last_frame = frame_num
                        except (ValueError, IndexError):
                            continue
            except Empty:
                pass

            try:
                while True:
                    line = stderr_queue.get_nowait()
                    error_lines.append(line)
            except Empty:
                pass
            
            time.sleep(0.1)

        stdout_thread.join()
        stderr_thread.join()

        pbar.close()

        if process.returncode != 0:
            # Capture any final output from stderr
            while not stderr_queue.empty():
                error_lines.append(stderr_queue.get_nowait())

            self.logger.error(f"ffmpeg copy failed with exit code {process.returncode} during '{description}'.")
            filtered_errors = [line for line in error_lines if re.search(r'(error|invalid|no such file|failed)', line, re.IGNORECASE)]
            if filtered_errors:
                self.logger.error(f"Filtered ffmpeg error output:\n{''.join(filtered_errors)}")
            else:
                self.logger.error(f"Full ffmpeg stderr (no specific error keywords found):\n{''.join(error_lines)}")
            print(f"\n- {description} failed. Check logs.")
            return False

        self.logger.info(f"Successfully completed: {description}")
        return True

    def _get_main_video_stream_index(self, video_path):
        """
        Uses ffprobe to find the index of the main video stream.
        The main video stream is determined as the one that is not an attached picture.
        """
        self.logger.info(f"Probing for main video stream in: {video_path}")
        command = [
            "ffprobe",
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=index,codec_type:stream_tags=attached_pic",
            "-of", "json",
            f'"{video_path}"'
        ]
        try:
            result = subprocess.run(' '.join(command), capture_output=True, text=True, check=True, encoding='utf-8', shell=True)
            data = json.loads(result.stdout)
            
            video_streams = [s for s in data.get('streams', []) if s.get('codec_type') == 'video']
            
            # Filter out attached pictures (cover art)
            main_streams = [s for s in video_streams if s.get('tags', {}).get('attached_pic', '0') == '0']

            if not main_streams:
                self.logger.error("No suitable video stream found (all might be attached pictures).")
                # Fallback to first video stream if none are explicitly main
                if video_streams:
                    stream_index = video_streams[0]['index']
                    self.logger.warning(f"Could not find a definitive main stream, falling back to first video stream at index: {stream_index}")
                    return stream_index
                return None

            stream_index = main_streams[0]['index']
            self.logger.info(f"Found main video stream at index: {stream_index}")
            return stream_index

        except (subprocess.CalledProcessError, json.JSONDecodeError, IndexError) as e:
            self.logger.error(f"Failed to get main video stream index for {video_path}: {e}", exc_info=True)
            return None

    def _run_dovi_tool_with_progress(self, command, description):
        # This function can now use the general-purpose _run_command
        return self._run_command(command, description)

    def transcode(self):
        self.logger.info(f"=== Entering transcode() for job {self.job_id} ===")
        import json
        try:
            self.logger.info(f"Job dict: {json.dumps(self.job, indent=2)}")
        except Exception as e:
            self.logger.warning(f"Could not serialize job dict: {e}")
        # Explicitly check input file existence
        if not os.path.exists(self.original_input_path):
            self.logger.error(f"Input file does not exist: {self.original_input_path}")
            raise FileNotFoundError(f"Input file does not exist: {self.original_input_path}")
        try:
            import traceback
            job_type = self.job.get('job_type', 'standard') # Default to standard if type is missing
            self.logger.info(f"=== Starting transcode pipeline for job {self.job_id} ===")
            self.logger.info(f"Job dict: {self.job}")
            print(f"\nStarting job {self.job_id} for: {os.path.basename(self.original_input_path)} (Type: {job_type})")

            def run_step(step_name, description, file_to_check, function, *args, **kwargs):
                if step_name not in self.job['steps']:
                    return True
                if self.job['steps'].get(step_name) == 'completed' and os.path.exists(file_to_check):
                    self.logger.info(f"Step '{step_name}' already completed. Skipping.")
                    print(f"- Skipping already completed step: {description}")
                    return True
                try:
                    self.logger.info(f"Starting step: {step_name} - {description}")
                    success = function(*args, **kwargs)
                except Exception as e:
                    self.logger.error(f"Exception in step '{step_name}': {e}", exc_info=True)
                    print(f"- Step '{step_name}' failed with exception. Check logs.")
                    self.job_queue.update_job_step_status(self.job_id, step_name, 'failed')
                    return False
                new_status = 'completed' if success else 'failed'
                if not success:
                    self.logger.error(f"Step '{step_name}' failed (no exception). Check previous log entries for details.")
                self.job_queue.update_job_step_status(self.job_id, step_name, new_status)
                return success

            try:
                # --- Universal Steps --- 
                if not run_step('copy_source', 'Copying source file locally', self.local_source_path, self._copy_source_file):
                    self.logger.error("transcode() failed during step: copy_source")
                    return False
                if not run_step('get_metadata', 'Getting video metadata', self.log_file, self._get_video_metadata, self.local_source_path):
                    self.logger.error("transcode() failed during step: get_metadata")
                    return False

                # Define output filenames first
                if job_type == 'dolby_vision':
                    output_filename = os.path.basename(self.original_input_path).replace('.mkv', '_DV_P8.mkv')
                else: # standard
                    output_filename = os.path.basename(self.original_input_path).replace('.mkv', '_x265.mkv')
                self.local_output_path = os.path.join(self.job_staging_dir, output_filename)
                self.output_path = os.path.join(os.path.dirname(self.original_input_path), output_filename)

                # Create a single quoted variable for use in commands
                quoted_local_source_path = f'"{self.local_source_path}"'

                # --- Dolby Vision Path --- 
                if job_type == 'dolby_vision':
                    video_stream_index = self._get_main_video_stream_index(self.local_source_path)
                    if video_stream_index is None:
                        self.logger.error("Could not determine video stream index. Aborting job.")
                        return False
                    
                    map_specifier = f"0:v:{video_stream_index}"
                    self.logger.info(f"Using map specifier '{map_specifier}' for ffmpeg.")

                    cmd1 = ["ffmpeg", "-i", quoted_local_source_path, "-map", map_specifier, "-c", "copy", "-y", f'"{self.p7_video_path}"']
                    if not run_step('extract_p7', 'Extracting Profile 7 HEVC stream', self.p7_video_path, self._run_ffmpeg_copy_with_progress, cmd1, self.total_frames, self.frame_rate, "Extracting P7 stream"):
                        self.logger.error("transcode() failed during step: extract_p7")
                        return False

                    cmd2 = ["dovi_tool", "-m", "2", "convert", "--discard", "-i", f'"{self.p7_video_path}"', "-o", f'"{self.p8_video_path}"']
                    if not run_step('convert_p8', 'Converting P7 to P8.1', self.p8_video_path, self._run_dovi_tool_with_progress, cmd2, "Converting to P8.1"):
                        self.logger.error("transcode() failed during step: convert_p8")
                        return False

                    cmd3 = ["dovi_tool", "extract-rpu", "-i", f'"{self.p8_video_path}"', "-o", f'"{self.rpu_path}"']
                    if not run_step('extract_rpu', 'Extracting RPU', self.rpu_path, self._run_dovi_tool_with_progress, cmd3, "Extracting RPU"):
                        self.logger.error("transcode() failed during step: extract_rpu")
                        return False

                    # Re-encode video only
                    cmd4 = ["ffmpeg", "-fflags", "+genpts", "-i", f'"{self.p8_video_path}"', "-an", "-sn", "-dn", "-c:v", "libx265", "-preset", "slow", "-crf", "20.5", "-threads", "10", "-x265-params", "pools=10:no-sao=1:early-skip=1:rd=3:me=hex", "-y", f'"{self.reencoded_video_path}"']
                    if not run_step('reencode_x265', 'Re-encoding to x265', self.reencoded_video_path, self._run_ffmpeg_with_progress, cmd4, self.total_frames, "Re-encoding to x265"):
                        self.logger.error("transcode() failed during step: reencode_x265")
                        return False

                    # Inject RPU
                    cmd5 = ["dovi_tool", "inject-rpu", "-i", f'"{self.reencoded_video_path}"', "--rpu-in", f'"{self.rpu_path}"', "-o", f'"{self.final_video_with_rpu_path}"']
                    if not run_step('inject_rpu', 'Injecting RPU', self.final_video_with_rpu_path, self._run_dovi_tool_with_progress, cmd5, "Injecting RPU"):
                        self.logger.error("transcode() failed during step: inject_rpu")
                        return False

                    # Remux final MKV
                    cmd6 = ["mkvmerge", "-o", f'"{self.local_output_path}"', "--language", "0:eng", f'"{self.final_video_with_rpu_path}"', "--no-video", quoted_local_source_path]
                    if not run_step('remux_final', 'Remuxing final MKV', self.local_output_path, self._run_command, cmd6, "Remuxing final MKV"):
                        self.logger.error("transcode() failed during step: remux_final")

                # --- Standard Path (Optimized) --- 
                else: # 'standard' job type
                    cmd_reencode_mux = [
                        "ffmpeg", "-fflags", "+genpts", "-i", quoted_local_source_path,
                        "-map", "0", "-c:v", "libx265", "-preset", "slow", "-crf", "20.5",
                        "-threads", "10", "-x265-params", "pools=10:no-sao=1:early-skip=1:rd=3:me=hex",
                        "-c:a", "copy", "-c:s", "copy", "-y", f'"{self.local_output_path}"'
                    ]
                    if not run_step('reencode_x265', 'Re-encoding and Muxing', self.local_output_path, self._run_ffmpeg_with_progress, cmd_reencode_mux, self.total_frames, "Re-encoding to x265"):
                        self.logger.error("transcode() failed during step: reencode_x265")
                        return False

                if not run_step('move_final', 'Moving final file to destination', self.output_path, self._move_final_file, self.local_output_path, self.output_path):
                    self.logger.error("transcode() failed during step: move_final")
                    return False

                print(f"\nSuccessfully transcoded {os.path.basename(self.original_input_path)} to {self.output_path}")
                self.logger.info(f"=== transcode() completed successfully for job {self.job_id} ===")
                return True
            except Exception as e:
                self.logger.error(f"UNHANDLED EXCEPTION in job pipeline: {e}", exc_info=True)
                print(f"- Unhandled exception in job pipeline. Check logs for details.")
                self.logger.error(f"=== transcode() failed for job {self.job_id} ===")
                return False
                return False

            # Define output filenames first
            if job_type == 'dolby_vision':
                output_filename = os.path.basename(self.original_input_path).replace('.mkv', '_DV_P8.mkv')
            else: # standard
                output_filename = os.path.basename(self.original_input_path).replace('.mkv', '_x265.mkv')
            self.local_output_path = os.path.join(self.job_staging_dir, output_filename)
            self.output_path = os.path.join(os.path.dirname(self.original_input_path), output_filename)

            # --- Dolby Vision Path --- 
            if job_type == 'dolby_vision':
                video_stream_index = self._get_main_video_stream_index(self.local_source_path)
                if video_stream_index is None:
                    self.logger.error("Could not determine video stream index. Aborting job.")
                    return False
                
                map_specifier = f"0:v:{video_stream_index}"
                self.logger.info(f"Using map specifier '{map_specifier}' for ffmpeg.")

                cmd1 = ["ffmpeg", "-i", f'"{self.local_source_path}"', "-map", map_specifier, "-c", "copy", "-y", f'"{self.p7_video_path}"']
                if not run_step('extract_p7', 'Extracting Profile 7 HEVC stream', self.p7_video_path, self._run_ffmpeg_copy_with_progress, cmd1, self.total_frames, self.frame_rate, "Extracting P7 stream"):
                    self.logger.error("transcode() failed during step: extract_p7")
                    return False

                cmd2 = ["dovi_tool", "-m", "2", "convert", "--discard", "-i", f'"{self.p7_video_path}"', "-o", f'"{self.p8_video_path}"']
                if not run_step('convert_p8', 'Converting P7 to P8.1', self.p8_video_path, self._run_dovi_tool_with_progress, cmd2, "Converting to P8.1"):
                    self.logger.error("transcode() failed during step: convert_p8")
                    return False

                cmd3 = ["dovi_tool", "extract-rpu", "-i", f'"{self.p8_video_path}"', "-o", f'"{self.rpu_path}"']
                if not run_step('extract_rpu', 'Extracting RPU', self.rpu_path, self._run_dovi_tool_with_progress, cmd3, "Extracting RPU"):
                    self.logger.error("transcode() failed during step: extract_rpu")
                    return False

                # Re-encode video only
                cmd4 = ["ffmpeg", "-fflags", "+genpts", "-i", f'"{self.p8_video_path}"', "-an", "-sn", "-dn", "-c:v", "libx265", "-preset", "medium", "-crf", "18", "-threads", "10", "-x265-params", "pools=10:no-sao=1:early-skip=1:rd=3:me=hex", "-y", f'"{self.reencoded_video_path}"']
                if not run_step('reencode_x265', 'Re-encoding to x265', self.reencoded_video_path, self._run_ffmpeg_with_progress, cmd4, self.total_frames, "Re-encoding to x265"):
                    self.logger.error("transcode() failed during step: reencode_x265")
                    return False

                # Inject RPU
                cmd5 = ["dovi_tool", "inject-rpu", "-i", f'"{self.reencoded_video_path}"', "--rpu-in", f'"{self.rpu_path}"', "-o", f'"{self.final_video_with_rpu_path}"']
                if not run_step('inject_rpu', 'Injecting RPU', self.final_video_with_rpu_path, self._run_dovi_tool_with_progress, cmd5, "Injecting RPU"):
                    self.logger.error("transcode() failed during step: inject_rpu")
                    return False

                # Remux final MKV
                cmd6 = ["mkvmerge", "-o", f'"{self.local_output_path}"', "--language", "0:eng", f'"{self.final_video_with_rpu_path}"', "--no-video", f'"{self.local_source_path}"']
                if not run_step('remux_final', 'Remuxing final MKV', self.local_output_path, self._run_command, cmd6, "Remuxing final MKV"):
                    self.logger.error("transcode() failed during step: remux_final")
                    return False

            # --- Standard Path (Optimized) --- 
            else: # 'standard' job type
                cmd_reencode_mux = [
                    "ffmpeg", "-fflags", "+genpts", "-i", f'"{self.local_source_path}"',
                    "-map", "0", "-c:v", "libx265", "-preset", "medium", "-crf", "18",
                    "-threads", "9", "-x265-params", "pools=9",
                    "-c:a", "copy", "-c:s", "copy", "-y", f'"{self.local_output_path}"'
                ]
                if not run_step('reencode_x265', 'Re-encoding and Muxing', self.local_output_path, self._run_ffmpeg_with_progress, cmd_reencode_mux, self.total_frames, "Re-encoding to x265"):
                    self.logger.error("transcode() failed during step: reencode_x265")
                    return False

            if not run_step('move_final', 'Moving final file to destination', self.output_path, self._move_final_file, self.local_output_path, self.output_path):
                self.logger.error("transcode() failed during step: move_final")
                return False

            print(f"\nSuccessfully transcoded {os.path.basename(self.original_input_path)} to {self.output_path}")
            self.logger.info(f"=== transcode() completed successfully for job {self.job_id} ===")
            return True
        except Exception as e:
            self.logger.error(f"UNHANDLED EXCEPTION in job pipeline: {e}", exc_info=True)
            print(f"- Unhandled exception in job pipeline. Check logs for details.")
            self.logger.error(f"=== transcode() failed for job {self.job_id} ===")
            return False

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
