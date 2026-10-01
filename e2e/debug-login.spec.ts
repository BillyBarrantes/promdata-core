/**
 * Debug: capture login page HTML to find the correct selectors.
 */
import { test as setup } from '@playwright/test';
import path from 'path';
import fs from 'fs';

const authFile = path.join(__dirname, '.auth', 'user.json');

setup('Debug login page', async ({ page }) => {
  await page.goto('http://localhost:3000/login');
  await page.waitForTimeout(3000);

  // Screenshot
  await page.screenshot({ path: 'e2e/results/debug-login.png', fullPage: true });

  // Dump all buttons and links
  const buttons = await page.locator('button, a, [role="button"]').allInnerTexts();
  console.log(' BUTTONS/LINKS found:', JSON.stringify(buttons));

  // Dump all inputs
  const inputs = await page.locator('input').evaluateAll(els =>
    els.map(e => ({ type: e.type, name: e.name, placeholder: e.placeholder, id: e.id }))
  );
  console.log(' INPUTS found:', JSON.stringify(inputs));

  // Dump full page HTML (first 3000 chars of body)
  const html = await page.locator('body').innerHTML();
  fs.writeFileSync('e2e/results/debug-login.html', html);
  console.log(' HTML saved to e2e/results/debug-login.html');
});
