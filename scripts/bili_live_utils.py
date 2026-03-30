import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from bilibili_api import Credential


DEFAULT_CREDENTIAL_FILE = Path("data/credential.json")
SCORE_PATTERN = re.compile(r"(10|[0-9])")


@dataclass(slots=True)
class DanmakuMessage:
    text: str
    uname: str | None
    uid: int | None
    user_hash: str | None
    dm_type: int | None
    emoticon_unique: str | None

    @property
    def participant_key(self) -> str | None:
        if self.uid and self.uid > 0:
            return f"uid:{self.uid}"
        if self.user_hash:
            return f"user_hash:{self.user_hash}"
        return None


def load_credential(
    credential_file: Path = DEFAULT_CREDENTIAL_FILE,
) -> Credential | None:
    sessdata = os.getenv("BILI_SESSDATA", "").strip()
    bili_jct = os.getenv("BILI_BILI_JCT", "").strip()
    buvid3 = os.getenv("BILI_BUVID3", "").strip()
    dedeuserid = os.getenv("BILI_DEDEUSERID", "").strip()
    ac_time_value = os.getenv("BILI_AC_TIME_VALUE", "").strip()

    if any((sessdata, bili_jct, buvid3, dedeuserid, ac_time_value)):
        return Credential(
            sessdata=sessdata or None,
            bili_jct=bili_jct or None,
            buvid3=buvid3 or None,
            dedeuserid=dedeuserid or None,
            ac_time_value=ac_time_value or None,
        )

    if not credential_file.exists():
        return None

    data = json.loads(credential_file.read_text(encoding="utf-8"))
    return Credential.from_cookies(data)


def save_credential(path: Path, credential: Credential) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(credential.get_cookies(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def extract_danmaku_message(event: dict) -> DanmakuMessage | None:
    data = event.get("data", {})
    info = data.get("info", [])
    if len(info) < 3:
        return None

    raw_meta = info[0] if isinstance(info[0], list) else []
    user_basic = info[2] if isinstance(info[2], list) else []
    user_block = (
        raw_meta[15] if len(raw_meta) > 15 and isinstance(raw_meta[15], dict) else {}
    )
    extra_raw = user_block.get("extra", "{}")

    try:
        extra = json.loads(extra_raw) if isinstance(extra_raw, str) else {}
    except json.JSONDecodeError:
        extra = {}

    uid_from_info = user_basic[0] if len(user_basic) > 0 else None
    uname_from_info = user_basic[1] if len(user_basic) > 1 else None
    uid_from_block = user_block.get("user", {}).get("uid")
    uname_from_block = user_block.get("user", {}).get("base", {}).get("name")

    uid = uid_from_info if isinstance(uid_from_info, int) and uid_from_info > 0 else None
    if uid is None and isinstance(uid_from_block, int) and uid_from_block > 0:
        uid = uid_from_block

    uname = uname_from_info or uname_from_block
    text = info[1] if len(info) > 1 and isinstance(info[1], str) else ""

    return DanmakuMessage(
        text=text,
        uname=uname,
        uid=uid,
        user_hash=extra.get("user_hash"),
        dm_type=extra.get("dm_type"),
        emoticon_unique=extra.get("emoticon_unique"),
    )


def build_summary(event: dict) -> dict:
    message = extract_danmaku_message(event)
    if message is None:
        return {}

    return {
        "text": message.text,
        "uname": message.uname,
        "uid": message.uid,
        "user_hash": message.user_hash,
        "dm_type": message.dm_type,
        "emoticon_unique": message.emoticon_unique,
        "participant_key": message.participant_key,
    }


def parse_score(text: str) -> int | None:
    normalized = text.strip()
    if not SCORE_PATTERN.fullmatch(normalized):
        return None
    return int(normalized)
