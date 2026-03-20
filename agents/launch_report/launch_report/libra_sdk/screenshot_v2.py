"""Playwright 截图引擎 v2 — DOM 语义定位版本

相比 screenshot.py 的改进：
- 所有元素定位从硬编码像素坐标改为基于 DOM 结构/语义（class 名、父子关系、文本内容）
- 提取公共 JS 辅助函数（isElementVisible, findVisibleModal, findChartContainer）
- modal 内元素用组件类型区分（.arco-select-single vs .arco-select-multiple）
- 顶栏/图表控制栏用排除法定位（排除 modal、排除图表区域）

截图类型：
- table: 全表截图（截取表格可见区域）
- table_range: 列范围截图（水平滚动到指定列范围）
- chart: 图表截图（开启图表、选择指标和趋势类型）
- age_breakdown: 多维分析截图（打开年龄维度分析）

使用模式：
- display_mode: "average" 时先切换到分天平均模式
"""
import asyncio
import json
import os
from pathlib import Path

from playwright.async_api import async_playwright

BASE_URL = "https://libra-sg.tiktok-row.net"
COOKIES_PATH = Path(__file__).parent.parent / "cookies.json"


# ── 公共 JS 辅助函数（注入到 page.evaluate 中复用）─────────────

_JS_HELPERS = """
function isElementVisible(el) {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return false;
    if (rect.bottom < 0 || rect.top > window.innerHeight) return false;
    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    return true;
}

function findVisibleModal() {
    for (const m of document.querySelectorAll('.arco-modal')) {
        const r = m.getBoundingClientRect();
        if (r.width > 0 && r.height > 0) return m;
    }
    return null;
}

function findCardWrapper(sidebarName) {
    // 按 sidebar_name 找到对应的 table-chart-wrapper 卡片容器
    if (!sidebarName) return null;
    const wrappers = document.querySelectorAll('[class*="table-chart-wrapper"]');
    for (const w of wrappers) {
        const r = w.getBoundingClientRect();
        if (r.x < 250) continue;  // 排除 sidebar
        if ((w.textContent || '').includes(sidebarName)) return w;
    }
    return null;
}

function findChartContainer(sidebarName) {
    // 如果指定了 sidebarName，优先在对应卡片内找 canvas
    const searchRoot = sidebarName ? findCardWrapper(sidebarName) : null;
    const canvases = searchRoot
        ? searchRoot.querySelectorAll('canvas')
        : document.querySelectorAll('canvas');
    for (const c of canvases) {
        const rect = c.getBoundingClientRect();
        if (rect.width < 400 || rect.height < 150) continue;
        if (!searchRoot && (rect.bottom < 0 || rect.top > window.innerHeight)) continue;
        // 向上查找卡片容器
        let p = c.parentElement;
        for (let i = 0; i < 20 && p; i++) {
            const cn = p.className;
            if (cn && typeof cn === 'string' && cn.includes('table-chart-wrapper')) return p;
            const text = p.textContent || '';
            if ((text.includes('从头累计') || text.includes('多天平均') ||
                 text.includes('Cumulate') || text.includes('Average')) &&
                p.querySelector('canvas') &&
                p.getBoundingClientRect().width > 600) return p;
            p = p.parentElement;
        }
    }
    return null;
}
"""


# ── 基础工具函数 ──────────────────────────────────────────────

async def _dismiss_popups(page):
    """关闭弹窗（通知、模态框、引导提示等）

    包括：arco 组件弹窗、"我知道了" 引导提示（可能多步）、
    popover/tooltip/dropdown 等浮层
    """
    # 1. 关闭 arco 组件弹窗
    await page.evaluate("""() => {
        document.querySelectorAll(
            '.arco-modal-close-icon, .arco-notification-close-btn, '
            + '.arco-message-close-btn, [aria-label="Close"]'
        ).forEach(el => {
            if (typeof el.click === 'function') el.click();
        });
    }""")
    await asyncio.sleep(0.5)

    # 2. 隐藏引导提示浮层（不点击"我知道了"，避免触发页面状态变更导致数据刷新）
    await page.evaluate("""() => {
        // 隐藏引导遮罩和弹出框
        const guideSelectors = [
            '[class*="guide"]', '[class*="Guide"]',
            '[class*="onboarding"]', '[class*="tour"]',
            '.arco-popover[class*="step"]',
        ];
        for (const sel of guideSelectors) {
            for (const el of document.querySelectorAll(sel)) {
                const r = el.getBoundingClientRect();
                if (r.width > 0 && r.height > 0) {
                    el.style.display = 'none';
                }
            }
        }
        // 隐藏包含"我知道了"按钮的浮层父元素
        const btns = document.querySelectorAll('button, [role="button"], .arco-btn');
        for (const btn of btns) {
            const text = btn.textContent.trim();
            if (text.includes('我知道了') || text.includes('知道了') || text.includes('Got it')) {
                // 向上找到浮层容器并隐藏
                let parent = btn.parentElement;
                for (let i = 0; i < 10; i++) {
                    if (!parent) break;
                    const style = window.getComputedStyle(parent);
                    if (style.position === 'fixed' || style.position === 'absolute') {
                        parent.style.display = 'none';
                        break;
                    }
                    parent = parent.parentElement;
                }
            }
        }
    }""")
    await asyncio.sleep(0.3)

    # 3. 关闭残留浮层（popover、tooltip、dropdown）
    # 注意：不隐藏 .arco-select-popup，否则会破坏版本选择器状态
    await page.evaluate("""() => {
        const selectors = [
            '.arco-popover-content', '.arco-tooltip-content',
            '.arco-dropdown-popup'
        ];
        for (const sel of selectors) {
            for (const el of document.querySelectorAll(sel)) {
                const r = el.getBoundingClientRect();
                if (r.width > 0 && r.height > 0) {
                    el.style.display = 'none';
                }
            }
        }
    }""")
    await asyncio.sleep(0.3)


async def _hide_overlays(page):
    """用 CSS 隐藏所有弹窗浮层（不点击按钮，避免触发数据刷新）

    与 _dismiss_popups 的区别：_dismiss_popups 通过点击按钮关闭弹窗，
    会触发页面事件导致数据重新加载。此函数仅通过 CSS display:none 隐藏，
    不触发任何页面交互。
    """
    await page.evaluate("""() => {
        // 0. 注入持久 CSS 规则，防止 JS 定时器重新显示弹窗
        if (!document.getElementById('_hide_overlays_style')) {
            const style = document.createElement('style');
            style.id = '_hide_overlays_style';
            style.textContent = `
                .arco-notification, .arco-message,
                [class*="indoc-floating"], [class*="indoc-tour"],
                .arco-popover-content, .arco-tooltip-content {
                    display: none !important;
                    visibility: hidden !important;
                    opacity: 0 !important;
                }
            `;
            document.head.appendChild(style);
        }

        // 1. 直接从 DOM 移除弹窗元素（比 display:none 更彻底）
        document.querySelectorAll(
            '.arco-notification, .arco-message'
        ).forEach(el => el.remove());

        // 2. 移除引导浮层（indoc-tour, indoc-floating）
        document.querySelectorAll(
            '[class*="indoc-floating"], [class*="indoc-tour"]'
        ).forEach(el => el.remove());

        // 3. 隐藏模态框遮罩（不移除，可能影响页面状态）
        document.querySelectorAll(
            '.arco-modal-wrapper, .arco-modal-mask'
        ).forEach(el => { el.style.display = 'none'; });

        // 4. 隐藏残留 popover/tooltip（不隐藏 select-popup）
        document.querySelectorAll(
            '.arco-popover-content, .arco-tooltip-content, .arco-dropdown-popup'
        ).forEach(el => {
            const r = el.getBoundingClientRect();
            if (r.width > 0 && r.height > 0) el.style.display = 'none';
        });
    }""")
    await asyncio.sleep(0.3)


async def _set_date_range(page, start_date, end_date):
    """设置 Libra 页面的日期范围

    通过操作顶部的 ArcoDesign DateRangePicker 修改日期。
    Libra 使用左闭右开区间，所以 end_date 需要 +1 天传入 input。

    Args:
        start_date: "YYYY-MM-DD" 格式
        end_date: "YYYY-MM-DD" 格式（展示的结束日期，代码内部会 +1）
    """
    from datetime import datetime, timedelta
    # Libra 左闭右开：显示 2/27~3/7 需要 input 写 2/27 和 3/8
    end_dt = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
    end_input_value = end_dt.strftime("%Y-%m-%d")

    # 读取当前值
    current = await page.evaluate("""() => {
        const inputs = document.querySelectorAll('.arco-picker-range input');
        if (inputs.length >= 2) {
            return { start: inputs[0].value, end: inputs[1].value };
        }
        return null;
    }""")
    if current:
        print(f"  日期范围: 当前 {current['start']} ~ {current['end']}")
        if current["start"] == start_date and current["end"] == end_input_value:
            print(f"  日期范围: 已是目标值，无需修改")
            return True

    print(f"  日期范围: 设置为 {start_date} ~ {end_date} (input: {end_input_value})")

    # 点击 DateRangePicker 打开面板
    picker = await page.query_selector(".arco-picker-range")
    if not picker:
        print("  ⚠ 未找到日期选择器")
        return False
    await picker.click()
    await asyncio.sleep(1)

    # 清空并填写开始日期
    start_input = await page.query_selector('.arco-picker-range input[placeholder="开始日期"]')
    if start_input:
        await start_input.click(click_count=3)
        await start_input.fill(start_date)
        await asyncio.sleep(0.3)

    # 清空并填写结束日期
    end_input = await page.query_selector('.arco-picker-range input[placeholder="结束日期"]')
    if end_input:
        await end_input.click(click_count=3)
        await end_input.fill(end_input_value)
        await asyncio.sleep(0.3)

    # 按回车确认
    await page.keyboard.press("Enter")
    await asyncio.sleep(5)

    # 验证设置结果
    result = await page.evaluate("""() => {
        const inputs = document.querySelectorAll('.arco-picker-range input');
        if (inputs.length >= 2) {
            return { start: inputs[0].value, end: inputs[1].value };
        }
        return null;
    }""")
    if result:
        ok = result["start"] == start_date and result["end"] == end_input_value
        print(f"  日期范围: 设置后 {result['start']} ~ {result['end']} ({'✓' if ok else '✗'})")
        return ok

    return False


