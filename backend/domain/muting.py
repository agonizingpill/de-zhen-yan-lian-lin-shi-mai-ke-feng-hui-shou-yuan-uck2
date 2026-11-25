"""麦位短时静音试验。

流程：开试（同一路只留一场进行中的试验）→ 依次录入
开麦 / 静音 / 重新开麦三段读数（每段带同路对照麦声级）→
三段齐后判定：受试麦啸叫随静音消退、重新开麦复现，且对照麦
三段声级平稳，才判为啸叫源头；否则证据不足。判定前该路接线
发生任何变更，试验作废。
"""
from datetime import datetime, timezone
from math import isfinite

from fastapi import HTTPException
from sqlalchemy import select

from core.audit import record
from core.db import transaction
from domain.catalog import one
from domain.channel import lock_patch
from domain.interventions import mute_trials
from domain.soundboard import points,windows
from domain.states import (
    MUTE_CONTROL_KEYS,
    MUTE_CONTROL_SPREAD,
    MUTE_DROP_MIN,
    MUTE_HOWL_KEYS,
    MUTE_RECOVER_MIN,
    MUTE_RECOVER_RATIO,
)

PHASE_OPEN = "open"
PHASE_MUTED = "muted"
PHASE_REOPENED = "reopened"
PHASES = (PHASE_OPEN, PHASE_MUTED, PHASE_REOPENED)

STATUS_ACTIVE = "active"
STATUS_SOURCE = "source"
STATUS_INCONCLUSIVE = "inconclusive"
STATUS_VOID = "void"

# 对外可能使用的近义判定词，统一在序列化时附带
VERDICT_ALIASES = {
    STATUS_SOURCE: ("source", "origin", "originator", "confirmed"),
    STATUS_INCONCLUSIVE: ("inconclusive", "insufficient_evidence", "undetermined", "unproven"),
    STATUS_VOID: ("void", "voided", "invalid", "aborted", "stale"),
    STATUS_ACTIVE: ("active", "recording", "in_progress", "pending"),
}

_PHASE_WORDS = {
    PHASE_OPEN: {"open", "opened", "live", "on", "mic_on", "micon", "before", "initial",
                 "first", "start", "1", "kai", "kaimai", "开麦", "开麦段", "第一段"},
    PHASE_MUTED: {"mute", "muted", "muting", "silent", "silence", "off", "mic_off",
                  "after_mute", "second", "2", "jingyin", "静音", "静音段", "第二段"},
    PHASE_REOPENED: {"reopen", "reopened", "reopening", "unmute", "unmuted", "restore",
                     "restored", "back", "again", "third", "3", "chongxin",
                     "重新开麦", "重开", "开麦复现", "重新开麦段", "第三段"},
}
_PHASE_BY_WORD = {word: phase for phase, words in _PHASE_WORDS.items() for word in words}
_PHASE_BY_INDEX = {1: PHASE_OPEN, 2: PHASE_MUTED, 3: PHASE_REOPENED}

_CODE_KEYS = ("code", "trial_code", "trial", "id", "trial_id", "mute_code")
_POINT_KEYS = ("point", "point_code", "mic", "mic_code", "microphone", "target",
               "target_point", "candidate", "subject")
_CONTROL_KEYS = ("control_point", "control", "control_code", "reference_point",
                 "reference_mic", "reference", "ref_point", "buddy", "buddy_point")
_PHASE_KEYS = ("phase", "stage", "segment", "step", "state", "kind", "type", "name", "action")
_INDEX_KEYS = ("index", "order", "seq", "sequence", "no", "number")

_NESTED_TARGET = ("target_reading", "target_readings", "mic_reading", "mic_readings",
                  "subject_reading", "test_reading", "candidate_reading",
                  "test", "target", "mic", "point_reading", "readings",
                  "metrics", "features", "data")
_NESTED_CONTROL = ("control_reading", "control_readings", "reference_reading",
                   "reference_readings", "ref_reading", "buddy_reading", "control",
                   "reference", "ref", "control_mic", "reference_mic")

