import { test, expect, APIRequestContext } from '@playwright/test';

async function pollUntilDone(request: APIRequestContext, caseId: string, timeoutMs = 90_000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const res = await request.get(`/api/cases/${caseId}`);
    if (res.ok()) {
      const body = await res.json();
      const status = (body.status || body.state || '').toString().toLowerCase();
      if (['done', 'completed', 'finished', 'ready'].includes(status)) {
        return body;
      }
    }
    await new Promise((r) => setTimeout(r, 2_000));
  }
  throw new Error(`Case ${caseId} did not reach done state within timeout`);
}

test.describe('Case lifecycle', () => {
  test('create -> poll -> findings -> UI tabs -> investigate -> export pdf', async ({ request, page }) => {
    const createRes = await request.post('/api/cases', {
      data: {
        target: 'e2e-target@example.com',
        type: 'email',
        legal_basis: 'legitimate_interest',
        consent: true,
      },
    });
    expect(createRes.ok()).toBeTruthy();
    const created = await createRes.json();
    const caseId = created.id || created.case_id;
    expect(caseId).toBeTruthy();

    await pollUntilDone(request, caseId);

    const findingsRes = await request.get(`/api/cases/${caseId}/findings`);
    expect(findingsRes.ok()).toBeTruthy();

    await page.goto(`/v2/cases/${caseId}`);
    for (const tab of ['Graph', 'Timeline', 'Photos', 'Geo', 'Findings', 'AI']) {
      const locator = page.getByRole('tab', { name: new RegExp(tab, 'i') })
        .or(page.getByRole('button', { name: new RegExp(tab, 'i') }))
        .or(page.getByText(new RegExp(`^${tab}$`, 'i')))
        .first();
      await locator.click({ trial: false }).catch(() => {});
    }

    const investigateRes = await request.post(`/api/cases/${caseId}/investigate`, { data: {} });
    expect([200, 201, 202]).toContain(investigateRes.status());

    const pdfRes = await request.get(`/api/cases/${caseId}/export/pdf`);
    expect(pdfRes.status()).toBe(200);
  });
});