async def _select_datacenter(page, region):
    """选择机房数据筛选

    Args:
        region: "ROW" 只选其他机房数据, "EU" 只选 EU-TTP 机房数据, None 不做操作
    """
    if not region:
        return

    region = region.upper()
    if region == "ROW":
        target = "其他机房数据"
        deselect = "EU-TTP机房数据"
    elif region == "EU":
        target = "EU-TTP机房数据"
        deselect = "其他机房数据"
    else:
        print(f"  [warn] 未知机房参数: {region}，跳过")
        return

    print(f"  机房筛选: {region} → 只选'{target}'")

    # 点击"全部机房数据"多选下拉框
    await page.evaluate("""() => {
        const sels = document.querySelectorAll('.arco-select.arco-select-multiple');
        for (const sel of sels) {
            if (sel.textContent.includes('机房') || sel.textContent.includes('其他') || sel.textContent.includes('EU')) {
                sel.click(); return true;
            }
        }
        return false;
    }""")
    await asyncio.sleep(1)

    # 取消不需要的选项
    await page.evaluate("""(target) => {
        const options = document.querySelectorAll('.arco-select-option');
        for (const o of options) {
            if (o.textContent.trim().includes(target) &&
                o.classList.contains('arco-select-option-selected')) {
                o.click(); return true;
            }
        }
        return false;
    }""", deselect)
    await asyncio.sleep(0.3)

    # 确保目标选项被选中
    await page.evaluate("""(target) => {
        const options = document.querySelectorAll('.arco-select-option');
        for (const o of options) {
            if (o.textContent.trim().includes(target) &&
                !o.classList.contains('arco-select-option-selected')) {
                o.click(); return true;
            }
        }
        return false;
    }""", target)
    await asyncio.sleep(0.3)

    # 关闭下拉框
    await page.keyboard.press("Escape")
    await asyncio.sleep(2)
    print(f"  机房筛选完成")


async def _click_sidebar_item(page, name):
    """点击 sidebar 指标组，返回是否成功

    选择器: [class*="metric-group"]，按文本匹配
    约束: 宽50-300, 高15-50（过滤掉父容器）
    v2 改进：先精确匹配 text === targetName，再 fallback 到 includes
    """
    result = await page.evaluate("""(targetName) => {
        const els = document.querySelectorAll('[class*="metric-group"]');
        let totalEls = els.length;

        // Phase 1: 精确匹配（避免 "Core-Key Metrics" 匹配到 "Core-Publish Days" 的父容器）
        for (const el of els) {
            const text = el.textContent.trim();
            const rect = el.getBoundingClientRect();
            if (text === targetName &&
                rect.width > 50 && rect.width < 300 &&
                rect.height > 15 && rect.height < 50) {
                el.scrollIntoView({ block: 'center' });
                el.click();
                return { clicked: true, total: totalEls, matchType: 'exact' };
            }
        }

        // Phase 2: fallback — includes 匹配（兼容名称不完全一致的情况）
        for (const el of els) {
            const text = el.textContent.trim();
            const rect = el.getBoundingClientRect();
            if (text && text.includes(targetName) &&
                rect.width > 50 && rect.width < 300 &&
                rect.height > 15 && rect.height < 50) {
                el.scrollIntoView({ block: 'center' });
                el.click();
                return { clicked: true, total: totalEls, matchType: 'includes' };
            }
        }

        return { clicked: false, total: totalEls };
    }""", name)

    if result and result.get("clicked"):
        if result.get("matchType") == "includes":
            print(f"    [sidebar] 注意: 使用 includes 匹配 (非精确)")
        await asyncio.sleep(3)
        # sidebar 点击后，将对应的卡片滚动到视口顶部
        await _scroll_card_into_view(page, name)
        return True

    print(f"  [debug] sidebar 元素总数: {result.get('total', 0)}")
    return False


async def _scroll_card_into_view(page, sidebar_name):
    """将指定 sidebar_name 对应的 table-chart-wrapper 卡片滚动到视口顶部

    解决多卡片同时可见时，图表/表格函数操作到错误卡片的问题。
    """
    scrolled = await page.evaluate("""(sidebarName) => {
        const wrappers = document.querySelectorAll('[class*="table-chart-wrapper"]');
        for (const w of wrappers) {
            const r = w.getBoundingClientRect();
            if (r.x < 250) continue;  // 排除 sidebar
            if ((w.textContent || '').includes(sidebarName)) {
                w.scrollIntoView({ block: 'start', behavior: 'instant' });
                return true;
            }
        }
        return false;
    }""", sidebar_name)
    if scrolled:
        await asyncio.sleep(1)
    return scrolled


async def _select_versions(page, version_names):
    """在版本选择器中勾选指定版本（如 ["v15", "v16"]）

    通过 .base-version-select 多选下拉框操作。
    会先取消所有非目标版本，再勾选目标版本。
    """
    if not version_names:
        return True

    # 点击打开版本下拉框
    opened = await page.evaluate("""() => {
        const sel = document.querySelector('.base-version-select');
        if (sel) { sel.click(); return true; }
        return false;
    }""")
    if not opened:
        print("    ⚠ 未找到版本选择器 (.base-version-select)")
        return False
    await asyncio.sleep(1)

    # 读取当前选项状态，执行勾选/取消
    result = await page.evaluate("""(targetNames) => {
        const options = document.querySelectorAll('.arco-select-option');
        const actions = [];
        for (const opt of options) {
            const text = (opt.textContent || '').trim();
            // 选项文本格式: "v15设为基准组"，提取版本名
            const match = text.match(/^(v\\d+)/);
            if (!match) continue;
            const vName = match[1];
            const isSelected = opt.classList.contains('arco-select-option-selected');
            const shouldSelect = targetNames.includes(vName);
            if (shouldSelect && !isSelected) {
                opt.click();
                actions.push({version: vName, action: 'selected'});
            } else if (!shouldSelect && isSelected) {
                opt.click();
                actions.push({version: vName, action: 'deselected'});
            }
        }
        return actions;
    }""", version_names)

    print(f"    [versions] {result}")

    # 关闭下拉框：移除焦点让 select 自然关闭（不强制隐藏 popup，避免丢失选中状态）
    await page.evaluate("""() => {
        if (document.activeElement) document.activeElement.blur();
    }""")
    await asyncio.sleep(3)
    return True


async def _wait_for_data_table(page, sidebar_name=None, timeout=15):
    """等待数据表格出现且数据已加载（非"暂无数据"状态）"""
    for _ in range(timeout * 2):
        status = await page.evaluate("""(sidebarName) => {
            let searchRoot = null;
            if (sidebarName) {
                const wrappers = document.querySelectorAll('[class*="table-chart-wrapper"]');
                for (const w of wrappers) {
                    const r = w.getBoundingClientRect();
                    if (r.x < 250) continue;
                    if ((w.textContent || '').includes(sidebarName)) {
                        searchRoot = w;
                        break;
                    }
                }
            }

            const tables = searchRoot
                ? searchRoot.querySelectorAll('table')
                : document.querySelectorAll('table');

            let hasLoading = false;
            for (const t of tables) {
                const r = t.getBoundingClientRect();
                if (r.width < 200 || r.height < 50) continue;
                if (!searchRoot && (r.y < 0 || r.y > window.innerHeight)) continue;

                const rows = t.querySelectorAll('tbody tr');
                if (rows.length > 0) {
                    const rowText = Array.from(rows).map(r => r.textContent.trim()).join('');
                    const cleaned = rowText.replace(/暂无数据/g, '').replace(/No Data/g, '').trim();
                    if (cleaned.length > 0) return 'ready';
                    hasLoading = true;
                    continue;
                }

                const text = t.textContent || '';
                if (text.includes('暂无数据') || text.includes('No Data')) {
                    hasLoading = true;
                }
            }
            return hasLoading ? 'loading' : 'no_table';
        }""", sidebar_name)
        if status == "ready":
            await asyncio.sleep(0.3)
            return True
        await asyncio.sleep(0.5)
    print(f"    ⚠ _wait_for_data_table 超时 ({timeout}s)，最后状态: {status}")
    return False


