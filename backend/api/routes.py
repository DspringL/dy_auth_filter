"""
FastAPI 路由层
GET  /api/login/status       - 登录状态
POST /api/login/qrcode       - 触发扫码登录
GET  /api/kols               - 达人列表（分页+筛选）
GET  /api/kols/{id}          - 达人详情
POST /api/tasks/run          - 触发搜索+筛选+私信任务
GET  /api/tasks/{id}         - 任务状态查询
GET  /api/config             - 读取系统配置
POST /api/config             - 保存系统配置
POST /api/contact/send       - 联系达人（私信/评论/两种）
POST /api/users/resolve      - 将抖音号/uid/URL解析为sec_user_id
GET  /api/stats              - 整体统计
"""
import asyncio as _asyncio
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.database import get_session
from backend.models.crud import (
    get_kols, get_kol_by_id, get_kol_by_sec_uid,
    create_task_log, finish_task_log,
    update_kol_dm_status, update_kol_comment_status,
    upsert_kol,
)
from backend.schemas.kol import (
    KOLOut, KOLListResponse, TaskCreate, TaskStatus,
    ContactRequest, ContactResult,
    DMRequest, DMResult, LoginStatus,
)

router = APIRouter(prefix="/api")

# ── 全局状态 ──────────────────────────────────────────────────────────────────
_task_status: dict[int, dict] = {}
_login_state  = {"logged_in": False, "pending": False}
_task_id_counter = 0

_browser_lock  = _asyncio.Lock()   # 防止并发初始化浏览器
_task_run_lock = _asyncio.Lock()   # 任务串行锁：同一时间只允许一个任务使用浏览器


# ── 登录 ──────────────────────────────────────────────────────────────────────

@router.get("/login/status", response_model=LoginStatus, tags=["登录"])
async def get_login_status():
    """检查当前登录状态（优先读 Cookie 文件，无文件则查浏览器 Profile）"""
    from crawler.core.login import load_cookies_as_dict, LOGIN_COOKIES, _save_cookies

    saved = load_cookies_as_dict()
    if any(saved.get(k, "").strip() for k in LOGIN_COOKIES):
        _login_state["logged_in"] = True
        return LoginStatus(logged_in=True, message="已登录")

    try:
        from crawler.core.browser import get_browser
        from crawler.core.login import check_login
        async with _browser_lock:
            manager = await get_browser()
            logged_in = await check_login(manager)
            if logged_in:
                await _save_cookies(manager)
        _login_state["logged_in"] = logged_in
        return LoginStatus(
            logged_in=logged_in,
            message="已登录" if logged_in else "未登录，请点击「扫码登录」",
        )
    except Exception as e:
        return LoginStatus(logged_in=False, message=f"检测异常：{e}")


@router.post("/login/qrcode", response_model=LoginStatus, tags=["登录"])
async def trigger_qrcode_login(background_tasks: BackgroundTasks):
    """触发二维码登录（后台异步），前端轮询 /api/login/status 获取结果"""
    if _login_state.get("pending"):
        return LoginStatus(logged_in=False, message="登录流程正在进行中，请查看浏览器窗口扫码")
    if _login_state.get("logged_in"):
        return LoginStatus(logged_in=True, message="已登录，无需重复操作")

    async def _do_login():
        _login_state["pending"] = True
        try:
            from crawler.core.browser import get_browser
            from crawler.core.login import ensure_login
            async with _browser_lock:
                manager = await get_browser()
            ok = await ensure_login(manager)
            _login_state["logged_in"] = ok
        except Exception as e:
            from loguru import logger
            logger.error(f"登录流程异常：{e}")
            _login_state["logged_in"] = False
        finally:
            _login_state["pending"] = False

    background_tasks.add_task(_do_login)
    return LoginStatus(
        logged_in=False,
        message="二维码登录已触发，请查看浏览器窗口使用抖音 App 扫码，扫码成功后页面自动刷新",
    )


# ── 达人查询 ──────────────────────────────────────────────────────────────────

