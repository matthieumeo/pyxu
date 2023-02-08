import collections
import collections.abc as cabc
import itertools
import math
import types
import typing as typ

import numpy as np

import pycsou.operator.blocks as pycb
import pycsou.operator.linop.base as pyclb
import pycsou.operator.linop.pad as pyclp
import pycsou.operator.linop.reduce as pyclr
import pycsou.operator.linop.stencil as pycls
import pycsou.runtime as pycrt
import pycsou.util as pycu
import pycsou.util.deps as pycd
import pycsou.util.ptype as pyct

try:
    import scipy.ndimage._filters as scif
except ImportError:
    import scipy.ndimage.filters as scif

__all__ = [
    "PartialDerivative",
    "Gradient",
    "Jacobian",
    "Divergence",
    "Hessian",
    "Laplacian",
    "DirectionalDerivative",
    "DirectionalGradient",
    "DirectionalLaplacian",
    "DirectionalHessian",
]

ModeSpec = pyclp.Pad.ModeSpec
KernelSpec = pycls.Stencil.KernelSpec
PDMetaFD = collections.namedtuple("FiniteDifferenceMeta", "sampling scheme accuracy")
PDMetaGD = collections.namedtuple("GaussianDerivativeMeta", "sampling sigma truncate")


def _sanitize_init_kwargs(
    order: typ.Union[pyct.Integer, cabc.Sequence[pyct.Integer, ...]],
    arg_shape: pyct.NDArrayShape,
    sampling: typ.Union[pyct.Real, cabc.Sequence[pyct.Real, ...]],
    diff_method: str,
    diff_kwargs: dict,
    axes: pyct.NDArrayAxis = None,
) -> tuple[
    cabc.Sequence[pyct.Integer, ...],
    cabc.Sequence[pyct.Real, ...],
    typ.Union[cabc.Sequence[pyct.Real, ...], cabc.Sequence[str, ...]],
    cabc.Sequence[pyct.Integer, ...],
    pyct.NDArrayAxis,
]:
    r"""
    Ensures that inputs have the appropriate shape and values.
    """

    def _ensure_tuple(param, param_name: str) -> typ.Union[tuple[pyct.Integer, ...], tuple[str, ...]]:
        r"""
        Enforces the input parameters to be tuples of the same size as `arg_shape`.
        """
        if not isinstance(param, cabc.Sequence) or isinstance(param, str):
            param = (param,)
        assert (len(param) == 1) | (len(param) <= len(arg_shape)), (
            f"The length of {param_name} cannot be larger than the"
            f"number of dimensions ({len(arg_shape)}) defined by `arg_shape`"
        )
        return param

    order = _ensure_tuple(order, param_name="order")
    sampling = _ensure_tuple(sampling, param_name="sampling")
    if len(sampling) == 1:
        sampling = sampling * len(arg_shape)
    assert all([_ >= 0 for _ in order]), "Order must be positive"
    assert all([_ > 0 for _ in sampling]), "Sampling must be strictly positive"

    if diff_method == "fd":
        param1_name = "scheme"
        param2_name = "accuracy"
        param1 = diff_kwargs.get("scheme", "forward")
        param2 = diff_kwargs.get("accuracy", 1)
    elif diff_method == "gd":
        param1_name = "sigma"
        param2_name = "truncate"
        param1 = diff_kwargs.get("sigma", 1.0)
        param2 = diff_kwargs.get("truncate", 3.0)
    else:
        raise NotImplementedError

    _param1 = _ensure_tuple(param1, param_name=param1_name)
    _param2 = _ensure_tuple(param2, param_name=param2_name)

    if param1_name == "sigma":
        assert all([p >= 0 for p in _param1]), "Sigma must be strictly positive"
    if param2_name == "accuracy":
        assert all([p >= 0 for p in _param2]), "Accuracy must be positive"
    elif param2_name == "truncate":
        assert all([p > 0 for p in _param2]), "Truncate must be strictly positive"

    if len(order) != len(arg_shape):
        assert axes is not None, (
            "If `order` is not a tuple with size of arg_shape, then `axes` must be" " specified. Got `axes=None`"
        )
        axes = _ensure_tuple(axes, param_name="axes")
        assert len(axes) == len(order), "`axes` must have the same number of elements as `order`"
    else:
        if axes is not None:
            axes = _ensure_tuple(axes, param_name="axes")
            assert len(axes) == len(order), "`axes` must have the same number of elements as `order`"
        else:
            axes = tuple([i for i in range(len(arg_shape))])

    if not (len(_param1) == len(order)):
        assert len(_param1) == 1, (
            f"Parameter `{param1_name}` inconsistent with the number of elements in " "parameter `order`."
        )
        _param1 = _param1 * len(arg_shape)

    if not (len(_param2) == len(order)):
        assert len(_param2) == 1, (
            f"Parameter `{param2_name}` inconsistent with the number of elements in " "parameter `order`."
        )
        _param2 = _param2 * len(arg_shape)

    return (
        order,
        sampling,
        _param1,
        _param2,
        axes,
    )


def _create_kernel(
    arg_shape: pyct.NDArrayShape,
    axes: pyct.NDArrayAxis,
    _fill_coefs: typ.Callable,
) -> tuple[cabc.Sequence[pyct.NDArray], pyct.NDArray]:
    r"""
    Creates kernel for stencil.
    """
    stencil_ids = [np.array([0])] * len(arg_shape)
    stencil_coefs = [np.array([1.0])] * len(arg_shape)
    center = np.zeros(len(arg_shape), dtype=int)

    # Create finite difference coefficients for each dimension
    for i, ax in enumerate(axes):
        stencil_ids[ax], stencil_coefs[ax], center[ax] = _fill_coefs(i)

    return stencil_coefs, center