async def _wait_for_chart_rendered(page, timeout=10):
    """等待图表 canvas 渲染完成（非空白 canvas）"""
    for _ in range(timeout * 2):
        ready = await page.evaluate("""() => {
            const canvases = document.querySelectorAll('canvas');
            for (const c of canvases) {
                const rect = c.getBoundingClientRect();
                if (rect.width < 400 || rect.height < 150) continue;
                if (rect.y < 0 || rect.y > window.innerHeight) continue;
                try {
                    const ctx = c.getContext('2d');
                    if (!ctx) continue;
                    const data = ctx.getImageData(
                        Math.floor(rect.width / 4), Math.floor(rect.height / 2), 1, 1
                    ).data;
                    if (data[3] > 0 && (data[0] < 250 || data[1] < 250 || data[2] < 250)) {
                        return true;
                    }
                } catch(e) {
                    return rect.width > 500 && rect.height > 200;
                }
            }
            return false;
        }""")
        if ready:
            return True
        await asyncio.sleep(0.5)
    print(f"    ⚠ _wait_for_chart_rendered 超时 ({timeout}s)")
    return False


async def _get_first_table_clip(page, sidebar_name=None):
    """获取当前指标组数据表格卡片的截图 clip 区域（两步法）

    Args:
        sidebar_name: 指标组名称，有值时优先在匹配卡片内找表格

    Returns: {x, y, width, height} 或 None
    """
    # 步骤 1: 找到表格并 scrollIntoView
    found = await page.evaluate("""(sidebarName) => {
        if (sidebarName) {
            const wrappers = document.querySelectorAll('[class*="table-chart-wrapper"]');
            for (const w of wrappers) {
                if (w.getBoundingClientRect().x < 250) continue;
                if (!(w.textContent || '').includes(sidebarName)) continue;
                const table = w.querySelector('.arco-table, [class*="arco-table"]');
                if (table) {
                    const rect = table.getBoundingClientRect();
                    if (rect.width >= 600) {
                        if (rect.y < 0 || rect.y > window.innerHeight) {
                            table.scrollIntoView({ block: 'start', behavior: 'instant' });
                        }
                        return true;
                    }
                }
            }
        }
        const tables = document.querySelectorAll('.arco-table, [class*="arco-table"]');
        for (const table of tables) {
            const rect = table.getBoundingClientRect();
            if (rect.width < 600 || rect.height < 50) continue;
            if (rect.y < 0 || rect.y > window.innerHeight) {
                table.scrollIntoView({ block: 'start', behavior: 'instant' });
            }
            return true;
        }
        return false;
    }""", sidebar_name)
    if not found:
        return None

    await asyncio.sleep(0.5)

    # 步骤 2: 读取实际位置
    clip = await page.evaluate("""(sidebarName) => {
        const vh = window.innerHeight;

        let searchRoot = null;
        if (sidebarName) {
            const wrappers = document.querySelectorAll('[class*="table-chart-wrapper"]');
            for (const w of wrappers) {
                if (w.getBoundingClientRect().x < 250) continue;
                if ((w.textContent || '').includes(sidebarName)) {
                    searchRoot = w;
                    break;
                }
            }
        }

        const tables = searchRoot
            ? searchRoot.querySelectorAll('.arco-table, [class*="arco-table"]')
            : document.querySelectorAll('.arco-table, [class*="arco-table"]');
        let fallback = null;

        for (const table of tables) {
            const rect = table.getBoundingClientRect();
            if (!searchRoot && (rect.y < 0 || rect.y > vh)) continue;
            if (rect.width < 600 || rect.height < 50) continue;

            let parent = table.parentElement;
            for (let i = 0; i < 8; i++) {
                if (!parent) break;
                const prect = parent.getBoundingClientRect();
                const text = parent.textContent || '';
                if ((text.includes('从头累计') || text.includes('多天平均') || text.includes('Cumulate') || text.includes('Average')) &&
                    prect.y >= 0 && prect.height > rect.height &&
                    prect.width > 600 && prect.width < window.innerWidth * 0.95) {
                    return {
                        x: Math.round(prect.x),
                        y: Math.round(prect.y),
                        width: Math.round(prect.width),
                        height: Math.round(prect.height),
                    };
                }
                parent = parent.parentElement;
            }

            if (!fallback && rect.y >= 0) {
                let p = table.parentElement?.parentElement || table.parentElement;
                if (p) {
                    const pr = p.getBoundingClientRect();
                    if (pr.y >= 0) {
                        fallback = {
                            x: Math.round(pr.x),
                            y: Math.round(pr.y),
                            width: Math.round(pr.width),
                            height: Math.round(pr.height),
                        };
                    }
                }
            }
        }
        return fallback || null;
    }""", sidebar_name)

    if not clip:
        return None

    # 步骤 3: 验证无遮挡
    overlays = await _verify_no_overlays(page, {
        "clipX": clip["x"], "clipY": clip["y"],
        "clipW": clip["width"], "clipH": clip["height"],
    })
    if overlays:
        print(f"    [clip] ⚠ 发现 {len(overlays)} 个遮挡元素，尝试隐藏...")
        await _hide_overlays(page)
        await asyncio.sleep(0.5)

    return clip


# ── Type 1: 全表截图 ─────────────────────────────────────────

async def _screenshot_table(page, filepath, sidebar_name=None):
    """截取当前指标组的数据表格"""
    clip = await _get_first_table_clip(page, sidebar_name=sidebar_name)
    if not clip:
        raise RuntimeError("未找到数据表格")

    pad = 4
    await page.screenshot(
        path=str(filepath),
        clip={
            "x": max(0, clip["x"] - pad),
            "y": max(0, clip["y"] - pad),
            "width": clip["width"] + pad * 2,
            "height": clip["height"] + pad * 2,
        },
    )


# ── Type 2: 列范围截图（table_range）──────────────────────────

async def _scroll_table_to_column(page, col_name):
    """水平滚动表格使指定列出现在冻结列右侧"""
    # 1. 重置滚动
    await page.evaluate("""() => {
        for (const tbl of document.querySelectorAll('table')) {
            const r = tbl.getBoundingClientRect();
            if (r.width < 200 || r.height < 50) continue;
            let p = tbl.parentElement;
            for (let j = 0; j < 8 && p; j++) {
                if (p.scrollWidth > p.clientWidth + 10) {
                    p.scrollLeft = 0;
                    return;
                }
                p = p.parentElement;
            }
        }
    }""")
    await asyncio.sleep(0.3)

    # 2. 读取列位置，计算目标 scrollLeft
    result = await page.evaluate("""(targetCol) => {
        for (const tbl of document.querySelectorAll('table')) {
            const r = tbl.getBoundingClientRect();
            if (r.width < 200 || r.height < 50) continue;
            if (r.y < -50 || r.y > window.innerHeight) continue;

            const headerRow = tbl.querySelector('thead tr');
            if (!headerRow) continue;
            const ths = headerRow.querySelectorAll('th');
            if (ths.length < 2) continue;

            const firstScrollableX = ths[1].getBoundingClientRect().x;

            let targetX = -1;
            for (const th of ths) {
                if (th.textContent.trim().includes(targetCol)) {
                    targetX = th.getBoundingClientRect().x;
                    break;
                }
            }
            if (targetX < 0) return { found: false };

            const scrollNeeded = Math.max(0, targetX - firstScrollableX);

            let p = tbl.parentElement;
            for (let j = 0; j < 8 && p; j++) {
                if (p.scrollWidth > p.clientWidth + 10) {
                    p.scrollLeft = scrollNeeded;
                    return { found: true, scrollLeft: scrollNeeded };
                }
                p = p.parentElement;
            }
            return { found: true, scrollLeft: 0 };
        }
        return { found: false };
    }""", col_name)

    if not result or not result.get("found"):
        return False

    await asyncio.sleep(0.3)
    return True


async def _reset_table_scroll(page):
    """重置表格水平滚动到最左"""
    await page.evaluate("""() => {
        for (const tbl of document.querySelectorAll('table')) {
            const r = tbl.getBoundingClientRect();
            if (r.width < 200 || r.height < 50) continue;
            let p = tbl.parentElement;
            for (let j = 0; j < 8 && p; j++) {
                if (p.scrollWidth > p.clientWidth + 10) {
                    p.scrollLeft = 0;
                    return;
                }
                p = p.parentElement;
            }
        }
    }""")
    await asyncio.sleep(0.3)


async def _verify_no_overlays(page, clip_info):
    """检查 clip 区域是否被弹窗/popover/tooltip 等遮挡"""
    return await page.evaluate("""(clip) => {
        const overlaySelectors = [
            '.arco-modal', '.arco-popover-content',
            '.arco-tooltip-content', '.arco-select-popup',
            '.arco-dropdown-popup'
        ];
        const overlays = [];
        for (const sel of overlaySelectors) {
            for (const el of document.querySelectorAll(sel)) {
                const r = el.getBoundingClientRect();
                if (r.width <= 0 || r.height <= 0) continue;
                if (r.right > clip.clipX && r.x < clip.clipX + clip.clipW &&
                    r.bottom > clip.clipY && r.y < clip.clipY + clip.clipH) {
                    overlays.push({
                        selector: sel,
                        x: Math.round(r.x), y: Math.round(r.y),
                        w: Math.round(r.width), h: Math.round(r.height)
                    });
                }
            }
        }
        return overlays.length > 0 ? overlays : null;
    }""", clip_info)