@router.get("/kols", response_model=KOLListResponse, tags=["达人"])
async def list_kols(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    dm_status: Optional[str] = Query(None, description="pending/sent/failed"),
    keyword: Optional[str] = Query(None, description="昵称模糊搜索"),
    min_followers: Optional[int] = Query(None, description="粉丝数下限（实际数量）"),
    session: AsyncSession = Depends(get_session),
):
    total, items = await get_kols(
        session, page=page, page_size=page_size,
        dm_status=dm_status, keyword=keyword, min_followers=min_followers,
    )
    return KOLListResponse(
        total=total, page=page, page_size=page_size,
        items=[KOLOut.model_validate(i) for i in items],
    )


@router.get("/kols/{kol_id}", response_model=KOLOut, tags=["达人"])
async def get_kol(kol_id: int, session: AsyncSession = Depends(get_session)):
    kol = await get_kol_by_id(session, kol_id)
    if not kol:
        raise HTTPException(status_code=404, detail="达人不存在")
    return KOLOut.model_validate(kol)


@router.delete("/kols/{kol_id}", tags=["达人"])
async def delete_kol(kol_id: int, session: AsyncSession = Depends(get_session)):
    """删除单个达人"""
    from backend.models.database import KOL
    kol = await get_kol_by_id(session, kol_id)
    if not kol:
        raise HTTPException(status_code=404, detail="达人不存在")
    await session.delete(kol)
    await session.commit()
    return {"ok": True, "id": kol_id}


@router.delete("/kols", tags=["达人"])
async def delete_kols_batch(
    ids: list[int],
    session: AsyncSession = Depends(get_session),
):
    """批量删除达人，body: [id1, id2, ...]"""
    from backend.models.database import KOL
    from sqlalchemy import delete as sa_delete
    if not ids:
        raise HTTPException(status_code=400, detail="ids 不能为空")
    await session.execute(sa_delete(KOL).where(KOL.id.in_(ids)))
    await session.commit()
    return {"ok": True, "deleted": len(ids)}


# ── 任务管理 ──────────────────────────────────────────────────────────────────

