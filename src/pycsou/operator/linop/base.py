import math
import types
import typing as typ
import warnings

import numpy as np
import scipy.sparse as sp
import sparse as ssp

import pycsou.abc as pyca
import pycsou.math.stencil as pycstencil
import pycsou.runtime as pycrt
import pycsou.util as pycu
import pycsou.util.deps as pycd
import pycsou.util.ptype as pyct
import pycsou.util.warning as pycuw

__all__ = ["IdentityOp", "NullOp", "NullFunc", "HomothetyOp", "DiagonalOp", "Sum"]


class IdentityOp(pyca.OrthProjOp):
    def __init__(self, dim: pyct.Integer):
        super().__init__(shape=(dim, dim))

    @pycrt.enforce_precision(i="arr")
    def apply(self, arr: pyct.NDArray) -> pyct.NDArray:
        return pycu.read_only(arr)

    @pycrt.enforce_precision(i="arr")
    def adjoint(self, arr: pyct.NDArray) -> pyct.NDArray:
        return pycu.read_only(arr)

    def svdvals(self, **kwargs) -> pyct.NDArray:
        return pyca.UnitOp.svdvals(self, **kwargs)

    def eigvals(self, **kwargs) -> pyct.NDArray:
        return pyca.UnitOp.svdvals(self, **kwargs)

    def asarray(self, **kwargs) -> pyct.NDArray:
        dtype = kwargs.pop("dtype", pycrt.getPrecision().value)
        xp = kwargs.pop("xp", pycd.NDArrayInfo.NUMPY.module())
        A = xp.eye(N=self.dim, dtype=dtype)
        return A

    @pycrt.enforce_precision(i="arr")
    def pinv(self, arr: pyct.NDArray, **kwargs) -> pyct.NDArray:
        out = arr.copy()
        out /= 1 + kwargs.pop("damp", 0)
        return out

    def dagger(self, **kwargs) -> pyct.OpT:
        cst = 1 / (1 + kwargs.pop("damp", 0))
        op = HomothetyOp(cst=cst, dim=self.dim)
        return op

    def trace(self, **kwargs) -> pyct.Real:
        return float(self.dim)


class NullOp(pyca.LinOp):
    """
    Null operator.

    This operator maps any input vector on the null vector.
    """

    def __init__(self, shape: pyct.OpShape):
        super().__init__(shape=shape)
        self._lipschitz = 0

    @pycrt.enforce_precision(i="arr")
    def apply(self, arr: pyct.NDArray) -> pyct.NDArray:
        xp = pycu.get_array_module(arr)
        return xp.broadcast_to(
            xp.array(0, arr.dtype),
            (*arr.shape[:-1], self.codim),
        )

    @pycrt.enforce_precision(i="arr")
    def adjoint(self, arr: pyct.NDArray) -> pyct.NDArray:
        xp = pycu.get_array_module(arr)
        return xp.broadcast_to(
            xp.array(0, arr.dtype),
            (*arr.shape[:-1], self.dim),
        )

    def svdvals(self, **kwargs) -> pyct.NDArray:
        N = pycd.NDArrayInfo
        xp = {True: N.CUPY, False: N.NUMPY}[kwargs.pop("gpu", False)].module()
        D = xp.zeros(kwargs.pop("k"), dtype=pycrt.getPrecision().value)
        return D

    def gram(self) -> pyct.OpT:
        op = NullOp(shape=(self.dim, self.dim))
        return op.asop(pyca.SelfAdjointOp).squeeze()

    def cogram(self) -> pyct.OpT:
        op = NullOp(shape=(self.codim, self.codim))
        return op.asop(pyca.SelfAdjointOp).squeeze()

    def asarray(self, **kwargs) -> pyct.NDArray:
        dtype = kwargs.pop("dtype", pycrt.getPrecision().value)
        xp = kwargs.pop("xp", pycd.NDArrayInfo.NUMPY.module())
        A = xp.zeros(self.shape, dtype=dtype)
        return A

    def trace(self, **kwargs) -> pyct.Real:
        return float(0)


def NullFunc(dim: pyct.Integer) -> pyct.OpT:
    """
    Null functional.

    This functional maps any input vector on the null scalar.
    """
    op = NullOp(shape=(1, dim)).squeeze()
    op._name = "NullFunc"
    return op


