from __future__ import annotations

import io
import os
import socket
import tempfile
import unittest
from pathlib import Path

from harness_support.safe_runtime import SafetyGuards, SafetyViolation


class GuardOperationsTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
