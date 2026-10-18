"""Run one class of test_server.py with another module standing in for server:
python3 tests/run_server_part.py <module> <TestClass> (used while server.py is drafted in parts)."""
import importlib, os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, os.path.dirname(__file__))
sys.modules["server"] = importlib.import_module(sys.argv[1])
import test_server
suite = unittest.defaultTestLoader.loadTestsFromTestCase(getattr(test_server, sys.argv[2]))
sys.exit(0 if unittest.TextTestRunner(verbosity=1).run(suite).wasSuccessful() else 1)
