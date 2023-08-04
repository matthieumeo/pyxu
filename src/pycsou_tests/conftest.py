import collections.abc as cabc
import inspect
import types
import typing as typ

import numpy as np
import pytest

import pycsou.info.deps as pycd
import pycsou.info.ptype as pyct
import pycsou.runtime as pycrt
import pycsou.util as pycu


@pytest.fixture(params=pycd.supported_array_modules())
def xp(request) -> types.ModuleType:
    return request.param


def flaky(func: cabc.Callable, args: dict, condition: bool, reason: str):
    # XFAIL if func(**args) fails when `condition` satisfied.
    # This function is required when pytest.mark.xfail() cannot be used.
    # (Reason: `condition` only available inside test body.)
    try:
        out = func(**args)
        return out
    except Exception:
        if condition:
            pytest.xfail(reason)
        else:
            raise


def isclose(
    a: typ.Union[pyct.Real, pyct.NDArray],
    b: typ.Union[pyct.Real, pyct.NDArray],
    as_dtype: pyct.DType,
) -> pyct.NDArray:
    """
    Equivalent of `xp.isclose`, but where atol is automatically chosen based on `as_dtype`.

    This function always returns a computed array, i.e. NumPy/CuPy output.
    """
    atol = {
        np.dtype(np.half): 3e-2,  # former pycrt.Width.HALF
        pycrt.Width.SINGLE.value: 2e-4,
        pycrt.CWidth.SINGLE.value: 2e-4,
        pycrt.Width.DOUBLE.value: 1e-8,
        pycrt.CWidth.DOUBLE.value: 1e-8,
    }
    # Numbers obtained by:
    # * \sum_{k >= (p+1)//2} 2^{-k}, where p=<number of mantissa bits>; then
    # * round up value to 3 significant decimal digits.
    # N_mantissa = [10, 23, 52, 112] for [half, single, double, quad] respectively.

    if (prec := atol.get(as_dtype)) is None:
        # should occur for integer types only
        prec = atol[pycrt.Width.DOUBLE.value]
    cast = lambda x: pycu.compute(x)
    eq = np.isclose(cast(a), cast(b), atol=prec)
    return eq


def allclose(
    a: pyct.NDArray,
    b: pyct.NDArray,
    as_dtype: pyct.DType,
) -> bool:
    """
    Equivalent of `all(isclose)`, but where atol is automatically chosen based on `as_dtype`.
    """
    return bool(np.all(isclose(a, b, as_dtype)))


def less_equal(
    a: pyct.NDArray,
    b: pyct.NDArray,
    as_dtype: pyct.DType,
) -> pyct.NDArray:
    """
    Equivalent of `a <= b`, but where equality tests are done at a chosen numerical precision.

    This function always returns a computed array, i.e. NumPy/CuPy output.
    """
    x = pycu.compute(a <= b)
    y = isclose(a, b, as_dtype)
    return x | y


class DisableTestMixin:
    """
    Disable certain tests based on user black-list.

    Example
    -------
    Consider the sample file below, where `test_1` and `test_3` should not be run.

    .. code-block:: python3

       # file: test_myclass.py

       import pytest
       import pycsou_tests.conftest as ct

       class TestMyClass(ct.DisableTestMixin):
           disable_test = {  # disable these tests
               "test_1",
               "test_3",
           }

           def test_1(self):
               self._skip_if_disabled()
               assert False  # should fail if not disabled

           def test_2(self):
               self._skip_if_disabled()
               assert True   # should pass

           def test_3(self):
               self._skip_if_disabled()
               assert False  # should fail if not disabled


    .. code-block:: bash

       $ pytest --quiet test_myclass.py

       s.s                           [100%]
       1 passed, 2 skipped in 0.59s             # -> test_[13]() were skipped/disabled.
    """

    disable_test: cabc.Set[str] = frozenset()

    def _skip_if_disabled(self):
        # Get name of function which invoked me.
        my_frame = inspect.currentframe()
        up_frame = inspect.getouterframes(my_frame)[1].frame
        up_finfo = inspect.getframeinfo(up_frame)
        up_fname = up_finfo.function
        if up_fname in self.disable_test:
            pytest.skip("disabled test")