def HomothetyOp(cst: pyct.Real, dim: pyct.Integer) -> pyct.OpT:
    """
    Scaling operator.

    Parameters
    ----------
    cst: pyct.Real
        Scaling factor.
    dim: pyct.Integer
        Dimension of the domain.

    Returns
    -------
    op: pyct.OpT
        (dim, dim) scaling operator.

    Notes
    -----
    This operator is not defined in terms of :py:func:`~pycsou.operator.linop.DiagonalOp` since it
    is array-backend-agnostic.
    """
    assert isinstance(cst, pyct.Real), f"cst: expected real, got {cst}."

    if np.isclose(cst, 0):
        op = NullOp(shape=(dim, dim))
    elif np.isclose(cst, 1):
        op = IdentityOp(dim=dim)
    else:  # build PosDef or SelfAdjointOp

        @pycrt.enforce_precision(i="arr")
        def op_apply(_, arr: pyct.NDArray) -> pyct.NDArray:
            out = arr.copy()
            out *= _._cst
            return out

        def op_svdvals(_, **kwargs) -> pyct.NDArray:
            N = pycd.NDArrayInfo
            xp = {True: N.CUPY, False: N.NUMPY}[kwargs.pop("gpu", False)].module()
            D = xp.full(
                shape=kwargs.pop("k"),
                fill_value=abs(_._cst),
                dtype=pycrt.getPrecision().value,
            )
            return D

        def op_eigvals(_, **kwargs) -> pyct.NDArray:
            D = _.svdvals(**kwargs)
            D *= np.sign(_._cst)
            return D

        @pycrt.enforce_precision(i="arr")
        def op_pinv(_, arr: pyct.NDArray, **kwargs) -> pyct.NDArray:
            out = arr.copy()
            scale = _._cst / (_._cst**2 + kwargs.pop("damp", 0))
            out *= scale
            return out

        def op_dagger(_, **kwargs) -> pyct.OpT:
            scale = _._cst / (_._cst**2 + kwargs.pop("damp", 0))
            op = HomothetyOp(cst=scale, dim=_.dim)
            return op

        def op_gram(_):
            return HomothetyOp(cst=_._cst**2, dim=_.dim)

        def op_trace(_, **kwargs):
            out = _._cst * _.codim
            return float(out)

        klass = pyca.PosDefOp if (cst > 0) else pyca.SelfAdjointOp
        op = klass(shape=(dim, dim))
        op._cst = cst
        op._lipschitz = abs(cst)
        op.apply = types.MethodType(op_apply, op)
        op.svdvals = types.MethodType(op_svdvals, op)
        op.eigvals = types.MethodType(op_eigvals, op)
        op.pinv = types.MethodType(op_pinv, op)
        op.dagger = types.MethodType(op_dagger, op)
        op.gram = types.MethodType(op_gram, op)
        op.cogram = op.gram
        op.trace = types.MethodType(op_trace, op)
        op._name = "HomothetyOp"

    return op.squeeze()


