import { test, expect } from '@playwright/test';

test.describe('GDPR right to be forgotten', () => {
  test('POST /forget pseudonymises findings', async ({ request }) => {
    const createRes = await request.post('/api/cases', {
      data: {
        target: 'forget-me@example.com',
        type: 'email',
        legal_basis: 'consent',
        consent: true,
      },
    });
    expect(createRes.ok()).toBeTruthy();
    const created = await createRes.json();
    const caseId = created.id || created.case_id;

    const forgetRes = await request.post(`/api/cases/${caseId}/forget`, { data: {} });
    expect([200, 202, 204]).toContain(forgetRes.status());

    const findingsRes = await request.get(`/api/cases/${caseId}/findings`);
    expect(findingsRes.ok()).toBeTruthy();
    const text = await findingsRes.text();
    expect(text.toLowerCase()).not.toContain('forget-me@example.com');
  });
});
