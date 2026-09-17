from __future__ import annotations

import builtins
import _thread
import io
import os
import socket
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


class SafetyViolation(RuntimeError):
    """Uma borda proibida foi alcançada durante uma execução do harness."""


@dataclass
class SafetyGuards:
    allowed_write_root: Path = field(default_factory=Path.cwd)
    events: list[str] = field(default_factory=list)
    _restore: list[Callable[[], None]] = field(default_factory=list, init=False)

    def _replace(self, owner: Any, name: str, replacement: Any) -> None:
        original = getattr(owner, name)
        setattr(owner, name, replacement)
        self._restore.append(lambda: setattr(owner, name, original))

    def _deny(self, kind: str, detail: str) -> None:
        event = f"{kind}:{detail}"
        self.events.append(event)
        raise SafetyViolation(event)

    @staticmethod
    def _is_env_path(value: Any) -> bool:
        try:
            path = Path(value)
            names = (path.name, path.resolve(strict=False).name)
        except TypeError:
            return False
        return any(name == ".env" or name.startswith(".env.") for name in names)

    def install(self) -> None:
        original_open = builtins.open
        original_io_open = io.open

        def guarded_open(file: Any, *args: Any, **kwargs: Any):
            if self._is_env_path(file):
                self._deny("env", str(file))
            mode = str(args[0] if args else kwargs.get("mode", "r"))
            if any(flag in mode for flag in ("w", "a", "x", "+")):
                self._guard_write(file)
            return original_open(file, *args, **kwargs)

        def guarded_io_open(file: Any, *args: Any, **kwargs: Any):
            if self._is_env_path(file):
                self._deny("env", str(file))
            mode = str(args[0] if args else kwargs.get("mode", "r"))
            if any(flag in mode for flag in ("w", "a", "x", "+")):
                self._guard_write(file)
            return original_io_open(file, *args, **kwargs)

        original_path_open = Path.open

        def guarded_path_open(path: Path, *args: Any, **kwargs: Any):
            if self._is_env_path(path):
                self._deny("env", str(path))
            mode = str(args[0] if args else kwargs.get("mode", "r"))
            if any(flag in mode for flag in ("w", "a", "x", "+")):
                self._guard_write(path)
            return original_path_open(path, *args, **kwargs)

        original_os_open = os.open

        def guarded_os_open(path: Any, flags: int, *args: Any, **kwargs: Any):
            self._guard_relative_dir_fd(path, kwargs.get("dir_fd"))
            if self._is_env_path(path):
                self._deny("env", str(path))
            write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
            if flags & write_flags:
                self._guard_write(path)
            return original_os_open(path, flags, *args, **kwargs)

        def deny_network(sock: socket.socket, address: Any) -> None:
            self._deny("network", repr(address))

        def deny_send(sock: socket.socket, *args: Any, **kwargs: Any) -> None:
            self._deny("network", repr(args[-1] if args else kwargs))

        def deny_create_connection(address: Any, *args: Any, **kwargs: Any) -> None:
            self._deny("network", repr(address))

        def deny_resolution(*args: Any, **kwargs: Any) -> None:
            self._deny("network", repr(args[0] if args else kwargs))

        def deny_process(*args: Any, **kwargs: Any) -> None:
            command = args[0] if args else kwargs.get("args", "unknown")
            self._deny("process", repr(command))

        def deny_thread(thread: threading.Thread, *args: Any, **kwargs: Any) -> None:
            self._deny("thread", thread.name)

        self._replace(builtins, "open", guarded_open)
        self._replace(io, "open", guarded_io_open)
        self._replace(Path, "open", guarded_path_open)
        self._replace(os, "open", guarded_os_open)
        self._replace(socket.socket, "connect", deny_network)
        self._replace(socket.socket, "connect_ex", deny_network)
        for name in ("send", "sendall", "sendto", "sendmsg", "sendfile"):
            if hasattr(socket.socket, name):
                self._replace(socket.socket, name, deny_send)
        self._replace(socket, "create_connection", deny_create_connection)
        for name in (
            "getaddrinfo", "gethostbyname", "gethostbyname_ex", "gethostbyaddr", "getnameinfo"
        ):
            self._replace(socket, name, deny_resolution)
        self._replace(subprocess, "Popen", deny_process)
        process_names = (
            "system", "popen", "fork", "forkpty", "posix_spawn", "posix_spawnp",
            "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve",
            "spawnvp", "spawnvpe", "execl", "execle", "execlp", "execlpe",
            "execv", "execve", "execvp", "execvpe", "startfile",
        )
        for name in process_names:
            if hasattr(os, name):
                self._replace(os, name, deny_process)
        self._replace(threading.Thread, "start", deny_thread)

        def deny_low_level_thread(*_args: Any, **_kwargs: Any) -> None:
            self._deny("thread", "low-level")

        for owner, name in (
            (_thread, "start_new_thread"),
            (_thread, "start_joinable_thread"),
            (threading, "_start_new_thread"),
            (threading, "_start_joinable_thread"),
        ):
            if hasattr(owner, name):
                self._replace(owner, name, deny_low_level_thread)

        def guard_path_mutation(name: str, *, destination: bool = False) -> None:
            original = getattr(Path, name)

            def guarded(path: Path, *args: Any, **kwargs: Any) -> Any:
                self._guard_write(path)
                if destination:
                    self._guard_write(args[0] if args else kwargs["target"])
                return original(path, *args, **kwargs)

            self._replace(Path, name, guarded)

        for name in ("mkdir", "rmdir", "unlink", "touch", "chmod", "symlink_to"):
            guard_path_mutation(name)
        for name in ("rename", "replace", "hardlink_to"):
            guard_path_mutation(name, destination=True)

        def guard_os_mutation(name: str, *, destination: bool = False) -> None:
            original = getattr(os, name)

            def guarded(path: Any, *args: Any, **kwargs: Any) -> Any:
                self._guard_write(path, dir_fd=kwargs.get("src_dir_fd", kwargs.get("dir_fd")))
                if destination:
                    self._guard_write(
                        args[0] if args else kwargs["dst"],
                        dir_fd=kwargs.get("dst_dir_fd", kwargs.get("dir_fd")),
                    )
                return original(path, *args, **kwargs)

            self._replace(os, name, guarded)

        for name in ("mkdir", "rmdir", "remove", "unlink", "chmod", "utime"):
            guard_os_mutation(name)
        for name in ("rename", "replace", "link"):
            guard_os_mutation(name, destination=True)
        original_symlink = os.symlink

        def guarded_symlink(source: Any, destination: Any, *args: Any, **kwargs: Any) -> Any:
            self._guard_write(destination, dir_fd=kwargs.get("dir_fd"))
            return original_symlink(source, destination, *args, **kwargs)

        self._replace(os, "symlink", guarded_symlink)

    def _guard_relative_dir_fd(self, value: Any, dir_fd: int | None) -> None:
        # O diretório real do descritor não é o CWD. Sem resolução confiável
        # entre plataformas, recusar a operação evita liberar outro diretório.
        if dir_fd is not None and not os.path.isabs(value):
            self._deny("dir_fd", str(value))

    def _guard_write(self, value: Any, *, dir_fd: int | None = None) -> None:
        if isinstance(value, int):
            self._deny("write", f"fd:{value}")
        self._guard_relative_dir_fd(value, dir_fd)
        target = Path(value).resolve(strict=False)
        root = self.allowed_write_root.resolve(strict=False)
        if target != root and root not in target.parents:
            self._deny("write", str(target))

    def close(self) -> None:
        while self._restore:
            self._restore.pop()()
