"""Run some of test_worker.py before rx_worker.py exists: python3 tests/run_part.py <module> <test names...>.
The named module stands in for rx_worker (used while the worker is drafted in parts)."""
import importlib, os, sys, types, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, os.path.dirname(__file__))
mod = importlib.import_module(sys.argv[1])
sys.modules.setdefault("rx_worker", mod)
import test_worker
suite = unittest.TestSuite(test_worker.Worker(n) for n in sys.argv[2:])
sys.exit(0 if unittest.TextTestRunner(verbosity=1).run(suite).wasSuccessful() else 1)
