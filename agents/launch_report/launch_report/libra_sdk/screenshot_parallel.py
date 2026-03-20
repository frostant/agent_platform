"""并行截图引擎 — 多浏览器实例并发处理指标组

基于 screenshot_v2 的核心函数，用 asyncio.Semaphore 控制并发数，
每个指标组在独立浏览器中执行，组之间互不干扰。

用法:
    from libra_sdk.screenshot_parallel import capture_screenshots_parallel
    results = await capture_screenshots_parallel(flight_id, groups, output_dir, max_workers=4)
"""
import asyncio
import json
import os
import time
from pathlib import Path

from playwright.async_api import async_playwright

from .screenshot_v2 import (
    BASE_URL,
    COOKIES_PATH,
    _click_sidebar_item,
    _hide_overlays,
    _screenshot_age_breakdown,
    _screenshot_chart,
    _screenshot_table,
    _screenshot_table_range,
    _select_datacenter,
    _switch_display_mode,
    _wait_for_data_table,
)


async def _process_group(
    pw, gi, group, flight_id, output_dir, cookies, datacenter,
    semaphore, headless, launch_args, url_params="",
):
    """处理单个指标组的所有截图（独立浏览器实例）"""
    gid = group["group_id"]
    gname = group["group_name"]
    sidebar_name = group.get("sidebar_name", gname)
    screenshots = group.get("screenshots", [])
    if not screenshots:
        return gid, []

    async with semaphore:
        t0 = time.time()
        print(f"\n--- [{gi+1:02d}] {gname} (开始, worker 已获取) ---")

        browser = None
        try:
            browser = await pw.chromium.launch(headless=headless, args=launch_args)
            context = await browser.new_context(viewport={"width": 1920, "height": 1080})
            await context.add_cookies(cookies)
            page = await context.new_page()

            report_url = f"{BASE_URL}/libra/flight/{flight_id}/report/main"
            extra = f"&{url_params}" if url_params else ""
            group_url = f"{report_url}?category=important&group_id={gid}{extra}"

            print(f"  [{gi+1:02d}] 导航到: {group_url}")
            await page.goto(group_url, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(8)
            await _hide_overlays(page)
            await asyncio.sleep(1)

            if datacenter:
                await _select_datacenter(page, datacenter)

            await _click_sidebar_item(page, sidebar_name)
            await _wait_for_data_table(page, sidebar_name=sidebar_name)

            paths = []
            current_display_mode = "cumulative"

            for i, spec in enumerate(screenshots):
                stype = spec["type"]
                label = spec.get("label", stype)
                fname = f"{gi+1:02d}_mg_{gid}_{i:02d}_{stype}.png"
                fpath = output_dir / fname

                range_spec = spec.get("range")
                if range_spec:
                    start_col = range_spec.get("start_col", "")
                    end_col = range_spec.get("end_col", "")
                else:
                    start_col = spec.get("start_col", "")
                    end_col = spec.get("end_col", "")

                try:
                    dm = spec.get("display_mode")
                    target_mode = dm if dm else "cumulative"
                    if target_mode != current_display_mode:
                        await _switch_display_mode(page, target_mode)
                        current_display_mode = target_mode
                        await _wait_for_data_table(page, sidebar_name=sidebar_name)

                    # 截图前隐藏可能延迟出现的弹窗
                    await _hide_overlays(page)

                    if stype == "table":
                        await _wait_for_data_table(page, sidebar_name=sidebar_name)
                        if start_col and end_col:
                            await _screenshot_table_range(page, start_col, end_col, fpath,
                                                          sidebar_name=sidebar_name)
                        else:
                            await _screenshot_table(page, fpath, sidebar_name=sidebar_name)

                    elif stype == "table_range":
                        await _wait_for_data_table(page, sidebar_name=sidebar_name)
                        await _screenshot_table_range(page, start_col, end_col, fpath,
                                                      sidebar_name=sidebar_name)

                    elif stype == "chart":
                        metric_name = spec.get("metric_name", "")
                        trend_type = spec.get("trend_type", "daily")
                        await _screenshot_chart(page, metric_name, trend_type, fpath,
                                                start_col=start_col, end_col=end_col,
                                                sidebar_name=sidebar_name)

                    elif stype == "age_breakdown":
                        age_dim = group.get("age_dimension", "predicted_age_group")
                        await _screenshot_age_breakdown(page, fpath,
                                                        start_col=start_col, end_col=end_col,
                                                        age_dimension=age_dim,
                                                        sidebar_name=sidebar_name)
                        # breakdown 后 reload 恢复干净状态
                        await page.goto(group_url, wait_until="domcontentloaded", timeout=60000)
                        await asyncio.sleep(8)
                        await _hide_overlays(page)
                        await asyncio.sleep(1)
                        if datacenter:
                            await _select_datacenter(page, datacenter)
                        await _click_sidebar_item(page, sidebar_name)
                        if current_display_mode != "cumulative":
                            await _switch_display_mode(page, current_display_mode)
                        await _wait_for_data_table(page, sidebar_name=sidebar_name)

                    else:
                        print(f"  [{gi+1:02d}] skip {fname} (未知类型: {stype})")

                    if fpath.exists():
                        kb = fpath.stat().st_size // 1024
                        paths.append(str(fpath))
                        print(f"  [{gi+1:02d}] ok {fname} ({kb}KB) - {label}")
                except Exception as e:
                    print(f"  [{gi+1:02d}] err {fname}: {e}")
                    if fpath.exists():
                        kb = fpath.stat().st_size // 1024
                        paths.append(str(fpath))
                        print(f"  [{gi+1:02d}] ⚠ {fname} ({kb}KB) - 异常但截图已保存")

            elapsed = time.time() - t0
            print(f"  [{gi+1:02d}] {gname} 完成 ({len(paths)}/{len(screenshots)}) {elapsed:.1f}s")
            return gid, paths

        except Exception as e:
            elapsed = time.time() - t0
            print(f"  [{gi+1:02d}] {gname} 异常: {e} ({elapsed:.1f}s)")
            return gid, []

        finally:
            if browser:
                await browser.close()


async def capture_screenshots_parallel(
    flight_id, groups, output_dir, cookies_path=None, datacenter=None, max_workers=4,
    start_date=None, end_date=None, base_vid=None, target_vid=None,
):
    """并行截取所有指标组的截图

    Args:
        flight_id: 实验 ID
        groups: [{group_id, group_name, sidebar_name, screenshots: [...]}]
        output_dir: 截图输出目录
        cookies_path: cookies 文件路径
        datacenter: 机房筛选 "ROW"/"EU"/None
        max_workers: 最大并发浏览器数（默认 4）
        start_date: 开始日期 "YYYY-MM-DD"（可选）
        end_date: 结束日期 "YYYY-MM-DD"（可选）
        base_vid: 对照组 vid（必须，多实验组时不可省略）
        target_vid: 目标实验组 vid（必须，多实验组时不可省略）

    Returns:
        {group_id: [截图路径列表]}

    Raises:
        ValueError: 未指定 base_vid 或 target_vid
    """
    if not base_vid or not target_vid:
        raise ValueError(
            "必须指定 base_vid 和 target_vid。"
            "请通过 Libra API 获取版本列表后传入对应的 vid。"
            "\n示例: capture_screenshots_parallel(..., base_vid=75706322, target_vid=75706323)"
        )

    cookies_path = Path(cookies_path or COOKIES_PATH)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    with open(cookies_path, encoding="utf-8") as f:
        cookies = json.load(f)

    # 构建 URL 参数（日期 + 版本 vid）
    # Libra URL 格式: versions=base_vid&versions=target_vid
    url_parts = []
    if start_date and end_date:
        url_parts.append(f"start_date={start_date}&end_date={end_date}&period_type=d")
    url_parts.append(f"versions={base_vid}&versions={target_vid}")
    url_params = "&".join(url_parts)

    headless = os.environ.get("PLAYWRIGHT_HEADLESS", "true").lower() != "false"
    launch_args = [
        "--disable-features=PrivateNetworkAccessRespectPreflightResults,BlockInsecurePrivateNetworkRequests",
        "--disable-web-security",
        "--disable-gpu",  # 并行时减少 GPU 资源争抢
    ]

    semaphore = asyncio.Semaphore(max_workers)
    print(f"并行模式: {len(groups)} 个组, 最大并发 {max_workers}")
    if url_params:
        print(f"URL 参数: {url_params}")

    t_total = time.time()

    async with async_playwright() as pw:
        tasks = [
            _process_group(
                pw, gi, group, flight_id, output_dir, cookies, datacenter,
                semaphore, headless, launch_args, url_params=url_params,
            )
            for gi, group in enumerate(groups)
        ]
        results_list = await asyncio.gather(*tasks, return_exceptions=True)

    results = {}
    for item in results_list:
        if isinstance(item, Exception):
            print(f"  [异常] {item}")
            continue
        gid, paths = item
        results[gid] = paths

    elapsed_total = time.time() - t_total
    print(f"\n并行截图总耗时: {elapsed_total:.1f}s")

    return results
