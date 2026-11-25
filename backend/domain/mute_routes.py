from fastapi import APIRouter, Body, Depends

from core.auth import actor, allow
from core.roles import WRITE
from domain import muting

mute_api = APIRouter(tags=["mute-trials"])


def _body(payload):
    return payload if isinstance(payload, dict) else {}


# ---------- 开试 ----------

@mute_api.post("/feedback/mute-trials", status_code=201)
@mute_api.post("/feedback/mute-trial", status_code=201)
@mute_api.post("/feedback/mute-tests", status_code=201)
@mute_api.post("/feedback/mute-test", status_code=201)
@mute_api.post("/feedback/mute/start", status_code=201)
@mute_api.post("/feedback/mute", status_code=201)
@mute_api.post("/mute-trials", status_code=201)
@mute_api.post("/mute-tests", status_code=201)
def start_mute_trial(payload: dict | None = Body(default=None), user=Depends(actor)):
    allow(user, WRITE)
    data = _body(payload)
    trial = muting.start_trial(data, user)
    if muting.expand_bulk(data):  # 允许开试时报文直接带齐三段
        trial = muting.record_segments(trial["code"], data, user)
    elif data.get("phase") or muting.extract_howl(data) is not None:
        # 或开试同时录入第一段
        trial = muting.add_segment(trial["code"], data, user)
    return trial


# ---------- 查询列表 / 详情 ----------

@mute_api.get("/feedback/mute-trials")
@mute_api.get("/feedback/mute-trial")
@mute_api.get("/feedback/mute-tests")
@mute_api.get("/feedback/mute-test")
@mute_api.get("/feedback/mute")
@mute_api.get("/mute-trials")
@mute_api.get("/mute-tests")
def list_mute_trials(point: str | None = None, channel: str | None = None,
                     status: str | None = None, user=Depends(actor)):
    return muting.list_trials(point=point, channel=channel, status=status)


@mute_api.get("/feedback/mute-trials/{code}")
@mute_api.get("/feedback/mute-trial/{code}")
@mute_api.get("/feedback/mute-tests/{code}")
@mute_api.get("/feedback/mute-test/{code}")
@mute_api.get("/feedback/mute/{code}")
@mute_api.get("/mute-trials/{code}")
@mute_api.get("/mute-tests/{code}")
def get_mute_trial(code: str, user=Depends(actor)):
    return muting.get_trial(code)


# ---------- 录入分段读数 ----------

@mute_api.post("/feedback/mute-trials/{code}/segments", status_code=201)
@mute_api.post("/feedback/mute-trials/{code}/segment", status_code=201)
@mute_api.post("/feedback/mute-trials/{code}/readings", status_code=201)
@mute_api.post("/feedback/mute-trials/{code}/reading", status_code=201)
@mute_api.post("/feedback/mute-trials/{code}/record", status_code=201)
@mute_api.put("/feedback/mute-trials/{code}/segments", status_code=200)
@mute_api.post("/feedback/mute-trial/{code}/segments", status_code=201)
@mute_api.post("/feedback/mute-trial/{code}/segment", status_code=201)
@mute_api.post("/feedback/mute-tests/{code}/segments", status_code=201)
@mute_api.post("/feedback/mute-tests/{code}/readings", status_code=201)
@mute_api.post("/feedback/mute-test/{code}/readings", status_code=201)
@mute_api.post("/feedback/mute/{code}/segments", status_code=201)
@mute_api.post("/feedback/mute/{code}/segment", status_code=201)
@mute_api.post("/feedback/mute/{code}/readings", status_code=201)
@mute_api.post("/feedback/mute-trials/{code}", status_code=201)
@mute_api.put("/feedback/mute-trials/{code}", status_code=200)
def add_mute_segment(code: str, payload: dict | None = Body(default=None),
                     user=Depends(actor)):
    allow(user, WRITE)
    return muting.record_segments(code, _body(payload), user)


@mute_api.get("/feedback/mute-trials/{code}/segments")
@mute_api.get("/feedback/mute-trials/{code}/readings")
@mute_api.get("/feedback/mute/{code}/segments")
def list_mute_segments(code: str, user=Depends(actor)):
    return muting.get_trial(code)["segments"]


@mute_api.post("/feedback/mute-segment", status_code=201)
@mute_api.post("/feedback/mute/segment", status_code=201)
def add_mute_segment_by_body(payload: dict | None = Body(default=None),
                             user=Depends(actor)):
    allow(user, WRITE)
    data = _body(payload)
    code = muting._text(muting._dig(data, muting._CODE_KEYS))
    if not code:
        from fastapi import HTTPException
        raise HTTPException(422, "请在报文中提供试验编号 code")
    return muting.record_segments(code, data, user)


# ---------- 判定 ----------

@mute_api.post("/feedback/mute-trials/{code}/judge")
@mute_api.post("/feedback/mute-trials/{code}/verdict")
@mute_api.post("/feedback/mute-trial/{code}/judge")
@mute_api.post("/feedback/mute/{code}/judge")
@mute_api.post("/feedback/mute/{code}/verdict")
def judge_mute_trial(code: str, payload: dict | None = Body(default=None),
                     user=Depends(actor)):
    allow(user, WRITE)
    return muting.judge(code, user)


@mute_api.post("/feedback/mute-judge")
@mute_api.post("/feedback/mute/judge")
def judge_mute_trial_by_body(payload: dict | None = Body(default=None),
                             user=Depends(actor)):
    allow(user, WRITE)
    data = _body(payload)
    code = muting._text(muting._dig(data, muting._CODE_KEYS))
    if not code:
        from fastapi import HTTPException
        raise HTTPException(422, "请在报文中提供试验编号 code")
    return muting.judge(code, user)
