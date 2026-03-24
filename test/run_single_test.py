"""
Simple test runner to diagnose issues
"""
import sys
import os

# Add parent directory to path (仅脚本模式需要)

def _run_diagnostics():
    # Add parent directory to path
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    print(f"Python version: {sys.version}")
    print(f"Current directory: {os.getcwd()}")
    print(f"Test directory: {os.path.dirname(__file__)}")
    print(f"Parent directory: {os.path.dirname(os.path.dirname(__file__))}")

    try:
        import config
        print(f"[OK] config imported successfully")
        print(f"  ENABLE_SIMULATION_MODE: {config.ENABLE_SIMULATION_MODE}")
    except Exception as e:
        print(f"[FAIL] Failed to import config: {e}")
        return 1

    try:
        from test_base import TestBase  # noqa: F401
        print(f"[OK] test_base imported successfully")
    except Exception as e:
        print(f"[FAIL] Failed to import test_base: {e}")
        return 1

    try:
        from test_utils import is_qmt_running  # noqa: F401
        print(f"[OK] test_utils imported successfully")
    except Exception as e:
        print(f"[FAIL] Failed to import test_utils: {e}")
        return 1

    try:
        from test_mocks import create_mock_qmt_trader  # noqa: F401
        print(f"[OK] test_mocks imported successfully")
    except Exception as e:
        print(f"[FAIL] Failed to import test_mocks: {e}")
        return 1

    print("\n" + "="*50)
    print("Running a simple test...")
    print("="*50)

    import unittest

    class SimpleTest(unittest.TestCase):
        def test_basic(self):
            """Basic sanity test"""
            self.assertTrue(True)
            print("[OK] Basic test passed")

    suite = unittest.TestLoader().loadTestsFromTestCase(SimpleTest)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(_run_diagnostics())
