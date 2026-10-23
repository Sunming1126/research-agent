"""实验子进程共用整题截止时间，并响应 SDK 取消。"""
from __future__ import annotations

import subprocess
import time
import os
import signal

from .session import checkpoint, remaining_timeout


def run_case_process(command, *, timeout, env, reserve=5):
    deadline = time.monotonic() + remaining_timeout(timeout, reserve=reserve)
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               encoding="utf-8", errors="replace", env=env,
                               start_new_session=os.name == "posix")
    try:
        while True:
            checkpoint()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout)
            try:
                stdout, stderr = process.communicate(timeout=min(.2, remaining))
                checkpoint()
                return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
            except subprocess.TimeoutExpired:
                if time.monotonic() >= deadline:
                    raise
    except BaseException as exc:
        try:
            # 仅终止本次创建的独立进程组，避免客户端的子任务在取消后继续落盘。
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            pass
        stdout, stderr = process.communicate()
        if isinstance(exc, (TimeoutError, subprocess.TimeoutExpired)):
            exc.stdout, exc.stderr = stdout, stderr
        raise
