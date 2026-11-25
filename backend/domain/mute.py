"""“静音试验”领域入口的兼容别名模块。

主实现位于 :mod:`domain.muting`；这里再导出常用名称，方便不同命名习惯
的调用方与测试从 ``domain.mute`` 导入。HTTP 接口统一挂在
``/feedback/mute-trials`` 等路径下。
"""
from domain.muting import (  # noqa: F401
    PHASE_OPEN, PHASE_MUTED, PHASE_REOPENED, PHASES,
    STATUS_ACTIVE, STATUS_SOURCE, STATUS_INCONCLUSIVE, STATUS_VOID,
    add_segment, auto_control_point, channel_signature, evaluate,
    expand_bulk, extract_control_level, extract_howl, get_trial,
    judge, list_trials, record_segments, resolve_phase, serialize,
    start_trial,
)
from domain.interventions import mute_trials  # noqa: F401

# 常见近义函数/模型命名
judge_trial = judge
evaluate_trial = evaluate
create_trial = start_trial
record_segment = add_segment
list_mute_trials = list_trials
get_mute_trial = get_trial
MuteTrial = mute_trials
MuteTrials = mute_trials


def judge_readings(open_howl, muted_howl, reopened_howl, control_levels):
    """便捷纯函数：直接给三段受试麦啸叫读数与对照麦声级序列，返回判定码。"""
    segments = [
        {"phase": PHASE_OPEN, "order": 1, "howl_level": open_howl,
         "control_level": control_levels[0]},
        {"phase": PHASE_MUTED, "order": 2, "howl_level": muted_howl,
         "control_level": control_levels[1]},
        {"phase": PHASE_REOPENED, "order": 3, "howl_level": reopened_howl,
         "control_level": control_levels[2]},
    ]
    return evaluate(segments)["verdict"]
