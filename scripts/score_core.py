from dataclasses import dataclass
from datetime import datetime

from bili_live_utils import DanmakuMessage, parse_score


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@dataclass(slots=True)
class ScoreEntry:
    participant_key: str
    uid: int | None
    user_hash: str | None
    uname: str
    score: int
    accepted_at: str


class ScoreSession:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.active = False
        self.started_at: str | None = None
        self.stopped_at: str | None = None
        self.entries: dict[str, ScoreEntry] = {}
        self.total_score = 0
        self.total_messages = 0
        self.invalid_messages = 0
        self.duplicate_messages = 0
        self.missing_identity_messages = 0

    def start(self) -> None:
        self.active = True
        self.started_at = now_text()
        self.stopped_at = None
        self.entries.clear()
        self.total_score = 0
        self.total_messages = 0
        self.invalid_messages = 0
        self.duplicate_messages = 0
        self.missing_identity_messages = 0

    def stop(self) -> None:
        self.active = False
        self.stopped_at = now_text()

    def status_line(self) -> str:
        if not self.active and not self.started_at:
            return "当前未开始统计。"

        average = self.total_score / len(self.entries) if self.entries else 0
        return (
            f"active={self.active} users={len(self.entries)} total={self.total_score} "
            f"avg={average:.2f} invalid={self.invalid_messages} "
            f"duplicate={self.duplicate_messages} missing_identity={self.missing_identity_messages}"
        )

    def accept_message(self, message: DanmakuMessage) -> tuple[str, ScoreEntry | None]:
        if not self.active:
            return ("inactive", None)

        self.total_messages += 1
        participant_key = message.participant_key
        if participant_key is None:
            self.missing_identity_messages += 1
            return ("missing_identity", None)

        score = parse_score(message.text)
        if score is None:
            self.invalid_messages += 1
            return ("invalid", None)

        if participant_key in self.entries:
            self.duplicate_messages += 1
            return ("duplicate", None)

        entry = ScoreEntry(
            participant_key=participant_key,
            uid=message.uid,
            user_hash=message.user_hash,
            uname=message.uname or "未知用户",
            score=score,
            accepted_at=now_text(),
        )
        self.entries[participant_key] = entry
        self.total_score += score
        return ("accepted", entry)

    def snapshot(self) -> dict:
        return {
            "active": self.active,
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "users": len(self.entries),
            "total_score": self.total_score,
            "average_score": self.total_score / len(self.entries) if self.entries else 0.0,
            "total_messages": self.total_messages,
            "invalid_messages": self.invalid_messages,
            "duplicate_messages": self.duplicate_messages,
            "missing_identity_messages": self.missing_identity_messages,
        }

    def render_report(self) -> str:
        lines = [
            "",
            "===== 本轮统计结果 =====",
            f"开始时间: {self.started_at or '-'}",
            f"结束时间: {self.stopped_at or now_text()}",
            f"有效人数: {len(self.entries)}",
            f"总分: {self.total_score}",
            f"平均分: {self.total_score / len(self.entries):.2f}"
            if self.entries
            else "平均分: 0.00",
            f"收到弹幕: {self.total_messages}",
            f"无效分数弹幕: {self.invalid_messages}",
            f"重复有效用户弹幕: {self.duplicate_messages}",
            f"缺少身份标识弹幕: {self.missing_identity_messages}",
            "明细:",
        ]

        if not self.entries:
            lines.append("  无有效记录")
            return "\n".join(lines)

        for index, entry in enumerate(self.entries.values(), start=1):
            identity = (
                f"uid={entry.uid}" if entry.uid else f"user_hash={entry.user_hash}"
            )
            lines.append(
                f"  {index}. score={entry.score} user={entry.uname} {identity} at={entry.accepted_at}"
            )
        return "\n".join(lines)