async def _scroll_and_get_clip(page, start_col, end_col, sidebar_name=None):
    """两阶段 clip 计算：先滚动到指定列范围，再读取真实 DOM 位置

    Args:
        sidebar_name: 有值时优先在匹配的 wrapper 内搜索表格，避免多组共存时找错
    """

    # ── 阶段 1: 找表格 + 执行滚动 ──
    scroll_result = await page.evaluate("""(args) => {
        const { startCol, endCol, sidebarName } = args;

        // 确定搜索范围
        let searchRoot = null;
        if (sidebarName) {
            const wrappers = document.querySelectorAll('[class*="table-chart-wrapper"]');
            for (const w of wrappers) {
                const r = w.getBoundingClientRect();
                if (r.x < 250) continue;
                if ((w.textContent || '').includes(sidebarName)) {
                    searchRoot = w; break;
                }
            }
        }

        function findTable(root, onlyViewport) {
            for (const tbl of (root || document).querySelectorAll('table')) {
                const r = tbl.getBoundingClientRect();
                if (r.width < 200 || r.height < 50) continue;
                if (onlyViewport && (r.y < -50 || r.y > window.innerHeight)) continue;
                const row = tbl.querySelector('thead tr');
                if (!row) continue;
                const ths = row.querySelectorAll('th');
                let hasStart = false, hasEnd = false;
                for (const th of ths) {
                    const txt = th.textContent.trim();
                    if (txt.includes(startCol)) hasStart = true;
                    if (txt.includes(endCol)) hasEnd = true;
                }
                if (hasStart && hasEnd) return tbl;
            }
            return null;
        }

        // 优先在 wrapper 内找，fallback 到全局
        let tbl = findTable(searchRoot, true);
        if (!tbl && searchRoot) tbl = findTable(searchRoot, false);
        if (!tbl) tbl = findTable(null, true);
        if (!tbl) {
            tbl = findTable(null, false);
            if (tbl) {
                tbl.scrollIntoView({ block: 'start', behavior: 'instant' });
            }
        }
        if (!tbl) return null;

        let scrollContainer = null;
        let p = tbl.parentElement;
        for (let j = 0; j < 8 && p; j++) {
            if (p.scrollWidth > p.clientWidth + 10) {
                scrollContainer = p;
                p.scrollLeft = 0;
                break;
            }
            p = p.parentElement;
        }

        const headerRow = tbl.querySelector('thead tr');
        if (!headerRow) return null;
        const ths = headerRow.querySelectorAll('th');
        if (ths.length < 2) return null;

        let startIdx = -1, endIdx = -1;
        for (let i = 0; i < ths.length; i++) {
            const txt = ths[i].textContent.trim();
            if (startIdx < 0 && txt.includes(startCol)) startIdx = i;
            if (txt.includes(endCol)) endIdx = i;
        }
        if (startIdx < 0 || endIdx < 0) return null;

        let frozenCount = 0;
        for (let i = 0; i < ths.length; i++) {
            if (window.getComputedStyle(ths[i]).position === 'sticky') {
                frozenCount++;
            } else {
                break;
            }
        }
        if (frozenCount === 0) frozenCount = 1;

        const startX = ths[startIdx].getBoundingClientRect().x;
        const firstScrollX = ths[frozenCount].getBoundingClientRect().x;
        const scrollNeeded = Math.max(0, startX - firstScrollX);
        if (scrollContainer) scrollContainer.scrollLeft = scrollNeeded;

        return { ok: true, startIdx, endIdx, frozenCount, scrollNeeded };
    }""", {"startCol": start_col, "endCol": end_col, "sidebarName": sidebar_name})

    if not scroll_result or not scroll_result.get("ok"):
        return None

    await asyncio.sleep(0.5)

    # ── 阶段 2: 读取真实后滚动位置，计算精确 clip ──
    clip_info = await page.evaluate("""(args) => {
        const { startCol, endCol, sidebarName } = args;

        // 确定搜索范围（与阶段 1 一致）
        let searchRoot = null;
        if (sidebarName) {
            const wrappers = document.querySelectorAll('[class*="table-chart-wrapper"]');
            for (const w of wrappers) {
                const r = w.getBoundingClientRect();
                if (r.x < 250) continue;
                if ((w.textContent || '').includes(sidebarName)) {
                    searchRoot = w; break;
                }
            }
        }

        function findTable(root) {
            for (const tbl of (root || document).querySelectorAll('table')) {
                const r = tbl.getBoundingClientRect();
                if (r.width < 200 || r.height < 50) continue;
                const row = tbl.querySelector('thead tr');
                if (!row) continue;
                const ths = row.querySelectorAll('th');
                let hasStart = false, hasEnd = false;
                for (const th of ths) {
                    const txt = th.textContent.trim();
                    if (txt.includes(startCol)) hasStart = true;
                    if (txt.includes(endCol)) hasEnd = true;
                }
                if (hasStart && hasEnd) return tbl;
            }
            return null;
        }

        const tbl = findTable(searchRoot) || findTable(null);
        if (!tbl) return null;

        const headerRow = tbl.querySelector('thead tr');
        if (!headerRow) return null;
        const ths = headerRow.querySelectorAll('th');

        let frozenCount = 0;
        for (let i = 0; i < ths.length; i++) {
            if (window.getComputedStyle(ths[i]).position === 'sticky') {
                frozenCount++;
            } else {
                break;
            }
        }
        if (frozenCount === 0) frozenCount = 1;

        const frozenLeft = ths[0].getBoundingClientRect().x;
        const frozenRight = ths[frozenCount - 1].getBoundingClientRect().right;

        let startIdx = -1, endIdx = -1;
        for (let i = 0; i < ths.length; i++) {
            const txt = ths[i].textContent.trim();
            if (startIdx < 0 && txt.includes(startCol)) startIdx = i;
            if (txt.includes(endCol)) endIdx = i;
        }
        if (startIdx < 0 || endIdx < 0) return null;

        const startRect = ths[startIdx].getBoundingClientRect();
        const endRect = ths[endIdx].getBoundingClientRect();
        const endRight = endRect.x + endRect.width;

        let scrollContainer = null;
        let p = tbl.parentElement;
        for (let j = 0; j < 8 && p; j++) {
            if (p.scrollWidth > p.clientWidth + 10) {
                scrollContainer = p;
                break;
            }
            p = p.parentElement;
        }

        const containerRect = scrollContainer
            ? scrollContainer.getBoundingClientRect()
            : tbl.getBoundingClientRect();
        const containerLeft = containerRect.x;
        const containerRight = containerLeft + (scrollContainer ? scrollContainer.clientWidth : containerRect.width);

        let cardTop, cardBottom;
        const tblRect = tbl.getBoundingClientRect();
        cardTop = tblRect.y;
        cardBottom = tblRect.y + tblRect.height;

        let pp = tbl.parentElement;
        for (let k = 0; k < 15 && pp; k++) {
            const pr = pp.getBoundingClientRect();
            const text = pp.textContent || '';
            if ((text.includes('从头累计') || text.includes('多天平均') || text.includes('Cumulate') || text.includes('Average')) &&
                pr.y >= 0 && pr.height > tblRect.height &&
                pr.width > 600 && pr.width < window.innerWidth * 0.95) {
                cardTop = pr.y;
                cardBottom = pr.y + pr.height;
                break;
            }
            pp = pp.parentElement;
        }

        const clipX = Math.round(containerLeft);
        const clipRight = Math.round(Math.min(endRight, containerRight));
        const clipY = Math.round(cardTop);
        const clipH = Math.round(cardBottom - cardTop);
        const clipW = clipRight - clipX;

        const overflow = endRight > containerRight;
        const viewportW = window.innerWidth;
        const neededWidth = overflow
            ? viewportW + Math.ceil(endRight - containerRight) + 50
            : viewportW;

        return {
            clipX, clipY, clipW, clipH,
            overflow, neededWidth,
            _debug: {
                frozenLeft: Math.round(frozenLeft),
                frozenRight: Math.round(frozenRight),
                startColX: Math.round(startRect.x),
                endColRight: Math.round(endRight),
                containerLeft: Math.round(containerLeft),
                containerRight: Math.round(containerRight),
            }
        };
    }""", {"startCol": start_col, "endCol": end_col, "sidebarName": sidebar_name})

    if not clip_info:
        return None

    dbg = clip_info.get("_debug", {})
    print(f"    [clip] frozen={dbg.get('frozenLeft')}~{dbg.get('frozenRight')}, "
          f"startCol.x={dbg.get('startColX')}, endCol.right={dbg.get('endColRight')}, "
          f"container={dbg.get('containerLeft')}~{dbg.get('containerRight')}")
    print(f"    [clip] → clipX={clip_info['clipX']}, clipY={clip_info['clipY']}, "
          f"clipW={clip_info['clipW']}, clipH={clip_info['clipH']}, overflow={clip_info.get('overflow')}")

    # ── 阶段 3: 验证无遮挡 ──
    overlays = await _verify_no_overlays(page, clip_info)
    if overlays:
        print(f"    [clip] ⚠ 发现 {len(overlays)} 个遮挡元素，尝试隐藏...")
        await _hide_overlays(page)
        await asyncio.sleep(0.5)

    return clip_info


async def _screenshot_with_clip(page, filepath, clip_info):
    """用 clip_info 裁剪截图"""
    pad = 4
    await page.screenshot(
        path=str(filepath),
        clip={
            "x": max(0, clip_info["clipX"] - pad),
            "y": max(0, clip_info["clipY"] - pad),
            "width": clip_info["clipW"] + pad * 2,
            "height": clip_info["clipH"] + pad * 2,
        },
    )


