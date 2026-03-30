import argparse
import asyncio
import sys
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QThread, Qt, Signal, QVariantAnimation
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtGui import QPixmap

from bilibili_api import live, login_v2

from bili_live_utils import (
    DEFAULT_CREDENTIAL_FILE,
    extract_danmaku_message,
    load_credential,
    save_credential,
)
from score_core import ScoreEntry, ScoreSession, now_text


class QrLoginThread(QThread):
    qr_ready = Signal(bytes)
    status_changed = Signal(str)
    login_successful = Signal(str)
    login_failed = Signal(str)

    def __init__(self, credential_file: Path, interval: float = 2.0) -> None:
        super().__init__()
        self.credential_file = credential_file
        self.interval = interval
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        try:
            asyncio.run(self._run_login())
        except Exception as exc:
            self.login_failed.emit(f"扫码登录失败: {exc}")

    async def _run_login(self) -> None:
        qr_login = login_v2.QrCodeLogin(login_v2.QrCodeLoginChannel.WEB)
        await qr_login.generate_qrcode()
        self.qr_ready.emit(qr_login.get_qrcode_picture().content)

        last_state = None
        while not self._stop_requested:
            state = await qr_login.check_state()
            if state != last_state:
                last_state = state
                if state == login_v2.QrCodeLoginEvents.SCAN:
                    self.status_changed.emit("请使用哔哩哔哩 App 扫码登录")
                elif state == login_v2.QrCodeLoginEvents.CONF:
                    self.status_changed.emit("已扫码，等待你在手机上确认")
                elif state == login_v2.QrCodeLoginEvents.TIMEOUT:
                    self.login_failed.emit("二维码已过期，请点击刷新二维码")
                    return
                elif state == login_v2.QrCodeLoginEvents.DONE:
                    credential = qr_login.get_credential()
                    save_credential(self.credential_file, credential)
                    self.login_successful.emit(str(self.credential_file.resolve()))
                    return

            await asyncio.sleep(self.interval)


