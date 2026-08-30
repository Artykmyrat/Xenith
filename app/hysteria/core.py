"""The hysteria2 daemon, supervised beside the xray core.

Close to `app/xray/core.py` in shape, and deliberately so — the health check,
the log buffer and the startup job all read the same on both — but not shared
with it. The two daemons differ in the things a shared base would have to
paper over: hysteria takes its configuration as a file rather than on stdin,
writes to stderr rather than stdout, and has no assets directory, no API port
and no accounts to push.
"""

import atexit
import re
import subprocess
import threading
from collections import deque
from contextlib import contextmanager

from app import logger
from app.hysteria import config as hysteria_config
from config import DEBUG, HYSTERIA_EXECUTABLE_PATH

VERSION_RE = re.compile(r"^\s*Version:\s*v?(\S+)", re.MULTILINE)


class HysteriaCore:
    def __init__(self, executable_path: str = HYSTERIA_EXECUTABLE_PATH):
        self.executable_path = executable_path
        self.config_path = None

        self._version = None
        self.process = None
        self.restarting = False

        self._logs_buffer = deque(maxlen=100)
        self._temp_log_buffers = {}

        atexit.register(lambda: self.stop() if self.started else None)

    @property
    def version(self):
        """Version of the hysteria executable, looked up on first access.

        Lazily, so importing this module does not shell out to a binary that a
        development machine has no reason to carry.
        """
        if self._version is None:
            self._version = self.get_version()
        return self._version

    def get_version(self):
        cmd = [self.executable_path, "version"]
        try:
            output = subprocess.check_output(cmd, stderr=subprocess.STDOUT).decode("utf-8")
        except (OSError, subprocess.SubprocessError):
            return None

        match = VERSION_RE.search(output)
        return match.group(1) if match else None

    def __capture_process_logs(self):
        """Hysteria logs to stderr, one JSON object per line."""

        def capture():
            while self.process:
                output = self.process.stderr.readline()
                if output:
                    output = output.strip()
                    self._logs_buffer.append(output)
                    for buf in list(self._temp_log_buffers.values()):
                        buf.append(output)
                    if DEBUG:
                        logger.debug(output)

                elif not self.process or self.process.poll() is not None:
                    break

        threading.Thread(target=capture, daemon=True).start()

    @contextmanager
    def get_logs(self):
        buf = deque(self._logs_buffer, maxlen=100)
        buf_id = id(buf)
        try:
            self._temp_log_buffers[buf_id] = buf
            yield buf
        finally:
            del self._temp_log_buffers[buf_id]
            del buf

    @property
    def started(self):
        if not self.process:
            return False

        return self.process.poll() is None

    def start(self):
        """Render the configuration, then run the daemon against it.

        Rendering here rather than at startup only means a restart picks up an
        edited .env — and that a configuration the panel cannot render (no
        certificate, most likely) fails loudly at the point of starting rather
        than leaving a daemon running on a stale file.
        """
        if self.started:
            raise RuntimeError("Hysteria is started already")

        self.config_path = hysteria_config.write()

        self.process = subprocess.Popen(
            [self.executable_path, "server", "--config", self.config_path],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
        )

        logger.warning(f"Hysteria2 {self.version or ''} started".replace("  ", " "))
        self.__capture_process_logs()

    def stop(self):
        if not self.started:
            return

        self.process.terminate()
        self.process = None
        logger.warning("Hysteria2 stopped")

    def restart(self):
        if self.restarting:
            return

        try:
            self.restarting = True
            logger.warning("Restarting Hysteria2...")
            self.stop()
            self.start()
        finally:
            self.restarting = False