def DiagonalOp(
    vec: pyct.NDArray,
    enable_warnings: bool = True,
) -> pyct.OpT:
    r"""
    Diagonal linear operator :math:`L: \mathbf{x} \to \text{diag}(\mathbf{v}) \mathbf{x}`.

    Notes
    -----
    :py:func:`~pycsou.operator.linop.base.DiagonalOp` instances are **not arraymodule-agnostic**:
    they will only work with NDArrays belonging to the same array module as ``vec``.
    Moreover, inner computations may cast input arrays when the precision of ``vec`` does not match
    the user-requested precision.
    If such a situation occurs, a warning is raised.

    Parameters
    ----------
    vec: pyct.NDArray
        (N,) diagonal scale factors.
    enable_warnings: bool
        If ``True``, emit a warning in case of precision mis-match issues.
    """
    assert len(vec) == np.prod(vec.shape), f"vec: {vec.shape} is not a DiagonalOp generator."
    if (dim := vec.size) == 1:  # Module-agnostic
        return HomothetyOp(cst=float(vec), dim=1)
    else:
        xp = pycu.get_array_module(vec)
        if pycu.compute(xp.allclose(vec, 0)):
            op = NullOp(shape=(dim, dim))
        elif pycu.compute(xp.allclose(vec, 1)):
            op = IdentityOp(dim=dim)
        else:  # build PosDef or SelfAdjointOp

            @pycrt.enforce_precision(i="arr")
            def op_apply(_, arr):
                if (_._vec.dtype != arr.dtype) and _._enable_warnings:
                    msg = "Computation may not be performed at the requested precision."
                    warnings.warn(msg, pycuw.PrecisionWarning)
                out = arr.copy()
                out *= _._vec
                return out

            def op_asarray(_, **kwargs) -> pyct.NDArray:
                N = pycd.NDArrayInfo
                dtype = kwargs.pop("dtype", pycrt.getPrecision().value)
                xp = kwargs.pop("xp", pycd.NDArrayInfo.NUMPY.module())

                v = pycu.compute(_._vec.astype(dtype=dtype, copy=False))
                v = pycu.to_NUMPY(v)
                A = xp.diag(v)
                return A

            def op_gram(_):
                return DiagonalOp(
                    vec=_._vec**2,
                    enable_warnings=_._enable_warnings,
                )

            def op_svdvals(_, **kwargs):
                k = kwargs.pop("k")
                which = kwargs.pop("which", "LM")
                N = pycd.NDArrayInfo
                xp = {True: N.CUPY, False: N.NUMPY}[kwargs.pop("gpu", False)].module()
                D = xp.abs(pycu.compute(_._vec))
                D = D[xp.argsort(D)]
                D = D.astype(pycrt.getPrecision().value, copy=False)
                return D[:k] if (which == "SM") else D[-k:]

            def op_eigvals(_, **kwargs):
                k = kwargs.pop("k")
                which = kwargs.pop("which", "LM")
                N = pycd.NDArrayInfo
                xp = {True: N.CUPY, False: N.NUMPY}[kwargs.pop("gpu", False)].module()
                D = pycu.compute(_._vec)
                D = D[xp.argsort(xp.abs(D))]
                D = D.astype(pycrt.getPrecision().value, copy=False)
                return D[:k] if (which == "SM") else D[-k:]

            @pycrt.enforce_precision(i="arr")
            def op_pinv(_, arr: pyct.NDArray, **kwargs) -> pyct.NDArray:
                damp = kwargs.pop("damp", 0)
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    scale = _._vec / (_._vec**2 + damp)
                    scale[xp.isnan(scale)] = 0
                out = arr.copy()
                out *= scale
                return out

            def op_dagger(_, **kwargs) -> pyct.OpT:
                damp = kwargs.pop("damp", 0)
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    scale = _._vec / (_._vec**2 + damp)
                    scale[xp.isnan(scale)] = 0
                return DiagonalOp(
                    vec=scale,
                    enable_warnings=_._enable_warnings,
                )

            def op_trace(_, **kwargs):
                return float(_._vec.sum())

            klass = pyca.PosDefOp if pycu.compute(xp.all(vec > 0)) else pyca.SelfAdjointOp
            op = klass(shape=(dim, dim))
            op._vec = vec
            op._enable_warnings = bool(enable_warnings)
            op._lipschitz = pycu.compute(xp.abs(vec).max())
            op.apply = types.MethodType(op_apply, op)
            op.asarray = types.MethodType(op_asarray, op)
            op.gram = types.MethodType(op_gram, op)
            op.cogram = op.gram
            op.svdvals = types.MethodType(op_svdvals, op)
            op.eigvals = types.MethodType(op_eigvals, op)
            op.pinv = types.MethodType(op_pinv, op)
            op.dagger = types.MethodType(op_dagger, op)
            op.trace = types.MethodType(op_trace, op)
            op._name = "DiagonalOp"

        return op.squeeze()


