/**
 * STEP 1: Google OAuth login — saves session state for automated tests.
 *
 * Run:  npx playwright test e2e/setup-login.spec.ts --headed
 *
 * Browser opens → clicks Google → you log in → session saved.
 * All subsequent tests reuse the saved state from e2e/.auth/user.json.
 */
import { test as setup } from '@playwright/test';
import path from 'path';

const authFile = path.join(__dirname, '.auth', 'user.json');

setup('Google OAuth login → save session', async ({ page }) => {
  // Navigate to login page
  await page.goto('http://localhost:3000/login');

  // Wait for the Supabase Auth form to render
  await page.waitForSelector('button, a', { timeout: 15_000 });
  await page.waitForTimeout(2000);

  console.log('\n╔══════════════════════════════════════════════════╗');
  console.log('║  CLICK EN BOTÓN GOOGLE EN EL NAVEGADOR          ║');
  console.log('║  Inicia sesión con tu Google y vuelve a la app  ║');
  console.log('║  Cuando veas el chat, cierra esta pestaña        ║');
  console.log('╚══════════════════════════════════════════════════╝\n');

  // Try to find and click the Google provider button
  // Supabase auth-ui renders provider buttons with the provider name
  const googleButton = page.locator('button:has-text("Google"), a:has-text("Google"), button:has-text("google"), a:has-text("google")').first();
  const isGoogleVisible = await googleButton.isVisible().catch(() => false);

  if (isGoogleVisible) {
    console.log('🔍 Botón Google encontrado, clickeando...');
    // Click and wait for popup/redirect
    const [popup] = await Promise.all([
      page.waitForEvent('popup', { timeout: 10_000 }).catch(() => null),
      googleButton.click(),
    ]);

    if (popup) {
      console.log('🌐 Popup de Google abierto. Inicia sesión ahí.');
      // Wait for the popup to close (user completes login)
      await popup.waitForEvent('close', { timeout: 300_000 }).catch(() => {});
      console.log('✅ Popup cerrado, esperando sesión...');
    } else {
      console.log('🔄 Redirect a Google. Inicia sesión y serás redirigido.');
    }
  } else {
    console.log('⚠️ Botón Google no encontrado. Inicia sesión manualmente.');
  }

  // Wait until Supabase session is stored in localStorage
  await page.waitForFunction(() => {
    const keys = Object.keys(localStorage);
    return keys.some(k => k.startsWith('sb-') && k.endsWith('-auth-token'));
  }, { timeout: 300_000 });

  // Wait for redirect to main page
  await page.waitForURL('**/', { timeout: 15_000 }).catch(() => {});
  await page.waitForTimeout(2000);

  // Save the authenticated session state
  await page.context().storageState({ path: authFile });
  console.log(`\n✅ Sesión guardada en: ${authFile}`);
});
