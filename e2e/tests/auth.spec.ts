import { test, expect, request as pwRequest } from '@playwright/test';

test.describe('Authentication & rate limiting', () => {
  test('rejects missing API key with 401', async ({ playwright, baseURL }) => {
    const ctx = await playwright.request.newContext({ baseURL, extraHTTPHeaders: {} });
    const res = await ctx.get('/api/cases');
    expect([401, 403]).toContain(res.status());
    await ctx.dispose();
  });

  test('rejects invalid API key with 401', async ({ playwright, baseURL }) => {
    const ctx = await playwright.request.newContext({
      baseURL,
      extraHTTPHeaders: { Authorization: 'Bearer invalid-key-xxx' },
    });
    const res = await ctx.get('/api/cases');
    expect([401, 403]).toContain(res.status());
    await ctx.dispose();
  });

  test('rate limit eventually returns 429', async ({ request }) => {
    let sawRateLimit = false;
    for (let i = 0; i < 200; i++) {
      const res = await request.get('/api/cases');
      if (res.status() === 429) {
        sawRateLimit = true;
        break;
      }
    }
    expect(sawRateLimit).toBeTruthy();
  });
});