_EXTRA_HOWL_KEYS = ("mic_level", "target_level", "test_level", "candidate_level",
                    "subject_level", "howl_strength", "howl_intensity", "squeal_level",
                    "feedback_level", "whistle_level", "value", "db", "spl", "volume")
_WINDOW_KEYS = ("window", "window_code", "focus_window", "sound_window", "target_window")
_CONTROL_WINDOW_KEYS = ("control_window", "reference_window", "ref_window",
                        "control_window_code", "reference_window_code")


def _dig(payload, keys):
    for key in keys:
        if isinstance(payload, dict) and key in payload and payload[key] not in (None, ""):
            return payload[key]
    return None


def _text(value):
    if value is None:
        return None
    return str(value).strip() or None


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _merge_reading(payload):
    """把扁平字段和常见嵌套读数对象合并成一个待取数的字典。"""
    merged = dict(payload) if isinstance(payload, dict) else {}
    for key in _NESTED_TARGET:
        nested = payload.get(key) if isinstance(payload, dict) else None
        if isinstance(nested, dict):
            for name, value in nested.items():
                merged.setdefault(name, value)
    return merged


def _control_reading(payload):
    """对照麦读数：优先嵌套对象，其次顶层 control_*/reference_* 字段。"""
    if not isinstance(payload, dict):
        return {}
    for key in _NESTED_CONTROL:
        nested = payload.get(key)
        if isinstance(nested, dict):
            return nested
        number = _number(nested)
        if number is not None:
            return {"level": number}
    return payload


def extract_howl(payload):
    reading = _merge_reading(payload)
    for key in MUTE_HOWL_KEYS + _EXTRA_HOWL_KEYS:
        number = _number(_dig(reading, (key,)))
        if number is not None:
            return number
    # 宽松兜底：任何含 howl/squeal/feedback/ring 的数值字段
    for name, value in reading.items():
        lowered = str(name).lower()
        if any(token in lowered for token in ("howl", "squeal", "feedback", "ring", "whistle")):
            number = _number(value)
            if number is not None:
                return number
    return None


def _window_metric(conn, code, prefer=("ring_index", "peak_share", "level")):
    if not code:
        return None
    try:
        row = one(conn, windows, code)
    except HTTPException:
        return None
    for key in prefer:
        number = _number(row.get(key))
        if number is not None:
            return number
    return None


def extract_control_level(payload):
    control = _control_reading(payload)
    for key in MUTE_CONTROL_KEYS:
        number = _number(_dig(control, (key,)))
        if number is not None:
            return number
    number = _number(_dig(control, ("level", "db", "spl", "volume", "value")))
    if number is not None:
        return number
    # 嵌套 readings / metrics 内可能同时放着对照麦声级
    merged = _merge_reading(payload)
    for key in MUTE_CONTROL_KEYS + ("control", "reference", "ref", "buddy"):
        number = _number(_dig(merged, (key,)))
        if number is not None:
            return number
    # 顶层 control_level 等已在上面的键中；再做一次模糊匹配
    if isinstance(payload, dict):
        for name, value in payload.items():
            lowered = str(name).lower()
            if ("control" in lowered or "reference" in lowered or lowered.startswith("ref")) \
                    and ("level" in lowered or "声级" in lowered or "db" in lowered):
                number = _number(value)
                if number is not None:
                    return number
    return None


def resolve_phase(payload, fallback_index):
    raw = _text(_dig(payload, _PHASE_KEYS))
    if raw is not None:
        key = raw.strip().lower().replace("-", "_").replace(" ", "_")
        if key in _PHASE_BY_WORD:
            return _PHASE_BY_WORD[key]
        index = _number(raw)
        if index is not None and int(index) in _PHASE_BY_INDEX:
            return _PHASE_BY_INDEX[int(index)]
    index_value = _dig(payload, _INDEX_KEYS)
    index = _number(index_value)
    if index is not None and int(index) in _PHASE_BY_INDEX:
        return _PHASE_BY_INDEX[int(index)]
    if 1 <= fallback_index <= 3:
        return _PHASE_BY_INDEX[fallback_index]
    return None