async def _screenshot_table_range(page, start_col, end_col, filepath, sidebar_name=None):
    """水平滚动表格到指定列范围并截图，列范围超宽时自动扩展 viewport"""
    info = await _scroll_and_get_clip(page, start_col, end_col, sidebar_name=sidebar_name)
    if not info:
        raise RuntimeError(f"未找到表格或列: start={start_col}, end={end_col}")

    widened = False
    if info.get("overflow"):
        new_w = info["neededWidth"]
        await page.set_viewport_size({"width": new_w, "height": 1080})
        await asyncio.sleep(2)
        info = await _scroll_and_get_clip(page, start_col, end_col, sidebar_name=sidebar_name)
        if not info:
            raise RuntimeError(f"扩展 viewport 后仍未找到列: {start_col} ~ {end_col}")
        widened = True

    await _screenshot_with_clip(page, filepath, info)
    await _reset_table_scroll(page)

    if widened:
        await page.set_viewport_size({"width": 1920, "height": 1080})
        await asyncio.sleep(0.5)


# ── Type 3: 图表截图（chart）──────────────────────────────────
# [v2 重构] _toggle_chart: 用 isElementVisible 替代 rect.y < 100 硬编码

async def _toggle_chart(page, enable, sidebar_name=None):
    """开/关当前卡片的图表开关

    v2 改进：用 isElementVisible + closest('.arco-modal') 排除替代硬编码 y 坐标
    v2.1: 加入 sidebar_name 过滤，在正确的卡片内找开关
    """
    result = await page.evaluate("""([enable, sidebarName]) => {
        """ + _JS_HELPERS + """
        // 如果指定了 sidebarName，限制搜索范围到对应卡片
        const wrapper = sidebarName ? findCardWrapper(sidebarName) : null;
        const searchRoot = wrapper || document;
        const sws = searchRoot.querySelectorAll('.arco-switch');
        for (const sw of sws) {
            if (!isElementVisible(sw)) continue;
            // 排除 modal 内的 switch
            if (sw.closest('.arco-modal')) continue;
            // 检查附近有 "图表" 文字
            let nearText = '';
            let p = sw.parentElement;
            for (let i = 0; i < 3 && p; i++) {
                nearText = (p.textContent || '').trim();
                if (nearText.includes('图表')) break;
                p = p.parentElement;
            }
            if (!nearText.includes('图表')) continue;

            const checked = sw.classList.contains('arco-switch-checked');
            if (checked === enable) return { ok: true, action: 'already' };
            sw.click();
            return { ok: true, action: 'clicked' };
        }
        return { ok: false };
    }""", [enable, sidebar_name])

    if result and result.get("ok"):
        if result.get("action") == "clicked":
            await asyncio.sleep(2)
        return True
    return False


# [v2 重构] _select_chart_metric: 用 findChartContainer 替代 y:300-500, x<500

async def _select_chart_metric(page, metric_name, sidebar_name=None):
    """在图表控制栏选择指标（arco-select 下拉框）

    v2 改进：通过 canvas 向上查找卡片容器，在容器内搜索 .arco-select，
    而非用 y:300-500, x<500 硬编码坐标。
    v2.1: 加入 sidebar_name 过滤，确保在正确卡片内操作。
    包含重试逻辑：图表刚开启时 canvas 可能还未渲染完成。
    """
    # 点击下拉框打开（带重试，等 canvas 渲染）
    clicked = None
    for _retry in range(5):
        clicked = await page.evaluate("""(sidebarName) => {
            """ + _JS_HELPERS + """
            const container = findChartContainer(sidebarName);
            if (!container) return { ok: false, reason: 'no_chart_container' };

            const canvas = container.querySelector('canvas');
            const canvasTop = canvas ? canvas.getBoundingClientRect().y : Infinity;

            const sels = container.querySelectorAll('.arco-select');
            for (const sel of sels) {
                const rect = sel.getBoundingClientRect();
                if (rect.width < 80 || rect.height <= 0) continue;
                if (canvas && rect.y > canvasTop) continue;
                sel.click();
                return { ok: true };
            }
            return { ok: false, reason: 'no_select_in_container' };
        }""", sidebar_name)
        if clicked and clicked.get("ok"):
            break
        await asyncio.sleep(1)

    if not clicked or not clicked.get("ok"):
        print(f"    ⚠ 图表指标下拉框定位失败: {clicked}")
        return False

    await asyncio.sleep(0.8)

    # 从下拉列表选择（先精确匹配，再模糊匹配）
    selected = await page.evaluate("""(name) => {
        const options = document.querySelectorAll('.arco-select-option');
        // 1. 精确 includes 匹配
        for (const opt of options) {
            const text = (opt.textContent || '').trim();
            if (text.includes(name)) {
                opt.click();
                return { ok: true, text: text };
            }
        }
        // 2. 模糊匹配：拆分指标名为关键词，全部出现则匹配
        const parts = name.split(/[-/\\s]+/).filter(p => p.length > 1);
        if (parts.length > 0) {
            for (const opt of options) {
                const text = (opt.textContent || '').trim().toLowerCase();
                if (parts.every(p => text.includes(p.toLowerCase()))) {
                    opt.click();
                    return { ok: true, text: (opt.textContent || '').trim() };
                }
            }
        }
        return { ok: false };
    }""", metric_name)

    if not selected or not selected.get("ok"):
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.5)
        print(f"    ⚠ 未找到图表指标: {metric_name}")
        return False

    await asyncio.sleep(1)
    return True


# [v2 重构] _click_chart_radio: 用 findChartContainer 替代 y:300-500

async def _click_chart_radio(page, text, sidebar_name=None):
    """点击图表控制栏的 arco-radio-button（差异值/统计值/累计趋势/分段趋势）

    v2 改进：在图表卡片容器内按文本匹配，而非用 y:300-500 坐标过滤
    v2.1: 加入 sidebar_name 过滤
    """
    result = None
    for _retry in range(5):
        result = await page.evaluate("""([targetText, sidebarName]) => {
            """ + _JS_HELPERS + """
            const container = findChartContainer(sidebarName);
            if (!container) return { ok: false, reason: 'no_chart_container' };

            const labels = container.querySelectorAll('.arco-radio-button');
            for (const label of labels) {
                const t = (label.textContent || '').trim();
                if (t === targetText) {
                    label.click();
                    return { ok: true };
                }
            }
            return { ok: false, reason: 'no_matching_radio' };
        }""", [text, sidebar_name])
        if result and result.get("ok"):
            break
        await asyncio.sleep(1)

    if result and result.get("ok"):
        await asyncio.sleep(1)
        return True
    return False


async def _get_chart_clip(page, sidebar_name=None):
    """获取图表完整卡片的 clip"""
    return await page.evaluate("""(sidebarName) => {
        """ + _JS_HELPERS + """
        const searchRoot = sidebarName ? findCardWrapper(sidebarName) : null;
        const canvases = searchRoot
            ? searchRoot.querySelectorAll('canvas')
            : document.querySelectorAll('canvas');
        for (const c of canvases) {
            const rect = c.getBoundingClientRect();
            if (rect.width < 500 || rect.height < 200) continue;

            let cardTop = rect.y;
            let cardX = rect.x;
            let cardW = rect.width;
            let p = c.parentElement;
            for (let i = 0; i < 15 && p; i++) {
                const pr = p.getBoundingClientRect();
                const text = p.textContent || '';
                if ((text.includes('从头累计') || text.includes('多天平均') || text.includes('Cumulate') || text.includes('Average')) &&
                    pr.y >= 0 && pr.height > rect.height + 50 &&
                    pr.width > 600 && pr.width < window.innerWidth * 0.95) {
                    cardTop = pr.y;
                    cardX = pr.x;
                    cardW = pr.width;
                    break;
                }
                p = p.parentElement;
            }

            const canvasBottom = rect.y + rect.height;
            let legendBottom = canvasBottom + 30;
            const all = document.querySelectorAll('[class*="ChartLineName"], [class*="chart-legend"], [class*="legend"]');
            for (const el of all) {
                const er = el.getBoundingClientRect();
                if (er.y >= canvasBottom - 5 && er.y < canvasBottom + 150 && er.width > 50) {
                    legendBottom = Math.max(legendBottom, er.y + er.height);
                }
            }

            const pad = 4;
            return {
                x: Math.max(0, Math.round(cardX) - pad),
                y: Math.max(0, Math.round(cardTop) - pad),
                width: Math.min(Math.round(cardW) + pad * 2, 1920),
                height: Math.min(Math.round(legendBottom - cardTop) + pad * 2, 1080),
            };
        }
        return null;
    }""", sidebar_name)


async def _screenshot_chart(page, metric_name, trend_type, filepath,
                            start_col=None, end_col=None, sidebar_name=None):
    """开启图表 → 选指标 → 差异值 → 趋势类型 → 截图 → 关闭图表"""
    if start_col and end_col:
        await _scroll_and_get_clip(page, start_col, end_col, sidebar_name=sidebar_name)

    if not await _toggle_chart(page, True, sidebar_name=sidebar_name):
        raise RuntimeError("未找到图表开关")

    if metric_name:
        await _select_chart_metric(page, metric_name, sidebar_name=sidebar_name)

    await _click_chart_radio(page, "差异值", sidebar_name=sidebar_name)

    if trend_type == "cumulative":
        await _click_chart_radio(page, "累计趋势", sidebar_name=sidebar_name)
    else:
        await _click_chart_radio(page, "分段趋势", sidebar_name=sidebar_name)

    await _wait_for_chart_rendered(page)

    clip = await _get_chart_clip(page, sidebar_name=sidebar_name)
    if clip:
        await page.screenshot(path=str(filepath), clip=clip)
    else:
        await page.screenshot(
            path=str(filepath),
            clip={"x": 300, "y": 200, "width": 1600, "height": 700},
        )

    await _toggle_chart(page, False, sidebar_name=sidebar_name)


