import itertools

import numpy as np
import pytest
from test_diff import DiffOpMixin, apply_gradient, apply_hessian

import pyxu.info.deps as pxd
import pyxu.info.ptype as pxt
import pyxu.operator as pxo
import pyxu_tests.operator.conftest as conftest


class DirDerOpMixin(DiffOpMixin):
    @pytest.fixture(
        params=[
            #          (dim_shape, directions)
            (
                (5,),
                ((1.0,),),
            ),
            (
                (5, 5, 5),
                ((0.1, 2.0, 1.0),),
            ),
        ]
    )
    def _spec(self, request):
        # (dim_shape, directions) configs to test
        return request.param

    @pytest.fixture
    def dim_shape(self, _spec):
        return _spec[0]

    @pytest.fixture
    def diff_method(self):
        return "fd"

    @pytest.fixture(params=["constant", "varying"])
    def directions(self, _spec, dim_shape, request):
        dirs = np.array(_spec[1])
        for i in range(len(dirs)):
            dirs[i] = dirs[i] / np.linalg.norm(dirs[i])

        if request.param == "constant":
            pass
        else:
            expand_dims = -np.arange(len(dim_shape)) - 1
            broadcast_arr = np.ones(
                (
                    1,
                    len(dim_shape),
                )
                + dim_shape
            )
            dirs = np.expand_dims(dirs, expand_dims.tolist()) * broadcast_arr

        return dirs

    def test_math2_lipschitz(self, op, _data_estimate_lipschitz, _gpu, ndi):
        N = pxd.NDArrayInfo
        if ndi is not N.DASK:
            super().test_math2_lipschitz(op, _data_estimate_lipschitz, _gpu)
        else:
            pytest.skip("SVD values cannot be computed with SciPy's `svds()` for `dirs` with Dask backend.")

    def test_math3_lipschitz(self, op, _data_estimate_diff_lipschitz, _gpu, ndi):
        N = pxd.NDArrayInfo
        if ndi is not N.DASK:
            super().test_math2_lipschitz(op, _data_estimate_diff_lipschitz, _gpu)
        else:
            pytest.skip("SVD values cannot be computed with SciPy's `svds()` for `dirs` with Dask backend.")


class TestDirectionalDerivative(DirDerOpMixin):
    @pytest.fixture
    def codim_shape(self, dim_shape) -> pxt.NDArrayShape:
        return dim_shape

    @pytest.fixture(params=[1, 2])
    def order(self, request):
        return request.param

    @pytest.fixture
    def diff_kwargs(self, dim_shape, order, directions, ndi, width, sampling, diff_method):
        xp = ndi.module()
        return {
            "dim_shape": dim_shape,
            "order": order,
            "directions": xp.array(directions[0], dtype=width.value),
            "mode": "constant",
            "sampling": sampling,
            "diff_method": diff_method,
        }

    @pytest.fixture
    def diff_op(self):
        return pxo.DirectionalDerivative

    @pytest.fixture
    def data_apply(self, op, dim_shape, diff_method, gt_diffs, directions, order) -> conftest.DataLike:
        arr = self._random_array(dim_shape, seed=20)
        out = np.zeros(dim_shape)
        grad_directions = np.arange(len(dim_shape), dtype=int)
        if order == 1:
            grad = apply_gradient(arr, dim_shape, gt_diffs, grad_directions, diff_method, mode="constant")
            for i in range(len(dim_shape)):
                out += directions[0][i] * grad[i].squeeze()
        else:  # order == 2
            hess_directions = tuple(list(_) for _ in itertools.combinations_with_replacement(grad_directions, 2))
            hess = apply_hessian(arr, dim_shape, gt_diffs, hess_directions, diff_method, mode="constant")
            c = 0
            for i in range(len(dim_shape)):
                for j in range(i, len(dim_shape)):
                    factor = 1 if i == j else 2
                    out += directions[0][i] * hess[c].squeeze() * directions[0][j] * factor
                    c += 1

        return dict(
            in_=dict(arr=arr),
            out=out,
        )


class TestDirectionalGradient(DirDerOpMixin):
    @pytest.fixture(
        params=[
            #          (dim_shape, directions)
            (
                (5, 5, 5),
                (
                    (0.1, 2.0, 1.0),
                    (0.1, 1.0, 2.0),
                    (2.0, 0.1, 1.0),
                    (2.0, 1.0, 0.1),
                ),
            ),
        ]
    )
    def _spec(self, request):
        # (dim_shape, directions) configs to test
        return request.param

    @pytest.fixture
    def codim_shape(self, directions, dim_shape) -> pxt.NDArrayShape:
        return (len(directions),) + dim_shape

    @pytest.fixture
    def diff_kwargs(self, dim_shape, directions, ndi, width, sampling, diff_method):
        xp = ndi.module()
        return {
            "dim_shape": dim_shape,
            "directions": [xp.array(direction, dtype=width.value) for direction in directions],
            "mode": "constant",
            "sampling": sampling,
            "diff_method": diff_method,
        }

    @pytest.fixture
    def diff_op(self):
        return pxo.DirectionalGradient

    @pytest.fixture
    def data_apply(self, op, dim_shape, diff_method, gt_diffs, directions) -> conftest.DataLike:
        arr = self._random_array(dim_shape, seed=20)
        out = np.zeros((len(directions), *dim_shape))
        grad_directions = np.arange(len(dim_shape), dtype=int)
        grad = apply_gradient(arr, dim_shape, gt_diffs, grad_directions, diff_method, mode="constant")
        for j, direction in enumerate(directions):
            for i in range(len(dim_shape)):
                out[j] += direction[i] * grad[i].squeeze()

        return dict(
            in_=dict(arr=arr),
            out=out,
        )


