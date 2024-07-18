import collections.abc as cabc
import itertools

import numpy as np
import pytest
import scipy.ndimage as scimage
import skimage

import pyxu.info.deps as pxd
import pyxu.info.ptype as pxt
import pyxu.operator as pxo
import pyxu.runtime as pxrt
import pyxu_tests.operator.conftest as conftest
import pyxu_tests.operator.linop.diff.test_diff as test_diff


@pytest.mark.filterwarnings("ignore::numba.core.errors.NumbaPerformanceWarning")
class FilterMixin(conftest.SquareOpT):
    @pytest.fixture
    def codim_shape(self, dim_shape) -> pxt.NDArrayShape:
        return dim_shape

    @pytest.fixture
    def mode(self):
        return "constant"

    @pytest.fixture(
        params=itertools.product(
            pxd.NDArrayInfo,
            pxrt.Width,
        )
    )
    def spec(
        self, dim_shape, mode, filter_klass, filter_kwargs, request
    ) -> tuple[pxt.OpT, pxd.NDArrayInfo, pxrt.Width]:
        ndi, width = request.param
        self._skip_if_unsupported(ndi)

        op = filter_klass(
            dim_shape=dim_shape,
            mode=mode,
            gpu=ndi.name == "CUPY",
            dtype=width.value,
            **filter_kwargs,
        )
        return op, ndi, width


class TestMovingAverage(FilterMixin):
    @pytest.fixture
    def filter_klass(self):
        return pxo.MovingAverage

    @pytest.fixture(
        params=[
            # dim_shape, size, center, origin (for scipy)
            ((5, 3, 4), 3, (0, 1, 2), (-1, 0, 1)),
            ((5, 3, 4), (5, 1, 3), None, 0),
        ]
    )
    def _spec(self, request):
        # (dim_shape, size, center, origin) configs to test.
        return request.param

    @pytest.fixture
    def dim_shape(self, _spec):
        dim_shape, _, _, _ = _spec
        return dim_shape

    @pytest.fixture
    def size(self, _spec):
        _, size, _, _ = _spec
        return size

    @pytest.fixture
    def center(self, _spec):
        _, _, center, _ = _spec
        return center

    @pytest.fixture
    def origin(self, _spec):
        _, _, _, origin = _spec
        return origin

    @pytest.fixture
    def filter_kwargs(self, size, center):
        return {"size": size, "center": center}

    @pytest.fixture
    def data_apply(self, _spec, mode) -> conftest.DataLike:
        dim_shape, size, _, origin = _spec
        arr = self._random_array(dim_shape)
        out = scimage.uniform_filter(arr, size=size, mode=mode, origin=origin)

        return dict(
            in_=dict(arr=arr),
            out=out,
        )


class TestGaussian(FilterMixin):
    @pytest.fixture
    def filter_klass(self):
        return pxo.GaussianFilter

    @pytest.fixture(
        params=[
            # dim_shape, sigma, order, truncate
            ((4, 4, 4), 3, (0, 1, 2), 1),
            ((8, 8, 8), (1, 2, 3), 1, 2),
        ]
    )
    def _spec(self, request):
        # (dim_shape, sigma, order, truncate) configs to test.
        return request.param

    @pytest.fixture
    def dim_shape(self, _spec):
        dim_shape, _, _, _ = _spec
        return dim_shape

    @pytest.fixture
    def filter_kwargs(self, _spec):
        _, sigma, order, truncate = _spec
        return {"sigma": sigma, "truncate": truncate, "order": order}

    @pytest.fixture
    def data_apply(self, _spec, mode) -> conftest.DataLike:
        dim_shape, sigma, order, truncate = _spec
        arr = self._random_array(dim_shape)
        out = scimage.gaussian_filter(arr, sigma=sigma, truncate=truncate, order=order, mode=mode)
        return dict(
            in_=dict(arr=arr),
            out=out,
        )


