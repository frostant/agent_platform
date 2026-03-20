"""手动获取 Libra cookies — 打开浏览器登录后按回车保存"""
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright


async def main():
    async with async_playwright() as p:
        print("正在打开浏览器...")
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto("https://libra-sg.tiktok-row.net")

        print("\n请在浏览器中完成登录（账号 + 密码 + 验证码）")
        print("登录成功看到 Libra 主界面后，回到终端按回车\n")
        input(">>> 登录完成后按回车 ")

        cookies = await context.cookies()
        out = Path(__file__).parent / "cookies.json"
        # 如果是符号链接，删掉再写真实文件
        if out.is_symlink():
            out.unlink()
        with open(out, "w") as f:
            json.dump(cookies, f, indent=2)
        print(f"\n保存了 {len(cookies)} 个 cookies 到 {out}")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
