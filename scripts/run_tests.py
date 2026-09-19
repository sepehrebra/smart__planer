"""Require DB configuration so CI cannot pass by silently skipping integration tests."""
import os
import unittest

if not os.getenv('SMARTPLANNER_TEST_DATABASE_URL'):
    raise SystemExit('SMARTPLANNER_TEST_DATABASE_URL must point to a disposable test database.')
suite = unittest.defaultTestLoader.discover('tests')
result = unittest.TextTestRunner(verbosity=2).run(suite)
native = os.getenv('SMARTPLANNER_TEST_DB_FLAVOR', 'native') == 'native'
raise SystemExit(0 if result.wasSuccessful() and (not native or not result.skipped) else 1)