# ── Type 4: 切换分天平均模式 ──────────────────────────────────
# [v2 重构] _switch_display_mode: 用 radio-group 语义 + 排除法替代 y:140-190

async def _switch_display_mode(page, mode):
    """切换 cumulative / average 显示模式

    v2 改进：通过 .arco-radio-group 语义定位 + 排除法（排除 modal 内、排除图表控制栏），
    而非用 y:140-190 硬编码坐标
    """
    if mode == "average":
        targets = ["平均", "Average", "average"]
    else:
        targets = ["累计", "Cumulate", "Cumulative"]

    for text in targets:
        clicked = await page.evaluate("""(targetText) => {
            // 策略 1: 找包含"累计"/"平均"的 arco-radio-group（排除 modal 和图表控制栏）
            const groups = document.querySelectorAll('.arco-radio-group');
            for (const g of groups) {
                // 排除 modal 内的
                if (g.closest('.arco-modal')) continue;
                const gText = g.textContent || '';
                // 排除图表控制栏的 radio group（包含"差异值"/"趋势"等字样）
                if (gText.includes('差异值') || gText.includes('统计值') ||
                    gText.includes('累计趋势') || gText.includes('分段趋势')) continue;
                // 应该包含"累计"或"平均"相关文字
                if (!(gText.includes('累计') || gText.includes('平均') ||
                      gText.includes('Cumulate') || gText.includes('Average'))) continue;

                const labels = g.querySelectorAll('.arco-radio-button');
                for (const label of labels) {
                    const t = (label.textContent || '').trim();
                    if (t === targetText) {
                        if (label.classList.contains('arco-radio-checked')) {
                            return { ok: true, action: 'already' };
                        }
                        label.click();
                        return { ok: true, action: 'clicked', text: t };
                    }
                }
            }

            // 策略 2 (fallback): 遍历所有 radio-button，排除 modal 和 canvas 附近的
            const allLabels = document.querySelectorAll('.arco-radio-button');
            for (const label of allLabels) {
                if (label.closest('.arco-modal')) continue;
                // 排除图表控制栏：检查祖先是否包含 canvas
                const wrapper = label.closest('[class*="table-chart-wrapper"]');
                if (wrapper && wrapper.querySelector('canvas')) {
                    const canvasRect = wrapper.querySelector('canvas').getBoundingClientRect();
                    const labelRect = label.getBoundingClientRect();
                    if (Math.abs(labelRect.y - canvasRect.y) < 200) continue;
                }
                const t = (label.textContent || '').trim();
                if (t === targetText) {
                    if (label.classList.contains('arco-radio-checked')) {
                        return { ok: true, action: 'already' };
                    }
                    label.click();
                    return { ok: true, action: 'clicked', text: t };
                }
            }
            return { ok: false };
        }""", text)

        if clicked and clicked.get("ok"):
            if clicked.get("action") == "clicked":
                await asyncio.sleep(3)
            return True

    print(f"  ⚠ 未找到显示模式 radio: {mode}")
    return False


# ── Type 5: 多维分析截图（age_breakdown）─────────────────────

async def _open_breakdown_modal(page, sidebar_name=None):
    """点击当前卡片的 "多维分析" 按钮，打开 modal

    Args:
        sidebar_name: 有值时优先在匹配的 wrapper 内搜索按钮，避免多组共存时点错
    """
    clicked = await page.evaluate("""(sidebarName) => {
        // 确定搜索范围
        let searchRoot = null;
        if (sidebarName) {
            const wrappers = document.querySelectorAll('[class*="table-chart-wrapper"]');
            for (const w of wrappers) {
                const r = w.getBoundingClientRect();
                if (r.x < 250) continue;
                if ((w.textContent || '').includes(sidebarName)) {
                    searchRoot = w; break;
                }
            }
        }

        const btns = (searchRoot || document).querySelectorAll('button');
        let candidates = [];
        for (const btn of btns) {
            if ((btn.textContent || '').trim() !== '多维分析') continue;
            const rect = btn.getBoundingClientRect();
            if (rect.width <= 0 || rect.height <= 0) continue;
            candidates.push({ btn, y: rect.y });
        }
        if (candidates.length === 0) return false;
        candidates.sort((a, b) => Math.abs(a.y - 200) - Math.abs(b.y - 200));
        const target = candidates[0];
        if (target.y < 50 || target.y > window.innerHeight - 50) {
            target.btn.scrollIntoView({ block: 'center', behavior: 'instant' });
        }
        target.btn.click();
        return true;
    }""", sidebar_name)
    if clicked:
        await asyncio.sleep(2)
    return clicked


async def _cancel_breakdown_modal(page):
    """关闭多维分析 modal（点击取消或 Escape）"""
    await page.evaluate("""() => {
        """ + _JS_HELPERS + """
        const modal = findVisibleModal();
        if (!modal) return;
        const btns = modal.querySelectorAll('button');
        for (const btn of btns) {
            if ((btn.textContent || '').trim() === '取消') {
                btn.click(); return;
            }
        }
    }""")
    await asyncio.sleep(1)


# [v2 重构] _screenshot_age_breakdown: 3 处硬编码改为 DOM 语义