@router.post("/tasks/run", response_model=TaskStatus, tags=["任务"])
async def run_task(req: TaskCreate, background_tasks: BackgroundTasks):
    """
    触发搜索→筛选→入库→私信全流程任务（后台异步）。
    不注入 session，避免与后台 session 产生 SQLite 写锁竞争。
    任务串行执行，防止多任务共用 Page 导致 XHR 监听器互相干扰。
    """
    global _task_id_counter
    _task_id_counter += 1
    task_id = _task_id_counter

    _task_status[task_id] = {
        "status": "running",
        "total_found": 0,
        "total_passed": 0,
        "total_sent": 0,
        "total_commented": 0,
        "started_at": datetime.now().isoformat(),
        "finished_at": None,
        "error_msg": None,
        "logs": [],  # 详细过程日志
    }

    def task_log(msg: str):
        """追加任务日志（带时间戳）"""
        ts = datetime.now().strftime("%H:%M:%S")
        entry = f"[{ts}] {msg}"
        _task_status[task_id]["logs"].append(entry)
        from loguru import logger as _lg
        _lg.info(f"[任务#{task_id}] {msg}")

    async def _run():
        from backend.models.database import AsyncSessionLocal
        from crawler.core.browser import get_browser
        from crawler.core.login import load_cookies_as_dict, LOGIN_COOKIES
        from crawler.services.search import DouyinSearchService
        from crawler.services.filter import KOLFilter, FilterConfig
        from crawler.services.dm import DMService
        from crawler.utils.config import get as cfg_get

        # 串行锁：等待上一个任务完成后再启动，避免共用浏览器冲突
        async with _task_run_lock:
            async with AsyncSessionLocal() as db:
                try:
                    # 记录任务
                    search_type = getattr(req, 'search_type', 'user')
                    task_type = f"search_{search_type}+filter+contact"
                    task_obj = await create_task_log(db, task_type, req.keywords)
                    real_id = task_obj.id
                    await db.commit()
                    _task_status[task_id]["db_id"] = real_id

                    # 加载运行时配置（DB 覆盖 > yaml）
                    from backend.models.config_store import load_config_cache, get_runtime
                    await load_config_cache(db)
                    task_log(f"任务已创建 DB_ID={real_id}，关键词：{', '.join(req.keywords)}")

                    # 检查登录
                    cookie_dict = load_cookies_as_dict()
                    if not any(cookie_dict.get(k, "").strip() for k in LOGIN_COOKIES):
                        raise RuntimeError("未检测到登录 Cookie，请先在管理后台点击「扫码登录」")
                    task_log("登录状态验证通过")

                    # 每个任务创建独立 Page
                    manager = await get_browser()
                    page = await manager.new_page()
                    task_log("浏览器页面已创建，正在打开抖音...")
                    try:
                        await page.goto(
                            "https://www.douyin.com",
                            wait_until="domcontentloaded",
                            timeout=30_000,
                        )
                        task_log("抖音主页加载完成")
                    except Exception as eg:
                        task_log(f"主页加载警告（忽略）：{eg}")

                    # 筛选配置（优先请求参数，其次运行时配置）
                    filter_cfg = FilterConfig(
                        min_followers   = req.min_followers   if req.min_followers   is not None else get_runtime("filter.min_followers", 0),
                        max_followers   = req.max_followers   if req.max_followers   is not None else get_runtime("filter.max_followers", 0),
                        min_total_likes = req.min_total_likes if req.min_total_likes is not None else get_runtime("filter.min_total_likes", 0),
                        require_shop    = req.require_shop    if req.require_shop    is not None else get_runtime("filter.require_shop", False),
                    )
                    task_log(f"筛选条件：粉丝≥{filter_cfg.min_followers}，点赞≥{filter_cfg.min_total_likes}，橱窗={filter_cfg.require_shop}")

                    # 搜索（根据 search_type 选择用户搜索或视频搜索）
                    search_svc = DouyinSearchService(page)
                    max_per_kw = req.max_per_keyword if req.max_per_keyword else int(get_runtime(
                        "search.max_users_per_keyword",
                        int(cfg_get("search.max_users_per_keyword", 50))
                    ))
                    task_log(f"开始搜索，类型={search_type}，每关键词最多 {max_per_kw} 条")

                    search_type = getattr(req, 'search_type', 'user')
                    if search_type == "video":
                        raw_profiles = await search_svc.batch_search_videos(
                            req.keywords, max_per_keyword=max_per_kw
                        )
                    else:
                        raw_profiles = await search_svc.batch_search(
                            req.keywords, max_per_keyword=max_per_kw
                        )
                    _task_status[task_id]["total_found"] = len(raw_profiles)
                    task_log(f"搜索完成，共找到 {len(raw_profiles)} 个候选达人")

                    # 筛选
                    kol_filter = KOLFilter(filter_cfg)
                    passed = kol_filter.apply(raw_profiles)
                    passed = kol_filter.sort_by_popularity(passed)
                    _task_status[task_id]["total_passed"] = len(passed)
                    task_log(f"筛选完成，通过 {len(passed)}/{len(raw_profiles)} 个达人")

                    # 补全 IP 属地（访问用户主页拦截 XHR）
                    if passed:
                        task_log(f"补全 IP 属地（最多 {min(len(passed), 30)} 条）...")
                        from crawler.services.user_detail import batch_fetch_ip_locations
                        ip_map = await batch_fetch_ip_locations(
                            page,
                            [p.sec_user_id for p in passed],
                            max_count=min(len(passed), 30),  # 最多补全 30 条
                        )
                        for profile in passed:
                            profile.ip_location = ip_map.get(profile.sec_user_id, "")
                        task_log(f"IP 属地补全完成，成功 {len([v for v in ip_map.values() if v])} 条")

                    # 入库
                    for profile in passed:
                        await upsert_kol(db, profile)
                    await db.commit()
                    task_log(f"达人数据已入库 {len(passed)} 条")

                    # 私信 / 评论
                    sent_count    = 0
                    comment_count = 0
                    method = getattr(req, 'contact_method', 'none')
                    task_log(f"联系方式：{method}")

                    if method in ("dm", "both") and passed:
                        from crawler.services.dm import DMService
                        task_log(f"开始发私信，共 {len(passed)} 个达人...")
                        dm_svc = DMService(page)
                        dm_result = await dm_svc.send_batch(passed)
                        sent_count = dm_result.get("sent", 0)
                        task_log(f"私信完成：发送 {sent_count}，跳过重复 {dm_result.get('skipped_dup',0)}，达限 {dm_result.get('skipped_limit',0)}，失败 {dm_result.get('failed',0)}")
                        for profile in passed:
                            if dm_svc._record.already_sent(profile.sec_user_id):
                                await update_kol_dm_status(db, profile.sec_user_id, "sent", datetime.now())
                        await db.commit()

                    if method in ("comment", "both") and passed:
                        from crawler.services.comment import CommentService
                        task_log(f"开始发评论，共 {len(passed)} 个达人...")
                        cmt_svc = CommentService(page)
                        cmt_result = await cmt_svc.send_batch(passed)
                        comment_count = cmt_result.get("sent", 0)
                        task_log(f"评论完成：发送 {comment_count}，跳过重复 {cmt_result.get('skipped_dup',0)}，达限 {cmt_result.get('skipped_limit',0)}，失败 {cmt_result.get('failed',0)}")
                        _task_status[task_id]["total_commented"] = comment_count
                        for profile in passed:
                            if cmt_svc._record.already_commented(profile.sec_user_id):
                                await update_kol_comment_status(db, profile.sec_user_id, "sent", datetime.now())
                        await db.commit()

                    _task_status[task_id]["total_sent"]      = sent_count
                    _task_status[task_id]["total_commented"] = comment_count
                    _task_status[task_id]["status"]          = "done"
                    _task_status[task_id]["finished_at"]     = datetime.now().isoformat()
                    task_log(f"✅ 任务完成：搜索 {len(raw_profiles)}，筛选 {len(passed)}，私信 {sent_count}，评论 {comment_count}")

                    await finish_task_log(db, real_id, len(raw_profiles), len(passed), sent_count, "done")
                    await db.commit()

                    await search_svc.close()
                    await page.close()

                except Exception as e:
                    from loguru import logger
                    logger.error(f"任务 {task_id} 失败：{e}")
                    err_msg = str(e)
                    _task_status[task_id]["status"] = "error"
                    _task_status[task_id]["error_msg"] = err_msg
                    _task_status[task_id]["finished_at"] = datetime.now().isoformat()
                    task_log(f"❌ 任务失败：{err_msg}")
                    try:
                        db_id = _task_status[task_id].get("db_id")
                        if db_id:
                            await finish_task_log(db, db_id, 0, 0, 0, "error", err_msg[:500])
                            await db.commit()
                    except Exception:
                        pass

    background_tasks.add_task(_run)
    return TaskStatus(task_id=task_id, status="running", started_at=datetime.now())


