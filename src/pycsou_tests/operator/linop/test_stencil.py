import itertools

import numpy as np
import pytest
import scipy.ndimage as snd

import pycsou.operator.linop as pycl
import pycsou.runtime as pycrt
import pycsou.util.deps as pycd
import pycsou.util.ptype as pyct
import pycsou_tests.operator.conftest as conftest


# We disable NumbaPerformanceWarnings due to solving small-scale problems at test time.
@pytest.mark.filterwarnings("ignore::numba.core.errors.NumbaPerformanceWarning")
class TestStencil(conftest.SquareOpT):
    disable_test = frozenset(
        conftest.SquareOpT.disable_test
        | {
            # Stencil does not support evaluating inputs at different precisions.
            "test_precCM_adjoint",
            "test_precCM_adjoint_dagger",
            "test_precCM_adjoint_T",
            "test_precCM_apply",
            "test_precCM_apply_dagger",
            "test_precCM_apply_T",
            "test_precCM_call",
            "test_precCM_call_dagger",
            "test_precCM_call_T",
            "test_precCM_eigvals",
            "test_precCM_eigvals",
            "test_precCM_pinv",
            "test_precCM_svdvals",
            # from_sciop() tests try round trip Stencil<>to_sciop()<>from_sciop().
            # Compounded effect of approximations make most tests fail.
            # There is no reason to use from_sciop() in Stencil -> safe to disable.
            "test_value_from_sciop",
            "test_prec_from_sciop",
            "test_backend_from_sciop",
        }
    )

    @pytest.fixture(
        params=[
            # 1D, random center/mode ------------------------
            (
                (10, np.arange(1, 7), (0,), "constant"),
                ((10,), (np.arange(1, 7),), ((0,),), ("constant",)),
            ),
            (
                (10, np.arange(1, 7), (1,), "edge"),
                ((10,), (np.arange(1, 7),), ((1,),), ("edge",)),
            ),
            (
                (10, np.arange(1, 7), (2,), "wrap"),
                ((10,), (np.arange(1, 7),), ((2,),), ("wrap",)),
            ),
            (
                (10, np.arange(1, 7), (3,), "reflect"),
                ((10,), (np.arange(1, 7),), ((3,),), ("reflect",)),
            ),
            (
                (10, np.arange(1, 7), (4,), "symmetric"),
                ((10,), (np.arange(1, 7),), ((4,),), ("symmetric",)),
            ),
            # ND, random center/mode ------------------------
            (
                ((10, 11), np.arange(1, 9).reshape(2, 4), (0, 3), "constant"),
                ((10, 11), (np.arange(1, 9).reshape(2, 4),), ((0, 3),), ("constant", "constant")),
            ),
            (
                ((10, 11), np.arange(1, 9).reshape(2, 4), (1, 2), ("wrap", "reflect")),
                ((10, 11), (np.arange(1, 9).reshape(2, 4),), ((1, 2),), ("wrap", "reflect")),
            ),
            (
                ((10, 11), np.arange(1, 9).reshape(2, 4), (1, 1), ("edge", "symmetric")),
                ((10, 11), (np.arange(1, 9).reshape(2, 4),), ((1, 1),), ("edge", "symmetric")),
            ),
            # ND seperable, random center/mode --------------
            (
                ((10, 11), (np.arange(1, 7), np.arange(2, 5)), (3, 0), "constant"),
                (
                    (10, 11),
                    (np.arange(1, 7).reshape(-1, 1), np.arange(2, 5).reshape(1, -1)),
                    ((3, 0), (0, 0)),
                    ("constant", "constant"),
                ),
            ),
            (
                ((10, 11), (np.arange(1, 7), np.arange(2, 5)), (2, 1), ("edge", "wrap")),
                (
                    (10, 11),
                    (np.arange(1, 7).reshape(-1, 1), np.arange(2, 5).reshape(1, -1)),
                    ((2, 0), (0, 1)),
                    ("edge", "wrap"),
                ),
            ),
            (
                ((10, 11), (np.arange(1, 7), np.arange(2, 5)), (3, 2), ("reflect", "symmetric")),
                (
                    (10, 11),
                    (np.arange(1, 7).reshape(-1, 1), np.arange(2, 5).reshape(1, -1)),
                    ((3, 0), (0, 2)),
                    ("reflect", "symmetric"),
                ),
            ),
        ]
    )
    def _spec(self, request):
        # (arg_shape, kernel, center, mode) configs to test
        # * `request.param[0]` corresponds to raw inputs users provide to Stencil().
        # * `request.param[1]` corresponds to their ground-truth canonical parameterization.
        return request.param

    @pytest.fixture
    def arg_shape(self, _spec):  # canonical representation
        arg_shape, _, _, _ = _spec[1]
        return arg_shape

    @pytest.fixture
    def kernel(self, _spec):  # canonical representation (NumPy)
        _, kernel, _, _ = _spec[1]
        return kernel

    @pytest.fixture
    def center(self, _spec):  # canonical representation
        _, _, center, _ = _spec[1]
        return center

    @pytest.fixture
    def mode(self, _spec):  # canonical representation
        _, _, _, mode = _spec[1]
        return mode

    @pytest.fixture(
        params=itertools.product(
            pycd.NDArrayInfo,
            pycrt.Width,
        )
    )
    def spec(self, _spec, request) -> tuple[pyct.OpT, pycd.NDArrayInfo, pycrt.Width]:
        ndi, width = request.param
        arg_shape, kernel, center, mode = _spec[0]  # user-provided form

        # transform kernel to right backend
        if (xp := ndi.module()) is None:
            pytest.skip(f"{ndi} unsupported on this machine.")
        try:
            pycd.NDArrayInfo.from_obj(kernel)  # passes if array object
            kernel = xp.array(kernel)
        except:
            kernel = [xp.array(k) for k in kernel]

        with pycrt.Precision(width):
            op = pycl.Stencil(
                arg_shape=arg_shape,
                kernel=kernel,
                center=center,
                mode=mode,
            )
        return op, ndi, width

    @pytest.fixture
    def data_shape(self, arg_shape) -> pyct.OpShape:
        codim = dim = np.prod(arg_shape)
        return (codim, dim)

    @pytest.fixture(params=[0, 1, 2])  # different seeds to test robustness
    def data_apply(
        self,
        arg_shape,
        kernel,
        center,
        mode,
        request,
    ) -> conftest.DataLike:
        arr = self._random_array(arg_shape, seed=request.param)

        # Pad input in excess of what is stricly required (using Pad(); assumed correct)
        if len(kernel) == 1:  # non-seperable filter
            pad_width = [(w, w) for w in kernel[0].shape]
        else:  # seperable filter(s)
            pad_width = [(k.shape[i],) * 2 for (i, k) in enumerate(kernel)]
        pad = pycl.Pad(
            arg_shape=arg_shape,
            pad_width=pad_width,
            mode=mode,
        )
        corr_in = pad.apply(arr.reshape(-1)).reshape(pad._pad_shape)

        # perform correlation via scipy.ndimage.correlate
        corr_out = corr_in.copy()
        for (k, c) in zip(kernel, center):
            origin = [cc - (n // 2) for (cc, n) in zip(c, k.shape)]
            corr_out = snd.correlate(
                input=corr_out,
                weights=k,
                mode="constant",
                cval=0,
                origin=origin,
            )

        # Trim fat off (using Trim(); assumed correct)
        trim = pycl.Trim(
            arg_shape=corr_out.shape,
            trim_width=pad_width,
        )
        out = trim.apply(corr_out.reshape(-1)).reshape(arg_shape)

        return dict(
            in_=dict(arr=arr.reshape(-1)),
            out=out.reshape(-1),
        )