class TestDoG(FilterMixin):
    @pytest.fixture
    def filter_klass(self):
        return pxo.DifferenceOfGaussians

    @pytest.fixture(
        params=[
            # dim_shape, low_sigma, high_sigma, low_truncate, high_truncate
            ((4, 4, 4), 1, (2, 1.5, 3), 1, 1),
            ((8, 8, 8), (1, 2, 3), 4, 1, 1),
        ]
    )
    def _spec(self, request):
        # (dim_shape, low_sigma, high_sigma, low_truncate, high_truncate) configs to test.
        return request.param

    @pytest.fixture
    def dim_shape(self, _spec):
        dim_shape, _, _, _, _ = _spec
        return dim_shape

    @pytest.fixture
    def filter_kwargs(self, _spec):
        _, low_sigma, high_sigma, low_truncate, high_truncate = _spec
        return {
            "low_sigma": low_sigma,
            "high_sigma": high_sigma,
            "low_truncate": low_truncate,
            "high_truncate": high_truncate,
        }

    @pytest.fixture
    def data_apply(self, _spec, mode) -> conftest.DataLike:
        dim_shape, low_sigma, high_sigma, low_truncate, high_truncate = _spec
        arr = self._random_array(dim_shape)
        out_low = scimage.gaussian_filter(arr, sigma=low_sigma, truncate=low_truncate, order=0, mode=mode)
        out_high = scimage.gaussian_filter(arr, sigma=high_sigma, truncate=high_truncate, order=0, mode=mode)
        out = out_low - out_high
        return dict(
            in_=dict(arr=arr),
            out=out,
        )


class TestLaplace(FilterMixin):
    @pytest.fixture
    def filter_klass(self):
        return pxo.Laplace

    @pytest.fixture(params=[(8,), (4, 4, 4)])
    def dim_shape(self, request):
        return request.param

    @pytest.fixture
    def filter_kwargs(self):
        return dict()

    @pytest.fixture
    def data_apply(self, dim_shape, mode) -> conftest.DataLike:
        arr = self._random_array(dim_shape)
        out = scimage.laplace(arr, mode=mode)
        return dict(
            in_=dict(arr=arr),
            out=out,
        )


@pytest.mark.filterwarnings("ignore::numba.core.errors.NumbaPerformanceWarning")
class EdgeFilterMixin(conftest.DiffMapT):
    @pytest.fixture
    def codim_shape(self, dim_shape) -> pxt.NDArrayShape:
        return dim_shape

    @pytest.fixture
    def mode(self):
        return "constant"

    @pytest.fixture(
        params=itertools.product(
            pxd.NDArrayInfo,
            pxrt.Width,
        )
    )
    def spec(
        self, dim_shape, mode, filter_klass, filter_kwargs, request
    ) -> tuple[pxt.OpT, pxd.NDArrayInfo, pxrt.Width]:
        ndi, width = request.param
        self._skip_if_unsupported(ndi)
        op = filter_klass(
            dim_shape=dim_shape,
            mode=mode,
            gpu=ndi.name == "CUPY",
            dtype=width.value,
            **filter_kwargs,
        )
        return op, ndi, width

    @pytest.fixture(
        params=[
            # dim_shape, axis, axis_scipy
            ((4,), None, (0,)),
            ((4, 4, 4), (0, 2), (0, 2)),
        ]
    )
    def _spec(self, request):
        # dim_shape, axis, axis_scipy
        return request.param

    @pytest.fixture
    def dim_shape(self, _spec):
        dim_shape, _, _ = _spec
        return dim_shape

    @pytest.fixture
    def axis(self, _spec):
        _, axis, _ = _spec
        return axis

    @pytest.fixture
    def axis_skimage(self, _spec):
        _, _, axis_skimage = _spec
        return axis_skimage

    @pytest.fixture
    def filter_kwargs(self, axis):
        return {"axis": axis}

    @pytest.fixture
    def data_apply(self, dim_shape, filter_skimage, axis_skimage, mode) -> conftest.DataLike:
        arr = self._random_array(dim_shape)
        out = filter_skimage(arr, mode=mode, axis=axis_skimage)

        return dict(
            in_=dict(arr=arr),
            out=out,
        )

    @pytest.fixture
    def data_math_lipschitz(self, op) -> cabc.Collection[np.ndarray]:
        N_test = 10
        x = self._random_array((N_test, *op.dim_shape))
        return x


class TestSobel(EdgeFilterMixin):
    @pytest.fixture
    def filter_klass(self):
        return pxo.Sobel

    @pytest.fixture
    def filter_skimage(self):
        return skimage.filters.sobel


class TestPrewitt(EdgeFilterMixin):
    @pytest.fixture
    def filter_klass(self):
        return pxo.Prewitt

    @pytest.fixture
    def filter_skimage(self):
        return skimage.filters.prewitt


class TestScharr(EdgeFilterMixin):
    @pytest.fixture
    def filter_klass(self):
        return pxo.Scharr

    @pytest.fixture
    def filter_skimage(self):
        return skimage.filters.scharr