@router.get("/tasks/{task_id}", response_model=TaskStatus, tags=["任务"])
async def get_task_status(task_id: int):
    if task_id not in _task_status:
        raise HTTPException(status_code=404, detail="任务不存在")
    s = _task_status[task_id]
    return TaskStatus(
        task_id=task_id,
        status=s["status"],
        total_found=s["total_found"],
        total_passed=s["total_passed"],
        total_sent=s.get("total_sent", 0),
        total_commented=s.get("total_commented", 0),
        started_at=datetime.fromisoformat(s["started_at"]) if s.get("started_at") else None,
        finished_at=datetime.fromisoformat(s["finished_at"]) if s.get("finished_at") else None,
        error_msg=s.get("error_msg"),
        logs=s.get("logs", []),
    )


@router.delete("/tasks/{task_id}", tags=["任务"])
async def delete_task(task_id: int):
    """删除单个任务记录"""
    if task_id not in _task_status:
        raise HTTPException(status_code=404, detail="任务不存在")
    del _task_status[task_id]
    return {"ok": True, "task_id": task_id}


@router.delete("/tasks", tags=["任务"])
async def delete_all_tasks():
    """清空所有任务记录"""
    count = len(_task_status)
    _task_status.clear()
    return {"ok": True, "deleted": count}


# ── 用户 ID 解析 ──────────────────────────────────────────────────────────────

