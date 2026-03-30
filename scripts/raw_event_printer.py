import argparse
import asyncio
import json
import signal
from datetime import datetime

from bilibili_api import live

from bili_live_utils import build_summary, load_credential


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Connect to a Bilibili live room and print raw events."
    )
    parser.add_argument("room_id", type=int, help="Bilibili live room display ID")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Print all events instead of only DANMU_MSG",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable bilibili-api debug logging",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Print parsed summary fields before the raw event payload",
    )
    return parser.parse_args()


def dump_event(event: dict) -> None:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[{stamp}] EVENT {event.get('type', 'UNKNOWN')}")
    print(json.dumps(event, ensure_ascii=False, indent=2, default=str))

def print_summary(event: dict) -> None:
    summary = build_summary(event)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[{stamp}] SUMMARY {event.get('type', 'UNKNOWN')}")
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


async def shutdown(client: live.LiveDanmaku, connect_task: asyncio.Task) -> None:
    if client.get_status() == live.LiveDanmaku.STATUS_ESTABLISHED:
        await client.disconnect()
        return

    connect_task.cancel()
    try:
        await connect_task
    except asyncio.CancelledError:
        pass


async def run() -> None:
    args = parse_args()
    credential = load_credential()
    client = live.LiveDanmaku(
        room_display_id=args.room_id,
        debug=args.debug,
        credential=credential,
    )

    @client.on("VERIFICATION_SUCCESSFUL")
    async def _on_verified(event: dict) -> None:
        dump_event(event)

    @client.on("LIVE")
    async def _on_live(event: dict) -> None:
        dump_event(event)

    @client.on("PREPARING")
    async def _on_preparing(event: dict) -> None:
        dump_event(event)

    if args.all:

        @client.on("ALL")
        async def _on_all(event: dict) -> None:
            if args.summary_only:
                print_summary(event)
            dump_event(event)

    else:

        @client.on("DANMU_MSG")
        async def _on_danmaku(event: dict) -> None:
            if args.summary_only:
                print_summary(event)
            dump_event(event)

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _request_stop() -> None:
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            pass

    connect_task = asyncio.create_task(client.connect())

    done, _ = await asyncio.wait(
        {connect_task, asyncio.create_task(stop_event.wait())},
        return_when=asyncio.FIRST_COMPLETED,
    )

    if stop_event.is_set():
        await shutdown(client, connect_task)
        return

    if connect_task in done:
        await connect_task


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
