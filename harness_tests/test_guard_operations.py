from __future__ import annotations

import io
import os
import socket
import tempfile
import _thread
import threading
import unittest
from pathlib import Path

from harness_support.safe_runtime import SafetyGuards, SafetyViolation


class GuardOperationsTests(unittest.TestCase):
    def test_bind_de_socket_nao_pode_criar_inode_fora_da_raiz(self) -> None:
        original_bind = socket.socket.bind
        calls: list[object] = []
        socket.socket.bind = lambda _sock, address: calls.append(address)
        try:
            with tempfile.TemporaryDirectory() as root:
                allowed = Path(root) / "allowed"
                allowed.mkdir()
                guards = SafetyGuards(allowed_write_root=allowed)
                guards.install()
                try:
                    with socket.socket(socket.AF_UNIX) as unix_socket:
                        with self.assertRaises(SafetyViolation):
                            unix_socket.bind(str(Path(root) / "outside.sock"))
                    with socket.socket(socket.AF_INET) as inet_socket:
                        with self.assertRaises(SafetyViolation):
                            inet_socket.bind(("127.0.0.1", 0))
                    self.assertEqual(calls, [])
                    self.assertEqual(len(guards.events), 2)
                finally:
                    guards.close()
        finally:
            socket.socket.bind = original_bind

    def test_threads_de_baixo_nivel_nao_iniciam(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            guards = SafetyGuards(allowed_write_root=Path(root))
            guards.install()
            try:
                starts = [
                    getattr(module, name)
                    for module, name in (
                        (_thread, "start_new_thread"),
                        (_thread, "start_joinable_thread"),
                        (threading, "_start_joinable_thread"),
                    )
                    if hasattr(module, name)
                ]
                for start in starts:
                    with self.assertRaises(SafetyViolation):
                        start(lambda: None, ())
                self.assertEqual(len(guards.events), len(starts))
                self.assertTrue(all(event.startswith("thread:") for event in guards.events))
            finally:
                guards.close()
    def test_os_system_nao_inicia_processos(self) -> None:
        original_system = os.system
        calls: list[str] = []
        os.system = lambda command: calls.append(command) or 0
        try:
            with tempfile.TemporaryDirectory() as root:
                guards = SafetyGuards(allowed_write_root=Path(root))
                guards.install()
                try:
                    with self.assertRaises(SafetyViolation):
                        os.system("true")
                    self.assertEqual(calls, [])
                    self.assertTrue(
                        any(event.startswith("process:") for event in guards.events)
                    )
                finally:
                    guards.close()
        finally:
            os.system = original_system

    def test_io_open_protege_segredos_e_escrita(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            allowed = directory / "allowed"
            allowed.mkdir()
            secret = directory / ".env"
            secret.write_text("segredo sintético", encoding="utf-8")
            guards = SafetyGuards(allowed_write_root=allowed)
            guards.install()
            try:
                with self.assertRaises(SafetyViolation):
                    with io.open(secret, encoding="utf-8"):
                        pass
                with self.assertRaises(SafetyViolation):
                    with io.open(directory / "outside", "w", encoding="utf-8"):
                        pass
                self.assertFalse((directory / "outside").exists())
                self.assertGreaterEqual(len(guards.events), 2)
            finally:
                guards.close()

    def test_symlink_para_env_nao_permite_leitura(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            allowed = directory / "allowed"
            allowed.mkdir()
            secret = directory / ".env"
            secret.write_text("segredo sintético", encoding="utf-8")
            alias = allowed / "alias"
            alias.symlink_to(secret)
            guards = SafetyGuards(allowed_write_root=allowed)
            guards.install()
            try:
                for read in (
                    lambda: alias.read_text(encoding="utf-8"),
                    lambda: io.open(alias, encoding="utf-8").read(),
                    lambda: open(alias, encoding="utf-8").read(),
                ):
                    with self.assertRaises(SafetyViolation):
                        read()
                self.assertEqual(len(guards.events), 3)
                self.assertTrue(all(event.startswith("env:") for event in guards.events))
            finally:
                guards.close()

    def test_envio_udp_nao_alcanca_socket_real(self) -> None:
        original_sendto = socket.socket.sendto
        sends: list[object] = []

        def fake_sendto(_socket: socket.socket, *args: object) -> int:
            sends.append(args)
            return 1

        socket.socket.sendto = fake_sendto
        try:
            with tempfile.TemporaryDirectory() as root:
                guards = SafetyGuards(allowed_write_root=Path(root))
                guards.install()
                try:
                    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
                        with self.assertRaises(SafetyViolation):
                            udp.sendto(b"teste", ("127.0.0.1", 9))
                    self.assertTrue(any(event.startswith("network:") for event in guards.events))
                    self.assertEqual(sends, [])
                finally:
                    guards.close()
        finally:
            socket.socket.sendto = original_sendto

    def test_resolucao_dns_nao_alcanca_rede(self) -> None:
        original_getaddrinfo = socket.getaddrinfo
        original_gethostbyname = socket.gethostbyname
        calls: list[str] = []
        socket.getaddrinfo = lambda *_a, **_k: calls.append("getaddrinfo") or []
        socket.gethostbyname = lambda *_a, **_k: calls.append("gethostbyname") or "127.0.0.1"
        try:
            with tempfile.TemporaryDirectory() as root:
                guards = SafetyGuards(allowed_write_root=Path(root))
                guards.install()
                try:
                    for lookup in (
                        lambda: socket.getaddrinfo("example.invalid", 443),
                        lambda: socket.gethostbyname("example.invalid"),
                    ):
                        with self.assertRaises(SafetyViolation):
                            lookup()
                    self.assertEqual(calls, [])
                    self.assertEqual(len(guards.events), 2)
                    self.assertTrue(all(event.startswith("network:") for event in guards.events))
                finally:
                    guards.close()
        finally:
            socket.getaddrinfo = original_getaddrinfo
            socket.gethostbyname = original_gethostbyname

    def test_truncamento_fora_da_raiz_e_bloqueado(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            allowed = directory / "allowed"
            allowed.mkdir()
            outside = directory / "outside"
            outside.write_text("conteúdo", encoding="utf-8")
            descriptor = os.open(outside, os.O_WRONLY)
            guards = SafetyGuards(allowed_write_root=allowed)
            guards.install()
            try:
                for truncate in (
                    lambda: os.truncate(outside, 0),
                    lambda: os.truncate(descriptor, 0),
                    lambda: os.ftruncate(descriptor, 0),
                ):
                    with self.assertRaises(SafetyViolation):
                        truncate()
                self.assertEqual(outside.read_text(encoding="utf-8"), "conteúdo")
                self.assertEqual(len(guards.events), 3)
            finally:
                guards.close()
                os.close(descriptor)

    def test_mutacoes_fora_da_raiz_sao_bloqueadas(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            allowed = directory / "allowed"
            allowed.mkdir()
            outside = directory / "outside"
            outside.write_text("conteúdo", encoding="utf-8")
            inside = allowed / "inside"
            inside.write_text("conteúdo", encoding="utf-8")
            guards = SafetyGuards(allowed_write_root=allowed)
            guards.install()
            try:
                with self.assertRaises(SafetyViolation):
                    (directory / "new-dir").mkdir()
                with self.assertRaises(SafetyViolation):
                    outside.unlink()
                with self.assertRaises(SafetyViolation):
                    outside.rename(allowed / "moved")
                with self.assertRaises(SafetyViolation):
                    os.mkdir(directory / "os-dir")
                with self.assertRaises(SafetyViolation):
                    os.unlink(outside)
                with self.assertRaises(SafetyViolation):
                    os.rename(outside, allowed / "os-moved")
                with self.assertRaises(SafetyViolation):
                    inside.replace(directory / "outside-replaced")
                with self.assertRaises(SafetyViolation):
                    os.replace(inside, directory / "os-outside-replaced")
                self.assertTrue(outside.exists())
                self.assertTrue(inside.exists())
                self.assertGreaterEqual(len(guards.events), 8)
            finally:
                guards.close()

    def test_dir_fd_nao_autoriza_escrita_fora_da_raiz(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            allowed = directory / "allowed"
            outside = directory / "outside"
            allowed.mkdir()
            outside.mkdir()
            (outside / "original").write_text("teste", encoding="utf-8")
            directory_fd = os.open(outside, os.O_RDONLY)
            previous_cwd = Path.cwd()
            try:
                os.chdir(allowed)
                guards = SafetyGuards(allowed_write_root=allowed)
                guards.install()
                try:
                    with self.assertRaises(SafetyViolation):
                        os.open("escaped", os.O_WRONLY | os.O_CREAT, dir_fd=directory_fd)
                    with self.assertRaises(SafetyViolation):
                        os.mkdir("escaped-dir", dir_fd=directory_fd)
                    with self.assertRaises(SafetyViolation):
                        os.rename(
                            "original", "renamed", src_dir_fd=directory_fd,
                            dst_dir_fd=directory_fd,
                        )
                    self.assertEqual(len(guards.events), 3)
                    self.assertFalse((outside / "escaped").exists())
                    self.assertFalse((outside / "escaped-dir").exists())
                    self.assertTrue((outside / "original").exists())
                finally:
                    guards.close()
            finally:
                os.chdir(previous_cwd)
                os.close(directory_fd)


if __name__ == "__main__":
    unittest.main()
