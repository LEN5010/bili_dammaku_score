from dataclasses import dataclass
from datetime import datetime
import re

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


POWER_PATTERN = re.compile(r"1+")
TRASH_PATTERN = re.compile(r"0+")


@dataclass(slots=True)
class HeatVoteHit:
    side: str
    uname: str
    uid: int | None
    user_hash: str | None
    raw_text: str
    accepted_at: str


class HeatVoteSession:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.active = False
        self.started_at: str | None = None
        self.stopped_at: str | None = None
        self.power_votes = 0
        self.trash_votes = 0
        self.total_messages = 0
        self.valid_messages = 0
        self.invalid_messages = 0
        self.recent_hits: list[HeatVoteHit] = []

    def start(self) -> None:
        self.active = True
        self.started_at = now_text()
        self.stopped_at = None
        self.power_votes = 0
        self.trash_votes = 0
        self.total_messages = 0
        self.valid_messages = 0
        self.invalid_messages = 0
        self.recent_hits.clear()

    def stop(self) -> None:
        self.active = False
        self.stopped_at = now_text()

    def accept_message(self, message: DanmakuMessage) -> tuple[str, HeatVoteHit | None]:
        if not self.active:
            return ("inactive", None)

        self.total_messages += 1
        normalized = message.text.strip()

        if POWER_PATTERN.fullmatch(normalized):
            hit = HeatVoteHit(
                side="power",
                uname=message.uname or "未知用户",
                uid=message.uid,
                user_hash=message.user_hash,
                raw_text=normalized,
                accepted_at=now_text(),
            )
            self.power_votes += 1
            self.valid_messages += 1
            self.recent_hits.insert(0, hit)
            del self.recent_hits[80:]
            return ("accepted", hit)

        if TRASH_PATTERN.fullmatch(normalized):
            hit = HeatVoteHit(
                side="trash",
                uname=message.uname or "未知用户",
                uid=message.uid,
                user_hash=message.user_hash,
                raw_text=normalized,
                accepted_at=now_text(),
            )
            self.trash_votes += 1
            self.valid_messages += 1
            self.recent_hits.insert(0, hit)
            del self.recent_hits[80:]
            return ("accepted", hit)

        self.invalid_messages += 1
        return ("invalid", None)

    def snapshot(self) -> dict:
        total_votes = self.power_votes + self.trash_votes
        if self.power_votes > self.trash_votes:
            leader = "power"
        elif self.trash_votes > self.power_votes:
            leader = "trash"
        else:
            leader = "draw"

        return {
            "active": self.active,
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "power_votes": self.power_votes,
            "trash_votes": self.trash_votes,
            "total_votes": total_votes,
            "total_messages": self.total_messages,
            "valid_messages": self.valid_messages,
            "invalid_messages": self.invalid_messages,
            "leader": leader,
            "diff": abs(self.power_votes - self.trash_votes),
        }

    def result_title(self) -> str:
        if self.power_votes > self.trash_votes:
            return "本轮结果：实力票更多"
        if self.trash_votes > self.power_votes:
            return "本轮结果：抽象票更多"
        return "本轮结果：两边打平"

    def result_summary(self) -> str:
        snapshot = self.snapshot()
        lines = [
            self.result_title(),
            f"实力票：{self.power_votes}",
            f"抽象票：{self.trash_votes}",
            f"总有效互动：{snapshot['total_votes']}",
            f"收到弹幕：{self.total_messages}",
            f"无效弹幕：{self.invalid_messages}",
        ]
        return "\n".join(lines)