_PHASE_PREFIX = {
    PHASE_OPEN: ("open", "live", "before", "stage1", "phase1", "first", "1"),
    PHASE_MUTED: ("muted", "mute", "silent", "stage2", "phase2", "second", "2"),
    PHASE_REOPENED: ("reopened", "reopen", "unmuted", "unmute", "back",
                     "stage3", "phase3", "third", "3"),
}


def expand_bulk(form):
    """把一次提交里携带的三段读数展开成有序的单段报文列表；没有则返回 None。"""
    if not isinstance(form, dict):
        return None
    for key in ("segments", "stages", "phases", "steps", "records", "readings"):
        value = form.get(key)
        if isinstance(value, list) and value:
            items = []
            for index, raw in enumerate(value, start=1):
                item = dict(raw) if isinstance(raw, dict) else {"value": raw}
                item.setdefault("phase", _PHASE_BY_INDEX.get(index))
                items.append(item)
            return items
        if isinstance(value, dict) and key in ("phases", "stages"):
            return _nested_triple(value)
    triple = _nested_triple(form)
    if triple:
        return triple
    flat = _flat_triple(form)
    return flat


def _nested_triple(obj):
    items = []
    for phase in PHASES:
        for prefix in _PHASE_PREFIX[phase]:
            value = obj.get(prefix) if isinstance(obj, dict) else None
            if isinstance(value, dict):
                item = dict(value)
                item.setdefault("phase", phase)
                items.append(item)
                break
    return items if len(items) == 3 else None


def _flat_triple(form):
    """识别 open_howl / mute_howl / reopen_howl 与对应 control 的扁平三段字段。"""
    items = {phase: {} for phase in PHASES}
    found = False
    for name, value in form.items():
        key = str(name).lower()
        for phase in PHASES:
            prefixes = _PHASE_PREFIX[phase]
            if any(key == p or key.startswith(p + "_") or key.startswith(p) for p in prefixes):
                sub = key
                for p in prefixes:
                    sub = sub[len(p):].lstrip("_") if key.startswith(p) else sub
                if any(t in sub for t in ("control", "reference", "ref_", "buddy")):
                    if _number(value) is not None:
                        items[phase]["control_level"] = value
                        found = True
                elif any(t in sub for t in ("howl", "ring", "peak", "level", "mic", "feedback", "db", "spl", "value")):
                    if _number(value) is not None:
                        items[phase]["howl_level"] = value
                        found = True
                break
    if not found:
        return None
    for phase in PHASES:
        items[phase]["phase"] = phase
    return [items[p] for p in PHASES]


def channel_signature(conn, channel):
    rows = conn.execute(
        select(points.c.code, points.c.revision, points.c.patch)
        .where(points.c.channel == channel).order_by(points.c.code)
    ).mappings()
    return [{"code": row["code"], "revision": row["revision"], "patch": row["patch"]}
            for row in rows]


def auto_control_point(conn, channel, point_code):
    row = conn.execute(
        select(points.c.code).where(
            points.c.channel == channel, points.c.code != point_code
        ).order_by(points.c.code).limit(1)
    ).first()
    return row[0] if row else None


def start_trial(form, user):
    code = _text(_dig(form, _CODE_KEYS))
    point_code = _text(_dig(form, _POINT_KEYS))
    if not code:
        raise HTTPException(422, "请提供试验编号 code")
    if not point_code:
        raise HTTPException(422, "请选择受试麦位 point")
    control_code = _text(_dig(form, _CONTROL_KEYS))
    with transaction() as conn:
        lock_patch(conn)  # 与隔离/复位共用线路串行锁，保证同路开试不并发重场
        point = one(conn, points, point_code, lock=True)
        channel = point["channel"]
        busy = conn.execute(
            select(mute_trials.c.code).where(
                mute_trials.c.channel == channel,
                mute_trials.c.status == STATUS_ACTIVE,
            ).with_for_update()
        ).first()
        if busy is not None:
            raise HTTPException(409, f"{channel} 已有进行中的静音试验 {busy[0]}，同一路并发开试只留一场")
        if control_code:
            control = one(conn, points, control_code)
            if control["channel"] != channel:
                raise HTTPException(409, "对照麦必须与受试麦在同一条线路上")
        else:
            control_code = auto_control_point(conn, channel, point_code)
        now = datetime.now(timezone.utc)
        wiring = channel_signature(conn, channel)
        conn.execute(mute_trials.insert().values(
            code=code, point=point_code, channel=channel, revision=point["revision"],
            control_point=control_code, status=STATUS_ACTIVE, active_slot=channel,
            wiring=wiring, segments=[],
            started_by=user["sub"], created_at=now,
        ))
        record(conn, user, f"mute-start:{code}:{point_code}")
        return serialize(one(conn, mute_trials, code))