class TestStructureTensor(conftest.DiffMapT):
    @pytest.fixture(
        params=[
            # 1,
            1.5,
            # 2
        ]
    )
    def sampling(self, request):
        return request.param

    @pytest.fixture(params=pxd.NDArrayInfo)
    def ndi(self, request):
        # [Sepand] Not inferred from spec(diff_kwargs) [unlike most Pyxu operators] since diff_kwargs() needs this information beforehand.
        ndi_ = request.param
        if ndi_.module() is None:
            pytest.skip(f"{ndi_} unsupported on this machine.")
        return ndi_

    @pytest.fixture(params=pxrt.Width)
    def width(self, request):
        # [Sepand] Not inferred from spec(diff_kwargs) [unlike most Pyxu operators] since diff_kwargs() needs this information beforehand.
        return request.param

    @pytest.fixture(params=["fd"])
    def diff_method(self, request):
        return request.param

    @pytest.fixture(
        params=[
            #  Finite Diff. ,   Gaussian Der.
            # (diff_typ, acc), (sigma, truncate)
            (("central", 2), (1.0, 1.0)),
        ]
    )
    def init_params(self, diff_method, sampling, request):
        params_fd, params_gd = request.param
        if diff_method == "fd":
            return test_diff.diff_params_fd(params_fd[0], params_fd[1], sampling)
        elif diff_method == "gd":
            return test_diff.diff_params_gd(params_gd[0], params_gd[1], sampling)

    @pytest.fixture
    def diff_params(self, init_params):
        return init_params[0]

    @pytest.fixture
    def gt_diffs(self, init_params):
        return init_params[1]

    @pytest.fixture
    def codim_shape(self, dim_shape) -> pxt.NDArrayShape:
        ndim = len(dim_shape)
        if ndim > 1:
            return (ndim * (ndim + 1) / 2,) + dim_shape
        else:
            return dim_shape

    @pytest.fixture
    def mode(self):
        return "constant"

    @pytest.fixture
    def spec(self, dim_shape, filter_kwargs, ndi, width) -> tuple[pxt.OpT, pxd.NDArrayInfo, pxrt.Width]:
        op = pxo.StructureTensor(
            dim_shape=dim_shape,
            gpu=ndi == pxd.NDArrayInfo.CUPY,
            width=width,
            **filter_kwargs,
        )
        return op, ndi, width

    @pytest.fixture(params=[(8,), (4, 4)])
    def dim_shape(self, request):
        return request.param

    @pytest.fixture
    def smooth_sigma(self):
        return 2.0

    @pytest.fixture
    def smooth_truncate(self):
        return 2.0

    @pytest.fixture(params=[False, True])
    def parallel(self, request):
        return request.param

    @pytest.fixture
    def filter_kwargs(self, diff_method, smooth_sigma, smooth_truncate, sampling, parallel, diff_params, mode):
        return {
            "diff_method": diff_method,
            "smooth_sigma": smooth_sigma,
            "smooth_truncate": smooth_truncate,
            "sampling": sampling,
            "parallel": parallel,
            "diff_params": diff_params,
            "mode": mode,
        }

    @pytest.fixture
    def data_apply(self, dim_shape, diff_method, gt_diffs, filter_kwargs) -> conftest.DataLike:
        arr = self._random_array(dim_shape)
        directions = [i for i in range(len(dim_shape))]
        # Cannot use skimage structure tensor as it uses the sobel filter as derivative.
        derivatives = test_diff.apply_gradient(
            arr,
            dim_shape=dim_shape,
            gt_diffs=gt_diffs,
            directions=directions,
            diff_method=diff_method,
            mode=filter_kwargs["mode"],
        )

        gt_smooth = test_diff.diff_params_gd(
            filter_kwargs["smooth_sigma"], filter_kwargs["smooth_truncate"], filter_kwargs["sampling"]
        )[1]

        out = []
        for der0, der1 in itertools.combinations_with_replacement(derivatives, 2):
            o = der0 * der1
            for dim in range(len(dim_shape)):
                o = test_diff.apply_derivative(
                    arr=o, dim_shape=dim_shape, axis=dim, gt_diffs=gt_smooth, order=0, mode=filter_kwargs["mode"]
                )
            out.append(o)
        out = np.stack(out)

        return dict(
            in_=dict(arr=arr),
            out=out,
        )
