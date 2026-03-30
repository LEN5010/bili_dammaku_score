import argparse
import asyncio
from pathlib import Path

from bilibili_api import login_v2
from bili_live_utils import save_credential


DEFAULT_CREDENTIAL_FILE = Path("data/credential.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Login to Bilibili with a terminal QR code."
    )
    parser.add_argument(
        "--credential-file",
        type=Path,
        default=DEFAULT_CREDENTIAL_FILE,
        help="Where to save the credential JSON",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=2.0,
        help="Polling interval in seconds",
    )
    return parser.parse_args()
async def run() -> None:
    args = parse_args()
    qr_login = login_v2.QrCodeLogin(login_v2.QrCodeLoginChannel.WEB)
    await qr_login.generate_qrcode()

    print("请使用哔哩哔哩 App 扫描并确认登录。")
    print()
    print(qr_login.get_qrcode_terminal())
    print()
    print(f"凭据将保存到: {args.credential_file.resolve()}")

    while True:
        state = await qr_login.check_state()

        if state == login_v2.QrCodeLoginEvents.SCAN:
            print("等待扫码...")
        elif state == login_v2.QrCodeLoginEvents.CONF:
            print("已扫码，等待确认...")
        elif state == login_v2.QrCodeLoginEvents.TIMEOUT:
            print("二维码已过期，请重新运行脚本。")
            return
        elif state == login_v2.QrCodeLoginEvents.DONE:
            credential = qr_login.get_credential()
            save_credential(args.credential_file, credential)

            print("登录成功。")
            print(f"SESSDATA 已保存到: {args.credential_file.resolve()}")
            print("后续可直接运行 raw_event_printer，无需再手动传环境变量。")
            return

        await asyncio.sleep(args.interval)


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\n已取消。")
