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
        print(f"- Re-encoding video...")
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, text=True, bufsize=1)
        
        pbar = tqdm(total=total_frames, unit=' frames', ncols=80, bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}{postfix}]')
        
        frame_regex = re.compile(r"frame=\s*(\d+)")

        for line in iter(process.stdout.readline, ''):
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
        # Step 1: Extract HEVC video stream
        cmd1 = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", self.input_path, "-map", "0:v:0",
            "-c:v", "copy", "-bsf:v", "hevc_mp4toannexb", "-y", self.hevc_file
        ]
        if not self._run_command(cmd1, "Extracting HEVC video stream"):
            return False

        # Step 2: Extract RPU with dovi_tool
        cmd2 = ["dovi_tool", "-m", "2", "extract-rpu", "-i", self.hevc_file, "-o", self.rpu_file]
        if not self._run_command(cmd2, "Extracting RPU for Dolby Vision"):
            return False

        # Step 3: Get total frames for progress bar
        total_frames = self._get_total_frames()
        if not total_frames:
            return False

        # Step 4: Re-encode HEVC with RPU injection and progress bar
        video_p8_hevc = os.path.join(self.persistent_temp_dir, "video_p8.hevc")
        cmd3 = [
            "ffmpeg", "-hide_banner", "-i", self.hevc_file, "-i", self.rpu_file,
            "-map", "0:v:0", "-map", "1:d:0",
            "-c:v", "libx265", "-crf", "18", "-preset", "slow",
            "-x265-params", f"dhdr10-info=true:repeat-headers=true:aud=true:hrd=true:colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc:master-display=G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,50):max-cll=1100,450:dolby-vision-profile=8.1:dolby-vision-rpu-file={self.rpu_file}:dolby-vision-check=true",
            "-y", video_p8_hevc
        ]
        if not self._run_ffmpeg_with_progress(cmd3, total_frames):
            return False

        # Step 5: Extract chapters
        cmd4 = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", self.input_path, "-f", "ffmetadata", "-y", self.chapters_file]
        if not self._run_command(cmd4, "Extracting chapters"):
            return False

        # Step 6: Remux final MKV
        cmd5 = [
            "mkvmerge", "-o", self.final_output_file,
            "--track-name", "0:HEVC (DV Profile 8.1)", video_p8_hevc,
            "--language", "1:en", "--track-name", "1:TrueHD 7.1 Atmos", "-a", "1", "-d", "-S", "-T", self.input_path,
            "--language", "2:en", "--track-name", "2:SRT", "-s", "2", "-d", "-A", "-T", self.input_path,
            "--chapter-language", "en", "--chapters", self.chapters_file
        ]
        if not self._run_command(cmd5, "Remuxing final MKV file"):
            return False

        self.logger.info(f"Successfully transcoded {self.input_path} to {self.final_output_file}")
        print(f"\nJob complete. Final file saved to: {self.final_output_file}")
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
