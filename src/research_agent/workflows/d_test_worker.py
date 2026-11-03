"""Linux-only helper. Fail closed if syscall isolation cannot be installed."""
import sys
import json
import os
import resource
import ctypes
# 预载标准库，装过滤器后不能通过 import 读取文件；不预载任何凭据或比赛材料。
import math, itertools, collections, functools, statistics, heapq, bisect, random
import decimal, fractions, operator, typing, array, re, dataclasses, copy, string
import io, contextlib, types


def isolate():
    lib=ctypes.CDLL('libseccomp.so.2',use_errno=True)
    resource.setrlimit(resource.RLIMIT_NOFILE,(3,3))
    lib.seccomp_init.argtypes=[ctypes.c_uint32];lib.seccomp_init.restype=ctypes.c_void_p
    lib.seccomp_syscall_resolve_name.argtypes=[ctypes.c_char_p];lib.seccomp_syscall_resolve_name.restype=ctypes.c_int
    lib.seccomp_rule_add.argtypes=[ctypes.c_void_p,ctypes.c_uint32,ctypes.c_int,ctypes.c_uint]
    lib.seccomp_load.argtypes=[ctypes.c_void_p]
    lib.seccomp_release.argtypes=[ctypes.c_void_p]
    ctx=lib.seccomp_init(0x00050000|1)  # 默认 EPERM，不允许读取文件、联网、创建进程或操作别的进程。
    if not ctx:raise RuntimeError('seccomp_init')
    try:
        names=('read write close fstat newfstatat lseek mmap mprotect munmap brk mremap madvise '
               'rt_sigaction rt_sigprocmask rt_sigreturn sigaltstack clock_gettime clock_getres '
               'gettimeofday time futex getpid gettid getrandom sched_yield exit exit_group').split()
        for name in names:
            number=lib.seccomp_syscall_resolve_name(name.encode())
            if number>=0 and lib.seccomp_rule_add(ctx,0x7fff0000,number,0)!=0:raise RuntimeError('seccomp_rule')
        if lib.seccomp_load(ctx)!=0:raise RuntimeError('seccomp_load')
    finally:lib.seccomp_release(ctx)


def main():
    payload=json.loads(sys.stdin.buffer.read(150001))
    code=compile(payload['source'],'<current-model-source>','exec')
    resource.setrlimit(resource.RLIMIT_AS,(256*1024*1024,256*1024*1024))
    resource.setrlimit(resource.RLIMIT_CPU,(5,5))
    resource.setrlimit(resource.RLIMIT_FSIZE,(131072,131072))
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    os.environ.clear()
    try:isolate()
    except Exception:
        print(json.dumps({'state':'isolation_unavailable'}));return
    rows=[]
    for request in payload['requests']:
        module=types.ModuleType('_current_candidate');sys.modules[module.__name__]=module
        try:
            with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
                exec(code,module.__dict__)
                value=module.run_experiment(request)
                encoded=json.dumps(value,allow_nan=False)
                if len(encoded)>16000:raise ValueError('result_too_large')
            rows.append({'ok':True,'value':json.loads(encoded)})
        except BaseException as exc:
            rows.append({'ok':False,'error':type(exc).__name__})
    print(json.dumps({'state':'isolated','results':rows},allow_nan=False))


if __name__=='__main__':main()
