
import sys
import os
import subprocess
import json
import shlex
import time
import threading
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QLabel, QLineEdit, QPushButton, QFileDialog, QProgressBar,
                             QTextEdit, QGroupBox, QComboBox, QMessageBox, QSplitter)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont

def parse_bitrate_to_bps(txt):
    if txt is None:
        return 0
    s = str(txt).strip().lower().replace(' ', '')
    mult = 1
    if s.endswith('k'):
        mult = 1000
        s = s[:-1]
    elif s.endswith('m'):
        mult = 1000_000
        s = s[:-1]
    try:
        val = float(s)
        return int(val * mult)
    except Exception:
        try:
            return int(s)
        except Exception:
            return 0

def parse_speed_x(value):
    v = (value or "").strip().lower().replace(" ", "")
    if v.endswith('x'):
        v = v[:-1]
    try:
        return float(v)
    except Exception:
        return 0.0

class AudioConverterThread(QThread):
    overall_progress_updated = pyqtSignal(int)
    file_progress_updated = pyqtSignal(int)  # -1 means indeterminate
    eta_updated = pyqtSignal(str)
    log_updated = pyqtSignal(str)
    current_file_changed = pyqtSignal(str, int, int)
    conversion_complete = pyqtSignal()
    error_occurred = pyqtSignal(str)

    def __init__(self, files, output_dir, config):
        super().__init__()
        self.files = files
        self.output_dir = output_dir
        self.config = config
        self._is_running = True

    # ---------- helpers ----------
    def _drain_stderr(self, pipe):
        for line in iter(pipe.readline, ''):
            txt = line.strip()
            if txt:
                self.log_updated.emit(txt)
        try:
            pipe.close()
        except Exception:
            pass

    def get_audio_stream_metadata(self, file_path):
        cmd = ["ffprobe","-v","quiet","-print_format","json",
               "-show_entries","stream=index:codec_type:codec_name:channels:sample_rate",
               "-select_streams","a", file_path]
        try:
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, check=True, encoding="utf-8", errors="replace")
            data = json.loads(result.stdout)
            return data.get("streams", [])
        except Exception as e:
            self.log_updated.emit(f"Could not probe audio streams for {os.path.basename(file_path)}. Error: {e}")
            return []

    def get_duration_seconds(self, file_path):
        cmd = ["ffprobe","-v","error","-show_entries","format=duration",
               "-of","default=nw=1:nk=1", file_path]
        try:
            out = subprocess.check_output(cmd, text=True, encoding="utf-8", errors="replace").strip()
            return float(out)
        except Exception as e:
            self.log_updated.emit(f"Could not read duration for {os.path.basename(file_path)}. Falling back to 1 hour. Error: {e}")
            return 3600.0

    def get_output_audio_bitrates(self, file_path):
        cmd = ["ffprobe","-v","error","-select_streams","a",
               "-show_entries","stream=index,bit_rate,codec_name,channels",
               "-of","json", file_path]
        try:
            out = subprocess.check_output(cmd, text=True, encoding="utf-8", errors="replace")
            data = json.loads(out)
            rates = []
            for st in data.get("streams", []):
                try:
                    br = int(st.get("bit_rate")) if st.get("bit_rate") is not None else None
                except Exception:
                    br = None
                rates.append({"index": st.get("index"),
                              "bit_rate": br,
                              "codec": st.get("codec_name"),
                              "channels": st.get("channels")})
            return rates
        except Exception as e:
            self.log_updated.emit(f"Could not read output audio bitrates for {os.path.basename(file_path)}: {e}")
            return []

    def _emit_overall(self, file_index_zero_based, file_progress_0to1, total_files):
        overall = int(((file_index_zero_based + file_progress_0to1) / max(total_files, 1)) * 100)
        self.overall_progress_updated.emit(overall)

    def stop(self):
        self._is_running = False
        try:
            if self.isRunning():
                self.wait(1000)
        except Exception:
            pass

    # ---------- main worker ----------
    def run(self):
        try:
            total_files = len(self.files)

            for i, filename in enumerate(self.files):
                if not self._is_running:
                    break

                input_path = filename
                base_name = os.path.basename(filename)

                os.makedirs(self.output_dir, exist_ok=True)
                output_path = os.path.join(self.output_dir, base_name)

                self.current_file_changed.emit(base_name, i + 1, total_files)
                self.log_updated.emit(f"Processing {base_name}...")

                audio_streams = self.get_audio_stream_metadata(input_path)
                duration_s = self.get_duration_seconds(input_path)

                if not audio_streams:
                    self.log_updated.emit(f"Skipping '{base_name}': No audio streams found.")
                    self._emit_overall(i, 1.0, total_files)
                    continue

                # Build command
                ffmpeg_cmd = ["ffmpeg", "-y", "-nostdin", "-hide_banner",
                              "-progress", "pipe:1", "-nostats", "-loglevel", "error",
                              "-i", input_path,
                              "-map", "0",
                              "-c:v", self.config["video_codec"],
                              "-c:s", self.config["subtitle_codec"],
                              "-c:t", "copy", "-c:d", "copy"]

                a_bitrate = self.config["bitrate"]
                a_channels = int(self.config["channels"])
                a_rate = int(self.config["samplerate"])

                for idx, _ in enumerate(audio_streams):
                    ffmpeg_cmd.extend([
                        f"-c:a:{idx}", "aac",
                        f"-b:a:{idx}", a_bitrate,
                        f"-ac:a:{idx}", str(a_channels),
                        f"-ar:a:{idx}", str(a_rate),
                    ])
                ffmpeg_cmd.extend(["-aac_coder", "twoloop"])

                if self.config.get("metadata_title"):
                    ffmpeg_cmd.extend(["-metadata:s:a", f"title={self.config['metadata_title']}"])

                ffmpeg_cmd.append(output_path)

                try:
                    try:
                        cmd_str = shlex.join(ffmpeg_cmd)
                    except Exception:
                        cmd_str = " ".join(ffmpeg_cmd)
                    self.log_updated.emit(f"Executing: {cmd_str}")

                    creationflags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
                    process = subprocess.Popen(
                        ffmpeg_cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        bufsize=1,
                        universal_newlines=True,
                        creationflags=creationflags
                    )

                    t = threading.Thread(target=self._drain_stderr, args=(process.stderr,), daemon=True)
                    t.start()

                    start_wall = time.time()
                    last_out_time = 0.0
                    total_size_bytes = 0
                    speed_x = 0.0
                    have_time = False

                    target_bps = parse_bitrate_to_bps(self.config["bitrate"]) * max(len(audio_streams), 1)
                    if target_bps <= 0:
                        target_bps = 192000 * max(len(audio_streams), 1)

                    last_poll = 0.0

                    # start in indeterminate mode until we glean something
                    self.file_progress_updated.emit(-1)
                    self.eta_updated.emit("calculating…")

                    while True:
                        if not self._is_running:
                            process.terminate()
                            break

                        # Read a progress line if available
                        line = process.stdout.readline()
                        if not line and process.poll() is not None:
                            break

                        if line:
                            s = line.strip()
                            if s and "=" in s:
                                key, value = s.split("=", 1)
                                key = key.strip()
                                value = value.strip()

                                if key == "out_time_ms":
                                    try:
                                        out_time_s = int(value) / 1_000_000.0
                                        if out_time_s >= 0:
                                            last_out_time = min(out_time_s, duration_s)
                                            have_time = True
                                    except Exception:
                                        pass
                                elif key == "out_time":
                                    try:
                                        h, m, sec = value.split(":")
                                        out_time_s = int(h) * 3600 + int(m) * 60 + float(sec)
                                        last_out_time = min(out_time_s, duration_s)
                                        have_time = True
                                    except Exception:
                                        pass
                                elif key == "total_size":
                                    try:
                                        total_size_bytes = int(value)
                                    except Exception:
                                        pass
                                elif key == "speed":
                                    speed_x = parse_speed_x(value)

                        # Poll actual on-disk file size as an extra fallback
                        now = time.time()
                        if (now - last_poll) > 0.25:
                            last_poll = now
                            try:
                                if os.path.exists(output_path):
                                    actual = os.path.getsize(output_path)
                                    if actual > total_size_bytes:
                                        total_size_bytes = actual
                            except Exception:
                                pass

                        # Compute progress
                        estimable = False
                        if have_time and duration_s > 0:
                            file_progress = last_out_time / duration_s
                            estimable = True
                        elif total_size_bytes > 0 and target_bps > 0 and duration_s > 0:
                            est_processed_s = (total_size_bytes * 8.0) / target_bps
                            file_progress = min(max(est_processed_s / duration_s, 0.0), 0.99)
                            estimable = True
                        elif speed_x > 0 and duration_s > 0:
                            elapsed = max(now - start_wall, 0.0)
                            est_processed_s = elapsed * speed_x
                            file_progress = min(max(est_processed_s / duration_s, 0.0), 0.99)
                            estimable = True

                        if estimable:
                            pct = int(max(0.0, min(file_progress, 1.0)) * 100)
                            self.file_progress_updated.emit(pct)

                            # ETA
                            if have_time:
                                remaining = max(duration_s - last_out_time, 0.0)
                            elif total_size_bytes > 0 and target_bps > 0:
                                est_processed_s = (total_size_bytes * 8.0) / target_bps
                                remaining = max(duration_s - est_processed_s, 0.0)
                            else:
                                elapsed = max(now - start_wall, 0.0)
                                est_processed_s = elapsed * max(speed_x, 1e-6)
                                remaining = max(duration_s - est_processed_s, 0.0)

                            eta_seconds = int(remaining)
                            eta_mm, eta_ss = divmod(eta_seconds, 60)
                            self.eta_updated.emit(f"{eta_mm:02d}:{eta_ss:02d} remaining")

                            # Overall
                            self._emit_overall(i, min(max(file_progress, 0.0), 1.0), total_files)
                        else:
                            # keep indeterminate
                            self.file_progress_updated.emit(-1)
                            self.eta_updated.emit("calculating…")

                        if not line:
                            time.sleep(0.05)

                    process.wait()

                    if not self._is_running:
                        self.log_updated.emit(f"Conversion of {base_name} cancelled.")
                        if os.path.exists(output_path):
                            try:
                                os.remove(output_path)
                            except Exception:
                                pass
                    elif process.returncode != 0:
                        try:
                            error_msg = process.stderr.read() if process.stderr else "Unknown FFmpeg error"
                        except Exception:
                            error_msg = "Unknown FFmpeg error"
                        msg = f"Error processing {base_name}.\nFFmpeg error: {error_msg}"
                        self.log_updated.emit(msg)
                        self.error_occurred.emit(msg)
                        if os.path.exists(output_path):
                            try:
                                os.remove(output_path)
                            except Exception:
                                pass
                    else:
                        rates = self.get_output_audio_bitrates(output_path)
                        if rates:
                            for r in rates:
                                br = r.get('bit_rate')
                                br_txt = f"{br//1000}k" if isinstance(br, int) else "unknown"
                                self.log_updated.emit(
                                    f"Output audio stream {r.get('index')} -> {r.get('codec')} "
                                    f"{r.get('channels')}ch @ {br_txt}"
                                )
                        self.log_updated.emit(f"Successfully converted {base_name}")
                        self.file_progress_updated.emit(100)
                        self._emit_overall(i, 1.0, total_files)

                except Exception as e:
                    error_msg = f"Error processing {base_name}: {str(e)}"
                    self.log_updated.emit(error_msg)
                    self.error_occurred.emit(error_msg)
                    if os.path.exists(output_path):
                        try:
                            os.remove(output_path)
                        except Exception:
                            pass

            if self._is_running:
                self.conversion_complete.emit()

        except Exception as e:
            self.error_occurred.emit(f"An unexpected error occurred: {str(e)}")


class AudioNormalizationApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Audio Normalization to AAC (improved)")
        self.setGeometry(100, 100, 900, 650)

        self.config = {
            "output_dir": "converted",
            "bitrate": "224k",
            "channels": 2,
            "samplerate": 48000,
            "video_codec": "copy",
            "subtitle_codec": "copy",
            "metadata_title": "AAC Stereo"
        }

        self.selected_files = []
        self.converter_thread = None

        self.setup_ui()
        self.check_ffmpeg_availability()

    def setup_ui(self):
        main_widget = QWidget()
        main_layout = QVBoxLayout(main_widget)

        splitter = QSplitter(Qt.Vertical)

        # Top
        top_widget = QWidget()
        top_layout = QVBoxLayout(top_widget)

        file_group = QGroupBox("File Selection")
        file_layout = QVBoxLayout()
        btn_row = QHBoxLayout()
        self.select_file_btn = QPushButton("Select Files")
        self.select_file_btn.clicked.connect(self.select_files)
        self.select_dir_btn = QPushButton("Select Directory")
        self.select_dir_btn.clicked.connect(self.select_directory)
        btn_row.addWidget(self.select_file_btn)
        btn_row.addWidget(self.select_dir_btn)
        self.files_label = QLabel("No files selected")
        self.files_label.setWordWrap(True)
        self.files_label.setMinimumHeight(50)
        self.files_label.setStyleSheet("background-color: #f0f0f0; border: 1px solid #ccc;")
        file_layout.addLayout(btn_row)
        file_layout.addWidget(self.files_label)
        file_group.setLayout(file_layout)

        config_group = QGroupBox("Configuration")
        config_layout = QVBoxLayout()

        out_row = QHBoxLayout()
        out_row.addWidget(QLabel("Output Directory:"))
        self.output_dir_edit = QLineEdit(self.config["output_dir"])
        out_row.addWidget(self.output_dir_edit)
        self.browse_output_btn = QPushButton("Browse...")
        self.browse_output_btn.clicked.connect(self.browse_output_dir)
        out_row.addWidget(self.browse_output_btn)
        config_layout.addLayout(out_row)

        audio_row = QHBoxLayout()
        # Bitrate
        br_col = QVBoxLayout()
        br_col.addWidget(QLabel("Bitrate:"))
        self.bitrate_combo = QComboBox()
        self.bitrate_combo.addItems(["128k","192k","224k","256k","320k","384k","448k","512k","640k"])
        self.bitrate_combo.setCurrentText(self.config["bitrate"])
        br_col.addWidget(self.bitrate_combo)
        audio_row.addLayout(br_col)
        # Channels
        ch_col = QVBoxLayout()
        ch_col.addWidget(QLabel("Channels:"))
        self.channels_combo = QComboBox()
        self.channels_combo.addItems(["1","2","6"])
        self.channels_combo.setCurrentText(str(self.config["channels"]))
        ch_col.addWidget(self.channels_combo)
        audio_row.addLayout(ch_col)
        # Sample rate
        sr_col = QVBoxLayout()
        sr_col.addWidget(QLabel("Sample Rate:"))
        self.samplerate_combo = QComboBox()
        self.samplerate_combo.addItems(["44100","48000","96000"])
        self.samplerate_combo.setCurrentText(str(self.config["samplerate"]))
        sr_col.addWidget(self.samplerate_combo)
        audio_row.addLayout(sr_col)

        config_layout.addLayout(audio_row)

        codec_row = QHBoxLayout()
        v_col = QVBoxLayout()
        v_col.addWidget(QLabel("Video Codec:"))
        self.video_codec_combo = QComboBox()
        self.video_codec_combo.addItems(["copy","libx264","libx265"])
        self.video_codec_combo.setCurrentText(self.config["video_codec"])
        v_col.addWidget(self.video_codec_combo)
        codec_row.addLayout(v_col)
        s_col = QVBoxLayout()
        s_col.addWidget(QLabel("Subtitle Codec:"))
        self.subtitle_codec_combo = QComboBox()
        self.subtitle_codec_combo.addItems(["copy","mov_text"])
        self.subtitle_codec_combo.setCurrentText(self.config["subtitle_codec"])
        s_col.addWidget(self.subtitle_codec_combo)
        codec_row.addLayout(s_col)
        config_group.setLayout(config_layout)

        top_layout.addWidget(file_group)
        top_layout.addWidget(config_group)

        # Middle: progress
        progress_widget = QWidget()
        progress_layout = QVBoxLayout(progress_widget)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Current file:"))
        self.current_file_label = QLabel("--")
        self.current_file_label.setStyleSheet("font-weight: bold;")
        row1.addWidget(self.current_file_label, 1)
        self.file_eta_label = QLabel("--:-- remaining")
        row1.addWidget(self.file_eta_label)
        progress_layout.addLayout(row1)

        self.file_progress_bar = QProgressBar()
        self.file_progress_bar.setRange(0, 100)
        self.file_progress_bar.setValue(0)
        progress_layout.addWidget(self.file_progress_bar)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Overall queue:"))
        progress_layout.addLayout(row2)

        self.overall_progress_bar = QProgressBar()
        self.overall_progress_bar.setRange(0, 100)
        self.overall_progress_bar.setValue(0)
        progress_layout.addWidget(self.overall_progress_bar)

        # Bottom: log
        log_widget = QWidget()
        log_layout = QVBoxLayout(log_widget)
        log_layout.addWidget(QLabel("Conversion Log:"))
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Courier New", 9))
        log_layout.addWidget(self.log_text)

        # Action buttons
        action_layout = QHBoxLayout()
        self.convert_btn = QPushButton("Convert")
        self.convert_btn.clicked.connect(self.start_conversion)
        self.clear_log_btn = QPushButton("Clear Log")
        self.clear_log_btn.clicked.connect(self.clear_log)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancel_conversion)
        action_layout.addWidget(self.convert_btn)
        action_layout.addWidget(self.clear_log_btn)
        action_layout.addWidget(self.cancel_btn)

        splitter.addWidget(top_widget)
        splitter.addWidget(progress_widget)
        splitter.addWidget(log_widget)
        splitter.setSizes([320, 120, 260])

        main_layout.addWidget(splitter)
        main_layout.addLayout(action_layout)

        self.setCentralWidget(main_widget)

    def check_ffmpeg_availability(self):
        try:
            subprocess.run(["ffmpeg", "-version"], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["ffprobe", "-version"], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.log_updated("FFmpeg and FFprobe are available.")
        except Exception:
            QMessageBox.critical(self, "Error",
                                 "FFmpeg or FFprobe not found. Please ensure they are installed and in your system's PATH.")
            self.convert_btn.setEnabled(False)

    def select_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select Media Files", "", "Media Files (*.mkv *.mka *.flac *.wav *.mp4 *.m4a *.mp3);;All Files (*)"
        )
        if files:
            self.selected_files = files
            self.update_files_display()

    def select_directory(self):
        directory = QFileDialog.getExistingDirectory(self, "Select Directory")
        if directory:
            try:
                self.selected_files = [os.path.join(directory, f) for f in os.listdir(directory)
                                       if os.path.splitext(f.lower())[1] in (".mkv",".mka",".flac",".wav",".mp4",".m4a",".mp3")]
                if not self.selected_files:
                    QMessageBox.information(self, "No Files", "No supported media files found in the selected directory.")
                else:
                    self.update_files_display()
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Error reading directory: {str(e)}")

    def update_files_display(self):
        if len(self.selected_files) <= 5:
            self.files_label.setText("\n".join(self.selected_files))
        else:
            self.files_label.setText("\n".join(self.selected_files[:5]) +
                                     f"\n... and {len(self.selected_files) - 5} more files")

    def browse_output_dir(self):
        directory = QFileDialog.getExistingDirectory(self, "Select Output Directory")
        if directory:
            self.output_dir_edit.setText(directory)

    def start_conversion(self):
        if not self.selected_files:
            QMessageBox.warning(self, "No Files", "Please select files to convert.")
            return

        self.config["output_dir"] = self.output_dir_edit.text()
        self.config["bitrate"] = self.bitrate_combo.currentText()
        self.config["channels"] = int(self.channels_combo.currentText())
        self.config["samplerate"] = int(self.samplerate_combo.currentText())
        self.config["video_codec"] = self.video_codec_combo.currentText()
        self.config["subtitle_codec"] = self.subtitle_codec_combo.currentText()
        self.config["metadata_title"] = "AAC Stereo"

        os.makedirs(self.config["output_dir"], exist_ok=True)

        self.convert_btn.setEnabled(False)
        self.select_file_btn.setEnabled(False)
        self.select_dir_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)

        self.log_text.clear()
        self.log_updated("Starting conversion...")

        self.converter_thread = AudioConverterThread(
            self.selected_files,
            self.config["output_dir"],
            self.config
        )
        self.converter_thread.overall_progress_updated.connect(self.update_overall_progress)
        self.converter_thread.file_progress_updated.connect(self.update_file_progress)
        self.converter_thread.eta_updated.connect(self.update_eta)
        self.converter_thread.log_updated.connect(self.log_updated)
        self.converter_thread.current_file_changed.connect(self.on_current_file_changed)
        self.converter_thread.conversion_complete.connect(self.conversion_complete)
        self.converter_thread.error_occurred.connect(self.show_error)
        self.converter_thread.start()

    def cancel_conversion(self):
        if self.converter_thread and self.converter_thread.isRunning():
            self.converter_thread.stop()
            self.log_updated("Conversion cancelled by user.")
            self.reset_ui()

    def update_overall_progress(self, value):
        self.overall_progress_bar.setValue(value)
        QApplication.processEvents()

    def update_file_progress(self, value):
        if value < 0:
            # indeterminate
            self.file_progress_bar.setRange(0, 0)
        else:
            if self.file_progress_bar.minimum() == 0 and self.file_progress_bar.maximum() == 0:
                # switch back to determinate
                self.file_progress_bar.setRange(0, 100)
            self.file_progress_bar.setValue(value)
        QApplication.processEvents()

    def update_eta(self, text):
        self.file_eta_label.setText(text)
        QApplication.processEvents()

    def on_current_file_changed(self, name, idx, total):
        self.current_file_label.setText(f"{idx}/{total} — {name}")
        self.file_progress_bar.setRange(0, 100)
        self.file_progress_bar.setValue(0)
        self.file_eta_label.setText("calculating…")

    def log_updated(self, message):
        self.log_text.append(message)
        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        QApplication.processEvents()

    def conversion_complete(self):
        self.log_updated("Conversion complete!")
        QMessageBox.information(self, "Complete",
                                f"Processing complete. Converted files are in the '{self.config['output_dir']}' directory.")
        self.reset_ui()

    def show_error(self, error_message):
        QMessageBox.critical(self, "Error", error_message)
        self.reset_ui()

    def reset_ui(self):
        self.convert_btn.setEnabled(True)
        self.select_file_btn.setEnabled(True)
        self.select_dir_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.overall_progress_bar.setValue(0)
        self.file_progress_bar.setRange(0, 100)
        self.file_progress_bar.setValue(0)
        self.file_eta_label.setText("--:-- remaining")
        self.current_file_label.setText("--")

    def clear_log(self):
        self.log_text.clear()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = AudioNormalizationApp()
    window.show()
    sys.exit(app.exec_())
