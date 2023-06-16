import collections.abc as cabc
import itertools

import pycsou.abc.operator as pyco
import pycsou.operator.linop.base as pyclb
import pycsou.operator.linop.diff as pycld
import pycsou.operator.linop.pad as pyclp
import pycsou.operator.linop.stencil as pycls
import pycsou.runtime as pycrt
import pycsou.util as pycu
import pycsou.util.deps as pycd
import pycsou.util.ptype as pyct

try:
    import scipy.ndimage._filters as scif
except ImportError:
    import scipy.ndimage.filters as scif

import functools
import typing as typ

import numpy as np

IndexSpec = cabc.Sequence[pyct.Integer]
KernelSpec = pycls.Stencil.KernelSpec
ModeSpec = pyclp.Pad.ModeSpec


__all__ = [
    "MovingAverage",
    "Gaussian",
    "DifferenceOfGaussians",
    "DoG",
    "Laplace",
    "Sobel",
    "Prewitt",
    "Scharr",
    "StructureTensor",
]


def _to_canonical_form(_, arg_shape):
    if not isinstance(_, cabc.Sequence):
        _ = (_,) * len(arg_shape)
    else:
        assert len(_) == len(arg_shape)
    return _


def _get_axes(axis, ndim):
    if axis is None:
        axes = list(range(ndim))
    elif np.isscalar(axis):
        axes = [axis]
    else:
        axes = axis
    return axes


def _sanitize_inputs(arg_shape, dtype, gpu):

    ndim = len(arg_shape)

    if dtype is None:
        dtype = pycrt.getPrecision().value

    if gpu:
        assert pycd.CUPY_ENABLED
        import cupy as xp
    else:
        import numpy as xp
    return ndim, dtype, xp