def _ExplicitLinOp(
    cls: pyct.OpC,
    mat: typ.Union[pyct.NDArray, pyct.SparseArray],
    enable_warnings: bool = True,
) -> pyct.OpT:
    r"""
    Build a linear operator from its matrix representation.

    Given a matrix :math:`\mathbf{A}\in\mathbb{R}^{M\times N}`, the *explicit linear operator*
    associated to :math:`\mathbf{A}` is defined as

    .. math::

       f_\mathbf{A}(\mathbf{x})
       =
       \mathbf{A}\mathbf{x},
       \qquad
       \forall \mathbf{x}\in\mathbb{R}^N,

    with adjoint given by:

    .. math::

       f^\ast_\mathbf{A}(\mathbf{z})
       =
       \mathbf{A}^T\mathbf{z},
       \qquad
       \forall \mathbf{z}\in\mathbb{R}^M.

    Parameters
    ----------
    cls: pyct.OpC
        LinOp sub-class to instantiate.
    mat: pyct.NDArray | pyct.SparseArray
        (M, N) matrix generator.
        The input array can be *dense* or *sparse*.
        Accepted sparse arrays are:

        * CPU: COO/CSC/CSR/BSR/GCXS
        * GPU: COO/CSC/CSR
    enable_warnings: bool
        If ``True``, emit a warning in case of precision mis-match issues.

    Notes
    -----
    * :py:class:`~pycsou.operator.linop.base._ExplicitLinOp` instances are **not
      arraymodule-agnostic**: they will only work with NDArrays belonging to the same (dense) array
      module as ``mat``.
      Moreover, inner computations may cast input arrays when the precision of ``mat`` does not
      match the user-requested precision.
      If such a situation occurs, a warning is raised.

    * The matrix provided in ``__init__()`` is used as-is and can be accessed via ``.mat``.
    """

    def _standard_form(A):
        fail_dense = False
        try:
            pycd.NDArrayInfo.from_obj(A)
        except:
            fail_dense = True

        fail_sparse = False
        try:
            pycd.SparseArrayInfo.from_obj(A)
        except:
            fail_sparse = True

        if fail_dense and fail_sparse:
            raise ValueError("mat: format could not be inferred.")
        else:
            return A

    def _matmat(A, b, warn: bool = True) -> pyct.NDArray:
        # A: (M, N) dense/sparse
        # b: (..., N) dense
        # out: (..., M) dense
        if (A.dtype != b.dtype) and warn:
            msg = "Computation may not be performed at the requested precision."
            warnings.warn(msg, pycuw.PrecisionWarning)

        M, N = A.shape
        sh_out = (*b.shape[:-1], M)
        b = b.reshape((-1, N)).T  # (N, (...).prod)
        out = A.dot(b)  # (M, (...).prod)
        return out.T.reshape(sh_out)

    @pycrt.enforce_precision(i="arr")
    def op_apply(_, arr: pyct.NDArray) -> pyct.NDArray:
        return _matmat(_.mat, arr, warn=_._enable_warnings)

    @pycrt.enforce_precision(i="arr")
    def op_adjoint(_, arr: pyct.NDArray) -> pyct.NDArray:
        return _matmat(_.mat.T, arr, warn=_._enable_warnings)

    def op_asarray(_, **kwargs) -> pyct.NDArray:
        N = pycd.NDArrayInfo
        S = pycd.SparseArrayInfo
        dtype = kwargs.pop("dtype", pycrt.getPrecision().value)
        xp = kwargs.pop("xp", pycd.NDArrayInfo.NUMPY.module())

        try:  # Sparse arrays
            info = S.from_obj(_.mat)
            if info in (S.SCIPY_SPARSE, S.CUPY_SPARSE):
                f = lambda _: _.toarray()
            elif info == S.PYDATA_SPARSE:
                f = lambda _: _.todense()
            A = f(_.mat.astype(dtype))  # `copy` field not ubiquitous
        except:  # Dense arrays
            info = N.from_obj(_.mat)
            A = pycu.compute(_.mat.astype(dtype, copy=False))
        finally:
            A = pycu.to_NUMPY(A)

        return xp.array(A, dtype=dtype)

    def op_trace(_, **kwargs) -> pyct.Real:
        if _.dim != _.codim:
            raise NotImplementedError
        else:
            try:
                tr = _.mat.trace()
            except:
                # .trace() missing for [PYDATA,CUPY]_SPARSE API.
                S = pycd.SparseArrayInfo
                info = S.from_obj(_.mat)
                if info == S.PYDATA_SPARSE:
                    # use `sparse.diagonal().sum()`, but array must be COO.
                    try:
                        A = _.mat.tocoo()  # GCXS inputs
                    except:
                        A = _.mat  # COO inputs
                    finally:
                        tr = info.module().diagonal(A).sum()
                elif info == S.CUPY_SPARSE:
                    tr = _.mat.diagonal().sum()
                else:
                    raise ValueError(f"Unknown sparse format {_.mat}.")
            return float(tr)

    def op_lipschitz(_, **kwargs) -> pyct.Real:
        # We want to piggy-back onto Lin[Op,Func].lipschitz() to compute the Lipschitz constant L.
        # Problem: LinOp.lipschitz() relies on svdvals() or hutchpp() to compute L, and they take
        # different parameters to do computations on the GPU.
        # Solution:
        # * we add the relevant kwargs before calling the LinOp.lipschitz() + drop all unrecognized
        #   kwargs there as needed.
        # * similarly for LinFunc.lipschitz().
        N = pycd.NDArrayInfo
        S = pycd.SparseArrayInfo

        try:  # Dense arrays
            info = N.from_obj(_.mat)
            kwargs.update(
                xp=info.module(),
                gpu=info == N.CUPY,
            )
        except:  # Sparse arrays
            info = S.from_obj(_.mat)
            gpu = info == S.CUPY_SPARSE
            kwargs.update(
                xp=N.CUPY.module() if gpu else N.NUMPY.module(),
                gpu=gpu,
            )

        if _.codim == 1:
            L = pyca.LinFunc.lipschitz(_, **kwargs)
        else:
            L = _.__class__.lipschitz(_, **kwargs)
        return L

    op = cls(shape=mat.shape)
    op.mat = _standard_form(mat)
    op._enable_warnings = bool(enable_warnings)
    op.apply = types.MethodType(op_apply, op)
    op.adjoint = types.MethodType(op_adjoint, op)
    op.asarray = types.MethodType(op_asarray, op)
    op.lipschitz = types.MethodType(op_lipschitz, op)
    op.trace = types.MethodType(op_trace, op)
    op._name = "_ExplicitLinOp"
    return op


