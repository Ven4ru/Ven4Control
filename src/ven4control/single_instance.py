"""Защита от второго запущенного экземпляра приложения.

Нужна вместе с задачей планировщика: после загрузки без входа приложение уже
работает, а вход пользователя запускает его второй раз через `HKCU\\...\\Run`.
Два процесса открыли бы независимые SSH-сессии к одним и тем же устройствам и
показали бы два значка в трее.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QAbstractSocket, QLocalServer, QLocalSocket


SERVER_NAME = "Ven4Control-instance"

SHOW_MESSAGE = b"show"

CONNECT_TIMEOUT = 300
WRITE_TIMEOUT = 1000
READ_TIMEOUT = 300


class SingleInstanceGuard(QObject):
    """Определяет, главный ли это процесс, и принимает просьбы показать окно."""

    show_requested = Signal()

    def __init__(self, name: str = SERVER_NAME, parent: QObject | None = None):
        super().__init__(parent)
        self._name = name
        self._server: QLocalServer | None = None

    def try_acquire(self) -> bool:
        """True, если процесс стал главным; False — приложение уже работает."""
        if self._peer_exists():
            return False
        # Аварийно завершённый процесс оставляет за собой сокет, который иначе
        # навсегда занимает имя и не даёт слушать.
        QLocalServer.removeServer(self._name)
        server = QLocalServer(self)
        if not server.listen(self._name):
            if server.serverError() == QAbstractSocket.SocketError.AddressInUseError:
                # Имя занято, хотя проверка никого не нашла: главный процесс
                # успел подняться между проверкой и этой строкой. Спрашиваем
                # ещё раз, теперь он уже отвечает.
                return not self._peer_exists()
            # Отказ по другой причине (права, окружение): работа без защиты от
            # дублирования лучше отказа запуститься.
            return True
        server.newConnection.connect(self._accept)
        self._server = server
        return True

    def _peer_exists(self) -> bool:
        probe = QLocalSocket()
        probe.connectToServer(self._name)
        if not probe.waitForConnected(CONNECT_TIMEOUT):
            return False
        probe.disconnectFromServer()
        return True

    def notify_show(self) -> bool:
        """Просит уже работающий процесс показать своё окно."""
        socket = QLocalSocket()
        socket.connectToServer(self._name)
        if not socket.waitForConnected(CONNECT_TIMEOUT):
            return False
        socket.write(SHOW_MESSAGE)
        # Ждать отправки обязательно: процесс завершается сразу после вызова, а
        # сокет с неотправленными байтами уносит сообщение с собой. Метод
        # flush() здесь не помогает — без цикла событий он не доводит запись до
        # конца и оставляет байты в буфере.
        delivered = socket.waitForBytesWritten(WRITE_TIMEOUT)
        socket.disconnectFromServer()
        return delivered

    def _accept(self) -> None:
        if self._server is None:
            return
        while True:
            connection = self._server.nextPendingConnection()
            if connection is None:
                return
            # Сообщение читается сразу: отправитель успевает закрыть сокет
            # раньше, чем очередь событий дошла бы до readyRead, и просьба
            # показать окно терялась бы. Ждать приходится единицы миллисекунд.
            if connection.bytesAvailable() or connection.waitForReadyRead(READ_TIMEOUT):
                if SHOW_MESSAGE in bytes(connection.readAll()):
                    self.show_requested.emit()
            connection.disconnectFromServer()
            connection.deleteLater()
