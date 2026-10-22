"""公开样例子进程；只使用标准库，不接收网关凭据。

Linux 优先安装系统调用限制；静态约束和此子进程不代替平台正式算法沙箱。
"""
from __future__ import annotations

import ast
import importlib
import json
import math
import os
import sys
import types


ALLOWED_MODULES = {"__future__", "math", "cmath", "collections", "heapq", "itertools", "functools", "random",
                   "statistics", "re", "json", "bisect", "array", "operator", "typing", "dataclasses",
                   "decimal", "fractions", "time", "copy", "enum", "string", "hashlib"}
DENIED_NAMES = {"eval", "exec", "compile", "open", "input", "getattr", "setattr", "delattr",
                "globals", "locals", "vars", "dir", "help", "breakpoint", "memoryview", "print"}
DENIED_ATTRIBUTES = {"sys", "os", "io", "builtins", "importlib", "modules", "subprocess", "socket"}


def validate_source(source):
    tree = ast.parse(source, feature_version=(3, 9))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            if getattr(node, "level", 0) or any(name.split(".")[0] not in ALLOWED_MODULES for name in names):
                raise ValueError("sample self-test permits algorithm standard-library imports only")
        if isinstance(node, ast.Name) and (node.id in DENIED_NAMES or node.id.startswith("__")):
            raise ValueError("sample self-test forbids dynamic loading, I/O and introspection")
        if isinstance(node, ast.Attribute) and (node.attr.startswith("_") or node.attr in DENIED_ATTRIBUTES):
            raise ValueError("sample self-test forbids private or runtime attributes")
    return tree


def restrict_process(seconds):
    status = {"resource_limits": False, "syscall_filter": False}
    if os.name != "posix":
        return status
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, (seconds, seconds))
    resource.setrlimit(resource.RLIMIT_AS, (384 * 1024 * 1024, 384 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    status["resource_limits"] = True
    try:
        import ctypes
        import ctypes.util
        library = ctypes.util.find_library("seccomp")
        if not library:
            return status
        lib = ctypes.CDLL(library, use_errno=True)
        lib.seccomp_init.argtypes = [ctypes.c_uint32]
        lib.seccomp_init.restype = ctypes.c_void_p
        lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
        lib.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
        lib.seccomp_load.argtypes = [ctypes.c_void_p]
        lib.seccomp_release.argtypes = [ctypes.c_void_p]
        ctx = lib.seccomp_init(0x7fff0000)
        if not ctx:
            return status
        try:
            for name in ("socket", "socketpair", "connect", "accept", "accept4", "bind", "listen",
                         "execve", "execveat", "clone", "clone3", "fork", "vfork", "ptrace",
                         "open", "openat", "openat2", "creat", "unlink", "unlinkat", "rename", "renameat"):
                number = lib.seccomp_syscall_resolve_name(name.encode())
                if number >= 0 and lib.seccomp_rule_add(ctx, 0x00050000 | 1, number, 0) != 0:
                    return status
            status["syscall_filter"] = lib.seccomp_load(ctx) == 0
        finally:
            lib.seccomp_release(ctx)
    except (ImportError, OSError, AttributeError):
        pass
    return status


def main():
    message = json.load(sys.stdin)
    source, request = message["source"], message["request"]
    code = compile(validate_source(source), "<current-case-model-source>", "exec")
    for name in ALLOWED_MODULES:
        importlib.import_module(name)
    importlib.import_module("random").seed(request.get("protocol", {}).get("seed", 42))
    import builtins
    safe = {name: getattr(builtins, name) for name in (
        "abs", "all", "any", "bool", "bytes", "bytearray", "callable", "chr", "classmethod", "dict", "divmod",
        "enumerate", "filter", "float", "format", "frozenset", "hash", "int", "isinstance", "issubclass",
        "iter", "len", "list", "map", "max", "min", "next", "object", "ord", "pow", "property", "range",
        "repr", "reversed", "round", "set", "slice", "sorted", "staticmethod", "str", "sum", "super",
        "tuple", "type", "zip", "Exception", "ValueError", "TypeError", "RuntimeError", "KeyError",
        "IndexError", "StopIteration", "AssertionError", "ZeroDivisionError", "NotImplementedError",
        "__build_class__")}

    def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
        if level or name.split(".")[0] not in ALLOWED_MODULES:
            raise ImportError("module unavailable in algorithm self-test")
        return builtins.__import__(name, globals, locals, fromlist, level)

    safe["__import__"] = safe_import
    module = types.ModuleType("current_case_algorithm")
    module.__dict__["__builtins__"] = safe
    sys.modules[module.__name__] = module
    isolation = restrict_process(int(message.get("seconds", 8)))
    exec(code, module.__dict__)
    result = module.run_experiment(request)
    rank_result = None
    if request.get("case_id") == "D-02" and isinstance(result, dict) and result.get("candidate") is not None:
        selected = result.get("details", {}).get("selected")
        rank_request = dict(request, phase="rank", data=dict(request["data"], selected=selected))
        rank_result = module.run_experiment(rank_request)
    # 标准 JSON 禁止 NaN/Infinity，限制输出避免不正常结果耗尽父进程内存。
    encoded = json.dumps({"result": result, "rank_result": rank_result, "isolation": isolation}, ensure_ascii=False, allow_nan=False)
    if len(encoded.encode("utf-8")) > 1024 * 1024:
        raise ValueError("sample result exceeds one MiB")
    print(encoded)


if __name__ == "__main__":
    try:
        main()
    except BaseException as exc:
        print(json.dumps({"error_type": type(exc).__name__, "message": str(exc)[:1600]}, ensure_ascii=False))
        sys.exit(1)