def Sum(arg_shape, axis=None) -> pyct.OpT:
    """
    Summation Operator.

    This operator re-arranges the input array to a multidimensional array of shape ``arg_shape`` and then reduces the
    array via summation across one or more ``axis``.

    If the input array :math:`\mathbf{x}` consists on a 3D array, and ``axis=-1``:

    .. math::
        \mathbf{y}_{i,j} = \sum_{k}{\mathbf{x}_{i,j,k}}

    **Adjoint**
    The adjoint of the sum introduces new dimensions via spreading along the specified ``axes``:
    If the input array :math:`\mathbf{x}` consists on a 2D array, and ``axis=-1``:

    .. math::
        \mathbf{y}_{i,j,k} = \mathbf{x}_{i,j}

    Parameters
    ----------
    arg_shape: pyct.NDArrayShape
        Shape of the data to be reduced.
    axis: int, tuple
        Axis or axes along which a sum is performed. The default, axis=None, will sum all the elements of the input
        array. If axis is negative it counts from the last to the first axis.

    Notes
    -----

    The Lipschitz constant is defined via the following Cauchy-Schwartz inequality (using a vectorized view the input
    array):

    .. math::
        \Vert s(\mathbf{x}) \Vert^{2}_{2} = \Vert \sum_{i}^{N} \mathbf{x}_{i} \Vert^{2}_{2} = (\sum_{i}^{N} \mathbf{x}_{i}) ^{2} \leq N \sum_{i}^{N} \mathbf{x}_{i}^{2},

    which suggest an upper bound of the Lipschitz constant of :math:`\sqrt{N}`, where :math:`N` is the total number of
    elements reduced by the summation (all elements in this example).
    """

    if axis is None:
        axis = np.arange(len(arg_shape))
    elif not isinstance(axis, (list, tuple)):
        axis = [
            axis,
        ]
    elif isinstance(axis, tuple):
        axis = list(axis)
    for i in range(len(axis)):
        axis[i] = len(arg_shape) - 1 if axis[i] == -1 else axis[i]

    arg_shape, axis = np.array(arg_shape), np.array(axis)
    adjoint_shape = [d for i, d in enumerate(arg_shape) if i not in axis]

    dim = int(np.prod(arg_shape).item())
    codim = int((np.prod(arg_shape) / np.prod(arg_shape[axis])).item())

    # Create array of ones with arg_shape dims for adjoint
    tile = np.ones(len(arg_shape) + 1, dtype=int)
    tile[axis + 1] = arg_shape[axis]

    @pycrt.enforce_precision(i="arr")
    def op_apply(_, arr: pyct.NDArray) -> pyct.NDArray:
        return arr.reshape(-1, *arg_shape).sum(axis=tuple(axis + 1)).reshape(arr.shape[:-1] + (codim,))

    @pycrt.enforce_precision(i="arr")
    def op_adjoint(_, arr: pyct.NDArray) -> pyct.NDArray:
        xp = pycu.get_array_module(arr)
        out = xp.expand_dims(arr.reshape(-1, *adjoint_shape), tuple(axis + 1))
        out = xp.tile(out, tile).reshape(arr.shape[:-1] + (dim,))
        return out

    klass = pyca.LinOp if codim != 1 else pyca.LinFunc
    op = klass(shape=(codim, dim))

    op._lipschitz = np.sqrt(np.prod(arg_shape[axis]))
    op.apply = types.MethodType(op_apply, op)
    op.adjoint = types.MethodType(op_adjoint, op)
    op._name = "Sum"

    return op


