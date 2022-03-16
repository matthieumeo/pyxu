import warnings

import numpy as np

import pycsou.abc.operator as pyco
import pycsou.linop.base as pycb
import pycsou.runtime as pycrt
import pycsou.util as pycu
import pycsou.util.ptype as pyct


@pycrt.enforce_precision(o=True)
def hutchpp(linop: pyco.LinOp, m: int = 4002, xp: pyct.ArrayModule = np, seed: float = 0):
    r"""
    Computes an stochastic estimate of the trace for linear operators based on the Hutch++ algorithm (specifically,
    algorithm 3 of the paper https://arxiv.org/abs/2010.09649.

    Parameters
    ----------
    linop: :py:class:`~pycsou.abc.operator.LinOp`
        Linear operator object compliant with Pycsou's interface with square shape.
    m: int
        The number of queries desired to estimate the trace of the linear operator. ``m`` is set to 4002 by default,
        based on the analysis of variance described in theorem 10 of the Hutch++ paper. This default number of queries
        corresponds to having an estimation error smaller than 0.01 with a probability of 0.9.
    xp:  pycsou.util.ptype.ArrayModule
         Which array module to use to represent the output.
    seed: int
        Seed for the random number generator.


    Returns
    -------
    float
        Hutch++ stochastic estimate of the trace.

    Notes
    -----
    This function calls Scipy’s function: :py:func:`scipy.sparse.linalg.qr`. See the documentation of this function
    for more information on its behaviour and the underlying LAPACK routines it relies on.
    """

    if xp == np:
        kwargs = {"mode": "economic"}
        import scipy

        xlin = scipy.linalg
    elif pycu.deps.CUPY_ENABLED:  # Scipy casts any input to the LinOp as a Numpy array so the cupyx version is needed.
        import cupy as cp

        if xp == cp:
            xlin = cp.linalg
            kwargs = {"mode": "reduced"}
    else:
        import dask.array as da

        if xp == da:
            xlin = da
            kwargs = {}

    d = linop.shape[1]
    if m >= d:
        warnings.warn(
            "Full trace computation performed. Stochastic trace estimation not performed because the number "
            "of queries is larger or equal to the dimension of the linear operator.",
            UserWarning,
        )
        return xp.trace(linop.apply(xp.eye(d)))

    if isinstance(linop, pycb.ExplicitLinOp):
        if xp != pycu.get_array_module(linop.mat):
            warnings.warn(
                f"The array module of the :py:class:`~pycsou.linop.base.ExplicitLinOp` "
                f"({pycu.get_array_module(linop.mat)}) and the requested array module "
                f"({xp}) are different.",
                UserWarning,
            )

    rng = xp.random.default_rng(seed=seed)
    s = rng.standard_normal(size=(d, (m + 2) // 4))
    g = rng.binomial(n=1, p=0.5, size=(d, (m - 2) // 2)) * 2 - 1
    q, _ = xlin.qr(linop.apply(s.T).T, **kwargs)
    proj = g - q @ (q.T @ g)
    return xp.trace(q.T @ linop.apply(q.T).T) + (2.0 / (m - 2)) * xp.trace(proj.T @ linop.apply(proj.T).T)