async def _screenshot_age_breakdown(page, filepath, start_col=None, end_col=None,
                                     age_dimension="predicted_age_group",
                                     sidebar_name=None, _retry=0):
    """打开多维分析 → 添加年龄维度 → 全选 → 确认 → 截图

    v2 改进：
    - 维度下拉框：modal 内 .arco-select-single（替代 y:500-700, x<500）
    - 值选择器：modal 内 .arco-select-multiple（替代 y:500-700, x>500）
    - 关闭下拉框：Escape 键（替代 mouse.click(400, 500)）
    """
    # Step 1: 打开 modal
    if not await _open_breakdown_modal(page, sidebar_name=sidebar_name):
        raise RuntimeError("未找到多维分析按钮")

    # Step 2: 添加维度拆分
    await page.evaluate("""() => {
        const btns = document.querySelectorAll('button');
        for (const btn of btns) {
            if ((btn.textContent || '').trim().includes('添加维度拆分')) {
                btn.click(); return;
            }
        }
    }""")
    await asyncio.sleep(1)

    # Step 3: 打开维度下拉框 → 选年龄维度
    # [v2] 用 modal 内 .arco-select-single（非 disabled）替代坐标定位
    dim_pos = await page.evaluate("""() => {
        """ + _JS_HELPERS + """
        const modal = findVisibleModal();
        if (!modal) return null;

        // 维度下拉框是 modal 内的 .arco-select-single，且非 disabled
        // modal 中可能有多个 single select（维度、修正方法、百分比），只有维度下拉框是可用的
        const sels = modal.querySelectorAll('.arco-select.arco-select-single:not(.arco-select-disabled)');
        // 取最后一个可见的非 disabled select（新添加的维度行在最下面）
        let target = null;
        for (const sel of sels) {
            if (isElementVisible(sel)) {
                target = sel;
            }
        }
        if (!target) return null;
        const rect = target.getBoundingClientRect();
        return { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 };
    }""")

    if dim_pos:
        # 必须用 mouse.click，JS click 不触发 ArcoDesign 下拉框
        await page.mouse.click(dim_pos["x"], dim_pos["y"])
    else:
        print("    ⚠ 未找到维度下拉框（modal 内无 .arco-select-single）")
    await asyncio.sleep(0.8)

    selected = await page.evaluate("""(dimName) => {
        const opts = document.querySelectorAll('.arco-select-option');
        for (const opt of opts) {
            if ((opt.textContent || '').trim().includes(dimName)) {
                opt.click();
                return true;
            }
        }
        return false;
    }""", age_dimension)
    if not selected:
        print(f"    ⚠ 未找到维度: {age_dimension}，取消 breakdown")
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.3)
        await _cancel_breakdown_modal(page)
        raise RuntimeError(f"未找到维度: {age_dimension}")
    await asyncio.sleep(1.5)

    # Step 4: 打开值选择器
    # [v2] 用 modal 内 .arco-select-multiple 替代坐标定位
    await page.evaluate("""() => {
        """ + _JS_HELPERS + """
        const modal = findVisibleModal();
        if (!modal) return;

        // 值选择器是 modal 内的 .arco-select-multiple
        const sels = modal.querySelectorAll('.arco-select.arco-select-multiple');
        // 取最后一个可见的（对应刚添加的维度行）
        let target = null;
        for (const sel of sels) {
            if (isElementVisible(sel)) {
                target = sel;
            }
        }
        if (target) target.click();
    }""")
    await asyncio.sleep(1)

    # Step 5: 全选
    await page.evaluate("""() => {
        const labels = document.querySelectorAll('.arco-checkbox');
        for (const label of labels) {
            if ((label.textContent || '').trim() === '全选') {
                label.click(); return;
            }
        }
    }""")
    await asyncio.sleep(0.5)
    # [v2] 点击 modal 标题区域关闭值下拉框（Escape 会关闭整个 modal，不可用）
    modal_title_pos = await page.evaluate("""() => {
        """ + _JS_HELPERS + """
        const modal = findVisibleModal();
        if (!modal) return null;
        const title = modal.querySelector('.arco-modal-title');
        if (title) {
            const r = title.getBoundingClientRect();
            return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
        }
        // fallback: 点击 modal 上方区域
        const r = modal.getBoundingClientRect();
        return { x: r.x + r.width / 2, y: r.y + 30 };
    }""")
    if modal_title_pos:
        await page.mouse.click(modal_title_pos["x"], modal_title_pos["y"])
    await asyncio.sleep(0.5)

    # Step 6: 点击确认
    await page.evaluate("""() => {
        """ + _JS_HELPERS + """
        const modal = findVisibleModal();
        if (!modal) return;
        const btns = modal.querySelectorAll('.arco-btn-primary');
        for (const btn of btns) {
            if ((btn.textContent || '').trim() === '多维分析') {
                btn.click(); return;
            }
        }
    }""")
    await asyncio.sleep(3)
    # 等 breakdown 表格加载（等数据行出现且高度稳定）
    timed_out = False
    last_height = 0
    for _attempt in range(60):
        bd_status = await page.evaluate("""(sidebarName) => {
            let searchRoot = document;
            if (sidebarName) {
                const wrappers = document.querySelectorAll('[class*="table-chart-wrapper"]');
                for (const w of wrappers) {
                    const r = w.getBoundingClientRect();
                    if (r.x < 250) continue;
                    if ((w.textContent || '').includes(sidebarName)) {
                        searchRoot = w; break;
                    }
                }
            }
            for (const t of searchRoot.querySelectorAll('table')) {
                const r = t.getBoundingClientRect();
                if (r.width < 200 || r.height < 50) continue;
                const headerRow = t.querySelector('thead tr');
                if (!headerRow) continue;
                const ths = headerRow.querySelectorAll('th');
                let hasDimCol = false;
                for (const th of ths) {
                    if (th.textContent.trim() === '维度值') { hasDimCol = true; break; }
                }
                if (!hasDimCol) continue;
                const rows = t.querySelectorAll('tbody tr');
                return { found: true, rows: rows.length, height: Math.round(r.height) };
            }
            return { found: false, rows: 0, height: 0 };
        }""", sidebar_name)
        if bd_status["found"] and bd_status["rows"] > 0:
            # 等高度稳定（连续两次相同高度才认为数据加载完成）
            if bd_status["height"] == last_height and last_height > 0:
                await asyncio.sleep(0.3)
                break
            last_height = bd_status["height"]
        await asyncio.sleep(0.5)
    else:
        timed_out = True
        print(f"    ⚠ breakdown 表格数据未加载 (30s)，状态: {bd_status}")

    # 截图 — breakdown 表格
    if start_col and end_col:
        # [v2] 复用 _scroll_and_get_clip 做水平滚动 + clip 计算
        info = await _scroll_and_get_clip(page, start_col, end_col, sidebar_name=sidebar_name)
        if not info:
            raise RuntimeError(f"breakdown 表格未找到列: start={start_col}, end={end_col}")

        widened = False
        if info.get("overflow"):
            new_w = info["neededWidth"]
            await page.set_viewport_size({"width": new_w, "height": 1080})
            await asyncio.sleep(2)
            info = await _scroll_and_get_clip(page, start_col, end_col, sidebar_name=sidebar_name)
            if not info:
                raise RuntimeError(f"扩展 viewport 后仍未找到列: {start_col} ~ {end_col}")
            widened = True

        await _screenshot_with_clip(page, filepath, info)
        await _reset_table_scroll(page)

        if widened:
            await page.set_viewport_size({"width": 1920, "height": 1080})
            await asyncio.sleep(0.5)
    else:
        # 无 range：专门查找含"维度值"列的 breakdown 表格，在 sidebar_name wrapper 内搜索
        await page.evaluate("""(sidebarName) => {
            // 限定搜索范围到 sidebar_name 对应的 wrapper
            let searchRoot = document;
            if (sidebarName) {
                const wrappers = document.querySelectorAll('[class*="table-chart-wrapper"]');
                for (const w of wrappers) {
                    const r = w.getBoundingClientRect();
                    if (r.x < 250) continue;
                    if ((w.textContent || '').includes(sidebarName)) {
                        searchRoot = w; break;
                    }
                }
            }
            // 优先找含"维度值"列的 breakdown 表格
            for (const tbl of searchRoot.querySelectorAll('table')) {
                const r = tbl.getBoundingClientRect();
                if (r.width < 200 || r.height < 50) continue;
                const headerRow = tbl.querySelector('thead tr');
                if (!headerRow) continue;
                const ths = headerRow.querySelectorAll('th');
                let hasDim = false;
                for (const th of ths) {
                    if (th.textContent.trim() === '维度值') { hasDim = true; break; }
                }
                if (hasDim) {
                    tbl.scrollIntoView({ block: 'start', behavior: 'instant' });
                    return;
                }
            }
            // fallback：找大表格
            for (const tbl of searchRoot.querySelectorAll('table')) {
                const r = tbl.getBoundingClientRect();
                if (r.width > 600 && r.height > 200) {
                    tbl.scrollIntoView({ block: 'start', behavior: 'instant' });
                    return;
                }
            }
        }""", sidebar_name)
        await asyncio.sleep(0.5)

        # 获取 breakdown 表格的 clip（含"维度值"列的表格 → 向上找卡片容器）
        bd_clip = await page.evaluate("""(sidebarName) => {
            let searchRoot = document;
            if (sidebarName) {
                const wrappers = document.querySelectorAll('[class*="table-chart-wrapper"]');
                for (const w of wrappers) {
                    const r = w.getBoundingClientRect();
                    if (r.x < 250) continue;
                    if ((w.textContent || '').includes(sidebarName)) {
                        searchRoot = w; break;
                    }
                }
            }

            for (const t of searchRoot.querySelectorAll('table')) {
                const r = t.getBoundingClientRect();
                if (r.width < 200 || r.height < 50) continue;
                const headerRow = t.querySelector('thead tr');
                if (!headerRow) continue;
                const ths = headerRow.querySelectorAll('th');
                let hasDim = false;
                for (const th of ths) {
                    if (th.textContent.trim() === '维度值') { hasDim = true; break; }
                }
                if (!hasDim) continue;

                // 向上找卡片容器
                let cardTop = r.y, cardBottom = r.y + r.height;
                let pp = t.parentElement;
                for (let k = 0; k < 15 && pp; k++) {
                    const pr = pp.getBoundingClientRect();
                    const text = pp.textContent || '';
                    if ((text.includes('从头累计') || text.includes('多天平均') ||
                         text.includes('Cumulate') || text.includes('Average')) &&
                        pr.y >= 0 && pr.height > r.height &&
                        pr.width > 600 && pr.width < window.innerWidth * 0.95) {
                        cardTop = pr.y;
                        cardBottom = pr.y + pr.height;
                        break;
                    }
                    pp = pp.parentElement;
                }

                return {
                    clipX: Math.round(Math.min(r.x, 327)),
                    clipY: Math.round(cardTop),
                    clipW: Math.round(Math.min(r.width, window.innerWidth - 327)),
                    clipH: Math.round(cardBottom - cardTop),
                };
            }
            return null;
        }""", sidebar_name)

        # 截图前隐藏弹窗（多维分析页面可能有新弹窗）
        await _hide_overlays(page)

        if bd_clip:
            print(f"    [bd-clip] clipX={bd_clip['clipX']}, clipY={bd_clip['clipY']}, "
                  f"clipW={bd_clip['clipW']}, clipH={bd_clip['clipH']}")
            await _screenshot_with_clip(page, filepath, bd_clip)
        else:
            # 最后 fallback：用 _get_first_table_clip
            clip = await _get_first_table_clip(page, sidebar_name=sidebar_name)
            if clip:
                pad = 4
                await page.screenshot(
                    path=str(filepath),
                    clip={
                        "x": max(0, clip["x"] - pad),
                        "y": max(0, clip["y"] - pad),
                        "width": clip["width"] + pad * 2,
                        "height": clip["height"] + pad * 2,
                    },
                )
            else:
                raise RuntimeError("breakdown 后未找到数据表格")

    # 超时处理：已保存当前状态截图，重试或抛异常
    if timed_out:
        if _retry < 1:
            print(f"    ↻ breakdown 数据加载超时，关闭后重试...")
            try:
                await _close_age_breakdown(page, sidebar_name=sidebar_name)
                await asyncio.sleep(2)
            except Exception as e:
                print(f"    ⚠ 关闭 breakdown 失败: {e}")
            return await _screenshot_age_breakdown(
                page, filepath, start_col=start_col, end_col=end_col,
                age_dimension=age_dimension, sidebar_name=sidebar_name,
                _retry=_retry + 1,
            )
        else:
            raise RuntimeError(
                f"breakdown 表格数据加载超时 (30s×{_retry + 1})，已保存当前状态截图"
            )


# [v2 重构] _close_age_breakdown: modal 内 .arco-icon-delete 直接取，无需坐标

