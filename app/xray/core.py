import atexit
import re
import subprocess
import threading
from collections import deque
from contextlib import contextmanager

from app import logger
from app.xray.config import XRayConfig
from config import DEBUG, XRAY_GOGC, XRAY_MEMORY_LIMIT_PERCENT


def _memory_limit() -> int:
    """Bytes the core's heap may grow to before collection turns urgent.

    A share of what the machine has, because the core is not the only thing on
    it: the panel, its database and whatever else the admin runs are the rest.
    Zero when the share is turned off or the machine will not say how much
    memory it has, and then the runtime is left with no ceiling at all.
    """
    if XRAY_MEMORY_LIMIT_PERCENT <= 0:
        return 0
    try:
        import psutil

        total = psutil.virtual_memory().total
    except Exception:
        return 0
    return int(total * XRAY_MEMORY_LIMIT_PERCENT / 100)


def runtime_env(assets_path: str) -> dict:
    """The environment the core is started with.

    The core is a Go program the panel does not build, so the only handle on
    what its runtime does is here. Collection every time the heap doubles is
    the Go default and is tuned for programs that hold their data; a proxy
    holds almost nothing and allocates constantly per connection, so the
    default spends CPU on collections that free little — and each one is a
    pause a connection can be waiting through. GOGC lets the heap grow further
    between collections, and GOMEMLIMIT is what keeps that from being a way to
    run the machine out of memory: as the heap approaches the limit the
    runtime collects as hard as it needs to, whatever GOGC says.

    Nothing else from the panel's own environment is passed on. The core would
    read some of it — http_proxy is the one that would quietly change where
    traffic goes — and none of it is meant for it.
    """
    env = {"XRAY_LOCATION_ASSET": assets_path}

    if XRAY_GOGC > 0:
        env["GOGC"] = str(XRAY_GOGC)

    limit = _memory_limit()
    if limit:
        env["GOMEMLIMIT"] = f"{limit}B"

    return env


class XRayCore:
    def __init__(self,
                 executable_path: str = "/usr/bin/xray",
                 assets_path: str = "/usr/share/xray"):
        self.executable_path = executable_path
        self.assets_path = assets_path

        self._version = None
        self.process = None
        self.restarting = False

        self._logs_buffer = deque(maxlen=100)
        self._temp_log_buffers = {}
        self._on_start_funcs = []
        self._on_stop_funcs = []
        self._env = runtime_env(assets_path)

        atexit.register(lambda: self.stop() if self.started else None)

    @property
    def version(self):
        """Version of the Xray executable, looked up on first access.

        Resolved lazily so that importing this module does not shell out to the
        Xray binary, which need not be present on a development machine.
        """
        if self._version is None:
            self._version = self.get_version()
        return self._version

    def get_version(self):
        cmd = [self.executable_path, "version"]
        output = subprocess.check_output(cmd, stderr=subprocess.STDOUT).decode('utf-8')
        m = re.match(r'^Xray (\d+\.\d+\.\d+)', output)
        if m:
            return m.groups()[0]

    def get_x25519(self, private_key: str = None):
        cmd = [self.executable_path, "x25519"]
        if private_key:
            cmd.extend(['-i', private_key])
        output = subprocess.check_output(cmd, stderr=subprocess.STDOUT).decode('utf-8')
        m = re.match(r'Private key: (.+)\nPublic key: (.+)', output)
        if m:
            private, public = m.groups()
            return {
                "private_key": private,
                "public_key": public
            }

    def __capture_process_logs(self):
        def capture_and_debug_log():
            while self.process:
                output = self.process.stdout.readline()
                if output:
                    output = output.strip()
                    self._logs_buffer.append(output)
                    for buf in list(self._temp_log_buffers.values()):
                        buf.append(output)
                    logger.debug(output)

                elif not self.process or self.process.poll() is not None:
                    break

        def capture_only():
            while self.process:
                output = self.process.stdout.readline()
                if output:
                    output = output.strip()
                    self._logs_buffer.append(output)
                    for buf in list(self._temp_log_buffers.values()):
                        buf.append(output)

                elif not self.process or self.process.poll() is not None:
                    break

        if DEBUG:
            threading.Thread(target=capture_and_debug_log).start()
        else:
            threading.Thread(target=capture_only).start()

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

        if self.process.poll() is None:
            return True

        return False

    def start(self, config: XRayConfig):
        if self.started is True:
            raise RuntimeError("Xray is started already")

        if config.get('log', {}).get('logLevel') in ('none', 'error'):
            config['log']['logLevel'] = 'warning'

        cmd = [
            self.executable_path,
            "run",
            '-config',
            'stdin:'
        ]
        self.process = subprocess.Popen(
            cmd,
            env=self._env,
            stdin=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdout=subprocess.PIPE,
            universal_newlines=True
        )
        self.process.stdin.write(config.to_json())
        self.process.stdin.flush()
        self.process.stdin.close()
        logger.warning(f"Xray core {self.version} started")

        self.__capture_process_logs()

        # execute on start functions
        for func in self._on_start_funcs:
            threading.Thread(target=func).start()

    def stop(self):
        if not self.started:
            return

        self.process.terminate()
        self.process = None
        logger.warning("Xray core stopped")

        # execute on stop functions
        for func in self._on_stop_funcs:
            threading.Thread(target=func).start()

    def restart(self, config: XRayConfig):
        if self.restarting is True:
            return

        try:
            self.restarting = True
            logger.warning("Restarting Xray core...")
            self.stop()
            self.start(config)
        finally:
            self.restarting = False

    def on_start(self, func: callable):
        self._on_start_funcs.append(func)
        return func

    def on_stop(self, func: callable):
        self._on_stop_funcs.append(func)
        return func