def MovingAverage(
    arg_shape: pyct.NDArrayShape,
    size: typ.Union[typ.Tuple, pyct.Integer],
    center: typ.Optional[IndexSpec] = None,
    mode: ModeSpec = "constant",
    gpu: bool = False,
    dtype: typ.Optional[pyct.DType] = None,
):
    r"""
    Multidimensional moving average or uniform filter.

    Notes
    -----
    This operator performs a convolution between the input :math:`D`-dimensional NDArray
    :math:`\mathbf{x} \in \mathbb{R}^{N_0 \times \cdots \times N_{D-1}}` and a uniform
    :math:`D`-dimensional filter :math:`\mathbf{h} \in \mathbb{R}^{\text{size} \times \cdots \times \text{size}}` that
    computes the `size`-point local mean values using separable kernels for improved performance.

    .. math::

        y_{i} = \frac{1}{|\mathcal{N}_{i}|}\sum_{j \in \mathcal{N}_{i}} x_{j}

    Where :math:`\mathcal{N}_{i}` is the set of elements neighbouring the :math:`i`-th element of the input array, and
    :math:`\mathcal{N}_{i}|` denotes its cardinality, i.e. the total number of neighbors.


    Parameters
    ----------
    arg_shape: tuple
        Shape of the input array.
    size: int | tuple
        Size of the moving average kernel. If a single integer value is provided, then the moving average filter will
        have as many dimensions as the input array. If a tuple is provided, it should contain as many elements as
        ``arg_shape``. For example, the ``size=(1, 3)`` will convolve the input image with the filter
        ``[[1, 1, 1]] / 3``.

    center: IndexSpec
        (i_1, ..., i_D) index of the kernel's center. ``center`` defines how a kernel is overlaid on inputs to produce
        outputs. For odd `size`s, it defaults to the central element (``center=size//2``). For even ``size``s the desired
        center indices must be provided.

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
        * tuple[str, ...]: the `d`-th dimension uses `mode[d]` as boundary condition.

        (See :py:func:`numpy.pad` for details.)
    gpu: bool
        Input NDArray type (`True` for GPU, `False` for CPU). Defaults to `False`.
    dtype: pyct.DType
        Working precision of the linear operator.

    Returns
    -------
    op: :py:class:`~pycsou.abc.operator.LinOp`
        MovingAverage

    Example
    -------

    .. plot::

       import matplotlib.pyplot as plt

       arg_shape = (11, 11)
       image = np.zeros(arg_shape)
       image[5, 5] = 1.

       ma = MovingAverage(arg_shape, size=5)
       out = ma(image.ravel())
       plt.figure(figsize=(10, 5))
       plt.subplot(121)
       plt.imshow(image)
       plt.colorbar()
       plt.subplot(122)
       plt.imshow(out.reshape(*arg_shape))
       plt.colorbar()

    See Also
    --------
    :py:class:`~pycsou.operator.linop.filter.Gaussian`
    """
    size = _to_canonical_form(size, arg_shape)
    if center is None:
        assert all([s % 2 == 1 for s in size]), (
            "Can only infer center for odd `size`s. For even `size`s, please " "provide the desired `center`s."
        )
        center = [s // 2 for s in size]

    ndim, dtype, xp = _sanitize_inputs(arg_shape, dtype, gpu)

    kernel = [xp.ones(s, dtype=dtype) for s in size]  # use separable filters
    scale = 1 / np.prod(size)

    op = scale * pycls.Stencil(arg_shape=arg_shape, kernel=kernel, center=center, mode=mode)
    op._name = "MovingAverage"
    return op


def Gaussian(
    arg_shape: pyct.NDArrayShape,
    sigma: typ.Union[typ.Tuple[pyct.Real], pyct.Real] = 1.0,
    truncate: typ.Union[typ.Tuple[pyct.Real], pyct.Real] = 3.0,
    order: typ.Union[typ.Tuple[pyct.Integer], pyct.Integer] = 0,
    mode: ModeSpec = "constant",
    sampling: typ.Union[pyct.Real, cabc.Sequence[pyct.Real, ...]] = 1,
    gpu: bool = False,
    dtype: typ.Optional[pyct.DType] = None,
):
    r"""
    Multidimensional Gaussian filter.

    Notes
    -----
    This operator performs a convolution between the input :math:`D`-dimensional NDArray
    :math:`\mathbf{x} \in \mathbb{R}^{N_0 \times \cdots \times N_{D-1}}` and a Gaussian
    :math:`D`-dimensional filter :math:`\mathbf{h} \in \mathbb{R}^{\text{size} \times \cdots \times \text{size}}`
    using separable kernels for improved performance.

    .. math::

        y_{i} = \sum_{j \in \mathcal{N}_{i}} a_{j} x_{j} \exp(\frac{d_{ij}^{2}}{\sigma^{2}})

    Where :math:`\mathcal{N}_{i}` is the set of elements neighbouring the :math:`i`-th element of the input array, and
    :math:`a_{j} = \sum_{j \in \mathcal{N}_{i}} a_{j} \exp(\frac{d_{ij}^{2}}{\sigma^{2}})` normalizes the kernel to
    sum to one.


    Parameters
    ----------
    arg_shape: tuple
        Shape of the input array.
    sigma: float | tuple
        Standard deviation of the Gaussian kernel.  If a scalar value is provided, then the Gaussian filter will
        have as many dimensions as the input array. If a tuple is provided, it should contain as many elements as
        ``arg_shape``. Use ``0`` to prevent filtering in a given dimension. For example, the ``sigma=(0, 3)`` will
        convolve the input image in its last dimension.
    truncate: float | tuple
        Truncate the filter at this many standard deviations.
        Defaults to 3.0.
    order: int | tuple
        Gaussian derivative order. Use ``0`` for the standard Gaussian kernel.
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
        * tuple[str, ...]: the `d`-th dimension uses `mode[d]` as boundary condition.

        (See :py:func:`numpy.pad` for details.)
    sampling: pyct.Real | (pyct.Real, ..., pyct.Real)
            Sampling step (i.e. distance between two consecutive elements of an array).
            Defaults to 1.
    gpu: bool
        Input NDArray type (`True` for GPU, `False` for CPU). Defaults to `False`.
    dtype: pyct.DType
        Working precision of the linear operator.

    Returns
    -------
    op: :py:class:`~pycsou.abc.operator.LinOp`
        Gaussian

    Example
    -------

    .. plot::

       import matplotlib.pyplot as plt

       arg_shape = (11, 11)
       image = np.zeros(arg_shape)
       image[5, 5] = 1.

       gaussian = Gaussian(arg_shape, sigma=3)
       out = gaussian(image.ravel())
       plt.figure(figsize=(10, 5))
       plt.subplot(121)
       plt.imshow(image)
       plt.colorbar()
       plt.subplot(122)
       plt.imshow(out.reshape(*arg_shape))
       plt.colorbar()

    See Also
    --------
    :py:class:`~pycsou.operator.linop.filter.MovingAverage`,
    :py:class:`~pycsou.operator.linop.filter.DifferenceOfGaussians`
    """

    ndim, dtype, xp = _sanitize_inputs(arg_shape, dtype, gpu)
    sigma = _to_canonical_form(sigma, arg_shape)
    truncate = _to_canonical_form(truncate, arg_shape)
    order = _to_canonical_form(order, arg_shape)
    sampling = _to_canonical_form(sampling, arg_shape)

    kernel = [
        xp.array([1], dtype=dtype),
    ] * len(arg_shape)
    center = [0 for _ in range(len(arg_shape))]

    for i, (sigma_, truncate_, order_, sampling_) in enumerate(zip(sigma, truncate, order, sampling)):
        if sigma_:
            sigma_pix = sigma_ / sampling_
            radius = int(truncate_ * float(sigma_pix) + 0.5)
            kernel[i] = xp.asarray(np.flip(scif._gaussian_kernel1d(sigma_pix, order_, radius)), dtype=dtype)
            kernel[i] /= sampling_**order_
            center[i] = radius

    op = pycls.Stencil(arg_shape=arg_shape, kernel=kernel, center=center, mode=mode)
    op._name = "Gaussian"
    return op


def DifferenceOfGaussians(
    arg_shape: pyct.NDArrayShape,
    low_sigma=1.0,
    high_sigma=None,
    low_truncate=3.0,
    high_truncate=3.0,
    mode: ModeSpec = "constant",
    sampling: typ.Union[pyct.Real, cabc.Sequence[pyct.Real, ...]] = 1,
    gpu: bool = False,
    dtype: typ.Optional[pyct.DType] = None,
):
    r"""
    Multidimensional Difference of Gaussians filter.

    Notes
    -----

    This operator uses the Difference of Gaussians (DoG) method to a :math:`D`-dimensional NDArray
    :math:`\mathbf{x} \in \mathbb{R}^{N_0 \times \cdots \times N_{D-1}}` using separable kernels for improved
    performance. The DoG method blurs the input image with two Gaussian kernels with different sigma, and
    subtracts the more-blurred signal from the less-blurred image. This creates an output signal containing only the
    information from the original signal at the spatial scale indicated by the two sigmas.

    Parameters
    ----------
    arg_shape: tuple
        Shape of the input array.
    low_sigma: float | tuple
        Standard deviation of the Gaussian kernel with smaller sigmas across all axes. If a scalar value is provided,
        then the Gaussian filter will have as many dimensions as the input array. If a tuple is provided, it should
        contain as many elements as ``arg_shape``. Use ``0`` to prevent filtering in a given dimension. For example, the
        ``low_sigma=(0, 3)`` will convolve the input image in its last dimension.
    high_sigma: float | tuple | None
        Standard deviation of the Gaussian kernel with larger sigmas across all axes. If ``None`` is given (default),
        sigmas for all axes are calculated as ``1.6 * low_sigma``.
    low_truncate: float | tuple
        Truncate the filter at this many standard deviations.
        Defaults to 3.0.
    high_truncate: float | tuple
        Truncate the filter at this many standard deviations.
        Defaults to 3.0.
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
        * tuple[str, ...]: the `d`-th dimension uses `mode[d]` as boundary condition.

        (See :py:func:`numpy.pad` for details.)
    sampling: pyct.Real | (pyct.Real, ..., pyct.Real)
            Sampling step (i.e. distance between two consecutive elements of an array).
            Defaults to 1.
    gpu: bool
        Input NDArray type (`True` for GPU, `False` for CPU). Defaults to `False`.
    dtype: pyct.DType
        Working precision of the linear operator.

    Returns
    -------
    op: :py:class:`~pycsou.abc.operator.LinOp`
        DifferenceOfGaussians

    Example
    -------

    .. plot::

       import matplotlib.pyplot as plt

       arg_shape = (11, 11)
       image = np.zeros(arg_shape)
       image[5, 5] = 1.

       dog = DoG(arg_shape, low_sigma=3)
       out = dog(image.ravel())
       plt.figure(figsize=(10, 5))
       plt.subplot(121)
       plt.imshow(image)
       plt.colorbar()
       plt.subplot(122)
       plt.imshow(out.reshape(*arg_shape))
       plt.colorbar()

    See Also
    --------
    :py:class:`~pycsou.operator.linop.filter.Gaussian`, :py:class:`~pycsou.operator.linop.filter.Sobel`,
    :py:class:`~pycsou.operator.linop.filter.Prewitt`, :py:class:`~pycsou.operator.linop.filter.Scharr`,
    :py:class:`~pycsou.operator.linop.filter.StructureTensor`
    """

    low_sigma = _to_canonical_form(low_sigma, arg_shape)
    if high_sigma is None:
        high_sigma = (s * 1.6 for s in low_sigma)

    high_sigma = _to_canonical_form(high_sigma, arg_shape)
    low_truncate = _to_canonical_form(low_truncate, arg_shape)
    high_truncate = _to_canonical_form(high_truncate, arg_shape)

    kwargs = {
        "arg_shape": arg_shape,
        "order": 0,
        "mode": mode,
        "gpu": gpu,
        "dtype": dtype,
        "sampling": sampling,
    }
    op_low = Gaussian(sigma=low_sigma, truncate=low_truncate, **kwargs)
    op_high = Gaussian(sigma=high_sigma, truncate=high_truncate, **kwargs)
    op = op_low - op_high
    op._name = "DifferenceOfGaussians"
    return op


DoG = DifferenceOfGaussians


def Laplace(
    arg_shape: pyct.NDArrayShape,
    mode: ModeSpec = "constant",
    sampling: typ.Union[pyct.Real, cabc.Sequence[pyct.Real, ...]] = 1,
    gpu: bool = False,
    dtype: typ.Optional[pyct.DType] = None,
):
    r"""
    Multidimensional Laplace filter based on second derivatives approximated via finite differences.

    Notes
    -----

    This operator uses the applies the Laplace kernel :math:`[1 -2 1]`
    to a :math:`D`-dimensional NDArray :math:`\mathbf{x} \in \mathbb{R}^{N_0 \times \cdots \times N_{D-1}}` using
    separable kernels for improved performance. The Laplace filter is commonly used to find high-frequency components in
    the signal, such as for example, the edges in an image.

    Parameters
    ----------
    arg_shape: tuple
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
        * tuple[str, ...]: the `d`-th dimension uses `mode[d]` as boundary condition.

        (See :py:func:`numpy.pad` for details.)
    sampling: pyct.Real | (pyct.Real, ..., pyct.Real)
            Sampling step (i.e. distance between two consecutive elements of an array).
            Defaults to 1.
    gpu: bool
        Input NDArray type (`True` for GPU, `False` for CPU). Defaults to `False`.
    dtype: pyct.DType
        Working precision of the linear operator.

    Returns
    -------
    op: :py:class:`~pycsou.abc.operator.LinOp`
        DifferenceOfGaussians

    Example
    -------

    .. plot::

       import matplotlib.pyplot as plt

       arg_shape = (11, 11)
       image = np.zeros(arg_shape)
       image[5, 5] = 1.

       laplace = Laplace(arg_shape)
       out = laplace(image.ravel())
       plt.figure(figsize=(10, 5))
       plt.subplot(121)
       plt.imshow(image)
       plt.colorbar()
       plt.subplot(122)
       plt.imshow(out.reshape(*arg_shape))
       plt.colorbar()

    See Also
    --------
    :py:class:`~pycsou.operator.linop.filter.Sobel`,
    :py:class:`~pycsou.operator.linop.filter.Prewitt`, :py:class:`~pycsou.operator.linop.filter.Scharr`,
    """

    ndim, dtype, xp = _sanitize_inputs(arg_shape, dtype, gpu)
    sampling = _to_canonical_form(sampling, arg_shape)
    centers = [[1 if i == dim else 0 for i in range(ndim)] for dim in range(ndim)]
    kernels = [
        xp.array([1.0, -2.0, 1.0]).reshape([-1 if i == dim else 1 for i in range(ndim)]) / sampling[dim]
        for dim in range(ndim)
    ]
    ops = [pycls.Stencil(arg_shape=arg_shape, kernel=k, center=c, mode=mode) for (k, c) in zip(kernels, centers)]
    op = functools.reduce(lambda x, y: x + y, ops)
    op._name = "Laplace"
    return op


def Sobel(
    arg_shape: pyct.NDArrayShape,
    axis: typ.Optional[typ.Tuple] = None,
    mode: ModeSpec = "constant",
    sampling: typ.Union[pyct.Real, cabc.Sequence[pyct.Real, ...]] = 1,
    gpu: bool = False,
    dtype: typ.Optional[pyct.DType] = None,
):
    r"""
    Multidimensional Sobel filter.

    Notes
    -----

    This operator uses the applies the multi-dimensional Sobel filter to a :math:`D`-dimensional NDArray
    :math:`\mathbf{x} \in \mathbb{R}^{N_0 \times \cdots \times N_{D-1}}` using separable kernels for improved
    performance.  The Sobel filter applies the following edge filter in the dimensions of interest:
        ``[1, 0, -1]``,
    and the smoothing filter on the rest of dimensions:
        ``[1, 2, 1] / 4``.
    The Sobel filter is commonly used to find high-frequency components in the signal, such as
    for example, the edges in an image.

    Parameters
    ----------
    arg_shape: tuple
        Shape of the input array.
    axis: int | tuple
        Compute the edge filter along this axis. If not provided, the edge magnitude is computed. This is defined as:

        ``np.sqrt(sum([sobel(array, axis=i)**2 for i in range(array.ndim)]) / array.ndim)``
        The magnitude is also computed if axis is a sequence.

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
        * tuple[str, ...]: the `d`-th dimension uses `mode[d]` as boundary condition.

        (See :py:func:`numpy.pad` for details.)
    sampling: pyct.Real | (pyct.Real, ..., pyct.Real)
            Sampling step (i.e. distance between two consecutive elements of an array).
            Defaults to 1.
    gpu: bool
        Input NDArray type (`True` for GPU, `False` for CPU). Defaults to `False`.
    dtype: pyct.DType
        Working precision of the linear operator.

    Returns
    -------
    op: :py:class:`~pycsou.abc.operator.LinOp`
        Sobel

    Example
    -------

    .. plot::

       import matplotlib.pyplot as plt

       arg_shape = (11, 11)
       image = np.zeros(arg_shape)
       image[5, 5] = 1.

       sobel = Sobel(arg_shape)
       out = sobel(image.ravel())
       plt.figure(figsize=(10, 5))
       plt.subplot(121)
       plt.imshow(image)
       plt.colorbar()
       plt.subplot(122)
       plt.imshow(out.reshape(*arg_shape))
       plt.colorbar()

    See Also
    --------
    :py:class:`~pycsou.operator.linop.filter.Prewitt`, :py:class:`~pycsou.operator.linop.filter.Scharr`,
    """
    smooth_kernel = np.array([1, 2, 1]) / 4
    return _EdgeFilter(
        arg_shape=arg_shape,
        smooth_kernel=smooth_kernel,
        filter_name="SobelFilter",
        axis=axis,
        mode=mode,
        sampling=sampling,
        gpu=gpu,
        dtype=dtype,
    )


def Prewitt(
    arg_shape: pyct.NDArrayShape,
    axis: typ.Optional[typ.Tuple] = None,
    mode: ModeSpec = "constant",
    sampling: typ.Union[pyct.Real, cabc.Sequence[pyct.Real, ...]] = 1,
    gpu: bool = False,
    dtype: typ.Optional[pyct.DType] = None,
):
    r"""
    Multidimensional Prewitt filter.

    Notes
    -----

    This operator uses the applies the multi-dimensional Prewitt filter to a :math:`D`-dimensional NDArray
    :math:`\mathbf{x} \in \mathbb{R}^{N_0 \times \cdots \times N_{D-1}}` using separable kernels for improved
    performance.  The Prewitt filter applies the following edge filter in the dimensions of interest:
        ``[1, 0, -1]``,
    and the smoothing filter on the rest of dimensions:
        ``[1, 1, 1] / 3``.
    The Prewitt filter is commonly used to find high-frequency components in the signal, such as
    for example, the edges in an image.

    Parameters
    ----------
    arg_shape: tuple
        Shape of the input array.
    axis: int | tuple
        Compute the edge filter along this axis. If not provided, the edge magnitude is computed. This is defined as:

        ``np.sqrt(sum([prewitt(array, axis=i)**2 for i in range(array.ndim)]) / array.ndim)``
        The magnitude is also computed if axis is a sequence.

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
        * tuple[str, ...]: the `d`-th dimension uses `mode[d]` as boundary condition.

        (See :py:func:`numpy.pad` for details.)
    sampling: pyct.Real | (pyct.Real, ..., pyct.Real)
            Sampling step (i.e. distance between two consecutive elements of an array).
            Defaults to 1.
    gpu: bool
        Input NDArray type (`True` for GPU, `False` for CPU). Defaults to `False`.
    dtype: pyct.DType
        Working precision of the linear operator.

    Returns
    -------
    op: :py:class:`~pycsou.abc.operator.LinOp`
        Prewitt

    Example
    -------

    .. plot::

       import matplotlib.pyplot as plt

       arg_shape = (11, 11)
       image = np.zeros(arg_shape)
       image[5, 5] = 1.

       prewitt = Prewitt(arg_shape)
       out = prewitt(image.ravel())
       plt.figure(figsize=(10, 5))
       plt.subplot(121)
       plt.imshow(image)
       plt.colorbar()
       plt.subplot(122)
       plt.imshow(out.reshape(*arg_shape))
       plt.colorbar()

    See Also
    --------
    :py:class:`~pycsou.operator.linop.filter.Sobel`, :py:class:`~pycsou.operator.linop.filter.Scharr`,
    """
    smooth_kernel = np.full((3,), 1 / 3)
    return _EdgeFilter(
        arg_shape=arg_shape,
        smooth_kernel=smooth_kernel,
        filter_name="Prewitt",
        axis=axis,
        mode=mode,
        sampling=sampling,
        gpu=gpu,
        dtype=dtype,
    )


def Scharr(
    arg_shape: pyct.NDArrayShape,
    axis: typ.Optional[typ.Tuple] = None,
    mode: ModeSpec = "constant",
    sampling: typ.Union[pyct.Real, cabc.Sequence[pyct.Real, ...]] = 1,
    gpu: bool = False,
    dtype: typ.Optional[pyct.DType] = None,
):
    r"""
    Multidimensional Scharr filter.

    Notes
    -----

    This operator uses the applies the multi-dimensional Scharr filter to a :math:`D`-dimensional NDArray
    :math:`\mathbf{x} \in \mathbb{R}^{N_0 \times \cdots \times N_{D-1}}` using separable kernels for improved
    performance.  The Scharr filter applies the following edge filter in the dimensions of interest:
        ``[1, 0, -1]``,
    and the smoothing filter on the rest of dimensions:
        ``[3, 10, 3] / 16``.
    The Scharr filter is commonly used to find high-frequency components in the signal, such as
    for example, the edges in an image.

    Parameters
    ----------
    arg_shape: tuple
        Shape of the input array.
    axis: int | tuple
        Compute the edge filter along this axis. If not provided, the edge magnitude is computed. This is defined as:

        ``np.sqrt(sum([scharr(array, axis=i)**2 for i in range(array.ndim)]) / array.ndim)``
        The magnitude is also computed if axis is a sequence.
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
        * tuple[str, ...]: the `d`-th dimension uses `mode[d]` as boundary condition.

        (See :py:func:`numpy.pad` for details.)
    sampling: pyct.Real | (pyct.Real, ..., pyct.Real)
            Sampling step (i.e. distance between two consecutive elements of an array).
            Defaults to 1.
    gpu: bool
        Input NDArray type (`True` for GPU, `False` for CPU). Defaults to `False`.
    dtype: pyct.DType
        Working precision of the linear operator.

    Returns
    -------
    op: :py:class:`~pycsou.abc.operator.LinOp`
        Scharr

    Example
    -------

    .. plot::

       import matplotlib.pyplot as plt

       arg_shape = (11, 11)
       image = np.zeros(arg_shape)
       image[5, 5] = 1.

       scharr = Scharr(arg_shape)
       out = scharr(image.ravel())
       plt.figure(figsize=(10, 5))
       plt.subplot(121)
       plt.imshow(image)
       plt.colorbar()
       plt.subplot(122)
       plt.imshow(out.reshape(*arg_shape))
       plt.colorbar()

    See Also
    --------
    :py:class:`~pycsou.operator.linop.filter.Sobel`, :py:class:`~pycsou.operator.linop.filter.Prewitt`,
    """
    smooth_kernel = np.array([3, 10, 3]) / 16
    return _EdgeFilter(
        arg_shape=arg_shape,
        smooth_kernel=smooth_kernel,
        filter_name="Scharr",
        axis=axis,
        mode=mode,
        sampling=sampling,
        gpu=gpu,
        dtype=dtype,
    )


def _EdgeFilter(
    arg_shape: pyct.NDArrayShape,
    smooth_kernel: KernelSpec,
    filter_name: str,
    axis: typ.Optional[typ.Tuple] = None,
    mode: ModeSpec = "constant",
    sampling: typ.Union[pyct.Real, cabc.Sequence[pyct.Real, ...]] = 1,
    gpu: bool = False,
    dtype: typ.Optional[pyct.DType] = None,
):
    ndim, dtype, xp = _sanitize_inputs(arg_shape, dtype, gpu)
    sampling = _to_canonical_form(sampling, arg_shape)

    axes = _get_axes(axis, ndim)

    return_magnitude = len(axes) > 1
    if return_magnitude:
        import pycsou.operator.map.ufunc as pycufunc

        size = np.prod(arg_shape).item()
        square = pycufunc.Square(size)
        sqrt = pycufunc.Sqrt(size)

    op_list = []
    for edge_dim in axes:
        kernel = [xp.array(1)] * len(arg_shape)
        center = np.ones(len(arg_shape), dtype=int)
        # We define the kernel reversed compared to Scipy or Skimage because we use correlation instead of convolution
        kernel[edge_dim] = xp.array([-1, 0, 1], dtype=dtype) / sampling[edge_dim]
        smooth_axes = list(set(range(ndim)) - {edge_dim})
        for smooth_dim in smooth_axes:
            kernel[smooth_dim] = xp.asarray(smooth_kernel, dtype=dtype) / sampling[smooth_dim]

        if return_magnitude:
            op_list.append(square * pycls.Stencil(arg_shape=arg_shape, kernel=kernel, center=center, mode=mode))
        else:
            op_list.append(pycls.Stencil(arg_shape=arg_shape, kernel=kernel, center=center, mode=mode))

    op = functools.reduce(lambda x, y: x + y, op_list)
    if return_magnitude:
        op = (1 / np.sqrt(ndim)) * (sqrt * op)

    op._name = filter_name
    return op


class StructureTensor(pyco.DiffMap):
    r"""
    Structure tensor operator.

    Notes
    -----
    The Structure Tensor, also known as the second-order moment tensor or the inertia tensor, is a matrix derived from
    the gradient of a function. It describes the distribution of the gradient (i.e., its prominent directions) in a
    specified neighbourhood around a point, and the degree to which those directions are coherent.
    The structure tensor of a :math:`D`-dimensional signal
    :math:`\mathbf{f} \in \mathbb{R}^{N_0 \times \cdots \times N_{D-1}}` can be written as:

    .. math::

        \mathbf{S}_\sigma \mathbf{f} = \mathbf{g}_{\sigma} * \nabla\mathbf{f} (\nabla\mathbf{f})^{\top} = \mathbf{g}_{\sigma} *
        \begin{bmatrix}
        \left( \dfrac{ \partial\mathbf{f} }{ \partial x_{0} } \right)^2 &  \dfrac{ \partial^{2}\mathbf{f} }{ \partial x_{0}\,\partial x_{1} } & \cdots & \dfrac{ \partial\mathbf{f} }{ \partial x_{0} } \dfrac{ \partial\mathbf{f} }{ \partial x_{D-1} } \\
        \dfrac{ \partial\mathbf{f} }{ \partial x_{1} } \dfrac{ \partial\mathbf{f} }{ \partial x_{0} } & \left( \dfrac{ \partial\mathbf{f} }{ \partial x_{1} }\right)^2 & \cdots & \dfrac{ \partial\mathbf{f} }{ \partial x_{1} } \dfrac{ \partial\mathbf{f} }{ \partial x_{D-1} } \\
        \vdots & \vdots & \ddots & \vdots \\
        \dfrac{ \partial\mathbf{f} }{ \partial x_{D-1} } \dfrac{ \partial\mathbf{f} }{ \partial x_{0} } & \dfrac{ \partial\mathbf{f} }{ \partial x_{D-1} } \dfrac{ \partial\mathbf{f} }{ \partial x_{1} } & \cdots & \left( \dfrac{ \partial\mathbf{f} }{ \partial x_{D-1}} \right)^2
        \end{bmatrix},

    where :math:`\mathbf{g}_{\sigma} \in \mathbb{R}^{N_0 \times \cdots \times N_{D-1}}` is a discrete Gaussian filter
    with standard variation :math:`\sigma` with which a convolution is performed elementwise.

    However, due to the symmetry of the structure tensor, only the upper triangular part is computed in practice:

    .. math::

        \mathbf{H}_{\mathbf{v}_1, \ldots ,\mathbf{v}_m} \mathbf{f} = \mathbf{g}_{\sigma} * \begin{bmatrix}
        \left( \dfrac{ \partial\mathbf{f} }{ \partial x_{0} } \right)^2 \\
        \dfrac{ \partial^{2}\mathbf{f} }{ \partial x_{0}\,\partial x_{1} } \\
        \vdots \\
        \left( \dfrac{ \partial\mathbf{f} }{ \partial x_{D-1}} \right)^2
        \end{bmatrix} \mathbf{f} \in \mathbb{R}^{\frac{D (D-1)}{2} \times N_0 \times \cdots \times N_{D-1}}

    Remark
    ------
    In case of using the finite differences (`diff_type="fd"`), the finite difference scheme defaults to `central`
    (see :py:class:`~pycsou.operator.linop.diff.PartialDerivative`).

    Example
    -------

    .. plot::

       import numpy as np
       import matplotlib.pyplot as plt
       from pycsou.operator.linop.diff import StructureTensor
       from pycsou.util.misc import peaks

       # Define input image
       n = 1000
       x = np.linspace(-3, 3, n)
       xx, yy = np.meshgrid(x, x)
       image = peaks(xx, yy)
       nsamples = 2
       arg_shape = image.shape  # (1000, 1000)
       images = np.tile(image, (nsamples, 1, 1)).reshape(nsamples, -1)
       print(images.shape)  # (2, 1000000)
       # Instantiate structure tensor operator
       structure_tensor = StructureTensor(arg_shape=arg_shape)

       outputs = structure_tensor(images)
       print(outputs.shape)  # (2, 3000000)
       # Plot
       outputs = structure_tensor.unravel(outputs)
       print(outputs.shape)  # (2, 3, 1000, 1000)
       plt.figure()
       plt.imshow(images[0].reshape(arg_shape))
       plt.colorbar()
       plt.title("Image")
       plt.axis("off")

       plt.figure()
       plt.imshow(outputs[0][0].reshape(arg_shape))
       plt.colorbar()
       plt.title(r"$\hat{S}_{xx}$")
       plt.axis("off")

       plt.figure()
       plt.imshow(outputs[0][1].reshape(arg_shape))
       plt.colorbar()
       plt.title(r"$\hat{S}_{xy}$")
       plt.axis("off")

       plt.figure()
       plt.imshow(outputs[0][2].reshape(arg_shape))
       plt.colorbar()
       plt.title(r"$\hat{S}_{yy}$")
       plt.axis("off")

    See Also
    --------
    :py:class:`~pycsou.operator.linop.diff.PartialDerivative`, :py:class:`~pycsou.operator.linop.diff.Gradient`,
    :py:class:`~pycsou.operator.linop.diff.Hessian`.
    """

    def __init__(
        self,
        arg_shape: pyct.NDArrayShape,
        diff_method="fd",
        smooth_sigma: typ.Union[pyct.Real, tuple[pyct.Real, ...]] = 1.0,
        smooth_truncate: typ.Union[pyct.Real, tuple[pyct.Real, ...]] = 3.0,
        mode: ModeSpec = "constant",
        sampling: typ.Union[pyct.Real, tuple[pyct.Real, ...]] = 1,
        gpu: bool = False,
        dtype: typ.Optional[pyct.DType] = None,
        parallel: bool = False,
        **diff_kwargs,
    ):
        self.arg_shape = arg_shape
        size = int(np.prod(arg_shape))
        ndim = len(arg_shape)
        ntriu = (ndim * (ndim + 1)) // 2
        super().__init__(shape=(ntriu * size, size))
        self.directions = tuple(
            list(_) for _ in itertools.combinations_with_replacement(np.arange(len(arg_shape)).astype(int), 2)
        )

        if diff_method == "fd":
            diff_kwargs.update({"scheme": diff_kwargs.pop("scheme", "central")})
        self.grad = pycld.Gradient(
            arg_shape=arg_shape,
            directions=None,
            mode=mode,
            gpu=gpu,
            dtype=dtype,
            sampling=sampling,
            parallel=parallel,
            **diff_kwargs,
        )

        if smooth_sigma:
            self.smooth = Gaussian(
                arg_shape=arg_shape,
                sigma=smooth_sigma,
                truncate=smooth_truncate,
                order=0,
                mode=mode,
                sampling=sampling,
                gpu=gpu,
                dtype=dtype,
            )
        else:
            self.smooth = pyclb.IdentityOp(dim=np.prod(arg_shape).item())

    def unravel(self, arr):
        return arr.reshape(-1, *arr.shape[:-1], *self.arg_shape).swapaxes(0, 1)

    def ravel(self, arr):
        return arr.swapaxes(0, 1).reshape(*arr.shape[: -1 - len(self.arg_shape)], -1)

    def apply(self, arr):
        xp = pycu.get_array_module(arr)
        sh = arr.shape[:-1]
        grad = self.grad.unravel(self.grad(arr))

        def slice_(ax):
            return (slice(None),) * len(sh) + (slice(ax, ax + 1),)

        return xp.concatenate(
            [
                self.grad.unravel(self.smooth((grad[slice_(i)] * grad[slice_(j)]).reshape(*sh, -1)))
                for i, j in self.directions
            ],
            axis=len(sh),
        ).reshape(*sh, -1)
