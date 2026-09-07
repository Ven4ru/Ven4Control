import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock
from uuid import uuid4

from PySide6.QtCore import QCoreApplication
from PySide6.QtNetwork import QAbstractSocket, QLocalServer

import ven4control
from ven4control.single_instance import SingleInstanceGuard


# Имя нарочно не боевое: на машине пользователя приложение может быть
# запущено прямо во время прогона тестов.
TEST_SERVER_NAME = "Ven4Control-instance-tests"

SOURCE_ROOT = Path(ven4control.__file__).resolve().parent.parent

# Второй экземпляр запускается отдельным процессом: блокирующее ожидание
# отправки удерживает GIL, и внутри одного процесса главный просто не успел бы
# принять сообщение. В жизни это и есть два разных процесса.
CHILD_CODE = """
import sys

sys.path.insert(0, sys.argv[1])
from PySide6.QtCore import QCoreApplication
from ven4control.single_instance import SingleInstanceGuard

QCoreApplication([])
guard = SingleInstanceGuard(sys.argv[2])
acquired = guard.try_acquire()
sent = guard.notify_show()
sys.exit(0 if not acquired and sent else 1)
"""


class SingleInstanceGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self) -> None:
        self.guards: list[SingleInstanceGuard] = []
        # Своё имя на каждый тест: сервер прошлого теста остаётся жив до выхода
        # из процесса и иначе занимал бы имя.
        self.name = f"{TEST_SERVER_NAME}-{uuid4().hex[:8]}"

    def tearDown(self) -> None:
        for guard in self.guards:
            for server in guard.findChildren(QLocalServer):
                server.close()
        QLocalServer.removeServer(self.name)

    def _guard(self) -> SingleInstanceGuard:
        guard = SingleInstanceGuard(self.name)
        self.guards.append(guard)
        return guard

    def _wait_for(self, condition, seconds: float = 15.0) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.application.processEvents()
            if condition():
                return True
            time.sleep(0.01)
        return bool(condition())

    def test_first_process_becomes_the_main_one(self) -> None:
        self.assertTrue(self._guard().try_acquire())

    def test_second_guard_loses_the_race(self) -> None:
        self.assertTrue(self._guard().try_acquire())
        self.assertFalse(self._guard().try_acquire())

    def test_second_process_asks_the_first_to_show_the_window(self) -> None:
        first = self._guard()
        self.assertTrue(first.try_acquire())
        requested: list[bool] = []
        first.show_requested.connect(lambda: requested.append(True))

        child = subprocess.Popen(
            [sys.executable, "-c", CHILD_CODE, str(SOURCE_ROOT), self.name]
        )
        try:
            self.assertTrue(self._wait_for(lambda: bool(requested)))
        finally:
            child.wait(30)
        # Ноль означает, что второй процесс и гонку проиграл, и сообщение отдал.
        self.assertEqual(0, child.returncode)

    def test_message_to_nobody_is_not_delivered(self) -> None:
        self.assertFalse(self._guard().notify_show())

    def test_busy_name_sends_the_process_back_to_the_main_one(self) -> None:
        """Главный процесс успел подняться между проверкой и listen().

        Живой гонки здесь не устроить — нужна синхронизация двух процессов с
        точностью до микросекунд, поэтому занятое имя изображается подменой.
        """
        guard = self._guard()
        with mock.patch.object(QLocalServer, "listen", return_value=False), \
                mock.patch.object(
                    QLocalServer,
                    "serverError",
                    return_value=QAbstractSocket.SocketError.AddressInUseError,
                ), \
                mock.patch.object(guard, "_peer_exists", side_effect=[False, True]):
            self.assertFalse(guard.try_acquire())

    def test_other_listen_failures_do_not_stop_the_application(self) -> None:
        guard = self._guard()
        with mock.patch.object(QLocalServer, "listen", return_value=False), \
                mock.patch.object(
                    QLocalServer,
                    "serverError",
                    return_value=(
                        QAbstractSocket.SocketError.SocketAccessError
                    ),
                ), \
                mock.patch.object(guard, "_peer_exists", return_value=False):
            self.assertTrue(guard.try_acquire())

    def test_abandoned_socket_does_not_block_startup(self) -> None:
        """Аварийно завершённый процесс оставляет имя занятым."""
        leftover = QLocalServer()
        self.addCleanup(leftover.close)
        leftover.listen(self.name)
        leftover.close()

        self.assertTrue(self._guard().try_acquire())


if __name__ == "__main__":
    unittest.main()
