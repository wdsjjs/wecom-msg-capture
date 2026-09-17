import { chromium, expect } from '@playwright/test';
import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

const base = process.env.DEMO_URL || 'http://127.0.0.1:4178';
const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'chrome' });
const context = await browser.newContext({ viewport: { width: 1768, height: 950 } });
const page = await context.newPage();
const errors = [];
const external = [];
page.on('pageerror', e => errors.push(e.message));
page.on('request', request => {
  if (new URL(request.url()).origin !== new URL(base).origin) external.push(request.url());
});
const output = new URL('../../output/playwright/', import.meta.url);
await mkdir(output, { recursive: true });
const screenshot = async name => page.screenshot({ path: fileURLToPath(new URL(name, output)), fullPage: true });
const chat = page.getByRole('region', { name: '访客 01的会话' });
const close = () => page.getByRole('button', { name: '关闭弹窗', exact: true }).click();
try {
  await page.goto(base);
  await expect(page.getByRole('heading', { name: '企微消息工作台 6 个会话' })).toBeVisible();
  await expect(page.getByRole('img', { name: '虚构设备的连接状态示例' })).toBeVisible();
  assert.equal(await page.locator('img').evaluateAll(images => images.every(image => image.complete && image.naturalWidth > 0)), true);
  await screenshot('desktop.png');

  await page.getByRole('textbox', { name: '搜索会话' }).fill('不存在的访客');
  await expect(page.getByText('没有匹配的会话')).toBeVisible();
  await page.getByRole('button', { name: '清空搜索' }).click();
  await page.getByRole('tab', { name: '未读 2', exact: true }).click();
  await page.getByRole('button', { name: /02 访客 02/ }).click();
  await expect(page.getByRole('tab', { name: '未读 1', exact: true })).toBeVisible();
  await page.getByRole('tab', { name: '全部', exact: true }).click();
  await page.getByRole('button', { name: /01 访客 01/ }).click();

  await page.getByRole('button', { name: '查看图片：设备状态示例', exact: true }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await expect(page.getByRole('img', { name: '虚构设备状态详情，无真实账号信息' })).toBeVisible();
  await close();
  await page.getByRole('button', { name: '演示草稿', exact: true }).click();
  await expect(page.getByRole('textbox', { name: '回复内容' })).not.toHaveValue('');
  await page.getByRole('button', { name: '发送回复', exact: true }).click();
  await expect(chat.getByText('演示发送成功', { exact: true })).toHaveCount(1);

  await page.getByRole('button', { name: '查看设备', exact: true }).click();
  await page.getByRole('button', { name: '模拟设备离线', exact: true }).click();
  await page.getByRole('button', { name: '完成', exact: true }).click();
  await expect(page.getByRole('textbox', { name: '回复内容' })).toBeDisabled();
  await expect(page.getByRole('button', { name: '模拟收到新消息' })).toBeDisabled();
  await screenshot('offline.png');
  await page.getByRole('button', { name: '查看设备', exact: true }).click();
  await page.getByRole('button', { name: '恢复设备连接', exact: true }).click();
  await page.getByRole('button', { name: '完成', exact: true }).click();

  const before = await chat.locator('.outgoing').count();
  await page.getByRole('switch', { name: '当前会话 AI 托管', exact: true }).click();
  await page.getByRole('button', { name: '模拟收到新消息' }).click();
  assert.equal(await chat.locator('.outgoing').count(), before);
  await page.getByRole('switch', { name: '当前会话 AI 托管', exact: true }).click();
  await page.getByRole('button', { name: '历史补录', exact: true }).click();
  await page.getByRole('button', { name: '开始演示补录', exact: true }).click();
  await screenshot('recovery.png');
  await close();
  await expect(page.getByRole('textbox', { name: '回复内容' })).toBeDisabled();
  const during = await chat.locator('.outgoing').count();
  await page.getByRole('button', { name: '模拟收到新消息' }).click();
  assert.equal(await chat.locator('.outgoing').count(), during);
  await expect(chat.locator('[data-history=true]')).toHaveCount(3);
  await page.getByRole('button', { name: '历史补录', exact: true }).click();
  await page.getByRole('button', { name: '结束恢复并继续接收', exact: true }).click();
  await close();
  assert.equal(await chat.locator('.outgoing').count(), during);
  await page.getByRole('button', { name: '模拟收到新消息' }).click();
  assert.equal(await chat.locator('.outgoing').count(), during + 1);
  await page.getByRole('button', { name: '运行记录', exact: true }).click();
  await expect(page.getByRole('table')).toBeVisible();
  await page.getByRole('combobox', { name: '筛选运行记录' }).selectOption('recovery');
  await expect(page.getByRole('row')).toHaveCount(3);
  await page.getByRole('button', { name: '消息工作台', exact: true }).click();
  await page.getByRole('button', { name: '重置演示', exact: true }).click();
  await page.getByRole('button', { name: '确认重置', exact: true }).click();
  await expect(chat.locator('[data-history=true]')).toHaveCount(0);
  await expect(page.getByRole('tab', { name: '未读 2', exact: true })).toBeVisible();

  for (const viewport of [{ width: 1440, height: 900 }, { width: 1024, height: 768 }, { width: 390, height: 844 }, { width: 360, height: 640 }]) {
    await page.setViewportSize(viewport);
    if (viewport.width < 640) {
      await page.getByRole('button', { name: /01 访客 01/ }).click();
      await expect(page.getByRole('textbox', { name: '回复内容' })).toBeVisible();
      await expect(chat.getByText('如果设备暂时离线，消息会丢失吗？', { exact: true })).toBeInViewport();
    }
    const dimensions = await page.evaluate(() => ({ width: document.documentElement.scrollWidth, client: innerWidth, height: document.documentElement.scrollHeight, available: innerHeight }));
    assert.ok(dimensions.width <= dimensions.client, JSON.stringify(dimensions));
    assert.ok(dimensions.height <= dimensions.available, JSON.stringify(dimensions));
    await screenshot(`${viewport.width}.png`);
    if (viewport.width < 640) await page.getByRole('button', { name: '返回会话列表' }).click();
  }
  assert.deepEqual(errors, []);
  assert.deepEqual(external, []);
  console.log('PASS: search, unread, images, draft/send, offline, takeover, recovery, live resume, logs, reset, 4 responsive viewports, no external requests or browser errors.');
} finally { await browser.close(); }
