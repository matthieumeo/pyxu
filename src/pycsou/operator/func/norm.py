import numpy as np

import pycsou.abc as pyca
import pycsou.math.linalg as pylinalg
import pycsou.runtime as pycrt
import pycsou.util as pycu
import pycsou.util.ptype as pyct

__all__ = [
    "L1Norm",
    "L2Norm",
    "SquaredL2Norm",
]


class ShiftLossMixin:
    def asloss(self, data: pyct.NDArray = None) -> pyct.OpT:
        from pycsou.operator.func.loss import shift_loss

        op = shift_loss(op=self, data=data)
        return op


class L1Norm(ShiftLossMixin, pyca.ProxFunc):
    r"""
    :math:`\ell_1`-norm, :math:`\Vert\mathbf{x}\Vert_1:=\sum_{i=1}^N |x_i|`.
    """

    def __init__(self, dim: pyct.Integer = None):
        r"""
        Parameters
        ----------
        dim: pyct.Integer
            Dimension size. (Default: domain-agnostic.)

        Notes
        -----
        The operator's Lipschitz constant is set to :math:`\infty` if domain-agnostic.
        It is recommended to set `dim` explicitly to compute a tight closed-form.
        """
        super().__init__(shape=(1, dim))
        if dim is None:
            self._lipschitz = np.inf
        else:
            self._lipschitz = np.sqrt(dim)

    @pycrt.enforce_precision(i="arr")
    def apply(self, arr: pyct.NDArray) -> pyct.NDArray:
        y = pylinalg.norm(arr, ord=1, axis=-1, keepdims=True)
        return y

    @pycrt.enforce_precision(i=("arr", "tau"))
    def prox(self, arr: pyct.NDArray, tau: pyct.Real) -> pyct.NDArray:
        xp = pycu.get_array_module(arr)
        y = xp.fmax(0, xp.fabs(arr) - tau)
        y *= xp.sign(arr)
        return y


class L2Norm(ShiftLossMixin, pyca.ProxFunc):
    r"""
    :math:`\ell_2`-norm, :math:`\Vert\mathbf{x}\Vert_2:=\sqrt{\sum_{i=1}^N |x_i|^2}`.
    """

    def __init__(self, dim: pyct.Integer = None):
        super().__init__(shape=(1, dim))
        self._lipschitz = 1
        self._diff_lipschitz = np.inf

    @pycrt.enforce_precision(i="arr")
    def apply(self, arr: pyct.NDArray) -> pyct.NDArray:
        y = pylinalg.norm(arr, ord=2, axis=-1, keepdims=True)
        return y

    @pycrt.enforce_precision(i=("arr", "tau"))
    def prox(self, arr: pyct.NDArray, tau: pyct.Real) -> pyct.NDArray:
        xp = pycu.get_array_module(arr)
        scale = 1 - tau / xp.fmax(self.apply(arr), tau)

        y = arr.copy()
        y *= scale.astype(dtype=arr.dtype)
        return y


class SquaredL2Norm(pyca._QuadraticFunc):
    r"""
    :math:`\ell^2_2`-norm, :math:`\Vert\mathbf{x}\Vert^2_2:=\sum_{i=1}^N |x_i|^2`.
    """

    def __init__(self, dim: pyct.Integer = None):
        r"""
        Parameters
        ----------
        dim: pyct.Integer
            Dimension size. (Default: domain-agnostic.)
        """
        super().__init__(shape=(1, dim))
        self._lipschitz = np.inf
        self._diff_lipschitz = 2

    @pycrt.enforce_precision(i="arr")
    def apply(self, arr: pyct.NDArray) -> pyct.NDArray:
        y = pylinalg.norm(arr, axis=-1, keepdims=True)
        y **= 2
        return y

    @pycrt.enforce_precision(i="arr")
    def grad(self, arr: pyct.NDArray) -> pyct.NDArray:
        return 2 * arr

    @pycrt.enforce_precision(i=("arr", "tau"))
    def prox(self, arr: pyct.NDArray, tau: pyct.Real) -> pyct.NDArray:
        y = arr.copy()
        y /= 2 * tau + 1
        return y

    def _hessian(self) -> pyct.OpT:
        from pycsou.operator.linop import IdentityOp

        if self.dim is None:
            msg = "\n".join(
                [
                    "hessian: domain-agnostic functionals unsupported.",
                    f"Explicitly set `dim` in {self.__class__}.__init__().",
                ]
            )
            raise ValueError(msg)
        return IdentityOp(dim=self.dim).squeeze()