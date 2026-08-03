import importlib.util
from pathlib import Path


_REAL_TEST = Path(__file__).parents[2] / "tests" / "predictions_payment_test.py"
_SPEC = importlib.util.spec_from_file_location(
    "_predictions_payment_test",
    _REAL_TEST,
)
assert _SPEC is not None
assert _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

PredictionPaymentIntentTest = _MODULE.PredictionPaymentIntentTest
