from enum import StrEnum

BANDS = ("BUS-A", "BUS-B", "BUS-C")
FEATURES = ("peak_share", "ring_index", "level", "self_echo")
SCORE_LIMIT = -1.55
HERTZ_TOLERANCE = 25
MIN_SHARED_POINTS = 3


class Patch(StrEnum):
    LIVE = "live"
    ISOLATED = "isolated"


class Finding(StrEnum):
    LOCAL = "local"
    COORDINATED = "coordinated"
    NORMAL = "normal"


class Ticket(StrEnum):
    ISOLATED = "isolated"
    RESTORED = "restored"


class MutePhase(StrEnum):
    OPEN = "open"
    MUTED = "muted"
    REOPENED = "reopened"


class MuteStatus(StrEnum):
    ACTIVE = "active"
    SOURCE = "source"
    INCONCLUSIVE = "inconclusive"
    VOID = "void"


# 静音试验判定阈值（读数均为 0..1 的归一化量；百分数/分贝量级会按量级换算）
MUTE_HOWL_KEYS = ("howl_level", "howl_strength", "howl", "ring_index", "peak_share", "level")
MUTE_CONTROL_KEYS = ("control_level", "reference_level", "ref_level", "buddy_level")
MUTE_DROP_MIN = 0.25        # 静音段啸叫至少回落的幅度
MUTE_RECOVER_MIN = 0.20     # 重新开麦后至少回升的幅度
MUTE_RECOVER_RATIO = 0.6    # 且回升到开麦段读数的六成以上
MUTE_CONTROL_SPREAD = 0.12  # 对照麦三段声级允许的最大极差
