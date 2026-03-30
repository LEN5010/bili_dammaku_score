import argparse
import asyncio
from pathlib import Path
import signal

from bilibili_api import live

from bili_live_utils import (
    DEFAULT_CREDENTIAL_FILE,
    extract_danmaku_message,
    load_credential,
)
from score_core import ScoreSession, now_text


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Interactive Bilibili live danmaku score counter."
    )
    parser.add_argument("room_id", type=int, help="Bilibili live room display ID")
    parser.add_argument(
        "--credential-file",
        type=Path,
        default=DEFAULT_CREDENTIAL_FILE,
        help="Credential JSON path",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable bilibili-api debug logging",
    )
    parser.add_argument(
        "--show-ignored",
        action="store_true",
        help="Print invalid or duplicate danmaku decisions",
    )
    return parser.parse_args()


def print_help() -> None:
    print("可用命令:")
    print("  start  开始一轮新的统计，并清空当前计分")
    print("  stop   停止统计，并输出本轮结果")
    print("  status 查看当前统计状态")
    print("  reset  清空当前统计，但不自动开始")
    print("  help   显示帮助")
    print("  quit   退出程序")


async def command_loop(
    client: live.LiveDanmaku,
    connect_task: asyncio.Task,
    session: ScoreSession,
) -> None:
    while True:
        try:
            raw = await asyncio.to_thread(input, "score> ")
        except EOFError:
            raw = "quit"

        command = raw.strip().lower()
        if not command:
            continue

        if command == "start":
            session.start()
            print(f"[{now_text()}] 已开始统计。")
        elif command == "stop":
            if not session.started_at:
                print("当前还没有开始过统计。")
                continue
            if session.active:
                session.stop()
            print(session.render_report())
        elif command == "status":
            print(session.status_line())
        elif command == "reset":
            session.reset()
            print("已清空当前统计状态。")
        elif command == "help":
            print_help()
        elif command in {"quit", "exit"}:
            if session.active:
                session.stop()
                print(session.render_report())
            if client.get_status() == live.LiveDanmaku.STATUS_ESTABLISHED:
                await client.disconnect()
            else:
                connect_task.cancel()
                try:
                    await connect_task
                except asyncio.CancelledError:
                    pass
            return
        else:
            print("未知命令，输入 help 查看可用命令。")


async def run() -> None:
    args = parse_args()
    credential = load_credential(args.credential_file)

    if credential is None:
        print("未找到登录凭据，将以匿名身份连接。")
        print("匿名连接时 UID 可能不可用，届时会退化为按 user_hash 去重。")
    else:
        print(f"已加载凭据: {args.credential_file.resolve()}")

    client = live.LiveDanmaku(
        room_display_id=args.room_id,
        debug=args.debug,
        credential=credential,
    )
    session = ScoreSession()
    connected_event = asyncio.Event()
    stop_event = asyncio.Event()

    @client.on("VERIFICATION_SUCCESSFUL")
    async def _on_verified(_: dict) -> None:
        print(f"[{now_text()}] 已连接直播间 {args.room_id}")
        print("仅接受 0-10 的纯整数弹幕。")
        print_help()
        connected_event.set()

    @client.on("LIVE")
    async def _on_live(_: dict) -> None:
        print(f"[{now_text()}] 直播状态: 开播中")

    @client.on("PREPARING")
    async def _on_preparing(_: dict) -> None:
        print(f"[{now_text()}] 直播状态: 准备中 / 可能已下播")

    @client.on("DANMU_MSG")
    async def _on_danmaku(event: dict) -> None:
        message = extract_danmaku_message(event)
        if message is None:
            return

        decision, entry = session.accept_message(message)
        if decision == "accepted" and entry is not None:
            identity = f"uid={entry.uid}" if entry.uid else f"user_hash={entry.user_hash}"
            print(
                f"[{entry.accepted_at}] 记分成功 score={entry.score} user={entry.uname} "
                f"{identity} total={session.total_score} users={len(session.entries)}"
            )
        elif args.show_ignored and session.active:
            print(
                f"[{now_text()}] 已忽略 decision={decision} "
                f"user={message.uname or '未知用户'} text={message.text!r}"
            )

    loop = asyncio.get_running_loop()

    def _request_stop() -> None:
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            pass

    connect_task = asyncio.create_task(client.connect())
    stop_task = asyncio.create_task(stop_event.wait())
    connected_task = asyncio.create_task(connected_event.wait())

    done, _ = await asyncio.wait(
        {connect_task, stop_task, connected_task},
        return_when=asyncio.FIRST_COMPLETED,
    )

    if not connected_task.done():
        connected_task.cancel()
        try:
            await connected_task
        except asyncio.CancelledError:
            pass

    if stop_task in done and stop_event.is_set():
        connect_task.cancel()
        try:
            await connect_task
        except asyncio.CancelledError:
            pass
        return

    if connect_task in done and not connected_event.is_set():
        await connect_task
        return

    command_task = asyncio.create_task(command_loop(client, connect_task, session))
    done, _ = await asyncio.wait(
        {connect_task, command_task, stop_task},
        return_when=asyncio.FIRST_COMPLETED,
    )

    if stop_task in done and stop_event.is_set():
        if session.active:
            session.stop()
            print(session.render_report())
        if client.get_status() == live.LiveDanmaku.STATUS_ESTABLISHED:
            await client.disconnect()
        else:
            connect_task.cancel()
            try:
                await connect_task
            except asyncio.CancelledError:
                pass
        if not command_task.done():
            command_task.cancel()
            try:
                await command_task
            except asyncio.CancelledError:
                pass
        return

    if command_task in done:
        await command_task
        if not connect_task.done():
            try:
                await connect_task
            except asyncio.CancelledError:
                pass
        return

    await connect_task
    if not command_task.done():
        command_task.cancel()
        try:
            await command_task
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