@router.post("/users/resolve", tags=["用户"])
async def resolve_users(body: dict):
    """
    将多种格式的用户标识解析为 sec_user_id。

    支持输入格式：
    - sec_user_id：以 MS4 开头，直接返回
    - 主页 URL：https://www.douyin.com/user/{sec_user_id}，从 URL 提取
    - 抖音号（short_id）：纯数字 7~12 位，通过搜索接口查找
    - 数字 uid：纯数字 >= 10 位，通过搜索接口查找

    请求体：{"identifiers": ["抖音号或URL或sec_user_id", ...]}
    返回：{"results": [{"input": "xxx", "sec_user_id": "MS4...", "nickname": "...", "status": "ok/not_found/error"}]}
    """
    import re
    from crawler.core.browser import get_browser

    identifiers: list[str] = body.get("identifiers", [])
    if not identifiers:
        raise HTTPException(status_code=400, detail="identifiers 不能为空")
    if len(identifiers) > 100:
        raise HTTPException(status_code=400, detail="单次最多 100 个")

    results = []

    # 先对每个标识符做格式识别
    to_lookup: list[dict] = []   # 需要通过浏览器查找的

    for raw in identifiers:
        inp = raw.strip()
        if not inp:
            continue

        # 1. sec_user_id：以 MS4 开头
        if inp.startswith("MS4") and len(inp) > 20:
            results.append({"input": inp, "sec_user_id": inp, "nickname": "", "status": "ok"})
            continue

        # 2. 主页 URL 提取 sec_user_id
        m = re.search(r"douyin\.com/user/(MS4[A-Za-z0-9_\-]+)", inp)
        if m:
            results.append({"input": inp, "sec_user_id": m.group(1), "nickname": "", "status": "ok"})
            continue

        # 3. 纯数字（抖音号 or uid）→ 需要查找
        if re.fullmatch(r"\d{6,20}", inp):
            to_lookup.append({"input": inp})
            continue

        # 4. 不认识的格式
        results.append({"input": inp, "sec_user_id": "", "nickname": "", "status": "unsupported",
                         "msg": "不支持的格式，请使用 sec_user_id / 主页URL / 抖音号（纯数字）"})

    # 对需要查找的标识符，优先从数据库查，没有再通过抖音搜索接口找
    if to_lookup:
        from backend.models.database import AsyncSessionLocal
        from sqlalchemy import select, or_
        from backend.models.database import KOL

        async with AsyncSessionLocal() as db_session:
            for item in to_lookup:
                inp = item["input"]
                # 1. 先查数据库（short_id 或 uid 匹配）
                db_result = await db_session.execute(
                    select(KOL.sec_user_id, KOL.nickname)
                    .where(or_(KOL.short_id == inp, KOL.uid == inp))
                    .limit(1)
                )
                row = db_result.first()
                if row:
                    results.append({
                        "input": inp,
                        "sec_user_id": row[0],
                        "nickname": row[1] or "",
                        "status": "ok",
                        "source": "database",
                    })
                else:
                    # 2. 数据库没有，标记需要通过浏览器搜索
                    item["need_browser"] = True

        # 过滤出仍需浏览器搜索的
        browser_lookup = [x for x in to_lookup if x.get("need_browser")]

        if browser_lookup:
            try:
                from crawler.core.browser import get_browser
                manager = await get_browser()
                page = await manager.new_page()
                try:
                    # 使用 /aweme/v1/web/query/user/ 接口
                    # 根据 unique_id（抖音号）直接查询 sec_user_id（来源：github.com/Johnserf-Seed/f2）
                    QUERY_USER_API = "/aweme/v1/web/query/user/"

                    for item in browser_lookup:
                        inp = item["input"]
                        result_holder: list = []
                        done = __import__("asyncio").Event()

                        async def on_response(response, _inp=inp, _holder=result_holder, _done=done):
                            if QUERY_USER_API not in response.url:
                                return
                            if response.status != 200:
                                _done.set()
                                return
                            try:
                                body = await response.body()
                                data = __import__("json").loads(body)
                                user = data.get("user") or data.get("data", {}).get("user", {})
                                sec_uid = user.get("sec_uid") or user.get("sec_user_id", "")
                                nickname = user.get("nickname", "")
                                short_id_resp = user.get("short_id", "") or user.get("unique_id", "")
                                if sec_uid:
                                    _holder.append({"sec_user_id": sec_uid, "nickname": nickname, "short_id": short_id_resp})
                            except Exception as e:
                                from loguru import logger
                                logger.debug(f"[resolve] query_user 解析失败: {e}")
                            finally:
                                _done.set()

                        page.on("response", on_response)
                        try:
                            # 导航到该抖音号的主页搜索页，触发 query_user 接口
                            from urllib.parse import quote
                            search_url = f"https://www.douyin.com/search/{quote(inp)}?type=user"
                            try:
                                await page.goto("about:blank", timeout=5000)
                            except Exception:
                                pass
                            try:
                                await page.goto(search_url, wait_until="domcontentloaded", timeout=20_000)
                            except Exception:
                                pass

                            import asyncio as _asyncio
                            try:
                                await _asyncio.wait_for(done.wait(), timeout=12.0)
                            except _asyncio.TimeoutError:
                                pass

                            if result_holder:
                                r = result_holder[0]
                                # 验证返回的 short_id 是否与输入匹配
                                if r["short_id"] == inp or r["sec_user_id"]:
                                    results.append({
                                        "input": inp,
                                        "sec_user_id": r["sec_user_id"],
                                        "nickname": r["nickname"],
                                        "status": "ok",
                                        "source": "query_user",
                                    })
                                else:
                                    results.append({
                                        "input": inp,
                                        "sec_user_id": "",
                                        "nickname": "",
                                        "status": "not_found",
                                        "msg": f"query_user 返回的用户与输入 {inp!r} 不匹配",
                                    })
                            else:
                                # query_user 未触发，退回搜索匹配
                                from crawler.services.search import DouyinSearchService
                                search_svc = DouyinSearchService(page)
                                found = False
                                async for profile in search_svc.search_users(inp, max_count=10):
                                    if str(profile.short_id) == inp or str(profile.uid) == inp:
                                        results.append({
                                            "input": inp,
                                            "sec_user_id": profile.sec_user_id,
                                            "nickname": profile.nickname,
                                            "status": "ok",
                                            "source": "search_fallback",
                                        })
                                        found = True
                                        break
                                if not found:
                                    results.append({
                                        "input": inp,
                                        "sec_user_id": "",
                                        "nickname": "",
                                        "status": "not_found",
                                        "msg": f"未找到抖音号/uid 为 {inp!r} 的用户",
                                    })
                        finally:
                            page.remove_listener("response", on_response)
                finally:
                    await page.close()
            except Exception as e:
                from loguru import logger
                logger.error(f"[resolve] 浏览器查询失败：{e}")
                for item in browser_lookup:
                    results.append({
                        "input": item["input"],
                        "sec_user_id": "",
                        "nickname": "",
                        "status": "error",
                        "msg": str(e),
                    })

    return {"results": results}


