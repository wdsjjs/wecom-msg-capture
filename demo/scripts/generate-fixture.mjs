import { chromium } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";

// This attachment is rendered from synthetic markup, never a captured desktop.
const browser = await chromium.launch({
  channel: process.env.PLAYWRIGHT_CHANNEL || "chrome",
});
const page = await browser.newPage({
  viewport: { width: 640, height: 390 },
  deviceScaleFactor: 1,
});
await page.setContent(`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><style>
*{box-sizing:border-box}body{margin:0;background:#f3f5f8;color:#3a4758;font:15px -apple-system,BlinkMacSystemFont,'PingFang SC',sans-serif;padding:28px}.window{background:white;border:1px solid #e0e5ed;border-radius:8px;overflow:hidden;box-shadow:0 8px 25px #20324708}header{display:flex;align-items:center;gap:7px;height:43px;padding:0 17px;background:#fafbfd;border-bottom:1px solid #e9edf2}.dot{height:9px;width:9px;background:#e5c6c5;border-radius:50%}.dot:nth-child(2){background:#e8dcae}.dot:nth-child(3){background:#bddac9}header span{margin-left:14px;color:#8a95a5;font-size:12px}main{padding:25px 28px}h1{font-size:22px;margin:0 0 7px;font-weight:600}p{font-size:12px;color:#97a2b2;margin:0 0 24px}.status{display:flex;align-items:center;justify-content:space-between;font-size:13px;padding:15px 0;border-top:1px solid #edf0f4}.status b{font-weight:400;color:#74a18b}.badge{float:right;padding:4px 8px;font-size:11px;background:#edf7f1;color:#64a080;border-radius:4px}footer{font-size:11px;color:#aab2bd;margin-top:18px}
</style><div class="window"><header><i class="dot"></i><i class="dot"></i><i class="dot"></i><span>WeCom Capture · 演示设备</span></header><main><span class="badge">在线</span><h1>演示 Mac</h1><p>demo-mac-01 · 虚构设备</p><div class="status">辅助功能<b>模拟已授权</b></div><div class="status">屏幕录制<b>模拟已授权</b></div><footer>示例图像，不包含真实设备或账号信息</footer></main></div></html>`);
await mkdir(new URL("../public/", import.meta.url), { recursive: true });
await page.screenshot({
  path: fileURLToPath(new URL("../public/device-sample.png", import.meta.url)),
});
await browser.close();