class TestDirectionalLaplacian(DirDerOpMixin):
    @pytest.fixture(
        params=[
            #          (dim_shape, directions)
            (
                (5, 5, 5),
                (
                    (0.1, 2.0, 1.0),
                    (0.1, 1.0, 2.0),
                    (2.0, 0.1, 1.0),
                    (2.0, 1.0, 0.1),
                ),
                (0.1, 0.2, 0.3, 0.5),
            ),
        ]
    )
    def _spec(self, request):
        # (dim_shape, directions) configs to test
        return request.param

    @pytest.fixture
    def weights(self, _spec):
        return _spec[2]

    @pytest.fixture
    def codim_shape(self, dim_shape) -> pxt.NDArrayShape:
        return dim_shape

    @pytest.fixture
    def diff_kwargs(self, dim_shape, directions, ndi, width, sampling, diff_method, weights):
        xp = ndi.module()
        return {
            "dim_shape": dim_shape,
            "directions": [xp.array(direction, dtype=width.value) for direction in directions],
            "mode": "constant",
            "sampling": sampling,
            "diff_method": diff_method,
            "weights": weights,
        }

    @pytest.fixture
    def diff_op(self):
        return pxo.DirectionalLaplacian

    @pytest.fixture
    def data_apply(self, op, dim_shape, diff_method, gt_diffs, directions, weights) -> conftest.DataLike:
        arr = self._random_array(dim_shape, seed=20)
        out = np.zeros(dim_shape)
        grad_directions = np.arange(len(dim_shape), dtype=int)
        hess_directions = tuple(list(_) for _ in itertools.combinations_with_replacement(grad_directions, 2))
        hess = apply_hessian(arr, dim_shape, gt_diffs, hess_directions, diff_method, mode="constant")
        for weight, direction in zip(weights, directions):
            c = 0
            for i in range(len(dim_shape)):
                for j in range(i, len(dim_shape)):
                    factor = 1 if i == j else 2
                    out += weight * direction[i] * hess[c].squeeze() * direction[j] * factor
                    c += 1

        return dict(
            in_=dict(arr=arr),
            out=out,
        )


class TestDirectionalHessian(DirDerOpMixin):
    @pytest.fixture(
        params=[
            #          (dim_shape, directions)
            (
                (5, 5, 5),
                (
                    (0.1, 2.0, 1.0),
                    (0.1, 1.0, 2.0),
                    (2.0, 0.1, 1.0),
                    (2.0, 1.0, 0.1),
                ),
            ),
        ]
    )
    def _spec(self, request):
        # (dim_shape, directions) configs to test
        return request.param

    @pytest.fixture
    def codim_shape(self, dim_shape, directions) -> pxt.NDArrayShape:
        ndim_hess = len(directions) * (len(directions) + 1) // 2
        sh = (ndim_hess,) + dim_shape
        return sh

    @pytest.fixture
    def diff_kwargs(self, dim_shape, directions, ndi, width, sampling, diff_method):
        xp = ndi.module()
        return {
            "dim_shape": dim_shape,
            "directions": [xp.array(direction, dtype=width.value) for direction in directions],
            "mode": "constant",
            "sampling": sampling,
            "diff_method": diff_method,
        }

    @pytest.fixture
    def diff_op(self):
        return pxo.DirectionalHessian

    @pytest.fixture
    def data_apply(self, op, dim_shape, diff_method, gt_diffs, directions) -> conftest.DataLike:
        arr = self._random_array(dim_shape, seed=20)
        grad_directions = np.arange(len(dim_shape), dtype=int)
        hess_directions = tuple(list(_) for _ in itertools.combinations_with_replacement(grad_directions, 2))
        dir_hess_directions = tuple(
            list(_) for _ in itertools.combinations_with_replacement(np.arange(len(directions)), 2)
        )
        out = np.zeros((len(dir_hess_directions), *dim_shape))
        hess = apply_hessian(arr, dim_shape, gt_diffs, hess_directions, diff_method, mode="constant")
        c1 = 0
        for k1, direction1 in enumerate(directions):
            for k2, direction2 in enumerate(directions[k1:]):
                c2 = 0
                for i in range(len(dim_shape)):
                    for j in range(i, len(dim_shape)):
                        factor = 1 if i == j else 2
                        out[c1] += direction1[i] * hess[c2].squeeze() * direction2[j] * factor
                        c2 += 1
                c1 += 1
        return dict(
            in_=dict(arr=arr),
            out=out,
        )
