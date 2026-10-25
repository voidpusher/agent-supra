"""Owned process-tree cancellation, with no shell or process-name matching.

On Windows a trusted trampoline joins a kill-on-close Job Object before starting
the requested command. This avoids the spawn-before-job-assignment race.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path


def windows_api():
    import ctypes as ct
    from ctypes import wintypes as wt

    class Basic(ct.Structure):
        _fields_ = [('process_time', ct.c_longlong), ('job_time', ct.c_longlong),
                    ('flags', wt.DWORD), ('min_ws', ct.c_size_t), ('max_ws', ct.c_size_t),
                    ('active_limit', wt.DWORD), ('affinity', ct.c_size_t),
                    ('priority', wt.DWORD), ('scheduling', wt.DWORD)]

    class IO(ct.Structure):
        _fields_ = [(name, ct.c_ulonglong) for name in ('read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes')]

    class Limits(ct.Structure):
        _fields_ = [('basic', Basic), ('io', IO), ('process_memory', ct.c_size_t),
                    ('job_memory', ct.c_size_t), ('peak_process', ct.c_size_t), ('peak_job', ct.c_size_t)]

    api = ct.WinDLL('kernel32', use_last_error=True)
    specs = {
        'CreateJobObjectW': ([ct.c_void_p, wt.LPCWSTR], wt.HANDLE),
        'SetInformationJobObject': ([wt.HANDLE, ct.c_int, ct.c_void_p, wt.DWORD], wt.BOOL),
        'AssignProcessToJobObject': ([wt.HANDLE, wt.HANDLE], wt.BOOL),
        'GetCurrentProcess': ([], wt.HANDLE),
        'TerminateJobObject': ([wt.HANDLE, wt.UINT], wt.BOOL),
        'CloseHandle': ([wt.HANDLE], wt.BOOL),
    }
    for name, (args, result) in specs.items():
        getattr(api, name).argtypes = args
        getattr(api, name).restype = result
    return ct, api, Limits


class ProcessTree:
    def __init__(self, command, *, cwd, env):
        self.job = None
        if os.name == 'nt':
            ct, self.api, Limits = windows_api()
            self.job = self.api.CreateJobObjectW(None, None)
            if not self.job:
                raise OSError('Could not create the process job')
            limits = Limits()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            try:
                if not self.api.SetInformationJobObject(self.job, 9, ct.byref(limits), ct.sizeof(limits)):
                    raise OSError('Could not configure the process job')
                startup = subprocess.STARTUPINFO()
                startup.lpAttributeList = {'handle_list': [self.job]}
                os.set_handle_inheritable(self.job, True)
                try:
                    # The venv's Windows python.exe is a redirector that can retain
                    # inherited job handles outside the job. Start the real base
                    # interpreter and load only our trusted, dependency-free code.
                    base = Path(sys.base_prefix) / 'python.exe'
                    if not base.is_file():
                        raise OSError('Could not locate the base Python interpreter')
                    package_root = str(Path(__file__).resolve().parents[1])
                    bootstrap = f'import sys; sys.path.insert(0, {package_root!r}); from agent_supra.watch_process import main; raise SystemExit(main())'
                    self.process = subprocess.Popen([str(base), '-I', '-S', '-c', bootstrap, str(self.job), '--', *command],
                        cwd=cwd, env=env, startupinfo=startup, close_fds=True,
                        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
                finally:
                    os.set_handle_inheritable(self.job, False)
            except BaseException:
                self.api.CloseHandle(self.job)
                self.job = None
                raise
        else:
            self.process = subprocess.Popen(command, cwd=cwd, env=env, start_new_session=True)

    def poll(self):
        return self.process.poll()

    def close(self):
        if os.name == 'nt':
            if self.job:
                self.api.TerminateJobObject(self.job, 125)
                # Also cancel a trusted trampoline still starting up, before it
                # joins the job. No user command runs before that join.
                if self.process.poll() is None:
                    try:
                        self.process.kill()
                    except OSError:
                        pass
                self.api.CloseHandle(self.job)
                self.job = None
        else:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
            # Also catch descendants left behind by an already-exited leader.
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.process.wait(timeout=5)


def main():
    if os.name != 'nt' or len(sys.argv) < 4 or sys.argv[2] != '--':
        return 125
    _, api, _ = windows_api()
    handle = int(sys.argv[1])
    joined = api.AssignProcessToJobObject(handle, api.GetCurrentProcess())
    api.CloseHandle(handle)  # Only the watcher keeps the job alive.
    if not joined:
        print('Agent Supra could not contain the command; it was not started.', file=sys.stderr)
        return 125
    try:
        return subprocess.call(sys.argv[3:], close_fds=True)
    except OSError:
        print('Agent Supra could not start the requested command.', file=sys.stderr)
        return 127


if __name__ == '__main__':
    raise SystemExit(main())