class _StencilOp(pyca.SquareOp):
    r"""
    Base class for NDArray computing functions that operate only on a local region of the NDArray through a
    multidimensional kernel, namely through correlation and convolution.

    This class leverages the :py:func:`numba.stencil` decorator, which allows to JIT (Just-In-Time) compile these
    functions to run more quickly.

    Parameters
    ----------
    stencil_coefs: NDArray
        Stencil coefficients. Must have the same number of dimensions as the input array's arg_shape (i.e., without the
        stacking dimension).
    center: NDArray
        Index of the kernel's center. Must be a 1-dimensional array with one element per dimension in ``stencil_coefs``.
    arg_shape: tuple
        Shape of the input array.
    enable_warnings: bool
        If ``True``, emit a warning in case of precision mismatch issues.


    Examples
    --------

    The following example creates a Stencil operator based on a 2-dimensional kernel. It shows how to perform correlation
    and convolution in CPU, GPU (Cupy) and distributed across different cores (Dask).

    .. code-block:: python3

       from pycsou.operator.linop.base import StencilOp
       import numpy as np
       import cupy as cp
       import dask.array as da

       nsamples = 2
       data_shape = (500, 100)
       da_blocks = (50, 10)

       # Numpy
       data = np.ones((nsamples, *data_shape)).reshape(nsamples, -1)
       # Cupy
       data_cu = cp.ones((nsamples, *data_shape)).reshape(nsamples, -1)
       # Dask
       data_da = da.from_array(data, chunks=da_blocks).reshape(nsamples, -1)

       kernel = np.array([[0.5, 0.0, 0.5],
                          [0.0, 0.0, 0.0],
                          [0.5, 0.0, 0.5]])

       center = np.array([1, 0])

       stencil = StencilOp(stencil_coefs=kernel, center=center, arg_shape=data_shape, boundary=0.)
       stencil_cu = StencilOp(stencil_coefs=cp.asarray(kernel), center=center, arg_shape=data_shape, boundary=0.)

       # Correlate images with kernels
       out = stencil(data).reshape(nsamples, *data_shape)
       out_da = stencil(data_da).reshape(nsamples, *data_shape).compute()
       out_cu = stencil_cu(data_cu).reshape(nsamples, *data_shape).get()

       # Convolve images with kernels
       out_adj = stencil.adjoint(data).reshape(nsamples, *data_shape)
       out_da_adj = stencil.adjoint(data_da).reshape(nsamples, *data_shape).compute()
       out_cu_adj = stencil_cu.adjoint(data_cu).reshape(nsamples, *data_shape).get()

    Notes
    -----

    Note that to perform stencil operations on GPU NDArrays, the stencil has to be instantiated with GPU kernel
    coefficients.

    - **Remark 1**. The :py:class:`~pycsou.operator.linop.base.StencilOp` class allows to perform both correlation and convolution. By
    default, the ``apply`` method will perform **correlation** of the input array with the given kernel / stencil,
    whereas the ``adjoint`` method will perform **convolution**.

    - **Remark 2**. When instantiated with a multidimensional kernel, the :py:class:`~pycsou.operator.linop.base.StencilOp` performs
    convolution and correlation operations as non-separable filters. When possible, the user can decide whether to
    separate the filtering operation by composing different stencils for different axis to accelerate performance. This
    approach is not guaranteed to improve performance due to the repeated copying of arrays associated to internal
    padding operations.

    - **Remark 3**. The stencil computation is only performed on those parts of the input array in which the kernel
    finds support. Padding is recommended to guarantee that the stencil computation is performed on all the input
    elements (see :py:class:~pycsou.operator.linop.PadOp`.)

    - **Remark 4*. By default, for GPU computing, the ``threadsperblock`` argument is set according to the following criteria:

        - Number of the  GPU's threads per block (:math:`c`), i.e.,:
           .. math::

               \prod_{i=0}^{D-1} t_{i} \leq c

           where :math:`t_{i}` is the number of threads per block in dimension :math:`i`, :math:`D` is the number of dimensions
           of the kernel.

        - Maximum number of contiguous threads as possible:

           Because arrays are stored in row-major order, a larger number of threads per block in the last axis of the Cupy
           array benefits the spatial locality in memory caching. For this reason ``threadsperblock`` is set to the maximum
           number in the last axis, and to the minimum possible (respecting the kernel shape) in the other axes.

           .. math::

               t_{i} = 2^{j} \leq k_{i}, s.t., 2^{j+1} > k_{i} \quad \textrm{for} \quad i\in[0, \dots, D-2],

    .. warning::

       The adjoint method is not correct unless padding of the input array is performed. This is a private class and not
       meant for using without composition with the PadOp.

       Due to code compilation the stencil methods assume arrays are in row-major or C order. If the input array is in
       Fortran or F order, a copy in C order is created automatically, which can lead to increased time and memory
       usage.
    """

    def __init__(
        self,
        stencil_coefs: pyct.NDArray,
        center: pyct.NDArray,
        arg_shape: pyct.OpShape,
        enable_warnings: bool = True,
    ):
        size = np.prod(arg_shape).item()

        super().__init__((size, size))

        self.arg_shape = arg_shape
        self.ndim = len(arg_shape)
        self._sanitize_inputs(stencil_coefs, center)
        self._make_stencils(self.stencil_coefs)
        self._lipschitz = 2 * abs(self.stencil_coefs).max()
        self._enable_warnings = bool(enable_warnings)

    @pycrt.enforce_precision(i="arr", o=True)
    def apply(self, arr: pyct.NDArray) -> pyct.NDArray:
        r"""
        Parameters
        ----------
        arr: NDArray
            Array to be correlated with the kernel.

        Returns
        -------
        out: NDArray
            NDArray with same shape as the input NDArray, correlated with kernel.
        """
        if (arr.dtype != self.stencil_coefs.dtype) and self._enable_warnings:
            msg = "Computation may not be performed at the requested precision."
            warnings.warn(msg, pycuw.PrecisionWarning)

        return self._apply_dispatch(arr)

    @pycrt.enforce_precision(i="arr", o=True)
    def adjoint(self, arr: pyct.NDArray) -> pyct.NDArray:
        r"""
        Parameters
        ----------
        arr: NDArray
            Array to be convolved with the kernel.

        Returns
        -------
        out: NDArray
            NDArray with same shape as the input NDArray, convolved with kernel.
        """
        if (arr.dtype != self.stencil_coefs.dtype) and self._enable_warnings:
            msg = "Computation may not be performed at the requested precision."
            warnings.warn(msg, pycuw.PrecisionWarning)

        return self._adjoint_dispatch(arr)

    def asarray(self, **kwargs) -> pyct.NDArray:
        r"""
        Make a matrix representation of the stencil operator.
        """
        dtype = kwargs.pop("dtype", pycrt.getPrecision().value)
        xp = kwargs.pop("xp", pycd.NDArrayInfo.NUMPY.module())
        dtype_ = self.stencil_coefs.dtype
        xp_ = pycu.get_array_module(self.stencil_coefs)

        E = xp_.eye(self.dim, dtype=dtype_)
        A = self.apply(E).T
        A = pycu.to_NUMPY(A) if xp_.__name__ == "cupy" and xp.__name__ != "cupy" else A
        return xp.array(A, dtype=dtype)

    def lipschitz(self, **kwargs) -> pyct.Real:
        r"""
        Compute a Lipschitz constant of the stencil operator.
        """
        N = pycd.NDArrayInfo
        info = N.from_obj(self.stencil_coefs)
        kwargs.update(
            xp=info.module(),
            gpu=info == N.CUPY,
        )
        return super().lipschitz(**kwargs)

    def _apply(self, arr: pyct.NDArray) -> pyct.NDArray:
        return self.stencil(arr.reshape(-1, *self.arg_shape)).reshape(*arr.shape)

    def _apply_dask(self, arr: pyct.NDArray) -> pyct.NDArray:
        return (
            arr.reshape(-1, *self.arg_shape)
            .map_overlap(self.stencil, depth=self.width, dtype=arr.dtype)
            .reshape(arr.shape)
        )

    def _apply_cupy(self, arr: pyct.NDArray) -> pyct.NDArray:
        xp = pycu.get_array_module(arr)
        arr_shape = arr.shape
        arr = arr.reshape(-1, *self.arg_shape)
        out = xp.zeros_like(arr)
        # Cuda grid cannot have more than 3D. In the case of arg_shape with 3D, the cuda grid loops across the 3D and
        # looping over stacking dimension is done within the following Python list comprehension.
        tbp, bpg = self._get_gpu_config(arr)
        self.stencil[bpg, tbp](arr, out) if len(self.arg_shape) < 3 else [
            self.stencil[bpg, tbp](arr[i], out[i]) for i in range(arr.shape[0])
        ]
        return out.reshape(arr_shape)

    def _adjoint(self, arr: pyct.NDArray) -> pyct.NDArray:
        return self.stencil_adjoint(arr.reshape(-1, *self.arg_shape)).reshape(arr.shape)

    def _adjoint_dask(self, arr: pyct.NDArray) -> pyct.NDArray:
        return (
            arr.reshape(-1, *self.arg_shape)
            .map_overlap(
                self.stencil_adjoint,
                depth=self.width,
                dtype=arr.dtype,
            )
            .reshape(arr.shape)
        )

    def _adjoint_cupy(self, arr: pyct.NDArray) -> pyct.NDArray:
        xp = pycu.get_array_module(arr)
        arr_shape = arr.shape
        arr = arr.reshape(-1, *self.arg_shape)
        out = xp.zeros_like(arr)
        tbp, bpg = self._get_gpu_config(arr)
        self.stencil_adjoint[bpg, tbp](arr, out) if len(self.arg_shape) < 3 else [
            self.stencil_adjoint[bpg, tbp](arr[i], out[i]) for i in range(arr.shape[0])
        ]
        return out.reshape(arr_shape)

    @pycu.redirect("arr", DASK=_apply_dask, CUPY=_apply_cupy)
    def _apply_dispatch(self, arr: pyct.NDArray) -> pyct.NDArray:
        return self._apply(arr)

    @pycu.redirect("arr", DASK=_adjoint_dask, CUPY=_adjoint_cupy)
    def _adjoint_dispatch(self, arr: pyct.NDArray) -> pyct.NDArray:
        return self._adjoint(arr)

    def _make_stencils_cpu(self, stencil_coefs: pyct.NDArray, **kwargs) -> None:
        # Create numba JIT-ted stencil functions for apply and adjoint methods.
        self.stencil = pycstencil.make_nd_stencil(coefficients=self.stencil_coefs, center=self.center)
        self.stencil_adjoint = pycstencil.make_nd_stencil(
            coefficients=self.stencil_coefs_adjoint, center=self.center_adjoint
        )

    def _make_stencils_gpu(self, stencil_coefs: pyct.NDArray, **kwargs) -> None:
        # Create numba.cuda JIT-ted functions for apply and adjoint methods.
        self.stencil = pycstencil.make_nd_stencil_gpu(
            coefficients=self.stencil_coefs, center=self.center, func_name="apply"
        )
        self.stencil_adjoint = pycstencil.make_nd_stencil_gpu(
            coefficients=self.stencil_coefs_adjoint, center=self.center_adjoint, func_name="adjoint"
        )

    @pycu.redirect("stencil_coefs", CUPY=_make_stencils_gpu)
    def _make_stencils(self, stencil_coefs: pyct.NDArray) -> None:
        self._make_stencils_cpu(stencil_coefs)

    def _sanitize_inputs(self, stencil_coefs: pyct.NDArray, center: pyct.NDArray):
        # Check that inputs have the correct shape and correctly handle the boundary conditions.
        assert len(center) == stencil_coefs.ndim == self.ndim, (
            "The stencil coefficients should have the same"
            " number of dimensions as `arg_shape` and the "
            "same length as `center`."
        )
        self.xp = xp = pycu.get_array_module(stencil_coefs)
        self.stencil_coefs = stencil_coefs
        self.center = self.center_dask = np.atleast_1d(center)
        self.stencil_coefs_adjoint = xp.flip(stencil_coefs)
        self.center_adjoint = np.array(stencil_coefs.shape) - 1 - np.atleast_1d(center)
        self.width = self._set_width(stencil_coefs.ndim)

    def _set_width(self, ndim):
        # set appropriate padding depth for different backends
        depth_right = np.atleast_1d(self.stencil_coefs.shape) - self.center - 1
        return tuple([(0, 0)] + [(self.center[i].item(), depth_right[i].item()) for i in range(ndim)])

    def _get_gpu_config(self, arr):
        # Get max number of threads in device
        t_max = arr.device.attributes["MaxThreadsPerBlock"]
        # Set at least as many threads as kernel elements per dimension
        _next_power_of_2 = lambda x: 1 if x == 0 else 2 ** (x - 1).bit_length()
        kernel_shape = self.stencil_coefs.shape
        tpb = [int(_next_power_of_2(kernel_shape[d])) for d in range(len(kernel_shape))]
        # Set maximum number of threads in the row-major order
        tpb[-1] = int(t_max / (np.prod(tpb) / tpb[-1]))
        # If kernel has less than 3D, add stacking dimension
        if len(self.arg_shape) < 3:
            tpb = [1] + tpb
        # If nthreads larger than a given array dimension size, use threads in other dimensions
        # This maximizes locality of cached memory (row-major order) to improve performance
        for i in range(len(tpb) - 1, -1, -1):
            while tpb[i] > self.arg_shape[i - 1] + np.sum(self.width[i]):
                tpb[i] = int(tpb[i] / 2)
                if i > 0:
                    tpb[i - 1] = int(tpb[i - 1] * 2)

        threadsperblock = tuple(tpb)

        # Define blockspergrid based on input array shape and threadsperblock
        aux_stacking = 0 if len(self.arg_shape) < 3 else 1
        blockspergrid = tuple([math.ceil(arr.shape[i + aux_stacking] / tpb) for i, tpb in enumerate(threadsperblock)])
        return threadsperblock, blockspergrid
