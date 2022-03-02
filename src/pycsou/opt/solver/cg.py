import typing as typ

import numpy as np

import pycsou.abc.operator as pyco
import pycsou.abc.solver as pycs
import pycsou.opt.stop as pycos
import pycsou.runtime as pycrt
import pycsou.util as pycu
import pycsou.util.ptype as pyct


class CG(pycs.Solver):
    r"""
    Conjugate Gradient Method.
    The Conjugate Gradient method solves a minimization problem of the form
     .. math::
        {\argmin_{x\in\mathbb{R}^{N}} ||Ax - b||_{2}^{2}},
    where:
    * :math:`\mathcal{A}: \mathbb{R}^{N} \to \mathbb{R}^{N}` is a *symmetric* *positive definite* operator.
    * :math:`\mathcal{b}: \mathbb{b}^{N}`.
    """

    def __init__(
        self,
        a: pyco.PosDefOp,
        b: pyct.NDArray,
        folder: typ.Optional[pyct.PathLike] = None,
        exist_ok: bool = False,
        writeback_rate: typ.Optional[int] = None,
        verbosity: int = 1,
        log_var: pyct.VarName = ("x",),
    ):
        super().__init__(
            folder=folder,
            exist_ok=exist_ok,
            writeback_rate=writeback_rate,
            verbosity=verbosity,
            log_var=log_var,
        )

        self._a = a
        self._b = pycrt.coerce(b)

    def fit(
        self,
        x0: pyct.NDArray = None,
        mode: pycs.Mode = pycs.Mode.BLOCK,
        tol: float = 1e-5,
    ):
        r"""
        Solve the minimization problem defined in :py:meth:`CG.__init__`, with the provided
        run-specific parameters.

        Parameters
        ----------
        x0: NDArray
           (..., N) Initial point(s) for the solution(s). Defaults to a zero NDArray if unspecified.
        mode: Mode
           Execution mode. See :py:class:`Solver` for usage examples.
           Useful method pairs depending on the execution mode:
           * BLOCK: fit()
           * ASYNC: fit() + busy() + stop()
           * MANUAL: fit() + steps()
        tol: Real
           Tolerance for convergence, norm(residual) <= tol.  Defaults to :math:`1e-5` if unspecified.
        """
        # Create a stopping criteria from opt, using tol, and then call super().fit() method

        xp = pycu.get_array_module(self._b)
        x0 = x0 if (x0 is None) else pycrt.coerce(x0)
        stop_crit = pycos.AbsError(eps=tol, var="r")

        if x0 is not None:
            msg = f"Input initial guess has a mismatch in its shape dimension with data array `b` "
            f"(shape {x0.shape} is not broadcastable with {self._b.shape})."
            if x0.shape[-1] != self._b.shape[-1]:
                raise ValueError(msg)
            else:
                try:
                    np.broadcast_shapes(x0.shape, self._b.shape)
                except:
                    raise ValueError(msg)
            try:
                assert pycu.get_array_module(x0) == xp
            except:
                raise ValueError(
                    f"Input initial guess has a mismatch in its array module with data array `b` "
                    f"(array module {pycu.get_array_module(x0)} is different from {xp}."
                )
        else:
            x0 = xp.zeros((1, self._b.shape[-1]), dtype=self._b.dtype)

        return super().fit(x0, mode=mode, stop_crit=stop_crit)

    def m_init(self, x0: pyct.NDArray, tol: float = 1e-5):
        self._mstate["x"] = x0
        self._mstate["r"] = self._b - self._a.apply(x0)
        self._mstate["p"] = self._mstate["r"].copy()

    def m_step(self):
        mst = self._mstate
        x = mst["x"]
        r = mst["r"]
        p = mst["p"]
        xp = pycu.get_array_module(x)
        ap = self._a.apply(p)
        rr = xp.linalg.norm(r, ord=2, axis=-1, keepdims=True) ** 2
        alpha = rr / (p * ap).sum(axis=-1, keepdims=True)
        x = x + alpha * p
        r = r - alpha * ap
        beta = xp.linalg.norm(r, ord=2, axis=-1, keepdims=True) ** 2 / rr
        p = r + beta * p
        mst["x"], mst["r"], mst["p"] = x, r, p

    def solution(self) -> pyct.NDArray:
        """
        Returns
        -------
        x: NDArray
            (..., N) The converged solution.
        """
        _, data = self.stats()
        return data.get("x")