# ── 联系达人（私信 / 评论 / 两种都发） ───────────────────────────────────────

@router.post("/contact/send", response_model=ContactResult, tags=["联系"])
async def send_contact(
    req: ContactRequest,
    session: AsyncSession = Depends(get_session),
):
    """
    对指定达人发起联系：私信 / 评论 / 两种都发。
    contact_method: dm | comment | both
    """
    from crawler.core.browser import get_browser
    from crawler.services.dm import DMService
    from crawler.services.comment import CommentService
    from crawler.services.search import KOLProfile

    manager = await get_browser()
    page = await manager.new_page()
    try:
        await page.goto("https://www.douyin.com", wait_until="domcontentloaded", timeout=30_000)
    except Exception:
        pass

    # 构建 profile 列表
    profiles = []
    for uid in req.sec_user_ids:
        kol = await get_kol_by_sec_uid(session, uid)
        if kol:
            profiles.append(KOLProfile(
                sec_user_id=kol.sec_user_id,
                nickname=kol.nickname or "",
                profile_url=kol.profile_url or f"https://www.douyin.com/user/{kol.sec_user_id}",
            ))

    result = ContactResult()

    # 私信
    if req.contact_method in ("dm", "both"):
        dm_svc = DMService(page)
        r = await dm_svc.send_batch(profiles)
        result.dm_sent          = r.get("sent", 0)
        result.dm_skipped_dup   = r.get("skipped_dup", 0)
        result.dm_skipped_limit = r.get("skipped_limit", 0)
        result.dm_failed        = r.get("failed", 0)
        for uid in req.sec_user_ids:
            kol = await get_kol_by_sec_uid(session, uid)
            if kol and dm_svc._record.already_sent(uid):
                await update_kol_dm_status(session, uid, "sent", datetime.now())

    # 评论
    if req.contact_method in ("comment", "both"):
        cmt_svc = CommentService(page)
        r = await cmt_svc.send_batch(profiles)
        result.comment_sent          = r.get("sent", 0)
        result.comment_skipped_dup   = r.get("skipped_dup", 0)
        result.comment_skipped_limit = r.get("skipped_limit", 0)
        result.comment_failed        = r.get("failed", 0)
        for uid in req.sec_user_ids:
            if cmt_svc._record.already_commented(uid):
                await update_kol_comment_status(session, uid, "sent", datetime.now())

    await page.close()
    return result