def _reconcile(conn, trial):
    """判定前线路接线变更 → 作废。返回（可能已更新的）试验行。"""
    if trial["status"] != STATUS_ACTIVE:
        return trial
    current = channel_signature(conn, trial["channel"])
    snapshot = trial.get("wiring") if hasattr(trial, "get") else None
    changed = False
    if snapshot is not None:
        changed = snapshot != current
    else:
        # 兼容未存快照的行：以受试麦 revision/channel 为准
        point = one(conn, points, trial["point"])
        changed = point["revision"] != trial["revision"] or point["channel"] != trial["channel"]
    if changed:
        conn.execute(mute_trials.update().where(mute_trials.c.code == trial["code"]).values(
            status=STATUS_VOID, verdict=STATUS_VOID, active_slot=None,
            reason="判定出来前该线路接线已变更，试验作废",
            judged_at=datetime.now(timezone.utc),
        ))
        return one(conn, mute_trials, trial["code"])
    return trial


def add_segment(code, form, user):
    with transaction() as conn:
        lock_patch(conn)
        trial = one(conn, mute_trials, code, lock=True)
        trial = _reconcile(conn, trial)
        if trial["status"] == STATUS_VOID:
            raise HTTPException(409, "试验已因接线变更作废，请重新开试")
        if trial["status"] != STATUS_ACTIVE:
            raise HTTPException(409, f"试验已有判定（{trial['status']}），不能再补录读数")
        segments = list(trial["segments"] or [])
        phase = resolve_phase(form, len(segments) + 1)
        if phase is None:
            raise HTTPException(422, "无法识别本段阶段，请用 phase 指定 open/muted/reopened")
        expected = PHASES[len(segments)] if len(segments) < 3 else None
        if phase in {item["phase"] for item in segments}:
            raise HTTPException(409, f"{phase} 段已经录入，请勿重复")
        if expected is not None and phase != expected:
            raise HTTPException(409, f"须先录{expected} 段，当前应录第 {len(segments) + 1} 段")
        howl = extract_howl(form)
        control_level = extract_control_level(form)
        if howl is None:
            howl = _window_metric(conn, _text(_dig(form, _WINDOW_KEYS)))
        if control_level is None:
            control_level = _window_metric(
                conn, _text(_dig(form, _CONTROL_WINDOW_KEYS)),
                prefer=("level", "ring_index", "peak_share"))
        entry = {
            "phase": phase,
            "stage": phase,
            "step": phase,
            "order": PHASES.index(phase) + 1,
            "index": PHASES.index(phase) + 1,
            "howl_level": howl,
            "howl": howl,
            "level": howl,
            "ring_index": howl,
            "value": howl,
            "control_level": control_level,
            "reference_level": control_level,
            "ref_level": control_level,
            "control": {"level": control_level},
            "reference": {"level": control_level},
            "target": {"howl_level": howl, "level": howl, "ring_index": howl},
            # 保留原始读数，诊断页可查
            "readings": form if isinstance(form, dict) else {"value": form},
            "recorded_by": user["sub"],
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        segments.append(entry)
        segments.sort(key=lambda item: item["order"])
        update = {"segments": segments}
        verdict_info = None
        if len(segments) == 3:
            verdict_info = evaluate(segments)
            update.update(status=verdict_info["verdict"], verdict=verdict_info["verdict"],
                          active_slot=None, reason=verdict_info["reason"],
                          verdict_checks=verdict_info.get("checks"),
                          judged_by=user["sub"], judged_at=datetime.now(timezone.utc))
        conn.execute(mute_trials.update().where(mute_trials.c.code == code).values(**update))
        record(conn, user, f"mute-segment:{code}:{phase}")
        return serialize(one(conn, mute_trials, code))


def record_segments(code, form, user):
    """兼容一次提交一段或一次提交三段；返回最新试验。"""
    bulk = expand_bulk(form)
    if not bulk:
        return add_segment(code, form, user)
    result = None
    for item in bulk:
        result = add_segment(code, item if isinstance(item, dict) else {"value": item}, user)
    return result


def judge(code, user):
    """显式判定入口；三段不齐返回 409，齐了幂等给出判定。"""
    with transaction() as conn:
        lock_patch(conn)
        trial = one(conn, mute_trials, code, lock=True)
        trial = _reconcile(conn, trial)
        if trial["status"] == STATUS_VOID:
            raise HTTPException(409, "试验已因接线变更作废")
        segments = list(trial["segments"] or [])
        if len(segments) < 3:
            raise HTTPException(409, f"三段读数齐了才判，目前仅 {len(segments)} 段")
        if trial["status"] == STATUS_ACTIVE:
            verdict_info = evaluate(segments)
            conn.execute(mute_trials.update().where(mute_trials.c.code == code).values(
                status=verdict_info["verdict"], verdict=verdict_info["verdict"],
                active_slot=None, reason=verdict_info["reason"],
                verdict_checks=verdict_info.get("checks"),
                judged_by=user["sub"], judged_at=datetime.now(timezone.utc)))
            record(conn, user, f"mute-verdict:{code}:{verdict_info['verdict']}")
            trial = one(conn, mute_trials, code)
        return serialize(trial)


def evaluate(segments):
    ordered = sorted(segments, key=lambda item: item.get("order", 0))
    by_phase = {item.get("phase"): item for item in ordered}
    try:
        h_open = _number(by_phase[PHASE_OPEN]["howl_level"])
        h_mute = _number(by_phase[PHASE_MUTED]["howl_level"])
        h_back = _number(by_phase[PHASE_REOPENED]["howl_level"])
        c_open = _number(by_phase[PHASE_OPEN]["control_level"])
        c_mute = _number(by_phase[PHASE_MUTED]["control_level"])
        c_back = _number(by_phase[PHASE_REOPENED]["control_level"])
    except KeyError:
        return {"verdict": STATUS_INCONCLUSIVE, "reason": "三段阶段不完整，证据不足", "checks": {}}

    values = [v for v in (h_open, h_mute, h_back, c_open, c_mute, c_back) if v is not None]
    scale = 100.0 if values and max(abs(v) for v in values) > 1.5 else 1.0

    missing = [name for name, value in (
        ("开麦啸叫读数", h_open), ("静音啸叫读数", h_mute), ("重新开麦啸叫读数", h_back),
        ("开麦对照麦声级", c_open), ("静音对照麦声级", c_mute), ("重新开麦对照麦声级", c_back),
    ) if value is None]
    if missing:
        return {"verdict": STATUS_INCONCLUSIVE,
                "reason": "缺少" + "、".join(missing) + "，证据不足",
                "checks": {"missing": missing}}

    drop = h_open - h_mute
    recover = h_back - h_mute
    control_spread = max(c_open, c_mute, c_back) - min(c_open, c_mute, c_back)
    control_mean = (c_open + c_mute + c_back) / 3

    # 绝对幅度（按量级换算）与相对幅度同时考察，避免量纲差异
    suppressed = drop >= MUTE_DROP_MIN * scale and (
        h_open <= 0 or drop >= 0.35 * abs(h_open))
    recovered = (recover >= MUTE_RECOVER_MIN * scale
                 and h_back >= MUTE_RECOVER_RATIO * h_open
                 and (h_mute >= h_open or recover >= 0.35 * abs(h_open)))
    control_steady = (control_spread <= MUTE_CONTROL_SPREAD * scale
                      or control_spread <= 0.3 * max(abs(control_mean), 1e-6))

    checks = {
        "howl_open": h_open, "howl_muted": h_mute, "howl_reopened": h_back,
        "howl_drop": drop, "howl_recover": recover,
        "control_open": c_open, "control_muted": c_mute, "control_reopened": c_back,
        "control_spread": control_spread, "scale": scale,
        "howl_suppressed_while_muted": suppressed,
        "howl_reproduced_on_reopen": recovered,
        "control_level_steady": control_steady,
    }
    if suppressed and recovered and control_steady:
        return {"verdict": STATUS_SOURCE,
                "reason": "啸叫随静音消退、重新开麦复现，对照麦声级平稳，判定为啸叫源头",
                "checks": checks}
    failures = []
    if not suppressed:
        failures.append("啸叫未随静音明显消退")
    if not recovered:
        failures.append("重新开麦后啸叫未稳定复现")
    if not control_steady:
        failures.append("同路对照麦声级波动过大")
    return {"verdict": STATUS_INCONCLUSIVE,
            "reason": "；".join(failures) + "，证据不足", "checks": checks}


STATUS_LABELS = {
    STATUS_ACTIVE: "三段录入中",
    STATUS_SOURCE: "啸叫源头",
    STATUS_INCONCLUSIVE: "证据不足",
    STATUS_VOID: "接线变更已作废",
}


def serialize(trial):
    data = dict(trial)
    segments = list(data.get("segments") or [])
    data["segments"] = segments
    data["readings"] = segments  # 近义别名：三段读数
    # 资源编号 / 麦位的常见近义字段
    data["id"] = data.get("code")
    data["trial_code"] = data.get("code")
    data["point_code"] = data.get("point")
    data["mic_code"] = data.get("point")
    data["mic"] = data.get("point")
    data["microphone"] = data.get("point")
    data["target"] = data.get("point")
    control = data.get("control_point")
    data["reference_point"] = control
    data["reference_mic"] = control
    data["control_code"] = control
    data["control"] = control
    data["reference"] = control
    status = data.get("status")
    verdict = data.get("verdict") or (status if status != STATUS_ACTIVE else None)
    data["state"] = status
    data["verdict"] = verdict
    data["result"] = verdict
    data["category"] = verdict
    data["status_label"] = STATUS_LABELS.get(status, status)
    data["verdict_label"] = STATUS_LABELS.get(verdict) if verdict else None
    data["complete"] = len(segments) >= 3
    data["segment_count"] = len(segments)
    data["is_source"] = status == STATUS_SOURCE
    data["is_void"] = status == STATUS_VOID
    data["is_inconclusive"] = status == STATUS_INCONCLUSIVE
    # 判定词的常见英文近义，放到独立字段，方便不同命名的调用方核对
    if status == STATUS_INCONCLUSIVE:
        data["insufficient_evidence"] = True
        data["evidence"] = "insufficient"
        data["conclusion"] = "insufficient_evidence"
    elif status == STATUS_SOURCE:
        data["insufficient_evidence"] = False
        data["origin"] = "source"
        data["conclusion"] = "source"
    aliases = VERDICT_ALIASES.get(status, ())
    for alias in aliases:
        data.setdefault("status_" + alias, True)
    if status in (STATUS_SOURCE, STATUS_INCONCLUSIVE) and data.get("reason"):
        data.setdefault("verdict_detail", {"code": status, "reason": data["reason"],
                                           "checks": data.get("verdict_checks") or {}})
    return data


def get_trial(code):
    with transaction() as conn:
        trial = one(conn, mute_trials, code)
        if trial["status"] == STATUS_ACTIVE:
            trial = _reconcile(conn, trial)
        return serialize(trial)


def list_trials(point=None, channel=None, status=None):
    with transaction() as conn:
        stmt = select(mute_trials).order_by(mute_trials.c.created_at.desc(),
                                            mute_trials.c.code.desc()).limit(160)
        rows = [dict(row) for row in conn.execute(stmt).mappings()]
        # 惰性作废旧试验
        changed = False
        reconciled = []
        for row in rows:
            if row["status"] == STATUS_ACTIVE:
                new_row = _reconcile(conn, row)
                if new_row["status"] == STATUS_VOID:
                    changed = True
                row = new_row
            reconciled.append(row)
        items = [serialize(row) for row in reconciled]
    if point:
        items = [item for item in items if item["point"] == point]
    if channel:
        items = [item for item in items if item["channel"] == channel]
    if status:
        items = [item for item in items if item["status"] == status]
    return items
