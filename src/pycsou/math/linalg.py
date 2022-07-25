import warnings

import dask.array as da
import numpy as np

import pycsou.abc.operator as pyco
import pycsou.operator.linop.base as pycb
import pycsou.runtime as pycrt
import pycsou.util as pycu
import pycsou.util.ptype as pyct


def norm(x: pyct.NDArray, **kwargs):
    """
    Matrix or vector norm.

    This function is identical to :py:func:`numpy.linalg.norm`.
    It exists to correct bugs in Dask's implementation.

    Parameters
    ----------
    x: pyct.NDArray
        Input array.
    **kwargs
        Any kwarg accepted by :py:func:`numpy.linalg.norm`.

    Returns
    -------
    nrm: pyct.NDArray
        Norm of the matrix or vector(s).
    """
    xp = pycu.get_array_module(x)
    nrm = xp.linalg.norm(x, **kwargs)
    nrm = nrm.astype(x.dtype, copy=False)  # dask bug: linalg.norm() always returns float64
    return nrm


def hutchpp(
    op: pyco.SquareOp,
    m: int = 4002,
    xp: pyct.ArrayModule = np,
    seed: float = 0,
    enable_warnings: bool = True,
) -> float:
    r"""
    Stochastic estimate of the trace of a linear operator based on the Hutch++ algorithm.
    (Specifically algorithm 3 of the paper https://arxiv.org/abs/2010.09649)

    Parameters
    ----------
    op: :py:class:`~pycsou.abc.operator.SquareOp`
    m: int
        Number of queries used to estimate the trace of the linear operator.

        ``m`` is set to 4002 by default based on the analysis of the variance described in theorem
        10. This default corresponds to having an estimation error smaller than 0.01 with a
        probability of 0.9.
    xp: pycsou.util.ptype.ArrayModule
        Array module used for internal computations.
    seed: int
        Seed for the random number generator.

    Returns
    -------
    tr: float
        Stochastic estimate of tr(op).
    """
    if m >= op.dim:
        if enable_warnings:
            warnings.warn(
                "Number of queries >= dim(op): fallback to deterministic trace eval.",
                UserWarning,
            )
        tr = 0
        e = xp.zeros(op.dim)
        for i in range(op.dim):
            e[:] = 0
            e[i] = 1
            tr += op.apply(e)[i]
    else:
        rng = np.random.default_rng(seed=seed)
        s = xp.asarray(rng.standard_normal(size=(op.dim, (m + 2) // 4)))
        g = xp.asarray(rng.choice((1, -1), size=(op.dim, (m - 2) // 2)))

        data = op.apply(s.T).T
        kwargs = dict(mode="reduced")
        if xp == da:
            data = data.rechunk({0: "auto", 1: -1})
            kwargs.pop("mode")

        q, _ = xp.linalg.qr(data, **kwargs)
        proj = g - q @ (q.T @ g)

        tr = (op.apply(q.T) @ q).trace()
        tr += (2 / (m - 2)) * (op.apply(proj.T) @ proj).trace()
    return float(tr)
