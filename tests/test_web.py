"""Built frontend remains same-origin without shadowing API routes."""
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
from smartplanner.api import create_app
from smartplanner.settings import Settings

@unittest.skipUnless(Path('frontend/dist/index.html').is_file(), 'Build frontend first.')
class WebTests(unittest.TestCase):
    def test_frontend_root_assets_and_api_routes(self):
        app = create_app(Settings('postgresql://unused', origin='http://localhost:8000'))
        with TestClient(app, base_url='http://localhost:8000') as client:
            response = client.get('/')
            self.assertEqual(response.status_code, 200)
            self.assertIn('dir="rtl"', response.text)
            self.assertEqual(response.headers['x-content-type-options'], 'nosniff')
            asset = next(Path('frontend/dist/assets').glob('*.js')).name
            self.assertEqual(client.get('/assets/' + asset).status_code, 200)
            self.assertEqual(client.get('/api/v1/health').json()['status'], 'ok')
            self.assertEqual(client.get('/api/v1/not-real').status_code, 404)
