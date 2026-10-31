import { defineConfig } from 'playwright/test';

const python = process.env.SMARTPLANNER_PYTHON || (process.platform === 'win32' ? '.venv\\Scripts\\python.exe' : 'python');

export default defineConfig({
  testDir: './tests/browser',
  timeout: 30000,
  workers: 1,
  reporter: [['list'], ['html', { open: 'never', outputFolder: 'playwright-report' }]],
  use: {
    baseURL: 'http://localhost:8765',
    viewport: { width: 1440, height: 900 },
    browserName: 'chromium',
    ...(process.env.CI ? {} : { channel: 'chrome' }),
    headless: true,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: {
    command: `"${python}" scripts/e2e_server.py`,
    env: { PYTHONPATH: 'src' },
    url: 'http://localhost:8765/api/v1/health',
    timeout: 30000,
    reuseExistingServer: false,
  },
});