def _FiniteDifference(
    order: typ.Union[pyct.Integer, cabc.Sequence[pyct.Integer, ...]],
    arg_shape: pyct.NDArrayShape,
    scheme: typ.Union[str, cabc.Sequence[str, ...]] = "forward",
    axes: pyct.NDArrayAxis = None,
    accuracy: typ.Union[pyct.Integer, cabc.Sequence[pyct.Integer, ...]] = 1,
    sampling: typ.Union[pyct.Real, cabc.Sequence[pyct.Real, ...]] = 1,
) -> tuple[cabc.Sequence[pyct.NDArray], pyct.NDArray]:
    r"""
    Finite difference base operator along a single dimension.

    This class is used by :py:class:`~pycsou.operator.linop.diff.PartialDerivative`,
    :py:class:`~pycsou.operator.linop.diff.Gradient` and :py:class:`~pycsou.operator.linop.diff.Hessian`.
    See :py:class:`~pycsou.operator.linop.diff.PartialDerivative.finite_difference` for documentation.

    See Also
    --------
    :py:class:`~pycsou.operator.linop.diff._GaussianDerivative`,
    :py:class:`~pycsou.operator.linop.diff.PartialDerivative`,
    :py:class:`~pycsou.operator.linop.diff.Gradient`,
    :py:class:`~pycsou.operator.linop.diff.Hessian`.

    Parameters
    ----------
    order: pyct.Integer | (pyct.Integer, ..., pyct.Integer)
        Derivative order. If a single integer value is provided, then `axes` should be provided to
        indicate which dimension should be differentiated.
        If a tuple is provided, it should contain as many elements as `arg_shape`.
    arg_shape: pyct.NDArrayShape
        Shape of the input array.
    scheme: str | (str, ..., str)
        Type of finite differences: ["forward", "backward", "central"].
        Defaults to "forward".
    axes: pyct.NDArrayAxis
        Axes to which apply the derivative.
        It maps the argument `order` to the specified dimensions of the input array.
        Defaults to None, assuming that the `order` argument has as many elements as dimensions of
        the input.
    accuracy: pyct.Integer | (pyct.Integer, ..., pyct.Integer)
        Determines the number of points used to approximate the derivative with finite differences
        (see `Notes`).
        Defaults to 1.
        If an int is provided, the same `accuracy` is assumed for all dimensions.
        If a tuple is provided, it should have as many elements as `arg_shape`.
    sampling: pyct.Real | (pyct.Real, ..., pyct.Real)
        Sampling step (i.e. distance between two consecutive elements of an array).
        Defaults to 1.
    """
    diff_kwargs = {"scheme": scheme, "accuracy": accuracy}
    order, sampling, scheme, accuracy, axes = _sanitize_init_kwargs(
        order=order,
        diff_method="fd",
        diff_kwargs=diff_kwargs,
        arg_shape=arg_shape,
        axes=axes,
        sampling=sampling,
    )

    def _compute_ids(order: pyct.Integer, scheme: str, accuracy: pyct.Integer) -> list:
        """
        Computes the Finite difference indices according to the order, type and accuracy.
        """
        if scheme == "central":
            n_coefs = 2 * ((order + 1) // 2) - 1 + accuracy
            ids = np.arange(-(n_coefs // 2), n_coefs // 2 + 1, dtype=int)
        else:
            n_coefs = order + accuracy
            if scheme == "forward":
                ids = np.arange(0, n_coefs, dtype=int)
            elif scheme == "backward":
                ids = np.arange(-n_coefs + 1, 1, dtype=int)
            else:
                raise ValueError(
                    f"Incorrect value for variable 'type'. 'type' should be ['forward', 'backward', "
                    f"'central'], but got {scheme}."
                )
        return ids.tolist()

    def _compute_coefficients(stencil_ids: cabc.Sequence, order: pyct.Integer, sampling: pyct.Real) -> pyct.NDArray:
        """
        Computes the finite difference coefficients based on the order and indices.
        """
        # vander doesn't allow precision specification
        stencil_mat = np.vander(
            np.array(stencil_ids),
            increasing=True,
        ).T.astype(pycrt.getPrecision().value)
        vec = np.zeros(len(stencil_ids), dtype=pycrt.getPrecision().value)
        vec[order] = math.factorial(order)
        coefs = np.linalg.solve(stencil_mat, vec)
        coefs /= sampling**order
        return coefs

    # FILL COEFFICIENTS
    def _fill_coefs(i: pyct.Integer) -> tuple[list[pyct.NDArray], pyct.Integer]:
        r"""
        Defines kernel elements.
        """
        stencil_ids = _compute_ids(order=order[i], scheme=scheme[i], accuracy=accuracy[i])
        stencil_coefs = _compute_coefficients(stencil_ids=stencil_ids, order=order[i], sampling=sampling[i])
        center = stencil_ids.index(0)
        return stencil_ids, stencil_coefs, center

    kernel, center = _create_kernel(arg_shape, axes, _fill_coefs)
    return kernel, center


def _GaussianDerivative(
    order: typ.Union[pyct.Integer, cabc.Sequence[pyct.Integer, ...]],
    arg_shape: pyct.NDArrayShape,
    sigma: typ.Union[pyct.Real, cabc.Sequence[pyct.Real, ...]],
    axes: pyct.NDArrayAxis = None,
    truncate: typ.Union[pyct.Real, cabc.Sequence[pyct.Real, ...]] = 3.0,
    sampling: typ.Union[pyct.Real, cabc.Sequence[pyct.Real, ...]] = 1,
):
    r"""
    Gaussian derivative base operator along a single dimension.

    This class is used by :py:class:`~pycsou.operator.linop.diff.PartialDerivative`,
    :py:class:`~pycsou.operator.linop.diff.Gradient` and :py:class:`~pycsou.operator.linop.diff.Hessian`.
    See :py:class:`~pycsou.operator.linop.diff.PartialDerivative.gaussian_derivative` for documentation.

    See Also
    --------
    :py:class:`~pycsou.operator.linop.diff._BaseDifferential`,
    :py:class:`~pycsou.operator.linop.diff._FiniteDifference`,
    :py:class:`~pycsou.operator.linop.diff.PartialDerivative`,
    :py:class:`~pycsou.operator.linop.diff.Gradient`,
    :py:class:`~pycsou.operator.linop.diff.Hessian`.

    Parameters
    ----------
    order: pyct.Integer | (pyct.Integer, ..., pyct.Integer)
        Derivative order.
        If a single integer value is provided, then `axes` should be provided to indicate which
        dimension should be used for differentiation.
        If a tuple is provided, it should contain as many elements as number of dimensions in
        `axes`.
    arg_shape: pyct.NDArrayShape
        Shape of the input array.
    sigma: pyct.Real | (pyct.Real, ..., pyct.Real)
        Standard deviation of the Gaussian kernel.
    axes: pyct.NDArrayAxis
        Axes to which apply the derivative.
        It maps the argument `order` to the specified dimensions of the input array.
        Defaults to None, assuming that the `order` argument has as many elements as dimensions of
        the input.
    truncate: pyct.Real | (pyct.Real, ..., pyct.Real)
        Truncate the filter at this many standard deviations (at each side from the origin).
        Defaults to 3.0.
    sampling: pyct.Real | (pyct.Real, ..., pyct.Real)
        Sampling step (i.e., the distance between two consecutive elements of an array).
        Defaults to 1.
    """
    diff_kwargs = {"sigma": sigma, "truncate": truncate}
    order, sampling, sigma, truncate, axes = _sanitize_init_kwargs(
        order=order,
        diff_method="gd",
        diff_kwargs=diff_kwargs,
        arg_shape=arg_shape,
        axes=axes,
        sampling=sampling,
    )

    def _fill_coefs(i: pyct.Integer) -> tuple[cabc.Sequence[pyct.Integer], pyct.NDArray, pyct.Integer]:
        r"""
        Defines kernel elements.
        """
        # make the radius of the filter equal to `truncate` standard deviations
        sigma_pix = sigma[i] / sampling[i]  # Sigma rescaled to pixel units
        radius = int(truncate[i] * float(sigma_pix) + 0.5)
        stencil_coefs = _gaussian_kernel1d(sigma=sigma_pix, order=order[i], sampling=sampling[i], radius=radius)
        stencil_ids = [i for i in range(-radius, radius + 1)]
        return stencil_ids, stencil_coefs, radius

    def _gaussian_kernel1d(
        sigma: pyct.Real,
        order: pyct.Integer,
        sampling: pyct.Real,
        radius: pyct.Integer,
    ) -> pyct.NDArray:
        """
        Computes a 1-D Gaussian convolution kernel.
        Wraps scipy.ndimage.filters._gaussian_kernel1d
        It flips the output because the original kernel is meant for convolution instead of correlation.
        """
        coefs = np.flip(scif._gaussian_kernel1d(sigma, order, radius))
        coefs /= sampling**order
        return coefs

    kernel, center = _create_kernel(arg_shape, axes, _fill_coefs)
    return kernel, center


def _PartialDerivative(
    kernel: KernelSpec,
    center: KernelSpec,
    arg_shape: pyct.NDArrayShape,
    mode: ModeSpec = "constant",
    gpu: bool = False,
    dtype: typ.Optional[pyct.DType] = None,
    meta: typ.Optional[typ.NamedTuple] = None,
) -> pyct.OpT:
    r"""
    Helper base class for partial derivative operator based on Numba stencils (see
    https://numba.pydata.org/numba-doc/latest/user/stencil.html).

    See Also
    --------
    :py:class:`~pycsou.operator.linop.diff.PartialDerivative`,
    :py:class:`~pycsou.operator.linop.stencil.stencil.Stencil`,
    :py:func:`~pycsou.math.stencil.make_nd_stencil`,
    :py:class:`~pycsou.operator.linop.diff._FiniteDifference`,
    :py:class:`~pycsou.operator.linop.diff._GaussianDerivative`.

    Parameters
    ----------
    kernel: KernelSpec
        Stencil coefficients.
        Two forms are accepted:

            * NDArray of rank-:math:`D`: denotes a non-seperable stencil.
            * tuple[NDArray_1, ..., NDArray_D]: a sequence of 1D stencils such that is filtered by
              the stencil `kernel[d]` along the :math:`d`-th dimension.
    center: IndexSpec
        (i_1, ..., i_D) index of the stencil's center.

        `center` defines how a kernel is overlaid on inputs to produce outputs.

        .. math::

           y[i_{1},\ldots,i_{D}]
           =
           \sum_{q_{1},\ldots,q_{D}=0}^{k_{1},\ldots,k_{D}}
           x[i_{1} - c_{1} + q_{1},\ldots,i_{D} - c_{D} + q_{D}]
           \,\cdot\,
           k[q_{1},\ldots,q_{D}]
    arg_shape: pyct.NDArrayShape
        Shape of the input array.
    mode: str | list(str)
        Boundary conditions.
        Multiple forms are accepted:

        * str: unique mode shared amongst dimensions.
          Must be one of:

          * 'constant' (default): zero-padding
          * 'wrap'
          * 'reflect'
          * 'symmetric'
          * 'edge'
        * tuple[str, ...]: the `d`-th dimension uses ``mode[d]`` as boundary condition.

        (See :py:func:`numpy.pad` for details.)
    gpu: bool
        Input NDArray type (`True` for GPU, `False` for CPU). Defaults to `False`.
    dtype: pyct.DType
        Working precision of the linear operator.
    meta: typ.NamedTuple
        Partial derivative metadata describing:

        * Diff_type
        * Sampling
        * Scheme (for finite differences)
        * Accuracy (for finite differences)
        * Sigma (for Gaussian derivative)
        * Truncate (for Gaussian derivative)
    """
    if dtype is None:
        dtype = pycrt.getPrecision().value

    if gpu:
        assert pycd.CUPY_ENABLED
        import cupy as xp
    else:
        import numpy as xp

    if isinstance(kernel, cabc.MutableSequence):
        for i in range(len(kernel)):
            kernel[i] = xp.array(kernel[i], dtype=dtype)
    else:
        kernel = xp.array(kernel, dtype=dtype)

    op = pycls.Stencil(arg_shape=arg_shape, kernel=kernel, center=center, mode=mode)
    setattr(op, "meta", meta)

    return op


class PartialDerivative:
    r"""
    Partial derivative operator based on `Numba stencils
    <https://numba.pydata.org/numba-doc/latest/user/stencil.html>`_.

    Notes
    -----
    This operator approximates the partial derivative of a :math:`D`-dimensional signal
    :math:`\mathbf{f} \in \mathbb{R}^{N_0 \times \cdots \times N_{D-1}}`

    .. math::

       \frac{\partial^{n} \mathbf{f}}{\partial x_0^{n_0} \cdots \partial x_{D-1}^{n_{D-1}}} \in
       \mathbb{R}^{N_0 \times \cdots \times N_{D-1}}

    where :math:`\frac{\partial^{n_i}}{\partial x_i^{n_i}}` is the :math:`n_i`-th order partial
    derivative along dimension :math:`i` and :math:`n = \prod_{i=0}^{D-1} n_{i}` is the total
    derivative order.

    Partial derivatives can be implemented with `finite differences
    <https://en.wikipedia.org/wiki/Finite_difference>`_ via the
    :py:meth:`~pycsou.operator.linop.diff.PartialDerivative.finite_difference` constructor, or with
    the `Gaussian derivative <https://www.crisluengo.net/archives/22/>`_ via the
    :py:meth:`~pycsou.operator.linop.diff.PartialDerivative.gaussian_derivative` constructor.

    **Adjoint**

    When using the :py:meth:`~pycsou.operator.linop.diff.PartialDerivative.finite_difference`
    constructor, the adjoint of the resulting linear operator will vary depending on the type of
    finite differences:

    * For ``forward`` type, the adjoint corresponds to:

        :math:`(\frac{\partial^{\text{fwd}}}{\partial x})^{\ast} = -\frac{\partial^{\text{bwd}}}{\partial x}`

    * For ``backward`` type, the adjoint corresponds to:

        :math:`(\frac{\partial^{\text{bwd}}}{\partial x})^{\ast} = -\frac{\partial^{\text{fwd}}}{\partial x}`

    * For ``central`` type, and for the
      :py:meth:`~pycsou.operator.linop.diff.PartialDerivative.gaussian_derivative` constructor, the
      adjoint corresponds to:

        :math:`(\frac{\partial}{\partial x})^{\ast} = -\frac{\partial}{\partial x}`


    .. warning::

       When dealing with high-order partial derivatives, the stencils required to compute them can
       become large, resulting in computationally expensive evaluations.
       In such scenarios, it can be more efficient to construct the partial derivative through a
       composition of lower-order partial derivatives.

    See Also
    --------
    :py:class:`~pycsou.operator.linop.diff.Gradient`,
    :py:class:`~pycsou.operator.linop.diff.Laplacian`,
    :py:class:`~pycsou.operator.linop.diff.Hessian`.
    """

    @staticmethod
    def finite_difference(
        order: cabc.Sequence[pyct.Integer, ...],
        arg_shape: pyct.NDArrayShape,
        scheme: typ.Union[str, cabc.Sequence[str, ...]] = "forward",
        accuracy: typ.Union[pyct.Integer, cabc.Sequence[pyct.Integer, ...]] = 1,
        mode: ModeSpec = "constant",
        gpu: bool = False,
        dtype: typ.Optional[pyct.DType] = None,
        sampling: typ.Union[pyct.Real, cabc.Sequence[pyct.Real, ...]] = 1,
    ) -> pyct.OpT:
        r"""
        Compute partial derivatives for multi-dimensional signals using finite differences.

        Parameters
        ----------
        arg_shape: pyct.NDArrayShape
            Shape of the input array.
        order: (pyct.Integer, ..., pyct.Integer)
            Derivative order for each dimension.
            The total order of the partial derivative is the sum of the elements of the tuple.
            Use zeros to indicate dimensions in which the derivative should not be computed.
        scheme: str | (str, ..., str)
            Type of finite differences: ['forward, 'backward, 'central'].
            Defaults to 'forward'.
            If a string is provided, the same `scheme` is assumed for all dimensions.
            If a tuple is provided, it should have as many elements as `order`.
        accuracy: pyct.Integer | (pyct.Integer, ..., pyct.Integer)
            Determines the number of points used to approximate the derivative with finite
            differences (see `Notes`).
            Defaults to 1.
            If an int is provided, the same `accuracy` is assumed for all dimensions.
            If a tuple is provided, it should have as many elements as `arg_shape`.
        mode: str | list(str)
            Boundary conditions.
            Multiple forms are accepted:

            * str: unique mode shared amongst dimensions.
              Must be one of:

              * 'constant' (default): zero-padding
              * 'wrap'
              * 'reflect'
              * 'symmetric'
              * 'edge'
            * tuple[str, ...]: the `d`-th dimension uses ``mode[d]`` as boundary condition.

            (See :py:func:`numpy.pad` for details.)
        gpu: bool
            Input NDArray type (`True` for GPU, `False` for CPU).
            Defaults to `False`.
        dtype: pyct.DType
            Working precision of the linear operator.
        sampling: pyct.Real | (pyct.Real, ..., pyct.Real)
            Sampling step (i.e. distance between two consecutive elements of an array).
            Defaults to 1.

        Returns
        -------
        op: :py:class:`~pycsou.abc.operator.LinOp`
            Partial derivative

        Notes
        -----

        We explain here finite differences for one-dimensional signals; this operator performs
        finite differences for multi-dimensional signals along dimensions specified by ``order``.

        This operator approximates derivatives with `finite differences
        <https://en.wikipedia.org/wiki/Finite_difference>`_.
        It is inspired by the `Finite Difference Coefficients Calculator
        <https://web.media.mit.edu/~crtaylor/calculator.html>`_ to construct finite-difference
        approximations for the desired *(i)* derivative order, *(ii)* approximation accuracy, and
        *(iii)* finite difference type.
        Three basic types of finite differences are supported, which lead to the following
        first-order (``order = 1``) operators with ``accuracy = 1`` and sampling step ``sampling =
        h`` for one-dimensional signals:

        - **Forward difference**:
            Approximates the continuous operator :math:`D_{F}f(x) = \frac{f(x+h) - f(x)}{h}` with
            the discrete operator

            .. math::

               \mathbf{D}_{F} f [n] = \frac{f[n+1] - f[n]}{h},

            whose kernel is :math:`d = \frac{1}{h}[-1, 1]` and center is (0, ).

        - **Backward difference**:
            Approximates the continuous operator :math:`D_{B}f(x) = \frac{f(x) - f(x-h)}{h}` with
            the discrete operator

            .. math::

               \mathbf{D}_{B} f [n] = \frac{f[n] - f[n-1]}{h},

            whose kernel is :math:`d = \frac{1}{h}[-1, 1]` and center is (1, ).

        - **Central difference**:
            Approximates the continuous operator :math:`D_{C}f(x) = \frac{f(x+h) - f(x-h)}{2h}` with
            the discrete operator

            .. math::

               \mathbf{C}_{F} f [n] = \frac{f[n+1] - f[n-1]}{2h},

            whose kernel is :math:`d = \frac{1}{h}[-\frac12, 0, \frac12]` and center is (1, ).

        .. warning::

           For forward and backward differences, higher-order operators correspond to the
           composition of first-order operators.
           This is not the case for central differences: the second-order continuous operator is
           given by :math:`D^2_{C}f(x) = \frac{f(x+h) - 2 f(x) + f(x-h)}{h}`, hence :math:`D^2_{C}
           \neq D_{C} \circ D_{C}`.
           The corresponding discrete operator is given by :math:`\mathbf{D}^2_{C} f [n] =
           \frac{f[n+1] - 2 f[n] + f[n-1]}{h}`, whose kernel is :math:`d = \frac{1}{h}[1, -2, 1]`
           and center is (1, ).
           We refer to `this paper
           <https://www.ams.org/journals/mcom/1988-51-184/S0025-5718-1988-0935077-0/S0025-5718-1988-0935077-0.pdf>`_
           for more details.

        For a given derivative order :math:`N\in\mathbb{Z}^{+}` and accuracy :math:`a\in\mathbb{Z}^{+}`, the size
        :math:`N_s` of the stencil kernel :math:`d` used for finite differences is given by:

        - For central differences:

            :math:`N_s = 2 \lfloor\frac{N + 1}{2}\rfloor - 1 + a`

        - For forward and backward differences:

            :math:`N_s = N + a`

        For :math:`N_s` given support indices :math:`\{s_1, \ldots , s_{N_s} \} \subset \mathbb{Z}`
        and a derivative order :math:`N<N_s`, the stencil kernel :math:`d = [d_1, \ldots, d_{N_s}]`
        of the finite-difference approximation of the derivative is obtained by solving the
        following system of linear equations (see the `Finite Difference Coefficients Calculator
        <https://web.media.mit.edu/~crtaylor/calculator.html>`_ documentation):

        **Remark 1:**

        The number of coeffiecients of the finite-difference kernel is chosen to guarantee the
        requested accuracy, but might be larger than requested accuracy.
        For example, if choosing `scheme='central'` with `accuracy=1`, it will create a kernel
        corresponding to `accuracy=2`, as it is the minimum accuracy possible for such scheme (see
        the `finite difference coefficient table
        <https://en.wikipedia.org/wiki/Finite_difference_coefficient>`_).

        .. math::

           \left(\begin{array}{ccc}
           s_{1}^{0} & \cdots & s_{N_s}^{0} \\
           \vdots & \ddots & \vdots \\
           s_{1}^{N_s-1} & \cdots & s_{N_s}^{N_s-1}
           \end{array}\right)\left(\begin{array}{c}
           d_{1} \\
           \vdots \\
           d_{N_s}
           \end{array}\right)= \frac{1}{h^{N}}\left(\begin{array}{c}
           \delta_{0, N} \\
           \vdots \\
           \delta_{i, N} \\
           \vdots \\
           \delta_{N_s-1, N}
           \end{array}\right),

        where :math:`\delta_{i, j}` is the Kronecker delta.

        This class inherits its methods from
        :py:class:`~pycsou.operator.linop.stencil.stencil.Stencil`.

        Example
        -------

        .. plot::

           import numpy as np
           import matplotlib.pyplot as plt
           from pycsou.operator.linop.diff import PartialDerivative
           from pycsou.util.misc import peaks

           x = np.linspace(-2.5, 2.5, 25)
           xx, yy = np.meshgrid(x, x)
           image = peaks(xx, yy)
           arg_shape = image.shape  # Shape of our image
           # Specify derivative order at each direction
           df_dx = (1, 0)  # Compute derivative of order 1 in first dimension
           d2f_dy2 = (0, 2)  # Compute derivative of order 2 in second dimension
           d3f_dxdy2 = (1, 2)  # Compute derivative of order 1 in first dimension and der. of order 2 in second dimension
           # Instantiate derivative operators
           sigma = 2.0
           diff1 = PartialDerivative.gaussian_derivative(order=df_dx, arg_shape=arg_shape, sigma=sigma / np.sqrt(2))
           diff2 = PartialDerivative.gaussian_derivative(order=d2f_dy2, arg_shape=arg_shape, sigma=sigma / np.sqrt(2))
           diff = PartialDerivative.gaussian_derivative(order=d3f_dxdy2, arg_shape=arg_shape, sigma=sigma)
           # Compute derivatives
           out1 = (diff1 * diff2)(image.ravel()).reshape(arg_shape)
           out2 = diff(image.ravel()).reshape(arg_shape)
           # Plot derivatives
           fig, axs = plt.subplots(1, 3, figsize=(15, 4))
           im = axs[0].imshow(image)
           axs[0].axis("off")
           axs[0].set_title("f(x,y)")
           plt.colorbar(im, ax=axs[0])
           axs[1].imshow(out1)
           axs[1].axis("off")
           axs[1].set_title(r"$\frac{\partial^{3} f(x,y)}{\partial x\partial y^{2}}$")
           plt.colorbar(im, ax=axs[1])

           axs[2].imshow(out2)
           axs[2].axis("off")
           axs[2].set_title(r"$\frac{\partial^{3} f(x,y)}{\partial x\partial y^{2}}$")
           plt.colorbar(im, ax=axs[2])

           # Test approximation error
           plt.figure()
           plt.imshow(abs(out1 - out2) / abs(out2)), plt.colorbar()

        """
        assert isinstance(order, cabc.Sequence), "`order` should be a tuple / list"
        assert len(order) == len(arg_shape)
        diff_kwargs = {"scheme": scheme, "accuracy": accuracy}
        order, sampling, scheme, accuracy, _ = _sanitize_init_kwargs(
            order=order,
            diff_method="fd",
            diff_kwargs=diff_kwargs,
            arg_shape=arg_shape,
            sampling=sampling,
        )

        # Compute a kernel for each axis
        kernel = [np.array(1)] * len(arg_shape)
        center = np.zeros(len(arg_shape), dtype=int)
        for ax in range(len(arg_shape)):
            if order[ax] > 0:
                k, c = _FiniteDifference(
                    order=order[ax],
                    arg_shape=arg_shape,
                    scheme=scheme[ax],
                    axes=ax,
                    accuracy=accuracy[ax],
                    sampling=sampling[ax],
                )
                kernel[ax] = k[ax]
                center[ax] = c[ax]

        meta = PDMetaFD(sampling=sampling, scheme=scheme, accuracy=accuracy)

        return _PartialDerivative(
            kernel=kernel,
            center=center,
            arg_shape=arg_shape,
            mode=mode,
            gpu=gpu,
            dtype=dtype,
            meta=meta,
        )

    @staticmethod
    def gaussian_derivative(
        arg_shape: pyct.NDArrayShape,
        order: cabc.Sequence[pyct.Integer, ...],
        sigma: typ.Union[pyct.Real, cabc.Sequence[pyct.Real, ...]] = 1.0,
        truncate: typ.Union[pyct.Real, cabc.Sequence[pyct.Real, ...]] = 3.0,
        mode: ModeSpec = "constant",
        gpu: bool = False,
        dtype: typ.Optional[pyct.DType] = None,
        sampling: typ.Union[pyct.Real, cabc.Sequence[pyct.Real, ...]] = 1,
    ) -> pyct.OpT:
        r"""
        Compute partial derivatives for multi-dimensional signals using gaussian derivatives.

        Parameters
        ----------
        arg_shape: pyct.NDArrayShape
            Shape of the input array.
        order: (pyct.Integer, ..., pyct.Integer)
            Derivative order for each dimension.
            The total order of the partial derivative is the sum of the elements of the tuple.
            Use zeros to indicate dimensions in which the derivative should not be computed.
        sigma: pyct.Real | (pyct.Real, ..., pyct.Real)
            Standard deviation for the Gaussian kernel.
            Defaults to 1.0.
            If a float is provided, the same `sigma` is assumed for all dimensions.
            If a tuple is provided, it should have as many elements as `order`.
        truncate: pyct.Real | (pyct.Real, ..., pyct.Real)
            Truncate the filter at this many standard deviations (at each side from the origin).
            Defaults to 3.0.
            If a float is provided, the same `truncate` is assumed for all dimensions.
            If a tuple is provided, it should have as many elements as `order`.
        mode: str | list(str)
            Boundary conditions.
            Multiple forms are accepted:

            * str: unique mode shared amongst dimensions.
              Must be one of:

              * 'constant' (default): zero-padding
              * 'wrap'
              * 'reflect'
              * 'symmetric'
              * 'edge'
            * tuple[str, ...]: the `d`-th dimension uses ``mode[d]`` as boundary condition.

            (See :py:func:`numpy.pad` for details.)
        gpu: bool
            Input NDArray type (`True` for GPU, `False` for CPU).
            Defaults to `False`.
        dtype: pyct.DType
            Working precision of the linear operator.
        sampling: pyct.Real | (pyct.Real, ..., pyct.Real)
            Sampling step (i.e., the distance between two consecutive elements of an array).
            Defaults to 1.

        Returns
        -------
        op: :py:class:`~pycsou.abc.operator.LinOp`
            Partial derivative.

        Notes
        -----
        We explain here Gaussian derivatives for one-dimensional signals; this operator performs
        partial Gaussian derivatives for multi-dimensional signals along dimensions specified by
        ``order``.

        A Gaussian derivative is an approximation of a derivative that consists in convolving the
        input function with a Gaussian function :math:`g` before applying a derivative.
        In the continuous domain, the :math:`N`-th order Gaussian derivative :math:`D^N_G` amounts
        to a convolution with the :math:`N`-th order derivative of :math:`g`:

        .. math::

           D^N_G f (x)
           =
           \frac{\mathrm{d}^N (f * g) }{\mathrm{d} x^N} (x)
           =
           f(x) * \frac{\mathrm{d}^N g}{\mathrm{d} x^N} (x).

        For discrete signals :math:`f[n]`, this operator is approximated by

        .. math::

           \mathbf{D}^N_G f [n]
           =
           f[n] *\frac{\mathrm{d}^N g}{\mathrm{d} x^N} \left(\frac{n}{h}\right),

        where :math:`h` is the spacing between samples and the operator :math:`*` is now a discrete
        convolution.

        .. warning::

           The operator :math:`\mathbf{D}_{G} \circ \mathbf{D}_{G}` is not directly related to
           :math:`\mathbf{D}_{G}^{2}`:
           Gaussian smoothing is performed twice in the case of the former, whereas it is performed
           only once in the case of the latter.

        Note that in contrast with finite differences (see
        :py:meth:`~pycsou.operator.linop.diff.PartialDerivative.finite_difference`), Gaussian
        derivatives compute exact derivatives in the continuous domain, since Gaussians can be
        differentiated analytically.
        This derivative is then sampled in order to perform a discrete convolution.

        This class inherits its methods from
        :py:class:`~pycsou.operator.linop.stencil.stencil.Stencil`.

        Example
        -------

        .. plot::

           import numpy as np
           import matplotlib.pyplot as plt
           from pycsou.operator.linop.diff import PartialDerivative
           from pycsou.util.misc import peaks
           x = np.linspace(-2.5, 2.5, 25)
           xx, yy = np.meshgrid(x, x)
           image = peaks(xx, yy)
           arg_shape = image.shape  # Shape of our image
           # Specify derivative order at each direction
           df_dx = (1, 0) # Compute derivative of order 1 in first dimension
           d2f_dy2 = (0, 2) # Compute derivative of order 2 in second dimension
           d3f_dxdy2 = (1, 2) # Compute derivative of order 1 in first dimension and der. of order 2 in second dimension
           # Instantiate derivative operators
           diff1 = PartialDerivative.gaussian_derivative(order=df_dx, arg_shape=arg_shape, sigma=2.0)
           diff2 = PartialDerivative.gaussian_derivative(order=d2f_dy2, arg_shape=arg_shape, sigma=2.0)
           diff = PartialDerivative.gaussian_derivative(order=d3f_dxdy2, arg_shape=arg_shape, sigma=2.0)
           # Compute derivatives
           out1 = (diff1 * diff2)(image.ravel()).reshape(arg_shape)
           out2 = diff(image.ravel()).reshape(arg_shape)
           plt.figure()
           plt.imshow(image),
           plt.axis('off')
           plt.colorbar()
           plt.title('f(x,y)')
           plt.figure()
           plt.imshow(out1.T)
           plt.axis('off')
           plt.title(r'$\frac{\partial^{3} f(x,y)}{\partial x\partial y^{2}}$')
           plt.figure()
           plt.imshow(out2.T)
           plt.axis('off')
           plt.title(r'$\frac{\partial^{3} f(x,y)}{\partial x\partial y^{2}}$')
           # Test
           assert np.allclose(out1, out2)

        """
        assert isinstance(order, cabc.Sequence), "`order` should be a tuple / list"
        assert len(order) == len(arg_shape)

        diff_kwargs = {"sigma": sigma, "truncate": truncate}
        order, sampling, sigma, truncate, _ = _sanitize_init_kwargs(
            order=order,
            diff_method="gd",
            diff_kwargs=diff_kwargs,
            arg_shape=arg_shape,
            sampling=sampling,
        )

        # Compute a kernel for each axes
        kernel = [np.array(1)] * len(arg_shape)
        center = np.zeros(len(arg_shape), dtype=int)
        for ax in range(len(arg_shape)):
            k, c = _GaussianDerivative(
                order=order[ax],
                arg_shape=arg_shape,
                sigma=sigma[ax],
                axes=ax,
                truncate=truncate[ax],
                sampling=sampling[ax],
            )
            kernel[ax] = k[ax]
            center[ax] = c[ax]

        meta = PDMetaGD(sampling=sampling, sigma=sigma, truncate=truncate)

        return _PartialDerivative(
            kernel=kernel,
            center=center,
            arg_shape=arg_shape,
            mode=mode,
            gpu=gpu,
            dtype=dtype,
            meta=meta,
        )


def _make_unravelable(op: pyct.OpT, arg_shape: typ.Optional[pyct.NDArrayShape] = None) -> pyct.OpT:
    def unravel(self, arr: pyct.NDArray) -> pyct.NDArray:
        return arr.reshape(*arr.shape[:-1], -1, *self.arg_shape)

    def ravel(self, arr: pyct.NDArray) -> pyct.NDArray:
        return arr.reshape(*arr.shape[: -1 - len(self.arg_shape)], -1)

    if arg_shape is not None:
        setattr(op, "arg_shape", arg_shape)

    setattr(op, "unravel", types.MethodType(unravel, op))
    setattr(op, "ravel", types.MethodType(ravel, op))
    return op


class _StackDiffHelper:
    r"""
    Helper class for Gradient and Hessian.

    Defines a method for computing and stacking partial derivatives.

    See Also
    --------
    :py:class:`~pycsou.operator.linop.diff.Gradient`,
    :py:class:`~pycsou.operator.linop.diff.Laplacian`,
    :py:class:`~pycsou.operator.linop.diff.Hessian`.
    """

    @staticmethod
    def _stack_diff_ops(
        arg_shape: pyct.NDArrayShape,
        directions: pyct.NDArrayAxis,
        diff_method: str,
        order: typ.Union[pyct.Integer, cabc.Sequence[pyct.Integer, ...]],
        param1: typ.Union[str, cabc.Sequence[str, ...], pyct.Real, cabc.Sequence[pyct.Real, ...]],
        param2: typ.Union[pyct.Integer, cabc.Sequence[pyct.Integer, ...], pyct.Real, cabc.Sequence[pyct.Real, ...]],
        mode: ModeSpec = "constant",
        gpu: bool = False,
        dtype: typ.Optional[pyct.DType] = None,
        sampling: typ.Union[pyct.Real, cabc.Sequence[pyct.Real, ...]] = 1,
        parallel: bool = False,
    ) -> pyct.OpT:

        if isinstance(mode, str):
            mode = (mode,)
        if isinstance(mode, cabc.Sequence):
            if len(mode) != len(arg_shape):
                assert len(mode) == 1
                mode = mode * len(arg_shape)

        else:
            raise ValueError("mode has to be a string or a tuple")
        dif_op = []
        for i in range(0, len(directions)):
            _order = np.zeros_like(arg_shape)
            _order[directions[i]] = order[i]
            _param1 = param1 if not isinstance(param1[0], (list, tuple)) else param1[i]
            _param2 = param2 if not isinstance(param2[0], (list, tuple)) else param2[i]
            if diff_method == "fd":
                dif_op.append(
                    PartialDerivative.finite_difference(
                        arg_shape=arg_shape,
                        order=tuple(_order),
                        scheme=_param1,
                        accuracy=_param2,
                        mode=mode,
                        gpu=gpu,
                        dtype=dtype,
                        sampling=sampling,
                    )
                )

            elif diff_method == "gd":
                dif_op.append(
                    PartialDerivative.gaussian_derivative(
                        arg_shape=arg_shape,
                        order=tuple(_order),
                        sigma=param1,
                        truncate=param2,
                        mode=mode,
                        gpu=gpu,
                        dtype=dtype,
                        sampling=sampling,
                    )
                )

        def visualize(_) -> str:
            r"""
            Show the :math:`D`-dimensional stacked partial derivative kernels.

            The kernel's center is identified by surrounding parentheses.

            Example
            -------
            .. code-block:: python3

               from pycsou.operator.linop import Hessian

               H = hessian(
                    arg_shape=(5, 6),
                    diff_method="fd",
               )
               print(H.visualize())  # Direction (0, 0)
                                     # [[(1.0)]
                                     #  [-2.0]
                                     #  [1.0]]
                                     #
                                     # Direction (1, 0)
                                     # [[(1.0) -1.0]
                                     #  [-1.0 1.0]]
                                     #
                                     # Direction (2, 0)
                                     # [[(1.0) -2.0 1.0]]
            """
            kernels = []
            for direction, stencil in _._block.items():
                kernels.append(f"\nDirection {direction} \n" + stencil.visualize())
            return "\n".join(kernels)

        if diff_method == "fd":
            meta = PDMetaFD(
                sampling=[op.meta.sampling for op in dif_op],
                scheme=[op.meta.scheme for op in dif_op],
                accuracy=[op.meta.accuracy for op in dif_op],
            )
        else:  # diff_method == "gd"
            meta = PDMetaGD(
                sampling=[op.meta.sampling for op in dif_op],
                sigma=[op.meta.sigma for op in dif_op],
                truncate=[op.meta.truncate for op in dif_op],
            )
        op = _make_unravelable(pycb.vstack(dif_op, parallel=parallel), arg_shape)
        setattr(op, "visualize", types.MethodType(visualize, op))
        setattr(op, "meta", meta)
        return op

    @staticmethod
    def _check_directions_and_order(
        arg_shape: pyct.NDArrayShape,
        directions: typ.Union[
            str,
            pyct.NDArrayAxis,
            cabc.Sequence[pyct.NDArrayAxis, ...],
        ],
    ) -> cabc.Sequence[cabc.Sequence[pyct.NDArrayAxis, ...], pyct.NDArrayAxis]:
        # Convert directions to canonical form
        def _check_directions(_directions):
            assert all(0 <= _ <= (len(arg_shape) - 1) for _ in _directions), (
                "Direction values must be between 0 and " "the number of dimensions in `arg_shape`."
            )

        if not isinstance(directions, cabc.Sequence):
            # This corresponds to [mode 0] in `Notes`
            directions = [directions, directions]
            _check_directions(directions)
            directions = (directions,)
        else:
            if isinstance(directions, str):
                # This corresponds to [mode 3] in Hessian `Notes`
                assert directions == "all", (
                    "Value for `directions` not implemented. The accepted directions types are"
                    "int, tuple or a str with the value `all`."
                )
                directions = tuple(
                    list(_) for _ in itertools.combinations_with_replacement(np.arange(len(arg_shape)).astype(int), 2)
                )
            elif not isinstance(directions[0], cabc.Sequence):
                # This corresponds to [mode 1] in Hessian  `Notes`
                assert len(directions) == 2, (
                    "If `directions` is a tuple, it should contain two elements, corresponding "
                    "to the i-th an j-th elements (dx_i and dx_j)"
                )
                directions = list(directions)
                _check_directions(directions)
                directions = (directions,)
            else:
                # This corresponds to [mode 2] in Hessian `Notes`
                for direction in directions:
                    _check_directions(direction)

        # Convert to canonical form for PartialDerivative (direction + order)
        _directions = [
            list(direction) if (len(np.unique(direction)) == len(direction)) else np.unique(direction).tolist()
            for direction in directions
        ]

        _order = [3 - len(np.unique(direction)) for direction in directions]

        return _directions, _order


def Gradient(
    arg_shape: pyct.NDArrayShape,
    directions: typ.Optional[pyct.NDArrayAxis] = None,
    diff_method: str = "fd",
    mode: ModeSpec = "constant",
    gpu: bool = False,
    dtype: typ.Optional[pyct.DType] = None,
    parallel: bool = False,
    **diff_kwargs,
) -> pyct.OpT:
    r"""
    Gradient operator.

    Notes
    -----

    This operator stacks the first-order partial derivatives of a :math:`D`-dimensional signal
    :math:`\mathbf{f} \in \mathbb{R}^{N_{0} \times \cdots \times N_{D-1}}` along each dimension:

    .. math::

       \boldsymbol{\nabla} \mathbf{f} = \begin{bmatrix}
       \frac{\partial \mathbf{f}}{\partial x_0} \\
       \vdots \\
       \frac{\partial \mathbf{f}}{\partial x_{D-1}}
       \end{bmatrix} \in \mathbb{R}^{D \times N_{0} \times \cdots \times N_{D-1}}

    The partial derivatives can be approximated by `finite differences
    <https://en.wikipedia.org/wiki/Finite_difference>`_ via the
    :py:meth:`~pycsou.operator.linop.diff.PartialDerivative.finite_difference` constructor or by the
    `Gaussian derivative <https://www.crisluengo.net/archives/22/>`_ via
    :py:meth:`~pycsou.operator.linop.diff.PartialDerivative.gaussian_derivative` constructor.
    The parametrization of the partial derivatives can be done via the keyword arguments
    `**diff_kwargs`, which will default to the same values as the
    :py:class:`~pycsou.operator.linop.diff.PartialDerivative` constructor.

    The gradient operator's method :py:meth:`~pycsou.operator.linop.diff.Gradient.unravel` allows
    reshaping the vectorized output gradient to ``[n_dirs, N0, ..., ND]`` (see the example below).

    Parameters
    ----------
    arg_shape: pyct.NDArrayShape
        Shape of the input array.
    directions: pyct.Integer | (pyct.Integer, ..., pyct.Integer) | None
        Gradient directions.
        Defaults to `None`, which computes the gradient for all directions.
    diff_method: str ['gd', 'fd']
        Method used to approximate the derivative.
        Must be one of:

        * 'fd' (default): finite differences
        * 'gd': Gaussian derivative
    mode: str | list(str)
        Boundary conditions.
        Multiple forms are accepted:

        * str: unique mode shared amongst dimensions.
          Must be one of:

          * 'constant' (default): zero-padding
          * 'wrap'
          * 'reflect'
          * 'symmetric'
          * 'edge'
        * tuple[str, ...]: the `d`-th dimension uses ``mode[d]`` as boundary condition.

        (See :py:func:`numpy.pad` for details.)
    gpu: bool
        Input NDArray type (`True` for GPU, `False` for CPU).
        Defaults to `False`.
    dtype: pyct.DType
        Working precision of the linear operator.
    parallel: bool
        If ``True``, use Dask to evaluate the different partial derivatives in parallel.
    diff_kwargs: dict
        Keyword arguments to parametrize partial derivatives (see
        :py:meth:`~pycsou.operator.linop.diff.PartialDerivative.finite_difference` and
        :py:meth:`~pycsou.operator.linop.diff.PartialDerivative.gaussian_derivative`)

    Returns
    -------
    op: :py:class:`~pycsou.abc.operator.LinOp`
        Gradient

    Example
    -------

    .. plot::

       import numpy as np
       import matplotlib.pyplot as plt
       from pycsou.operator.linop.diff import Gradient
       from pycsou.util.misc import peaks

       # Define input image
       n = 100
       x = np.linspace(-3, 3, n)
       xx, yy = np.meshgrid(x, x)
       image = peaks(xx, yy)
       arg_shape = image.shape  # (1000, 1000)
       # Instantiate gradient operator
       grad = Gradient(arg_shape=arg_shape)

       # Compute gradients
       output = grad(image.ravel()) # shape = (2000000, )
       df_dx, df_dy = grad.unravel(output) # shape = (2, 1000, 1000)

       # Plot image
       fig, axs = plt.subplots(1, 3, figsize=(15, 4))
       im = axs[0].imshow(image)
       axs[0].set_title("Image")
       axs[0].axis("off")
       plt.colorbar(im, ax=axs[0])

       # Plot gradient
       im = axs[1].imshow(df_dx)
       axs[1].set_title(r"$\partial f/ \partial x$")
       axs[1].axis("off")
       plt.colorbar(im, ax=axs[1])
       im = axs[2].imshow(df_dy)
       axs[2].set_title(r"$\partial f/ \partial y$")
       axs[2].axis("off")
       plt.colorbar(im, ax=axs[2])

    See Also
    --------
    :py:func:`~pycsou.operator.linop.diff.PartialDerivative`,
    :py:func:`~pycsou.operator.linop.diff.Jacobian`.
    """
    directions = tuple([i for i in range(len(arg_shape))]) if directions is None else directions
    axes = tuple([i for i in range(len(arg_shape)) if i in directions])
    order, sampling, param1, param2, _ = _sanitize_init_kwargs(
        order=(1,) * len(directions),
        axes=axes,
        arg_shape=arg_shape,
        sampling=diff_kwargs.get("sampling", 1.0),
        diff_method=diff_method,
        diff_kwargs=diff_kwargs,
    )
    op = _StackDiffHelper._stack_diff_ops(
        arg_shape=arg_shape,
        directions=directions,
        diff_method=diff_method,
        order=order,
        param1=param1,
        param2=param2,
        mode=mode,
        gpu=gpu,
        dtype=dtype,
        sampling=sampling,
        parallel=parallel,
    )
    op._name = "Gradient"
    return op


def Jacobian(
    arg_shape: pyct.NDArrayShape,
    n_channels: pyct.Integer,
    directions: typ.Optional[pyct.NDArrayAxis] = None,
    diff_method: str = "fd",
    mode: ModeSpec = "constant",
    gpu: bool = False,
    dtype: typ.Optional[pyct.DType] = None,
    parallel: bool = False,
    **diff_kwargs,
) -> pyct.OpT:
    r"""
    Jacobian operator.

    Notes
    -----

    This operator computes the first-order partial derivatives of a :math:`D`-dimensional
    vector-valued signal of :math:`C` variables or channels :math:`\mathbf{f} = [\mathbf{f}_{0},
    \ldots, \mathbf{f}_{C-1}]` with :math:`\mathbf{f}_{c} \in \mathbb{R}^{N_{0} \times \cdots \times
    N_{D-1}}`.

    The Jacobian of :math:`\mathbf{f}` is computed via the gradient as follows:

    .. math::

       \mathbf{J} \mathbf{f} = \begin{bmatrix}
       (\boldsymbol{\nabla} \mathbf{f}_{0})^{\top} \\
       \vdots \\
       (\boldsymbol{\nabla} \mathbf{f}_{C-1})^{\top} \\
       \end{bmatrix} \in \mathbb{R}^{C \times D \times N_0 \times \cdots \times N_{D-1}}

    The partial derivatives can be approximated by `finite differences
    <https://en.wikipedia.org/wiki/Finite_difference>`_ via the
    :py:meth:`~pycsou.operator.linop.diff.PartialDerivative.finite_difference` constructor or by the
    `Gaussian derivative <https://www.crisluengo.net/archives/22/>`_ via
    :py:meth:`~pycsou.operator.linop.diff.PartialDerivative.gaussian_derivative` constructor.
    The parametrization of the partial derivatives can be done via the keyword arguments
    `**diff_kwargs`, which will default to the same values as the
    :py:class:`~pycsou.operator.linop.diff.PartialDerivative` constructor.

    The Jacobian operator's method :py:meth:`~pycsou.operator.linop.diff.Jacobian.unravel` allows
    reshaping the vectorized output Jacobian to ``[..., n_channels, n_dirs, N0, ..., ND]`` (see the example below).

    **Remark**

    Pycsou's convention when it comes to field-vectors, is to work with vectorized arrays.
    However, the memory order of these arrays before raveling should be `[S_0, ..., S_D, C, N]`
    shape, with `S_0, ..., S_D` being stacking dimensions, `C` being the number of variables or
    channels, and `N` being the size of the domain (for example, `N = npixels_x * npixels_y` for a
    2D image).

    Parameters
    ----------
    arg_shape: pyct.NDArrayShape
        Shape of the input array.
    n_channels: pyct.Integer
        Number of channels or variables of the input vector-valued signal.
        The Jacobian with `n_channels==1` yields the gradient.
    directions: pyct.Integer | (pyct.Integer, ..., pyct.Integer) | None
        Gradient directions.
        Defaults to `None`, which computes the gradient for all directions.
    diff_method: str ['gd', 'fd']
        Method used to approximate the derivative.
        Must be one of:

        * 'fd' (default): finite differences
        * 'gd': Gaussian derivative
    mode: str | list(str)
        Boundary conditions.
        Multiple forms are accepted:

        * str: unique mode shared amongst dimensions.
          Must be one of:

          * 'constant' (default): zero-padding
          * 'wrap'
          * 'reflect'
          * 'symmetric'
          * 'edge'
        * tuple[str, ...]: the `d`-th dimension uses ``mode[d]`` as boundary condition.

        (See :py:func:`numpy.pad` for details.)
    gpu: bool
        Input NDArray type (`True` for GPU, `False` for CPU).
        Defaults to `False`.
    dtype: pyct.DType
        Working precision of the linear operator.
    parallel: bool
        If ``True``, use Dask to evaluate the different partial derivatives in parallel.
    diff_kwargs: dict
        Keyword arguments to parametrize partial derivatives (see
        :py:meth:`~pycsou.operator.linop.diff.PartialDerivative.finite_difference` and
        :py:meth:`~pycsou.operator.linop.diff.PartialDerivative.gaussian_derivative`)

    Returns
    -------
    op: :py:class:`~pycsou.abc.operator.LinOp`
        Jacobian

    Example
    -------

    .. plot::

       import numpy as np
       import matplotlib.pyplot as plt
       from pycsou.operator.linop.diff import Jacobian
       from pycsou.util.misc import peaks
       x = np.linspace(-2.5, 2.5, 25)
       xx, yy = np.meshgrid(x, x)
       image = np.tile(peaks(xx, yy), (3, 1, 1))
       jac = Jacobian(arg_shape=image.shape[1:], n_channels=image.shape[0])
       out = jac.unravel(jac(image.ravel()))
       fig, axes = plt.subplots(3, 2, figsize=(10, 15))
       for i in range(2):
           for j in range(3):
               axes[j, i].imshow(out[i, j].T, cmap=["Reds", "Greens", "Blues"][j])
               axes[j, i].set_title(f"$\partial I_{{{['R', 'G', 'B'][j]}}}/\partial{{{['x', 'y'][i]}}}$")
       plt.suptitle("Jacobian")

    See Also
    --------
    :py:func:`~pycsou.operator.linop.diff.Gradient`,
    :py:func:`~pycsou.operator.linop.diff.PartialDerivative`.
    """
    init_kwargs = dict(
        arg_shape=arg_shape,
        directions=directions,
        diff_method=diff_method,
        mode=mode,
        gpu=gpu,
        dtype=dtype,
        parallel=parallel,
        **diff_kwargs,
    )

    grad = Gradient(**init_kwargs)
    if n_channels > 1:
        op = pycb.block_diag(
            [
                grad,
            ]
            * n_channels
        )
    else:
        op = grad
    op._name = "Jacobian"
    return _make_unravelable(op, (n_channels, *arg_shape))


def Divergence(
    arg_shape: pyct.NDArrayShape,
    directions: typ.Optional[pyct.NDArrayAxis] = None,
    diff_method: str = "fd",
    mode: ModeSpec = "constant",
    gpu: bool = False,
    dtype: typ.Optional[pyct.DType] = None,
    parallel: bool = False,
    **diff_kwargs,
) -> pyct.OpT:
    r"""
    Divergence operator.

    Notes
    -----

    This operator computes the expansion or outgoingness of a :math:`D`-dimensional vector-valued
    signal of :math:`C` variables :math:`\mathbf{f} = [\mathbf{f}_{0}, \ldots, \mathbf{f}_{C-1}]`
    with :math:`\mathbf{f}_{c} \in \mathbb{R}^{N_{0} \times \cdots \times N_{D-1}}`.

    The Divergence of :math:`\mathbf{f}` is computed via the adjoint of the gradient as follows:

    .. math::

       \operatorname{Div} \mathbf{f} = \boldsymbol{\nabla}^{\ast} \mathbf{f}
       = \begin{bmatrix}
       \frac{\partial \mathbf{f}}{\partial x_0} + \cdots + \frac{\partial \mathbf{f}}{\partial x_{D-1}}
       \end{bmatrix} \in \mathbb{R}^{N_{0} \times \cdots \times N_{D-1}}

    The partial derivatives can be approximated by `finite differences
    <https://en.wikipedia.org/wiki/Finite_difference>`_ via the
    :py:meth:`~pycsou.operator.linop.diff.PartialDerivative.finite_difference` constructor or by the
    `Gaussian derivative <https://www.crisluengo.net/archives/22/>`_ via
    :py:meth:`~pycsou.operator.linop.diff.PartialDerivative.gaussian_derivative` constructor.
    The parametrization of the partial derivatives can be done via the keyword arguments
    `**diff_kwargs`, which will default to the same values as the
    :py:class:`~pycsou.operator.linop.diff.PartialDerivative` constructor.


    When using finite differences to compute the Divergence (i.e., ``diff_method = "fd"``), the
    divergence returns the adjoint of the gradient in reversed order:

    * For ``forward`` type divergence, the adjoint of the gradient of "backward" type is used.
    * For ``backward`` type divergence, the adjoint of the gradient of "forward" type is used.

    For ``central`` type divergence, and for the Gaussian derivative method (i.e., ``diff_method =
    "gd"``), the adjoint of the gradient of "central" type is used (no reversed order).

    The divergence operator's method :py:meth:`~pycsou.operator.linop.diff.Divergence.unravel` allows
    reshaping the vectorized output divergence to ``[..., N0, ..., ND]``.


    Parameters
    ----------
    arg_shape: pyct.NDArrayShape
        Shape of the input array.
    directions: pyct.Integer | (pyct.Integer, ..., pyct.Integer) | None
        Divergence directions.
        Defaults to `None`, which computes the divergence for all directions.
    diff_method: str ['gd', 'fd']
        Method used to approximate the derivative.
        Must be one of:

        * 'fd' (default): finite differences
        * 'gd': Gaussian derivative
    mode: str | list(str)
        Boundary conditions.
        Multiple forms are accepted:

        * str: unique mode shared amongst dimensions.
          Must be one of:

          * 'constant' (default): zero-padding
          * 'wrap'
          * 'reflect'
          * 'symmetric'
          * 'edge'
        * tuple[str, ...]: the `d`-th dimension uses ``mode[d]`` as boundary condition.

        (See :py:func:`numpy.pad` for details.)
    gpu: bool
        Input NDArray type (`True` for GPU, `False` for CPU).
        Defaults to `False`.
    dtype: pyct.DType
        Working precision of the linear operator.
    parallel: bool
        If ``True``, use Dask to evaluate the different partial derivatives in parallel.
    diff_kwargs: dict
        Keyword arguments to parametrize partial derivatives (see
        :py:meth:`~pycsou.operator.linop.diff.PartialDerivative.finite_difference` and
        :py:meth:`~pycsou.operator.linop.diff.PartialDerivative.gaussian_derivative`)

    Returns
    -------
    op: :py:class:`~pycsou.abc.operator.LinOp`
        Divergence

    Example
    -------

    .. plot::

       import numpy as np
       import matplotlib.pyplot as plt
       from pycsou.operator.linop.diff import Divergence, Jacobian, Laplacian
       from pycsou.util.misc import peaks

       n = 100
       x = np.linspace(-3, 3, n)
       xx, yy = np.meshgrid(x, x)
       image = peaks(xx, yy)
       arg_shape = image.shape  # (1000, 1000)
       grad = Gradient(arg_shape=arg_shape)
       div = Divergence(arg_shape=arg_shape)
       # Construct Laplacian via composition
       laplacian1 = div * grad
       # Compare to default Laplacian
       laplacian2 = Laplacian(arg_shape=arg_shape)
       output1 = laplacian1(image.ravel())
       output2 = laplacian2(image.ravel())
       fig, axes = plt.subplots(1, 2, figsize=(10, 5))
       im = axes[0].imshow(np.log(abs(output1)).reshape(*arg_shape))
       axes[0].set_title("Laplacian via composition")
       plt.colorbar(im, ax=axes[0])
       im = axes[1].imshow(np.log(abs(output1)).reshape(*arg_shape))
       axes[1].set_title("Default Laplacian")
       plt.colorbar(im, ax=axes[1])


    See Also
    --------
    :py:func:`~pycsou.operator.linop.diff.Gradient`,
    :py:func:`~pycsou.operator.linop.diff.PartialDerivative`.
    """
    if diff_method == "fd":
        change = {"central": "central", "forward": "backward", "backward": "forward"}
        scheme = diff_kwargs.get("scheme", "central")
        if isinstance(scheme, str):
            diff_kwargs.update({"scheme": change[scheme]})
        elif isinstance(scheme, cabc.Sequence):
            new_scheme = list(scheme)
            for i in range(len(new_scheme)):
                new_scheme[i] = change[scheme[i]]
            diff_kwargs.update(dict(scheme=new_scheme))

    init_kwargs = dict(
        diff_method=diff_method,
        mode=mode,
        gpu=gpu,
        dtype=dtype,
        **diff_kwargs,
    )

    # Add dummy parameters for artificial dimension
    for key in init_kwargs.keys():
        param = init_kwargs[key]
        if isinstance(param, cabc.Sequence):
            if not isinstance(param, str):
                param = list(param)
                param.insert(0, "dummy")
        init_kwargs.update({key: param})

    directions = tuple([i for i in range(len(arg_shape))]) if directions is None else directions
    n_dir = len(directions)

    pds = pycb.block_diag(
        [Gradient(arg_shape=arg_shape, directions=(direction,), **init_kwargs) for direction in directions],
        parallel=parallel,
    )
    op = pyclr.Sum(arg_shape=(n_dir,) + arg_shape, axis=0) * pds
    op._name = "Divergence"
    return _make_unravelable(op, arg_shape)


def Hessian(
    arg_shape: pyct.NDArrayShape,
    directions: typ.Union[
        str,
        cabc.Sequence[pyct.Integer, pyct.Integer],
        cabc.Sequence[cabc.Sequence[pyct.Integer, pyct.Integer], ...],
    ] = "all",
    diff_method: str = "fd",
    mode: ModeSpec = "constant",
    gpu: bool = False,
    dtype: typ.Optional[pyct.DType] = None,
    parallel: bool = False,
    **diff_kwargs,
) -> pyct.OpT:
    r"""
    Hessian operator.

    Notes
    -----

    The Hessian matrix or Hessian of a :math:`D`-dimensional signal :math:`\mathbf{f} \in
    \mathbb{R}^{N_0 \times \cdots \times N_{D-1}}` is the square matrix of second-order partial derivatives:

    .. math::

       \mathbf{H} \mathbf{f} = \begin{bmatrix}
       \dfrac{ \partial^{2}\mathbf{f} }{ \partial x_{0}^{2} } &  \dfrac{ \partial^{2}\mathbf{f} }{ \partial x_{0}\,\partial x_{1} } & \cdots & \dfrac{ \partial^{2}\mathbf{f} }{ \partial x_{0} \, \partial x_{D-1} } \\
       \dfrac{ \partial^{2}\mathbf{f} }{ \partial x_{1} \, \partial x_{0} } & \dfrac{ \partial^{2}\mathbf{f} }{ \partial x_{1}^{2} } & \cdots & \dfrac{ \partial^{2}\mathbf{f} }{\partial x_{1} \,\partial x_{D-1}} \\
       \vdots & \vdots & \ddots & \vdots \\
       \dfrac{ \partial^{2}\mathbf{f} }{ \partial x_{D-1} \, \partial x_{0} } & \dfrac{ \partial^{2}\mathbf{f} }{ \partial x_{D-1} \, \partial x_{1} } & \cdots & \dfrac{ \partial^{2}\mathbf{f} }{ \partial x_{D-1}^{2}}
       \end{bmatrix}

    The partial derivatives can be approximated by `finite differences
    <https://en.wikipedia.org/wiki/Finite_difference>`_ via the
    :py:meth:`~pycsou.operator.linop.diff.PartialDerivative.finite_difference` constructor or by the
    `Gaussian derivative <https://www.crisluengo.net/archives/22/>`_ via
    :py:meth:`~pycsou.operator.linop.diff.PartialDerivative.gaussian_derivative` constructor.
    The parametrization of the partial derivatives can be done via the keyword arguments
    `**diff_kwargs`, which will default to the same values as the
    :py:class:`~pycsou.operator.linop.diff.PartialDerivative` constructor.

    The Hessian being symmetric, only the upper triangular part at most needs to be computed.
    Due to the (possibly) large size of the full Hessian, 4 different options are handled:

    * [mode 0] ``directions`` is an integer, e.g.: ``directions=0``

    .. math::

       \partial^{2}\mathbf{f}/\partial x_{0}^{2}.

    * [mode 1] ``directions`` is a tuple of length 2, e.g.: ``directions=(0,1)``

    .. math::

       \partial^{2}\mathbf{f}/\partial x_{0}\partial x_{1}.

    * [mode 2] ``directions`` is a tuple of tuples, e.g.: ``directions=((0,0), (0,1))``

    .. math::

       \left(\frac{ \partial^{2}\mathbf{f} }{ \partial x_{0}^{2} }, \frac{ \partial^{2}\mathbf{f} }{ \partial x_{0}\partial x_{1} }\right).

    * [mode 3] ``directions = 'all'`` (default), computes the Hessian for all directions (only the
      upper triangular part of the Hessian matrix), in row order, i.e.:

    .. math::

       \left(\frac{ \partial^{2}\mathbf{f} }{ \partial x_{0}^{2} }, \frac{ \partial^{2}\mathbf{f} }
       { \partial x_{0}\partial x_{1} }, \, \ldots , \, \frac{ \partial^{2}\mathbf{f} }{ \partial x_{D-1}^{2} }\right).

    The shape of the output :py:class:`~pycsou.abc.operator.LinOp` depends on the number of computed
    directions; by default (all directions), we have :math:`\mathbf{H} \mathbf{f} \in
    \mathbb{R}^{\frac{D(D-1)}{2} \times N_0 \times \cdots \times N_{D-1}}`.

    The Hessian operator's :py:meth:`~pycsou.operator.linop.diff.Hessian.unravel` method allows
    reshaping the vectorized output Hessian to `[n_dirs, N0, ..., ND]` (see the example below).

    Parameters
    ----------
    arg_shape: pyct.NDArrayShape
        Shape of the input array.
    directions: pyct.Integer | (pyct.Integer, pyct.Integer) | ((pyct.Integer, pyct.Integer), ..., (pyct.Integer, pyct.Integer)) | 'all'
        Hessian directions.
        Defaults to `all`, which computes the Hessian for all directions. (See ``Notes``.)
    diff_method: str ['gd', 'fd']
        Method used to approximate the derivative.
        Must be one of:

        * 'fd' (default): finite differences
        * 'gd': Gaussian derivative
    mode: str | list(str)
        Boundary conditions.
        Multiple forms are accepted:

        * str: unique mode shared amongst dimensions.
          Must be one of:

          * 'constant' (default): zero-padding
          * 'wrap'
          * 'reflect'
          * 'symmetric'
          * 'edge'
        * tuple[str, ...]: the `d`-th dimension uses ``mode[d]`` as boundary condition.

        (See :py:func:`numpy.pad` for details.)
    gpu: bool
        Input NDArray type (`True` for GPU, `False` for CPU).
        Defaults to `False`.
    dtype: pyct.DType
        Working precision of the linear operator.
    parallel: bool
        If ``True``, use Dask to evaluate the different partial derivatives in parallel.
    diff_kwargs: dict
        Keyword arguments to parametrize partial derivatives (see
        :py:meth:`~pycsou.operator.linop.diff.PartialDerivative.finite_difference` and
        :py:meth:`~pycsou.operator.linop.diff.PartialDerivative.gaussian_derivative`)

    Returns
    -------
    op: :py:class:`~pycsou.abc.operator.LinOp`
        Hessian

    Example
    -------

    .. plot::

       import numpy as np
       import matplotlib.pyplot as plt
       from pycsou.operator.linop.diff import Hessian, PartialDerivative
       from pycsou.util.misc import peaks

       n = 100
       x = np.linspace(-3, 3, n)
       xx, yy = np.meshgrid(x, x)
       image = peaks(xx, yy)
       arg_shape = image.shape  # (1000, 1000)

       # Instantiate Hessian operator
       hessian = Hessian(arg_shape=arg_shape, directions="all")
       # Compute Hessian
       output = hessian(image.ravel()) # shape = (300000,)
       d2f_dx2, d2f_dxdy, d2f_dy2 = hessian.unravel(output)

       # Plot
       fig, axs = plt.subplots(1, 4, figsize=(20, 4))
       im = axs[0].imshow(image)
       plt.colorbar(im, ax=axs[0])
       axs[0].set_title("Image")
       axs[0].axis("off")

       im = axs[1].imshow(d2f_dx2)
       plt.colorbar(im, ax=axs[1])
       axs[1].set_title(r"$\partial^{2} f/ \partial x^{2}$")
       axs[1].axis("off")

       im = axs[2].imshow(d2f_dxdy)
       plt.colorbar(im, ax=axs[2])
       axs[2].set_title(r"$\partial^{2} f/ \partial x\partial y$")
       axs[2].axis("off")

       im = axs[3].imshow(d2f_dy2)
       plt.colorbar(im, ax=axs[3])
       axs[3].set_title(r"$\partial^{2} f/ \partial y^{2}$")
       axs[3].axis("off")

    See Also
    --------
    :py:class:`~pycsou.operator.linop.diff.PartialDerivative`,
    :py:class:`~pycsou.operator.linop.diff.Gradient`,
    :py:class:`~pycsou.operator.linop.diff.Laplacian`.
    """
    # We assume Schwarz's theorem holds and thus the symmetry of second derivatives.
    # For this reason, when directions == `all`, only the upper triangular part of the Hessian is
    # returned.
    # However, this might not hold for non-trivial padding conditions, and the user can demand all
    # Hessian components via the `directions` 2nd mode (see ``Notes``).

    order, sampling, param1, param2, _ = _sanitize_init_kwargs(
        order=(1,) * len(arg_shape),
        diff_method=diff_method,
        diff_kwargs=diff_kwargs,
        arg_shape=arg_shape,
        sampling=diff_kwargs.get("sampling", 1.0),
    )

    directions, order = _StackDiffHelper._check_directions_and_order(arg_shape, directions)

    # If diff_method is "fd" default to "central" for diag components and "forward" for off-diag.
    if (diff_method == "fd") and (diff_kwargs.get("scheme", None) is None):
        param1 = [("central", "central") if o == 2 else param1 for o in order]

    op = _StackDiffHelper._stack_diff_ops(
        arg_shape=arg_shape,
        directions=directions,
        diff_method=diff_method,
        order=order,
        param1=param1,
        param2=param2,
        mode=mode,
        gpu=gpu,
        dtype=dtype,
        sampling=sampling,
        parallel=parallel,
    )
    op._name = "Hessian"
    return op


def Laplacian(
    arg_shape: pyct.NDArrayShape,
    directions: typ.Optional[pyct.NDArrayAxis] = None,
    diff_method: str = "fd",
    mode: ModeSpec = "constant",
    gpu: bool = False,
    dtype: typ.Optional[pyct.DType] = None,
    parallel: bool = False,
    **diff_kwargs,
) -> pyct.OpT:
    r"""
    Laplacian operator.

    Notes
    -----

    The Laplacian of a :math:`D`-dimensional signal :math:`\mathbf{f} \in \mathbb{R}^{N_0 \times \cdots
    \times N_{D-1}}` is the sum of second-order partial derivatives across all input directions:

    .. math::

       \sum_{d = 0}^{D-1} \dfrac{ \partial^{2}\mathbf{f} }{ \partial x_{d}^{2} }

    The partial derivatives can be approximated by `finite differences
    <https://en.wikipedia.org/wiki/Finite_difference>`_ via the
    :py:meth:`~pycsou.operator.linop.diff.PartialDerivative.finite_difference` constructor or by the
    `Gaussian derivative <https://www.crisluengo.net/archives/22/>`_ via
    :py:meth:`~pycsou.operator.linop.diff.PartialDerivative.gaussian_derivative` constructor.
    The parametrization of the partial derivatives can be done via the keyword arguments
    `**diff_kwargs`, which will default to `scheme='central'` and `accuracy=2` for
    `diff_method='fd'` (finite difference), and the same values as the
    :py:class:`~pycsou.operator.linop.diff.PartialDerivative` constructor for `diff_method='gd'`
    (gaussian derivative).

    The Laplacian operator's method :py:meth:`~pycsou.operator.linop.diff.Laplacian.unravel` allows
    reshaping the vectorized output Laplacian to ``[..., N0, ..., ND]`` (see the example below).

    Parameters
    ----------
    arg_shape: pyct.NDArrayShape
        Shape of the input array.
    directions: pyct.Integer | (pyct.Integer, ..., pyct.Integer) | None
        Laplacian directions. Defaults to `None`, which computes the Laplacian with all directions.
    diff_method: str ['gd', 'fd']
        Method used to approximate the derivative.
        Must be one of:

        * 'fd' (default): finite differences
        * 'gd': Gaussian derivative
    mode: str | list(str)
        Boundary conditions.
        Multiple forms are accepted:

        * str: unique mode shared amongst dimensions.
          Must be one of:

          * 'constant' (default): zero-padding
          * 'wrap'
          * 'reflect'
          * 'symmetric'
          * 'edge'
        * tuple[str, ...]: the `d`-th dimension uses ``mode[d]`` as boundary condition.

        (See :py:func:`numpy.pad` for details.)
    gpu: bool
        Input NDArray type (`True` for GPU, `False` for CPU).
        Defaults to `False`.
    dtype: pyct.DType
        Working precision of the linear operator.
    parallel: bool
        If ``True``, use Dask to evaluate the different partial derivatives in parallel.
    diff_kwargs: dict
        Keyword arguments to parametrize partial derivatives (see
        :py:meth:`~pycsou.operator.linop.diff.PartialDerivative.finite_difference` and
        :py:meth:`~pycsou.operator.linop.diff.PartialDerivative.gaussian_derivative`)

    Returns
    -------
    op: :py:class:`~pycsou.abc.operator.LinOp`
        Laplacian

    Example
    -------

    .. plot::

       import numpy as np
       import matplotlib.pyplot as plt
       from pycsou.operator.linop.diff import Laplacian
       from pycsou.util.misc import peaks

       # Define input image
       n = 100
       x = np.linspace(-3, 3, n)
       xx, yy = np.meshgrid(x, x)
       image = peaks(xx, yy)

       arg_shape = image.shape  # (1000, 1000)
       # Compute Laplacian
       laplacian = Laplacian(arg_shape=arg_shape)
       output = laplacian(image.ravel())
       output = laplacian.unravel(output) # shape = (1, 1000, 1000)
       # Plot
       fig, axs = plt.subplots(1, 2, figsize=(10, 4))
       im = axs[0].imshow(image)
       plt.colorbar(im, ax=axs[0])
       axs[0].set_title("Image")
       axs[0].axis("off")

       im = axs[1].imshow(output.squeeze())
       plt.colorbar(im, ax=axs[1])
       axs[1].set_title(r"$\partial^{2} f/ \partial x^{2}+\partial^{2} f/ \partial y^{2}$")
       axs[1].axis("off")

       fig.show()

    See Also
    --------
    :py:class:`~pycsou.operator.linop.diff.PartialDerivative`,
    :py:class:`~pycsou.operator.linop.diff.Gradient`,
    :py:class:`~pycsou.operator.linop.diff.Hessian`.
    """
    ndims = len(arg_shape)
    directions = tuple([i for i in range(len(arg_shape))]) if directions is None else directions
    directions = [(i, i) for i in range(ndims) if i in directions]
    pds = Hessian(
        arg_shape=arg_shape,
        directions=directions,
        diff_method=diff_method,
        mode=mode,
        gpu=gpu,
        dtype=dtype,
        parallel=parallel,
        **diff_kwargs,
    )
    op = pyclr.Sum(arg_shape=(ndims,) + arg_shape, axis=0) * pds
    op._name = "Laplacian"
    return _make_unravelable(op, arg_shape)


def DirectionalDerivative(
    arg_shape: pyct.NDArrayShape,
    order: pyct.Integer,
    directions: typ.Union[pyct.NDArray, typ.Sequence[pyct.NDArray]],
    diff_method: str = "fd",
    mode: ModeSpec = "constant",
    parallel: bool = False,
    **diff_kwargs,
) -> pyct.OpT:
    r"""
    Directional derivative operator.

    Notes
    -----
    The **first-order** ``DirectionalDerivative`` of a :math:`D`-dimensional signal :math:`\mathbf{f} \in
    \mathbb{R}^{N_0 \times \cdots \times N_{D-1}}` applies a derivative along the direction specified by a constant
    unitary vector :math:`\mathbf{v} \in \mathbb{R}^D`:

    .. math::

        \boldsymbol{\nabla}_\mathbf{v} \mathbf{f} = \sum_{i=0}^{D-1} v_i \frac{\partial \mathbf{f}}{\partial x_i} \in
        \mathbb{R}^{N_0 \times \cdots \times N_{D-1}}

    or along spatially-varying directions :math:`\mathbf{v} = [\mathbf{v}_0, \ldots , \mathbf{v}_{D-1}]^\top \in
    \mathbb{R}^{D \times N_0 \times \cdots \times N_{D-1} }` where each direction :math:`\mathbf{v}_{\cdot, i_0, \ldots
    , i_{D-1}} \in \mathbb{R}^D` for any :math:`0 \leq i_d \leq N_d-1` with :math:`0 \leq d \leq D-1` is a unitary vector:

    .. math::

        \boldsymbol{\nabla}_\mathbf{v} \mathbf{f} = \sum_{i=0}^{D-1} \mathbf{v}_i \odot
        \frac{\partial \mathbf{f}}{\partial x_i} \in \mathbb{R}^{N_0 \times \cdots \times N_{D-1}},

    where :math:`\odot` denotes the Hadamard (elementwise) product.

    Note that choosing :math:`\mathbf{v}= \mathbf{e}_d \in \mathbb{R}^D` (the :math:`d`-th canonical basis vector)
    amounts to the first-order :py:func:`~pycsou.operator.linop.diff.PartialDerivative` operator applied along axis
    :math:`d`.

    The **second-order** ``DirectionalDerivative`` :math:`\boldsymbol{\nabla}^2_{\mathbf{v}_{1, 2}}` is obtained by composing the
    first-order directional derivatives :math:`\boldsymbol{\nabla}_{\mathbf{v}_{1}}` and
    :math:`\boldsymbol{\nabla}_{\mathbf{v}_{2}}`:

    .. math::

        \boldsymbol{\nabla}_{\mathbf{v}_{1}} (\boldsymbol{\nabla}_{\mathbf{v}_{2}} \mathbf{f}) =
        \boldsymbol{\nabla}_{\mathbf{v}_{1}} (\sum_{i=0}^{D-1} {v_{2}}_{i} \frac{\partial \mathbf{f}}{\partial x_i}) =
        \sum_{j=0}^{D-1} {v_{1}}_{j} \frac{\partial}{\partial x_j} (\sum_{i=0}^{D-1} {v_{2}}_{i} \frac{\partial \mathbf{f}}{\partial x_i}) =
        \sum_{i, j=0}^{D-1} {v_{1}}_{j} {v_{2}}_{i} \frac{\partial}{\partial x_j} \frac{\partial}{\partial x_i}\mathbf{f} =
        \mathbf{v}_{1}^{\top}\mathbf{H}\mathbf{f}\mathbf{v}_{2},

    where :math:`\mathbf{H}` is the discrete Hessian operator, implemented via
    :py:class:`~pycsou.operator.linop.diff.Hessian`.

    Higher-order ``DirectionalDerivative`` :math:`\boldsymbol{\nabla}^{N}_\mathbf{v}` can be obtained by composing the
    first-order directional derivative :math:`\boldsymbol{\nabla}_\mathbf{v}` :math:`N` times.

    The directional derivative operator's method :py:meth:`~pycsou.operator.linop.diff.DirectionalDerivative.unravel`
    allows reshaping the vectorized output directional derivative to ``[..., N0, ..., ND]`` (see the example below).

    .. warning::
        - :py:func:`~pycsou.operator.linop.diff.DirectionalDerivative` instances are **not array module-agnostic**: they
          will only work with NDArrays belonging to the same array module as ``directions``. Inner
          computations may recast input arrays when the precision of ``directions`` does not match the user-requested
          precision.
        - ``directions`` are always normalized to be unit vectors.

    Parameters
    ----------
    arg_shape: pyct.NDArrayShape
        Shape of the input array.
    order: pyct.Integer
        Which directional derivative (restricted to 1: First or 2: Second, see ``Notes``).
    directions: NDArray | tuple/list
        For ``order=1``, it can be a single direction (array of size :math:`(D,)`, where :math:`D` is the number of axes)
        or spatially-varying directions:

        * array of size :math:`(D, N_0 \times \ldots \times N_{D-1})` for ``order=1``, i.e., one direction per element
        in the gradient, and,

        * array of size :math:`(D * (D + 1) / 2, N_0 \times \ldots \times N_{D-1})` for ``order=2``, i.e., one direction
        per element in the Hessian.

        For ``order=2``, it can be a tuple/list of two single directions or spatially-varying dimensions of the same
        shape, which will compute :math:`\mathbf{v}_{1}^{\top}\mathbf{H}\mathbf{f}\mathbf{v}_{2}` or a single direction, as for
        ``order=1``, which will compute :math:`\mathbf{v}_{1}^{\top}\mathbf{H}\mathbf{f}\mathbf{v}_{1}`. Note that for
        ``order=2``, even though directions are spatially-varying, no differentiation is performed for this parameter.
    diff_method: str ['gd', 'fd']
        Method used to approximate the derivative. Must be one of:

        * 'fd' (default): finite differences
        * 'gd': Gaussian derivative
    mode: str | list(str)
        Boundary conditions.
        Multiple forms are accepted:

        * str: unique mode shared amongst dimensions.
          Must be one of:

          * 'constant' (default): zero-padding
          * 'wrap'
          * 'reflect'
          * 'symmetric'
          * 'edge'
        * tuple[str, ...]: the `d`-th dimension uses ``mode[d]`` as boundary condition.

        (See :py:func:`numpy.pad` for details.)
    parallel: bool
        If ``True``, use Dask to evaluate the different partial derivatives in parallel.
    diff_kwargs: dict
        Keyword arguments to parametrize partial derivatives (see
        :py:meth:`~pycsou.operator.linop.diff.PartialDerivative.finite_difference` and
        :py:meth:`~pycsou.operator.linop.diff.PartialDerivative.gaussian_derivative`)

    Returns
    -------
    op: :py:class:`~pycsou.abc.operator.LinOp`
            Directional derivative

    Example
    -------

    .. plot::

       import numpy as np
       import matplotlib.pyplot as plt
       from pycsou.operator.linop.diff import DirectionalDerivative
       from pycsou.util.misc import peaks

       x = np.linspace(-2.5, 2.5, 25)
       xx, yy = np.meshgrid(x, x)
       z = peaks(xx, yy)
       directions = np.zeros(shape=(2, z.size))
       directions[0, : z.size // 2] = 1
       directions[1, z.size // 2:] = 1
       dop = DirectionalDerivative(arg_shape=z.shape, order=1, directions=directions)
       out = dop.unravel(dop(z.ravel()))
       dop2 = DirectionalDerivative(arg_shape=z.shape, order=2, directions=directions)
       out2 = dop2.unravel(dop2(z.ravel()))
       fig, axs = plt.subplots(1, 3, figsize=(15, 5))
       axs = np.ravel(axs)
       h = axs[0].pcolormesh(xx, yy, z, shading="auto")
       axs[0].quiver(x, x, directions[1].reshape(xx.shape), directions[0].reshape(xx.shape))
       plt.colorbar(h, ax=axs[0])
       axs[0].set_title("Signal and directions of first derivatives")

       h = axs[1].pcolormesh(xx, yy, out.squeeze(), shading="auto")
       plt.colorbar(h, ax=axs[1])
       axs[1].set_title("First-order directional derivatives")

       h = axs[2].pcolormesh(xx, yy, out2.squeeze(), shading="auto")
       plt.colorbar(h, ax=axs[2])
       axs[2].set_title("Second-order directional derivative")

    See Also
    --------
    :py:func:`~pycsou.operator.linop.diff.Gradient`, :py:func:`~pycsou.operator.linop.diff.DirectionalGradient`
    """

    ndim = len(arg_shape)
    # For first directional derivative, ndim_diff == number of elements in gradient
    # For second directional derivative, ndim_diff == number of unique elements in Hessian
    ndim_diff = ndim if order == 1 else ndim * (ndim + 1) // 2

    assert order in [1, 2], "`order` should be either 1 or 2"

    # Ensure correct format of `directions`
    if order == 1:
        assert not isinstance(directions, cabc.Sequence), (
            "`directions` for first directional derivative should be an " "NDArray"
        )
        diff_op = Gradient
    else:
        if isinstance(directions, cabc.Sequence):
            error_msg = (
                "`directions` for second directional derivative should be an NDArray or a tuple/list with two "
                "NDArrays of the same shape"
            )
            assert len(directions) == 2, error_msg
            directions, directions2 = directions
            assert directions.shape == directions2.shape, error_msg
        else:
            directions2 = directions
        diff_op = Hessian

    assert directions.shape[0] == ndim, "The length of `directions` should match `len(arg_shape)`"

    xp = pycu.get_array_module(directions)
    gpu = xp == pycd.NDArrayInfo.CUPY.module()

    dtype = directions.dtype

    diff = diff_op(
        arg_shape=arg_shape,
        diff_method=diff_method,
        mode=mode,
        gpu=gpu,
        dtype=dtype,
        parallel=parallel,
        **diff_kwargs,
    )
    # normalize directions to unit norm
    norm_dirs = (directions / xp.linalg.norm(directions, axis=0, keepdims=True)).astype(dtype)

    if order == 1:
        op_name = "FirstDirectionalDerivative"
    else:  # order == 2
        op_name = "SecondDirectionalDerivative"
        # Compute directions' outer product (see Notes)

        norm_dirs2 = (directions2 / xp.linalg.norm(directions2, axis=0, keepdims=True)).astype(dtype)
        norm_dirs = norm_dirs[:, None, ...] * norm_dirs2[None, ...]
        # Multiply off-diag components x2 and use only triangular upper part of the outer product
        if ndim > 1:
            # (fancy nd indexing not supported in Dask)
            norm_dirs = norm_dirs.reshape(ndim**2, *norm_dirs.shape[2:])
            dummy_mat = np.arange(ndim**2).reshape(ndim, ndim)
            off_diag_inds = dummy_mat[np.triu_indices(ndim, k=1)].ravel()
            norm_dirs[off_diag_inds] *= 2
            inds = dummy_mat[np.triu_indices(ndim, k=0)].ravel()
            norm_dirs = norm_dirs[inds]
        else:
            norm_dirs = norm_dirs.ravel()

    if directions.ndim == 1:
        diag = xp.tile(norm_dirs, arg_shape + (1,)).transpose().ravel()

    else:
        diag = norm_dirs.ravel()

    dop = pyclb.DiagonalOp(diag)
    sop = pyclr.Sum(arg_shape=(ndim_diff,) + arg_shape, axis=0)
    op = sop * dop * diff

    dop_compute = pyclb.DiagonalOp(pycu.compute(diag))
    op_compute = sop * dop_compute * diff

    def op_svdvals(_, **kwargs) -> pyct.NDArray:
        return op_compute.svdvals(**kwargs)

    setattr(op, "svdvals", types.MethodType(op_svdvals, op))
    op._name = op_name
    return _make_unravelable(op, arg_shape=arg_shape)


def DirectionalGradient(
    arg_shape: pyct.NDArrayShape,
    directions: cabc.Sequence[pyct.NDArray],
    diff_method: str = "fd",
    mode: ModeSpec = "constant",
    parallel: bool = False,
    **diff_kwargs,
) -> pyct.OpT:
    r"""
    Directional gradient operator.

    Notes
    -----
    The ``DirectionalGradient`` of a :math:`D`-dimensional signal :math:`\mathbf{f} \in
    \mathbb{R}^{N_0 \times \cdots \times N_{D-1}}` stacks the directional derivatives of :math:`\mathbf{f}` along a list
    of :math:`m` directions :math:`\mathbf{v}_i` for :math:`1 \leq i \leq m`:

    .. math::

        \boldsymbol{\nabla}_{\mathbf{v}_1, \ldots ,\mathbf{v}_m} \mathbf{f} = \begin{bmatrix}
             \boldsymbol{\nabla}_{\mathbf{v}_1} \\
             \vdots\\
             \boldsymbol{\nabla}_{\mathbf{v}_m}\\
            \end{bmatrix} \mathbf{f} \in \mathbb{R}^{m \times N_0 \times \cdots \times N_{D-1}},

    where :math:`\boldsymbol{\nabla}_{\mathbf{v}_i}` is the first-order directional derivative along :math:`\mathbf{v}_i`
    implemented with :py:func:`~pycsou.operator.linop.diff.DirectionalDerivative`, with :math:`\mathbf{v}_i \in
    \mathbb{R}^D` or :math:`\mathbf{v}_i \in \mathbb{R}^{D \times N_0 \times \cdots \times N_{D-1}}`.

    Note that choosing :math:`m=D` and :math:`\mathbf{v}_i = \mathbf{e}_i \in \mathbb{R}^D` (the :math:`i`-th
    canonical basis vector) amounts to the :py:func:`~pycsou.operator.linop.diff.Gradient` operator.

    The directional gradient operator's method :py:meth:`~pycsou.operator.linop.diff.DirectionalGradient.unravel`
    allows reshaping the vectorized output directional derivative to ``[..., n_dirs, N0, ..., ND]`` (see the example
    below).

    Parameters
    ----------
    arg_shape: pyct.NDArrayShape
        Shape of the input array.
    directions: (pyct.NDArray, ..., pyct.NDArray)
        List of directions, either constant (array of size :math:`(D,)`) or spatially-varying (array of size
        :math:`(D, N_0, \ldots, N_{D-1})`)
    diff_method: str ['gd', 'fd']
        Method used to approximate the derivative. Must be one of:

        * 'fd' (default): finite differences
        * 'gd': Gaussian derivative
    mode: str | list(str)
        Boundary conditions.
        Multiple forms are accepted:

        * str: unique mode shared amongst dimensions.
          Must be one of:

          * 'constant' (default): zero-padding
          * 'wrap'
          * 'reflect'
          * 'symmetric'
          * 'edge'
        * tuple[str, ...]: the `d`-th dimension uses ``mode[d]`` as boundary condition.

        (See :py:func:`numpy.pad` for details.)
    parallel: bool
        If ``True``, use Dask to evaluate the different partial derivatives in parallel.
    diff_kwargs: dict
        Keyword arguments to parametrize partial derivatives (see
        :py:meth:`~pycsou.operator.linop.diff.PartialDerivative.finite_difference` and
        :py:meth:`~pycsou.operator.linop.diff.PartialDerivative.gaussian_derivative`)

    Returns
    -------
    op: :py:class:`~pycsou.abc.operator.LinOp`
            Directional gradient


    Example
    -------

    .. plot::

       import numpy as np
       import matplotlib.pyplot as plt
       from pycsou.operator.linop.diff import DirectionalGradient
       from pycsou.util.misc import peaks

       x = np.linspace(-2.5, 2.5, 25)
       xx, yy = np.meshgrid(x, x)
       z = peaks(xx, yy)
       directions1 = np.zeros(shape=(2, z.size))
       directions1[0, :z.size // 2] = 1
       directions1[1, z.size // 2:] = 1
       directions2 = np.zeros(shape=(2, z.size))
       directions2[1, :z.size // 2] = -1
       directions2[0, z.size // 2:] = -1
       arg_shape = z.shape
       dop = DirectionalGradient(arg_shape=arg_shape, directions=[directions1, directions2])
       out = dop.unravel(dop(z.ravel()))
       plt.figure()
       h = plt.pcolormesh(xx, yy, z, shading='auto')
       plt.quiver(x, x, directions1[1].reshape(arg_shape), directions1[0].reshape(xx.shape))
       plt.quiver(x, x, directions2[1].reshape(arg_shape), directions2[0].reshape(xx.shape), color='red')
       plt.colorbar(h)
       plt.title(r'Signal $\mathbf{f}$ and directions of derivatives')
       plt.figure()
       h = plt.pcolormesh(xx, yy, out[0], shading='auto')
       plt.colorbar(h)
       plt.title(r'$\nabla_{\mathbf{v}_0} \mathbf{f}$')
       plt.figure()
       h = plt.pcolormesh(xx, yy, out[1], shading='auto')
       plt.colorbar(h)
       plt.title(r'$\nabla_{\mathbf{v}_1} \mathbf{f}$')

    See Also
    --------
    :py:func:`~pycsou.operator.linop.diff.Gradient`, :py:func:`~pycsou.operator.linop.diff.DirectionalDerivative`
    """

    diag_ops = []
    diag_ops_compute = []

    ndim = len(arg_shape)
    assert isinstance(directions, cabc.Sequence)

    xp = pycu.get_array_module(directions[0])
    gpu = xp == pycd.NDArrayInfo.CUPY.module()
    dtype = directions[0].dtype

    grad = Gradient(
        arg_shape=arg_shape,
        diff_method=diff_method,
        mode=mode,
        gpu=gpu,
        dtype=dtype,
        parallel=parallel,
        **diff_kwargs,
    )
    sop = pyclr.Sum(
        arg_shape=(
            len(directions),
            ndim,
        )
        + arg_shape,
        axis=1,
    )
    for direction in directions:
        # normalize directions to unit norm
        norm_dirs = (direction / xp.linalg.norm(direction, axis=0, keepdims=True)).astype(dtype)
        if direction.ndim == 1:
            diag = xp.tile(norm_dirs, arg_shape + (1,)).transpose().ravel()
        else:
            diag = norm_dirs.ravel()

        diag_ops.append(pyclb.DiagonalOp(diag))
        diag_ops_compute.append(pyclb.DiagonalOp(pycu.compute(diag)))

    dop = pycb.vstack(diag_ops)
    dop_compute = pycb.vstack(diag_ops_compute)

    op = sop * dop * grad
    op_compute = sop * dop_compute * grad

    def op_svdvals(_, **kwargs) -> pyct.NDArray:
        return op_compute.svdvals(**kwargs)

    setattr(op, "svdvals", types.MethodType(op_svdvals, op))

    op._name = "DirectionalGradient"
    return _make_unravelable(op, arg_shape=arg_shape)


def DirectionalLaplacian(
    arg_shape: pyct.NDArrayShape,
    directions: cabc.Sequence[pyct.NDArray],
    weights: typ.Iterable = None,
    diff_method: str = "fd",
    mode: ModeSpec = "constant",
    parallel: bool = False,
    **diff_kwargs,
) -> pyct.OpT:
    r"""
    Directional Laplacian operator.

    Sum of the second directional derivatives of a multi-dimensional array (at least two dimensions are required)
    along multiple ``directions`` for each entry of the array.

    Notes
    -----

    The ``DirectionalLaplacian`` of a :math:`D`-dimensional signal :math:`\mathbf{f} \in
    \mathbb{R}^{N_0 \times \cdots \times N_{D-1}}` sums the second-order directional derivatives of :math:`\mathbf{f}`
    along a list of :math:`m` directions :math:`\mathbf{v}_i` for :math:`1 \leq i \leq m`:

    .. math::

        \boldsymbol{\Delta}_{\mathbf{v}_1, \ldots ,\mathbf{v}_m} \mathbf{f} = \sum_{i=1}^m
        \boldsymbol{\nabla}^2_{\mathbf{v}_i} \mathbf{f} \in \mathbb{R}^{N_0 \times \cdots \times N_{D-1}},

    where :math:`\boldsymbol{\nabla}^2_{\mathbf{v}_i}` is the second-order directional derivative along
    :math:`\mathbf{v}_i` implemented with :py:func:`~pycsou.operator.linop.diff.DirectionalDerivative`.

    Note that choosing :math:`m=D` and :math:`\mathbf{v}_i = \mathbf{e}_i \in \mathbb{R}^D` (the :math:`i`-th
    canonical basis vector) amounts to the :py:func:`~pycsou.operator.linop.diff.Laplacian` operator.

    The directional Laplacian operator's method :py:meth:`~pycsou.operator.linop.diff.DirectionalLaplacian.unravel`
    allows reshaping the vectorized output directional Laplacian to ``[..., N0, ..., ND]`` (see the example
    below).

    Parameters
    ----------
    arg_shape: pyct.NDArrayShape
        Shape of the input array.
    directions: (pyct.NDArray, ..., pyct.NDArray)
        List of directions, either constant (array of size :math:`(D,)`) or spatially-varying (array of size
        :math:`(D, N_0, \ldots, N_{D-1})`)
    weights: (pyct.Real, ..., pyct.Real) | None
        List of optional positive weights with which each second directional derivative operator is multiplied.
    diff_method: str ['gd', 'fd']
        Method used to approximate the derivative. Must be one of:

        * 'fd' (default): finite differences
        * 'gd': Gaussian derivative
    mode: str | list(str)
        Boundary conditions.
        Multiple forms are accepted:

        * str: unique mode shared amongst dimensions.
          Must be one of:

          * 'constant' (default): zero-padding
          * 'wrap'
          * 'reflect'
          * 'symmetric'
          * 'edge'
        * tuple[str, ...]: the `d`-th dimension uses ``mode[d]`` as boundary condition.

        (See :py:func:`numpy.pad` for details.)
    parallel: bool
        If ``True``, use Dask to evaluate the different partial derivatives in parallel.
    diff_kwargs: dict
        Keyword arguments to parametrize partial derivatives (see
        :py:meth:`~pycsou.operator.linop.diff.PartialDerivative.finite_difference` and
        :py:meth:`~pycsou.operator.linop.diff.PartialDerivative.gaussian_derivative`)

    Returns
    -------
    op: :py:class:`~pycsou.abc.operator.LinOp`
        Directional Laplacian

    Example
    -------

    .. plot::

       import numpy as np
       import matplotlib.pyplot as plt
       from pycsou.operator.linop.diff import DirectionalLaplacian
       from pycsou.util.misc import peaks

       x = np.linspace(-2.5, 2.5, 25)
       xx, yy = np.meshgrid(x, x)
       z = peaks(xx, yy)
       directions1 = np.zeros(shape=(2, z.size))
       directions1[0, :z.size // 2] = 1
       directions1[1, z.size // 2:] = 1
       directions2 = np.zeros(shape=(2, z.size))
       directions2[1, :z.size // 2] = -1
       directions2[0, z.size // 2:] = -1
       arg_shape = z.shape
       dop = DirectionalLaplacian(arg_shape=arg_shape, directions=[directions1, directions2])
       out = dop.unravel(dop(z.ravel()))
       plt.figure()
       h = plt.pcolormesh(xx, yy, z, shading='auto')
       plt.quiver(x, x, directions1[1].reshape(arg_shape), directions1[0].reshape(xx.shape))
       plt.quiver(x, x, directions2[1].reshape(arg_shape), directions2[0].reshape(xx.shape), color='red')
       plt.colorbar(h)
       plt.title('Signal and directions of derivatives')
       plt.figure()
       h = plt.pcolormesh(xx, yy, out.squeeze(), shading='auto')
       plt.colorbar(h)
       plt.title('Directional Laplacian')

    See Also
    --------
    :py:func:`~pycsou.operator.linop.diff.Laplacian`, :py:func:`~pycsou.operator.linop.diff.DirectionalDerivative`
    """
    assert isinstance(directions, cabc.Sequence)

    if weights is None:
        weights = [1.0] * len(directions)
    else:
        if len(weights) != len(directions):
            raise ValueError("The number of weights and directions provided differ.")

    xp = pycu.get_array_module(directions[0])
    gpu = xp == pycd.NDArrayInfo.CUPY.module()
    dtype = directions[0].dtype

    hess = Hessian(
        arg_shape=arg_shape,
        diff_method=diff_method,
        mode=mode,
        gpu=gpu,
        dtype=dtype,
        parallel=parallel,
        **diff_kwargs,
    )

    ndim = len(arg_shape)
    ndim_diff = ndim * (ndim + 1) // 2
    dop = []
    dop_compute = []
    for i, (weight, direction) in enumerate(zip(weights, directions)):
        # normalize directions to unit norm
        norm_dirs = (direction / xp.linalg.norm(direction, axis=0, keepdims=True)).astype(dtype)
        norm_dirs = norm_dirs[:, None, ...] * norm_dirs[None, ...]
        # Multiply off-diag components x2 and use only triangular upper part of the outer product
        if ndim > 1:
            # (fancy nd indexing not supported in Dask)
            norm_dirs = norm_dirs.reshape(ndim**2, *norm_dirs.shape[2:])
            dummy_mat = np.arange(ndim**2).reshape(ndim, ndim)
            off_diag_inds = dummy_mat[np.triu_indices(ndim, k=1)].ravel()
            norm_dirs[off_diag_inds] *= 2
            inds = dummy_mat[np.triu_indices(ndim, k=0)].ravel()
            norm_dirs = norm_dirs[inds]
        else:
            norm_dirs = norm_dirs.ravel()

        if direction.ndim == 1:
            dop.append(pyclb.DiagonalOp(weight * xp.tile(norm_dirs, arg_shape + (1,)).transpose().ravel()))
            dop_compute.append(
                pyclb.DiagonalOp(pycu.compute(weight * xp.tile(norm_dirs, arg_shape + (1,)).transpose().ravel()))
            )
        else:
            dop.append(pyclb.DiagonalOp(weight * norm_dirs.ravel()))
            dop_compute.append(pyclb.DiagonalOp(pycu.compute(weight * norm_dirs.ravel())))

    dop = pycb.vstack(dop)
    dop_compute = pycb.vstack(dop_compute)
    sop = pyclr.Sum(
        arg_shape=(
            len(directions),
            ndim_diff,
        )
        + arg_shape,
        axis=(0, 1),
    )
    op = sop * dop * hess
    op_compute = sop * dop_compute * hess

    def op_svdvals(_, **kwargs) -> pyct.NDArray:
        return op_compute.svdvals(**kwargs)

    setattr(op, "svdvals", types.MethodType(op_svdvals, op))

    op._name = "DirectionalLaplacian"
    return _make_unravelable(op, arg_shape=arg_shape)


def DirectionalHessian(
    arg_shape: pyct.NDArrayShape,
    directions: cabc.Sequence[pyct.NDArray],
    diff_method="gd",
    mode: ModeSpec = "constant",
    parallel: bool = False,
    **diff_kwargs,
) -> pyct.OpT:
    r"""
    Directional Hessian operator.

    Notes
    -----

    The ``DirectionalHessian`` of a :math:`D`-dimensional signal :math:`\mathbf{f} \in
    \mathbb{R}^{N_0 \times \cdots \times N_{D-1}}` stacks the second-order directional derivatives of :math:`\mathbf{f}`
    along a list of :math:`m` directions :math:`\mathbf{v}_i` for :math:`1 \leq i \leq m`:

    .. math::

        \mathbf{H}_{\mathbf{v}_1, \ldots ,\mathbf{v}_m} \mathbf{f} = \begin{bmatrix}
         \boldsymbol{\nabla}^2_{\mathbf{v}_0} & \cdots & \boldsymbol{\nabla}_{\mathbf{v}_0} \boldsymbol{\nabla}_{\mathbf{v}_{m-1}} \\
        \vdots & \ddots & \vdots \\
        \boldsymbol{\nabla}_{\mathbf{v}_{m-1}} \boldsymbol{\nabla}_{\mathbf{v}_0} & \cdots & \boldsymbol{\nabla}^2_{\mathbf{v}_{m-1}}
        \end{bmatrix} \mathbf{f},

    where :math:`\boldsymbol{\nabla}_{\mathbf{v}_i}` is the first-order directional derivative along
    :math:`\mathbf{v}_i` implemented with :py:func:`~pycsou.operator.linop.diff.DirectionalDerivative`.

    However, due to the symmetry of the Hessian, only the upper triangular part is computed in practice:

    .. math::

        \mathbf{H}_{\mathbf{v}_1, \ldots ,\mathbf{v}_m} \mathbf{f} = \begin{bmatrix}
        \boldsymbol{\nabla}^2_{\mathbf{v}_0}\\
        \boldsymbol{\nabla}_{\mathbf{v}_0} \boldsymbol{\nabla}_{\mathbf{v}_{1}} \\
        \vdots \\
        \boldsymbol{\nabla}^2_{\mathbf{v}_{m-1}}
        \end{bmatrix} \mathbf{f} \in \mathbb{R}^{\frac{m (m-1)}{2} \times N_0 \times \cdots \times N_{D-1}}

    Note that choosing :math:`m=D` and :math:`\mathbf{v}_i = \mathbf{e}_i \in \mathbb{R}^D` (the :math:`i`-th
    canonical basis vector) amounts to the :py:func:`~pycsou.operator.linop.diff.Hessian` operator.

    The directional Hessian operator's method :py:meth:`~pycsou.operator.linop.diff.DirectionalHessian.unravel`
    allows reshaping the vectorized output directional Hessian to ``[..., n_dirs, N0, ..., ND]`` (see the example
    below).

    Parameters
    ----------
    arg_shape: pyct.NDArrayShape
        Shape of the input array.
    directions: (pyct.NDArray, ..., pyct.NDArray)
        List of directions, either constant (array of size :math:`(D,)`) or spatially-varying (array of size
        :math:`(D, N_0, \ldots, N_{D-1})`)
    diff_method: str ['gd', 'fd']
        Method used to approximate the derivative. Must be one of:

        * 'fd' (default): finite differences
        * 'gd': Gaussian derivative
    mode: str | list(str)
        Boundary conditions.
        Multiple forms are accepted:

        * str: unique mode shared amongst dimensions.
          Must be one of:

          * 'constant' (default): zero-padding
          * 'wrap'
          * 'reflect'
          * 'symmetric'
          * 'edge'
        * tuple[str, ...]: the `d`-th dimension uses ``mode[d]`` as boundary condition.

        (See :py:func:`numpy.pad` for details.)
    parallel: bool
        If ``True``, use Dask to evaluate the different partial derivatives in parallel.
    diff_kwargs: dict
        Keyword arguments to parametrize partial derivatives (see
        :py:meth:`~pycsou.operator.linop.diff.PartialDerivative.finite_difference` and
        :py:meth:`~pycsou.operator.linop.diff.PartialDerivative.gaussian_derivative`)

    Returns
    -------
    op: :py:class:`~pycsou.abc.operator.LinOp`
            Directional Hessian

    Example
    -------

    .. plot::

       import numpy as np
       import matplotlib.pyplot as plt
       from pycsou.operator.linop.diff import DirectionalHessian
       from pycsou.util.misc import peaks

       x = np.linspace(-2.5, 2.5, 25)
       xx, yy = np.meshgrid(x, x)
       z = peaks(xx, yy)
       directions1 = np.zeros(shape=(2, z.size))
       directions1[0, :z.size // 2] = 1
       directions1[1, z.size // 2:] = 1
       directions2 = np.zeros(shape=(2, z.size))
       directions2[1, :z.size // 2] = -1
       directions2[0, z.size // 2:] = -1
       arg_shape = z.shape
       d_hess = DirectionalHessian(arg_shape=arg_shape, directions=[directions1, directions2])
       out = d_hess.unravel(d_hess(z.ravel()))
       plt.figure()
       h = plt.pcolormesh(xx, yy, z, shading='auto')
       plt.quiver(x, x, directions1[1].reshape(arg_shape), directions1[0].reshape(xx.shape))
       plt.quiver(x, x, directions2[1].reshape(arg_shape), directions2[0].reshape(xx.shape), color='red')
       plt.colorbar(h)
       plt.title(r'Signal $\mathbf{f}$ and directions of derivatives')
       plt.figure()
       h = plt.pcolormesh(xx, yy, out[0], shading='auto')
       plt.colorbar(h)
       plt.title(r'$\nabla^2_{\mathbf{v}_0} \mathbf{f}$')
       plt.figure()
       h = plt.pcolormesh(xx, yy, out[1], shading='auto')
       plt.colorbar(h)
       plt.title(r'$\nabla_{\mathbf{v}_0} \nabla_{\mathbf{v}_{1}} \mathbf{f}$')
       plt.figure()
       h = plt.pcolormesh(xx, yy, out[2], shading='auto')
       plt.colorbar(h)
       plt.title(r'$\nabla^2_{\mathbf{v}_1} \mathbf{f}$')

    See Also
    --------
    :py:func:`~pycsou.operator.linop.diff.Hessian`, :py:func:`~pycsou.operator.linop.diff.DirectionalDerivative`
    """

    # dir_deriv = []
    # kwargs = {
    #     "arg_shape": arg_shape,
    #     "diff_method": diff_method,
    #     "mode": mode,
    #     "parallel": parallel,
    #     **diff_kwargs,
    # }
    # for i, dir1 in enumerate(directions):
    #     for dir2 in directions[i:]:
    #         dir_deriv.append(DirectionalDerivative(order=2, directions=(dir1, dir2), **kwargs))
    #
    # op = pycb.vstack(dir_deriv)

    assert isinstance(directions, cabc.Sequence)

    xp = pycu.get_array_module(directions[0])
    gpu = xp == pycd.NDArrayInfo.CUPY.module()
    dtype = directions[0].dtype

    hess = Hessian(
        arg_shape=arg_shape,
        diff_method=diff_method,
        mode=mode,
        gpu=gpu,
        dtype=dtype,
        parallel=parallel,
        **diff_kwargs,
    )

    ndim = len(arg_shape)
    ndim_diff = ndim * (ndim + 1) // 2
    dop = []
    dop_compute = []
    for i1, direction1 in enumerate(directions):
        norm_dirs1 = (direction1 / xp.linalg.norm(direction1, axis=0, keepdims=True)).astype(dtype)
        for i2, direction2 in enumerate(directions[i1:]):
            norm_dirs2 = (direction2 / xp.linalg.norm(direction2, axis=0, keepdims=True)).astype(dtype)
            norm_dirs = norm_dirs1[:, None, ...] * norm_dirs2[None, ...]

            # Multiply off-diag components x2 and use only triangular upper part of the outer product
            if ndim > 1:
                # (fancy nd indexing not supported in Dask)
                norm_dirs = norm_dirs.reshape(ndim**2, *norm_dirs.shape[2:])
                dummy_mat = np.arange(ndim**2).reshape(ndim, ndim)
                off_diag_inds = dummy_mat[np.triu_indices(ndim, k=1)].ravel()
                norm_dirs[off_diag_inds] *= 2
                inds = dummy_mat[np.triu_indices(ndim, k=0)].ravel()
                norm_dirs = norm_dirs[inds]
            else:
                norm_dirs = norm_dirs.ravel()

            if norm_dirs.ndim == 1:
                dop.append(pyclb.DiagonalOp(xp.tile(norm_dirs, arg_shape + (1,)).transpose().ravel()))
                dop_compute.append(
                    pyclb.DiagonalOp(pycu.compute(xp.tile(norm_dirs, arg_shape + (1,)).transpose().ravel()))
                )
            else:
                dop.append(pyclb.DiagonalOp(norm_dirs.ravel()))
                dop_compute.append(pyclb.DiagonalOp(pycu.compute(norm_dirs.ravel())))

    dop = pycb.vstack(dop)
    dop_compute = pycb.vstack(dop_compute)
    ndim_hess = len(directions) * (len(directions) + 1) // 2
    sop = pyclr.Sum(
        arg_shape=(
            ndim_hess,
            ndim_diff,
        )
        + arg_shape,
        axis=1,
    )
    op = sop * dop * hess
    op_compute = sop * dop_compute * hess

    def op_svdvals(_, **kwargs) -> pyct.NDArray:
        return op_compute.svdvals(**kwargs)

    setattr(op, "svdvals", types.MethodType(op_svdvals, op))

    op._name = "DirectionalHessian"
    return _make_unravelable(op, arg_shape=arg_shape)