# 兼容旧接口 /dm/send
@router.post("/dm/send", response_model=DMResult, tags=["联系"])
async def send_dm_legacy(
    req: DMRequest,
    session: AsyncSession = Depends(get_session),
):
    """旧接口兼容：仅发私信"""
    from crawler.core.browser import get_browser
    from crawler.services.dm import DMService
    from crawler.services.search import KOLProfile

    manager = await get_browser()
    page = await manager.new_page()
    try:
        await page.goto("https://www.douyin.com", wait_until="domcontentloaded", timeout=30_000)
    except Exception:
        pass

    profiles = []
    for uid in req.sec_user_ids:
        kol = await get_kol_by_sec_uid(session, uid)
        if kol:
            profiles.append(KOLProfile(
                sec_user_id=kol.sec_user_id,
                nickname=kol.nickname or "",
                profile_url=kol.profile_url or f"https://www.douyin.com/user/{kol.sec_user_id}",
            ))

    dm_svc = DMService(page)
    result = await dm_svc.send_batch(profiles)
    for uid in req.sec_user_ids:
        await update_kol_dm_status(session, uid, "sent", datetime.now())
    await page.close()
    return DMResult(**result)


# ── 统计 ──────────────────────────────────────────────────────────────────────

@router.get("/stats", tags=["统计"])
async def get_stats(session: AsyncSession = Depends(get_session)):
    from sqlalchemy import select, func
    from backend.models.database import KOL
    from crawler.core.login import load_cookies_as_dict, LOGIN_COOKIES

    total_kols = (await session.execute(select(func.count(KOL.id)))).scalar_one()
    dm_sent    = (await session.execute(select(func.count(KOL.id)).where(KOL.dm_status == "sent"))).scalar_one()
    dm_pending = (await session.execute(select(func.count(KOL.id)).where(KOL.dm_status == "pending"))).scalar_one()
    has_shop   = (await session.execute(select(func.count(KOL.id)).where(KOL.has_shop == True))).scalar_one()

    saved = load_cookies_as_dict()
    logged_in = any(saved.get(k, "").strip() for k in LOGIN_COOKIES)
    _login_state["logged_in"] = logged_in

    return {
        "total_kols":   total_kols,
        "dm_sent":      dm_sent,
        "dm_pending":   dm_pending,
        "has_shop":     has_shop,
        "login_status": logged_in,
    }


# ── 系统配置 ──────────────────────────────────────────────────────────────────

@router.get("/config", tags=["配置"])
async def get_config(session: AsyncSession = Depends(get_session)):
    """读取当前系统配置（DB 覆盖 > yaml 默认）"""
    from backend.models.config_store import get_all_config
    return await get_all_config(session)


@router.post("/config", tags=["配置"])
async def save_config(
    updates: dict,
    session: AsyncSession = Depends(get_session),
):
    """
    保存系统配置到数据库。
    body 为 key/value 字典，key 必须是已知配置项。
    """
    from backend.models.config_store import set_config_values, CONFIG_KEYS
    # 过滤非法 key
    valid = {k: v for k, v in updates.items() if k in CONFIG_KEYS}
    if not valid:
        raise HTTPException(status_code=400, detail="没有合法的配置项")
    await set_config_values(session, valid)
    await session.commit()
    return {"saved": list(valid.keys())}