async def _close_age_breakdown(page, sidebar_name=None):
    """关闭多维分析，恢复普通表格视图

    v2 改进：在 modal 内直接查找 .arco-icon-delete，而非用 y:500-700 坐标过滤
    """
    if not await _open_breakdown_modal(page, sidebar_name=sidebar_name):
        print("    ⚠ 无法打开多维分析 modal 来关闭 breakdown")
        return False

    # [v2] 在 modal 内直接查找删除图标，不用坐标过滤
    icon_pos = await page.evaluate("""() => {
        """ + _JS_HELPERS + """
        const modal = findVisibleModal();
        if (!modal) return null;

        const icons = modal.querySelectorAll('.arco-icon-delete');
        for (const icon of icons) {
            if (isElementVisible(icon)) {
                const rect = icon.getBoundingClientRect();
                return { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 };
            }
        }
        return null;
    }""")
    if icon_pos:
        await page.mouse.click(icon_pos["x"], icon_pos["y"])
    else:
        print("    ⚠ 未找到维度删除按钮")
    await asyncio.sleep(1)

    # 点击确认（维度已删除，确认后恢复正常表格）
    await page.evaluate("""() => {
        """ + _JS_HELPERS + """
        const modal = findVisibleModal();
        if (!modal) return;
        const btns = modal.querySelectorAll('.arco-btn-primary');
        for (const btn of btns) {
            if ((btn.textContent || '').trim() === '多维分析') {
                btn.click(); return;
            }
        }
    }""")
    await asyncio.sleep(3)
    await _wait_for_data_table(page, sidebar_name=sidebar_name, timeout=5)
    return True


# ── 主入口 ────────────────────────────────────────────────────

async def capture_screenshots(flight_id, groups, output_dir, cookies_path=None,
                              datacenter=None, start_date=None, end_date=None,
                              versions=None):
    """截取所有指标组的截图

    Args:
        flight_id: 实验 ID
        groups: [{group_id, group_name, sidebar_name, screenshots: [...]}]
        output_dir: 截图输出目录
        cookies_path: cookies 文件路径
        datacenter: 机房筛选 "ROW"=其他机房 / "EU"=EU-TTP / None=不筛选
        start_date: 开始日期 "YYYY-MM-DD"（可选）
        end_date: 结束日期 "YYYY-MM-DD"（可选）
        versions: 版本名列表，如 ["v15", "v16"]（可选，通过 UI 勾选）

    Returns:
        {group_id: [截图路径列表]}
    """
    cookies_path = Path(cookies_path or COOKIES_PATH)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 构建 report URL，日期和版本都拼入 URL 参数
    report_url = f"{BASE_URL}/libra/flight/{flight_id}/report/main"
    url_params = []
    if start_date and end_date:
        url_params.append(f"start_date={start_date}&end_date={end_date}&period_type=d")
    if versions:
        url_params.append("&".join(f"versions={v}" for v in versions))
    results = {}

    async with async_playwright() as p:
        headless = os.environ.get("PLAYWRIGHT_HEADLESS", "true").lower() != "false"
        browser = await p.chromium.launch(
            headless=headless,
            args=[
                "--disable-features=PrivateNetworkAccessRespectPreflightResults,BlockInsecurePrivateNetworkRequests",
                "--disable-web-security",
            ],
        )
        context = await browser.new_context(viewport={"width": 1920, "height": 1080})

        with open(cookies_path, encoding="utf-8") as f:
            cookies = json.load(f)
        await context.add_cookies(cookies)

        for gi, group in enumerate(groups):
            gid = group["group_id"]
            gname = group["group_name"]
            sidebar_name = group.get("sidebar_name", gname)
            screenshots = group.get("screenshots", [])
            if not screenshots:
                continue

            # 每个 group 用新的 page tab，避免前一组的页面状态影响数据加载
            page = await context.new_page()

            # 每个 group 用 group_id URL 参数直接导航，日期和版本参数也拼入 URL
            extra_params = "&" + "&".join(url_params) if url_params else ""
            group_url = f"{report_url}?category=important&group_id={gid}{extra_params}"
            print(f"\n--- {gname} (sidebar: {sidebar_name}) ---")
            print(f"  导航到: {group_url}")
            await page.goto(group_url, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(8)

            # 用 CSS 隐藏弹窗浮层（不点击，避免触发数据刷新）
            await _hide_overlays(page)
            await asyncio.sleep(0.5)

            if datacenter:
                await _select_datacenter(page, datacenter)

            # URL 带 group_id 已将目标组定位到视口内，不需要 scrollIntoView
            # （scrollIntoView 会触发页面懒加载重渲染，导致数据被清除）
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

                    # 截图前再次隐藏可能延迟出现的弹窗（用 CSS，不点击）
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
                        # breakdown 后 reload 页面恢复干净状态（用 group_id URL）
                        await page.goto(group_url, wait_until="domcontentloaded", timeout=60000)
                        await asyncio.sleep(8)
                        await _hide_overlays(page)
                        await asyncio.sleep(0.5)
                        if datacenter:
                            await _select_datacenter(page, datacenter)
                        if current_display_mode != "cumulative":
                            await _switch_display_mode(page, current_display_mode)
                        await _wait_for_data_table(page, sidebar_name=sidebar_name)
                    else:
                        print(f"  skip {fname} (未知类型: {stype})")

                    if fpath.exists():
                        kb = fpath.stat().st_size // 1024
                        paths.append(str(fpath))
                        print(f"  ok {fname} ({kb}KB) - {label}")
                except Exception as e:
                    print(f"  err {fname}: {e}")
                    if fpath.exists():
                        kb = fpath.stat().st_size // 1024
                        paths.append(str(fpath))
                        print(f"  ⚠ {fname} ({kb}KB) - 异常但截图已保存")

            results[gid] = paths

            # 关闭当前 page，下一组会创建新 page
            await page.close()

        await browser.close()

    return results


async def capture_screenshots_isolated(flight_id, groups, output_dir, cookies_path=None,
                                       datacenter=None, start_date=None, end_date=None,
                                       versions=None):
    """最保守的截图模式：每个截图任务独立启动浏览器

    每个 group 的每个截图都在全新的浏览器实例中执行，
    彻底避免 DOM 残留、viewport 状态、数据缓存等问题。

    Args:
        start_date: 开始日期 "YYYY-MM-DD"（可选，不指定则使用页面默认）
        end_date: 结束日期 "YYYY-MM-DD"（可选，不指定则使用页面默认）
        versions: 版本 ID 列表，如 [75574616, 75574617]（可选，拼入 URL）
    """
    cookies_path = Path(cookies_path or COOKIES_PATH)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 构建 base URL 参数（日期 + 版本），每组再追加 group_id
    base_params = []
    if start_date and end_date:
        base_params.append(f"start_date={start_date}&end_date={end_date}&period_type=d")
    if versions:
        base_params.append("&".join(f"versions={v}" for v in versions))
    base_query = "&".join(base_params) if base_params else ""
    report_url = f"{BASE_URL}/libra/flight/{flight_id}/report/main"
    results = {}

    with open(cookies_path, encoding="utf-8") as f:
        cookies = json.load(f)

    headless = os.environ.get("PLAYWRIGHT_HEADLESS", "true").lower() != "false"
    launch_args = [
        "--disable-features=PrivateNetworkAccessRespectPreflightResults,BlockInsecurePrivateNetworkRequests",
        "--disable-web-security",
    ]

    async def _fresh_page(pw, group_id=None):
        """启动新浏览器 → 加载 cookies → 导航到 report 页面 → 等待加载"""
        # 构建完整 URL：base + group_id（直接通过 URL 导航到指定组，无需 sidebar 点击）
        url = report_url
        params = []
        if group_id:
            params.append(f"group_id={group_id}")
        if base_query:
            params.append(base_query)
        if params:
            url += "?" + "&".join(params)

        browser = await pw.chromium.launch(headless=headless, args=launch_args)
        context = await browser.new_context(viewport={"width": 1920, "height": 1080})
        await context.add_cookies(cookies)
        page = await context.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(8)
        await _dismiss_popups(page)
        await asyncio.sleep(1)
        if datacenter:
            await _select_datacenter(page, datacenter)
        return browser, page

    async with async_playwright() as pw:
        for gi, group in enumerate(groups):
            gid = group["group_id"]
            gname = group["group_name"]
            sidebar_name = group.get("sidebar_name", gname)
            screenshots = group.get("screenshots", [])
            if not screenshots:
                continue

            print(f"\n--- {gname} (sidebar: {sidebar_name}) ---")
            paths = []

            browser, page = None, None
            need_new_browser = True
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
                    if need_new_browser:
                        if browser:
                            await browser.close()
                        browser, page = await _fresh_page(pw, group_id=gid)
                        # URL 已带 group_id 导航到正确组，滚动卡片到视口顶部
                        await _scroll_card_into_view(page, sidebar_name)
                        await _wait_for_data_table(page, sidebar_name=sidebar_name)
                        current_display_mode = "cumulative"
                        need_new_browser = False

                    dm = spec.get("display_mode")
                    target_mode = dm if dm else "cumulative"
                    if target_mode != current_display_mode:
                        await _switch_display_mode(page, target_mode)
                        current_display_mode = target_mode
                        await _wait_for_data_table(page, sidebar_name=sidebar_name)

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
                        need_new_browser = True

                    else:
                        print(f"  skip {fname} (未知类型: {stype})")

                    if fpath.exists():
                        kb = fpath.stat().st_size // 1024
                        paths.append(str(fpath))
                        print(f"  ok {fname} ({kb}KB) - {label}")
                except Exception as e:
                    print(f"  err {fname}: {e}")
                    if fpath.exists():
                        kb = fpath.stat().st_size // 1024
                        paths.append(str(fpath))
                        print(f"  ⚠ {fname} ({kb}KB) - 异常但截图已保存")
                    need_new_browser = True

            if browser:
                await browser.close()
                browser = None

            results[gid] = paths

    return results
