from importlib import util
from pathlib import Path


_TEST_PATH = Path(__file__).resolve().parents[2] / "tests" / "predictions_payment_test.py"
_SPEC = util.spec_from_file_location("_predictions_payment_test", _TEST_PATH)
assert _SPEC is not None
assert _SPEC.loader is not None
_MODULE = util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

PaymentIntentExecutionTest = _MODULE.PaymentIntentExecutionTest