class LiveListenerThread(QThread):
    connected = Signal(int)
    connection_state = Signal(str)
    message_received = Signal(object)
    error_occurred = Signal(str)
    closed = Signal()

    def __init__(self, room_id: int, credential_file: Path, debug: bool = False) -> None:
        super().__init__()
        self.room_id = room_id
        self.credential_file = credential_file
        self.debug = debug
        self._loop = None
        self._stop_event = None
        self._client = None

    def stop(self) -> None:
        if self._loop and self._stop_event:
            self._loop.call_soon_threadsafe(self._stop_event.set)

    def run(self) -> None:
        try:
            asyncio.run(self._run_listener())
        except Exception as exc:
            self.error_occurred.emit(f"监听线程异常: {exc}")
        finally:
            self.closed.emit()

    async def _run_listener(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()
        credential = load_credential(self.credential_file)
        self._client = live.LiveDanmaku(
            room_display_id=self.room_id,
            debug=self.debug,
            credential=credential,
        )

        @self._client.on("VERIFICATION_SUCCESSFUL")
        async def _on_verified(_: dict) -> None:
            self.connection_state.emit("connected")
            self.connected.emit(self.room_id)

        @self._client.on("LIVE")
        async def _on_live(_: dict) -> None:
            self.connection_state.emit("live")

        @self._client.on("PREPARING")
        async def _on_preparing(_: dict) -> None:
            self.connection_state.emit("preparing")

        @self._client.on("DANMU_MSG")
        async def _on_danmaku(event: dict) -> None:
            message = extract_danmaku_message(event)
            if message is not None:
                self.message_received.emit(message)

        connect_task = asyncio.create_task(self._client.connect())
        stop_task = asyncio.create_task(self._stop_event.wait())

        done, _ = await asyncio.wait(
            {connect_task, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        if stop_task in done:
            if self._client.get_status() == live.LiveDanmaku.STATUS_ESTABLISHED:
                await self._client.disconnect()
            elif not connect_task.done():
                connect_task.cancel()
                try:
                    await connect_task
                except asyncio.CancelledError:
                    pass
            return

        try:
            await connect_task
        except Exception as exc:
            self.error_occurred.emit(f"直播间连接失败: {exc}")


class AnimatedValueLabel(QLabel):
    def __init__(self, decimals: int = 0, suffix: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.decimals = decimals
        self.suffix = suffix
        self._current_value = 0.0
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(340)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(self._on_value_changed)
        self.setText(self._format_value(0.0))

    def _format_value(self, value: float) -> str:
        if self.decimals == 0:
            return f"{int(round(value))}{self.suffix}"
        return f"{value:.{self.decimals}f}{self.suffix}"

    def _on_value_changed(self, value) -> None:
        self._current_value = float(value)
        self.setText(self._format_value(self._current_value))

    def set_animated_value(self, value: float) -> None:
        self._animation.stop()
        self._animation.setStartValue(self._current_value)
        self._animation.setEndValue(float(value))
        self._animation.start()

    def set_immediate_value(self, value: float) -> None:
        self._animation.stop()
        self._current_value = float(value)
        self.setText(self._format_value(self._current_value))


class StatCard(QFrame):
    def __init__(
        self,
        title: str,
        decimals: int = 0,
        accent: str = "#EB6A4B",
        suffix: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("StatCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(8)

        title_label = QLabel(title)
        title_label.setObjectName("CardTitle")
        title_label.setStyleSheet(f"color: {accent};")

        self.value_label = AnimatedValueLabel(decimals=decimals, suffix=suffix)
        self.value_label.setObjectName("CardValue")

        self.detail_label = QLabel("")
        self.detail_label.setObjectName("CardDetail")

        layout.addWidget(title_label)
        layout.addWidget(self.value_label)
        layout.addWidget(self.detail_label)

    def set_value(self, value: float) -> None:
        self.value_label.set_animated_value(value)

    def set_immediate_value(self, value: float) -> None:
        self.value_label.set_immediate_value(value)

    def set_detail(self, text: str) -> None:
        self.detail_label.setText(text)


class ReportDialog(QDialog):
    def __init__(self, report: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("本轮统计结果")
        self.resize(720, 480)

        layout = QVBoxLayout(self)
        editor = QPlainTextEdit()
        editor.setReadOnly(True)
        editor.setPlainText(report)

        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.accept)

        layout.addWidget(editor)
        layout.addWidget(close_button, alignment=Qt.AlignmentFlag.AlignRight)


class LoginPage(QWidget):
    refresh_requested = Signal()
    use_saved_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(48, 48, 48, 48)
        layout.setSpacing(18)

        badge = QLabel("BILI LIVE SCORE")
        badge.setObjectName("Badge")

        title = QLabel("扫码登录")
        title.setObjectName("HeroTitle")

        description = QLabel("使用哔哩哔哩 App 扫描二维码。登录成功后自动进入主界面。")
        description.setWordWrap(True)
        description.setObjectName("HeroBody")

        self.qr_label = QLabel("二维码生成中…")
        self.qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr_label.setObjectName("QrFrame")
        self.qr_label.setMinimumSize(280, 280)

        self.status_label = QLabel("准备生成二维码")
        self.status_label.setObjectName("StatusLabel")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        button_row = QHBoxLayout()
        self.refresh_button = QPushButton("刷新二维码")
        self.saved_button = QPushButton("使用本地登录")
        self.saved_button.setObjectName("GhostButton")
        button_row.addWidget(self.refresh_button)
        button_row.addWidget(self.saved_button)

        layout.addWidget(badge, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(title)
        layout.addWidget(description)
        layout.addSpacing(12)
        layout.addWidget(self.qr_label, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status_label)
        layout.addLayout(button_row)
        layout.addStretch(1)

        self.refresh_button.clicked.connect(self.refresh_requested)
        self.saved_button.clicked.connect(self.use_saved_requested)

    def set_saved_available(self, available: bool) -> None:
        self.saved_button.setVisible(available)

    def set_qr_content(self, content: bytes) -> None:
        pixmap = QPixmap()
        pixmap.loadFromData(content)
        self.qr_label.setPixmap(
            pixmap.scaled(
                280,
                280,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)


class MainPage(QWidget):
    connect_requested = Signal(int)
    disconnect_requested = Signal()
    start_requested = Signal()
    stop_requested = Signal()
    reset_requested = Signal()
    relogin_requested = Signal()

    def __init__(self, initial_room_id: int | None = None) -> None:
        super().__init__()

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(18)

        header = QHBoxLayout()
        left_header = QVBoxLayout()
        title = QLabel("Live Scoreboard")
        title.setObjectName("MainTitle")
        subtitle = QLabel("首条有效整数分数会被记入本轮统计。")
        subtitle.setObjectName("MainSubtitle")
        left_header.addWidget(title)
        left_header.addWidget(subtitle)

        right_header = QHBoxLayout()
        self.connection_chip = QLabel("未连接")
        self.connection_chip.setObjectName("ConnectionChip")
        self.relogin_button = QPushButton("重新扫码登录")
        self.relogin_button.setObjectName("GhostButton")
        right_header.addWidget(self.connection_chip)
        right_header.addWidget(self.relogin_button)

        header.addLayout(left_header, stretch=1)
        header.addLayout(right_header)

        control_bar = QFrame()
        control_bar.setObjectName("ControlBar")
        control_layout = QHBoxLayout(control_bar)
        control_layout.setContentsMargins(18, 14, 18, 14)
        control_layout.setSpacing(12)

        room_label = QLabel("直播间")
        self.room_input = QLineEdit()
        self.room_input.setPlaceholderText("输入直播间号")
        if initial_room_id:
            self.room_input.setText(str(initial_room_id))

        self.connect_button = QPushButton("连接")
        self.disconnect_button = QPushButton("断开")
        self.start_button = QPushButton("开始统计")
        self.stop_button = QPushButton("结束统计")
        self.reset_button = QPushButton("清空")
        self.disconnect_button.setObjectName("GhostButton")
        self.reset_button.setObjectName("GhostButton")

        control_layout.addWidget(room_label)
        control_layout.addWidget(self.room_input, stretch=1)
        control_layout.addWidget(self.connect_button)
        control_layout.addWidget(self.disconnect_button)
        control_layout.addWidget(self.start_button)
        control_layout.addWidget(self.stop_button)
        control_layout.addWidget(self.reset_button)

        stats_grid = QGridLayout()
        stats_grid.setHorizontalSpacing(14)
        stats_grid.setVerticalSpacing(14)
        self.total_card = StatCard("总分", accent="#EB6A4B")
        self.users_card = StatCard("有效人数", accent="#2A7F62")
        self.avg_card = StatCard("平均分", decimals=2, accent="#2463EB")
        self.invalid_card = StatCard("无效弹幕", accent="#7E8794")
        self.total_card.set_detail("本轮累计得分")
        self.users_card.set_detail("首次有效记录用户数")
        self.avg_card.set_detail("总分 / 有效人数")
        self.invalid_card.set_detail("无效 + 重复 + 缺少身份")
        stats_grid.addWidget(self.total_card, 0, 0)
        stats_grid.addWidget(self.users_card, 0, 1)
        stats_grid.addWidget(self.avg_card, 1, 0)
        stats_grid.addWidget(self.invalid_card, 1, 1)

        content_row = QHBoxLayout()
        content_row.setSpacing(14)

        records_panel = QFrame()
        records_panel.setObjectName("Panel")
        records_layout = QVBoxLayout(records_panel)
        records_layout.setContentsMargins(18, 16, 18, 16)
        records_layout.setSpacing(10)
        records_title = QLabel("最新有效记分")
        records_title.setObjectName("PanelTitle")
        self.records_list = QListWidget()
        self.records_list.setUniformItemSizes(True)
        records_layout.addWidget(records_title)
        records_layout.addWidget(self.records_list)

        log_panel = QFrame()
        log_panel.setObjectName("Panel")
        log_layout = QVBoxLayout(log_panel)
        log_layout.setContentsMargins(18, 16, 18, 16)
        log_layout.setSpacing(10)
        log_title = QLabel("运行日志")
        log_title.setObjectName("PanelTitle")
        self.log_output = QPlainTextEdit()
        self.log_output.setReadOnly(True)
        log_layout.addWidget(log_title)
        log_layout.addWidget(self.log_output)

        content_row.addWidget(records_panel, stretch=5)
        content_row.addWidget(log_panel, stretch=6)

        root.addLayout(header)
        root.addWidget(control_bar)
        root.addLayout(stats_grid)
        root.addLayout(content_row, stretch=1)

        self.connect_button.clicked.connect(self._emit_connect)
        self.disconnect_button.clicked.connect(self.disconnect_requested)
        self.start_button.clicked.connect(self.start_requested)
        self.stop_button.clicked.connect(self.stop_requested)
        self.reset_button.clicked.connect(self.reset_requested)
        self.relogin_button.clicked.connect(self.relogin_requested)

        self.set_connected(False)
        self.set_session_active(False)
        self.update_snapshot(
            {
                "active": False,
                "users": 0,
                "total_score": 0,
                "average_score": 0.0,
                "invalid_messages": 0,
                "duplicate_messages": 0,
                "missing_identity_messages": 0,
            }
        )

    def _emit_connect(self) -> None:
        raw = self.room_input.text().strip()
        if not raw.isdigit():
            QMessageBox.warning(self, "房间号错误", "请输入有效的纯数字直播间号。")
            return
        self.connect_requested.emit(int(raw))

    def set_connection_text(self, text: str) -> None:
        self.connection_chip.setText(text)

    def set_connected(self, connected: bool) -> None:
        self.connect_button.setEnabled(not connected)
        self.disconnect_button.setEnabled(connected)
        self.start_button.setEnabled(connected)

    def set_session_active(self, active: bool) -> None:
        self.stop_button.setEnabled(active)
        self.reset_button.setEnabled(not active)
        self.room_input.setEnabled(not active)

    def update_snapshot(self, snapshot: dict) -> None:
        self.total_card.set_value(snapshot.get("total_score", 0))
        self.users_card.set_value(snapshot.get("users", 0))
        self.avg_card.set_value(snapshot.get("average_score", 0.0))
        ignored = (
            snapshot.get("invalid_messages", 0)
            + snapshot.get("duplicate_messages", 0)
            + snapshot.get("missing_identity_messages", 0)
        )
        self.invalid_card.set_value(ignored)
        self.invalid_card.set_detail(
            "无效 {0} / 重复 {1} / 缺身份 {2}".format(
                snapshot.get("invalid_messages", 0),
                snapshot.get("duplicate_messages", 0),
                snapshot.get("missing_identity_messages", 0),
            )
        )

    def prepend_record(self, entry: ScoreEntry) -> None:
        identity = f"UID {entry.uid}" if entry.uid else f"HASH {entry.user_hash}"
        text = f"{entry.accepted_at}  {entry.uname}  {entry.score}分  {identity}"
        self.records_list.insertItem(0, QListWidgetItem(text))
        while self.records_list.count() > 200:
            self.records_list.takeItem(self.records_list.count() - 1)

    def clear_records(self) -> None:
        self.records_list.clear()

    def append_log(self, text: str) -> None:
        self.log_output.appendPlainText(f"[{now_text()}] {text}")


class MainWindow(QMainWindow):
    def __init__(self, credential_file: Path, initial_room_id: int | None, debug: bool) -> None:
        super().__init__()
        self.credential_file = credential_file
        self.debug = debug
        self.login_thread: QrLoginThread | None = None
        self.listener_thread: LiveListenerThread | None = None
        self.session = ScoreSession()

        self.setWindowTitle("Bili Live Scoreboard")
        self.resize(1220, 820)
        self.setMinimumSize(1080, 720)

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        self.login_page = LoginPage()
        self.main_page = MainPage(initial_room_id=initial_room_id)
        self.stack.addWidget(self.login_page)
        self.stack.addWidget(self.main_page)

        self.login_page.refresh_requested.connect(self.start_qr_login)
        self.login_page.use_saved_requested.connect(self.enter_main_page)

        self.main_page.connect_requested.connect(self.connect_room)
        self.main_page.disconnect_requested.connect(self.disconnect_room)
        self.main_page.start_requested.connect(self.start_session)
        self.main_page.stop_requested.connect(self.stop_session)
        self.main_page.reset_requested.connect(self.reset_session)
        self.main_page.relogin_requested.connect(self.show_login_page)

        self.login_page.set_saved_available(self.credential_file.exists())
        self.apply_style()

        if self.credential_file.exists():
            self.enter_main_page()
            self.main_page.append_log(f"已检测到登录凭据: {self.credential_file.resolve()}")
        else:
            self.show_login_page()

    def apply_style(self) -> None:
        self.setStyleSheet(
            """
            QWidget {
              color: #1F2A37;
              font-family: "Avenir Next", "PingFang SC", sans-serif;
              font-size: 14px;
            }
            QMainWindow {
              background: #F4EFE7;
            }
            QStackedWidget, LoginPage, MainPage {
              background: #F4EFE7;
            }
            QLabel {
              background: transparent;
            }
            #Badge {
              color: #EB6A4B;
              font-size: 12px;
              letter-spacing: 2px;
              font-weight: 700;
            }
            #HeroTitle {
              font-size: 38px;
              font-weight: 700;
              color: #19202A;
            }
            #HeroBody, #MainSubtitle, #CardDetail, #StatusLabel {
              color: #5B6673;
            }
            #QrFrame {
              background: #FFFDF8;
              border: 1px solid #E3D8CA;
              border-radius: 28px;
              padding: 20px;
            }
            #MainTitle {
              font-size: 30px;
              font-weight: 700;
              color: #19202A;
            }
            #ConnectionChip {
              background: #FEE9D7;
              color: #A64E31;
              border-radius: 14px;
              padding: 8px 14px;
              font-weight: 700;
            }
            #ControlBar, #Panel, #StatCard {
              background: #FFFDF8;
              border: 1px solid #E3D8CA;
              border-radius: 24px;
            }
            #CardTitle {
              font-size: 13px;
              font-weight: 700;
              letter-spacing: 1px;
            }
            #CardValue {
              font-size: 42px;
              font-weight: 800;
              color: #19202A;
            }
            #PanelTitle {
              font-size: 16px;
              font-weight: 700;
            }
            QLineEdit {
              background: #FFF8EE;
              border: 1px solid #D9CBB8;
              border-radius: 16px;
              padding: 10px 14px;
            }
            QPushButton {
              background: #1F2A37;
              color: #FFFDF8;
              border: none;
              border-radius: 16px;
              padding: 10px 16px;
              font-weight: 700;
            }
            QPushButton:hover {
              background: #243140;
            }
            QPushButton:disabled {
              background: #C8C0B5;
              color: #F8F3ED;
            }
            #GhostButton {
              background: #EFE4D6;
              color: #4F5B69;
            }
            #GhostButton:hover {
              background: #E7D9C7;
            }
            QListWidget, QPlainTextEdit {
              background: #FFFCF7;
              border: 1px solid #E8DDCF;
              border-radius: 18px;
              padding: 8px;
            }
            """
        )

    def show_login_page(self) -> None:
        self.disconnect_room()
        self.stack.setCurrentWidget(self.login_page)
        self.login_page.set_saved_available(self.credential_file.exists())
        self.login_page.set_status("准备生成二维码")
        self.start_qr_login()

    def enter_main_page(self) -> None:
        self.stop_login_thread()
        self.stack.setCurrentWidget(self.main_page)
        self.login_page.set_saved_available(self.credential_file.exists())

    def start_qr_login(self) -> None:
        self.stop_login_thread()
        self.login_page.set_status("二维码生成中…")
        self.login_thread = QrLoginThread(self.credential_file)
        self.login_thread.qr_ready.connect(self.login_page.set_qr_content)
        self.login_thread.status_changed.connect(self.login_page.set_status)
        self.login_thread.login_successful.connect(self.on_login_successful)
        self.login_thread.login_failed.connect(self.on_login_failed)
        self.login_thread.finished.connect(self.on_login_thread_finished)
        self.login_thread.start()

    def stop_login_thread(self) -> None:
        if self.login_thread is None:
            return
        self.login_thread.stop()
        self.login_thread.wait(3000)
        self.login_thread = None

    def on_login_thread_finished(self) -> None:
        if self.login_thread and not self.login_thread.isRunning():
            self.login_thread = None

    def on_login_successful(self, path: str) -> None:
        self.login_page.set_status("登录成功，正在进入主界面")
        self.main_page.append_log(f"扫码登录成功，凭据已保存到 {path}")
        self.enter_main_page()

    def on_login_failed(self, message: str) -> None:
        self.login_page.set_status(message)
        QMessageBox.information(self, "扫码登录", message)

    def connect_room(self, room_id: int) -> None:
        self.disconnect_room()
        self.main_page.set_connection_text(f"连接中 · 房间 {room_id}")
        self.main_page.append_log(f"开始连接直播间 {room_id}")

        self.listener_thread = LiveListenerThread(
            room_id=room_id,
            credential_file=self.credential_file,
            debug=self.debug,
        )
        self.listener_thread.connected.connect(self.on_room_connected)
        self.listener_thread.connection_state.connect(self.on_connection_state)
        self.listener_thread.message_received.connect(self.on_message_received)
        self.listener_thread.error_occurred.connect(self.on_listener_error)
        self.listener_thread.closed.connect(self.on_listener_closed)
        self.listener_thread.start()

    def disconnect_room(self) -> None:
        if self.session.active:
            self.session.stop()
            self.main_page.update_snapshot(self.session.snapshot())
            self.main_page.set_session_active(False)
            self.main_page.append_log("直播连接断开，当前统计已自动结束")

        if self.listener_thread is None:
            self.main_page.set_connected(False)
            self.main_page.set_connection_text("未连接")
            return

        self.listener_thread.stop()
        self.listener_thread.wait(4000)
        self.listener_thread = None
        self.main_page.set_connected(False)
        self.main_page.set_connection_text("未连接")
        self.main_page.append_log("已断开直播间连接")

    def on_room_connected(self, room_id: int) -> None:
        self.main_page.set_connected(True)
        self.main_page.set_connection_text(f"已连接 · 房间 {room_id}")
        self.main_page.append_log(f"已连接直播间 {room_id}")

    def on_connection_state(self, state: str) -> None:
        mapping = {
            "connected": "已连接",
            "live": "直播中",
            "preparing": "准备中",
        }
        self.main_page.set_connection_text(mapping.get(state, state))
        self.main_page.append_log(f"直播状态更新: {mapping.get(state, state)}")

    def on_listener_error(self, message: str) -> None:
        self.main_page.append_log(message)
        QMessageBox.warning(self, "直播连接异常", message)

    def on_listener_closed(self) -> None:
        if self.listener_thread and not self.listener_thread.isRunning():
            self.listener_thread = None
        self.main_page.set_connected(False)
        self.main_page.set_connection_text("未连接")

    def start_session(self) -> None:
        if self.listener_thread is None:
            QMessageBox.information(self, "开始统计", "请先连接直播间。")
            return

        self.session.start()
        self.main_page.clear_records()
        self.main_page.update_snapshot(self.session.snapshot())
        self.main_page.set_session_active(True)
        self.main_page.append_log("开始一轮新的统计")

    def stop_session(self) -> None:
        if not self.session.started_at:
            QMessageBox.information(self, "结束统计", "当前没有可结束的统计轮次。")
            return

        if self.session.active:
            self.session.stop()
        self.main_page.update_snapshot(self.session.snapshot())
        self.main_page.set_session_active(False)
        report = self.session.render_report()
        self.main_page.append_log("结束统计并生成报告")
        ReportDialog(report, self).exec()

    def reset_session(self) -> None:
        self.session.reset()
        self.main_page.clear_records()
        self.main_page.update_snapshot(self.session.snapshot())
        self.main_page.set_session_active(False)
        self.main_page.append_log("已清空当前统计状态")

    def on_message_received(self, message) -> None:
        decision, entry = self.session.accept_message(message)
        if decision == "accepted" and entry is not None:
            self.main_page.prepend_record(entry)
            self.main_page.update_snapshot(self.session.snapshot())
            identity = f"uid={entry.uid}" if entry.uid else f"user_hash={entry.user_hash}"
            self.main_page.append_log(
                f"记分成功 {entry.uname} -> {entry.score} 分 ({identity})"
            )
            return

        if self.session.active and decision != "inactive":
            self.main_page.update_snapshot(self.session.snapshot())

    def closeEvent(self, event) -> None:
        self.stop_login_thread()
        self.disconnect_room()
        super().closeEvent(event)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bilibili live danmaku GUI scoreboard.")
    parser.add_argument(
        "--credential-file",
        type=Path,
        default=DEFAULT_CREDENTIAL_FILE,
        help="Credential JSON path",
    )
    parser.add_argument(
        "--room-id",
        type=int,
        default=None,
        help="Optional initial live room ID",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable bilibili-api debug logging",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    app = QApplication(sys.argv)
    app.setApplicationName("Bili Live Scoreboard")
    app.setFont(QFont("Avenir Next", 13))

    window = MainWindow(
        credential_file=args.credential_file,
        initial_room_id=args.room_id,
        debug=args.debug,
    )
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
