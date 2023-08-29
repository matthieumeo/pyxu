import collections.abc as cabc
import typing as typ

import numpy as np

import pyxu.abc as pxa
import pyxu.info.ptype as pxt
import pyxu.runtime as pxrt
import pyxu.util as pxu

__all__ = [
    "Pad",
]


class Pad(pxa.LinOp):
    r"""
    Multi-dimensional padding operator.

    This operator pads the input array in each dimension according to specified widths.

    Notes
    -----
    * If inputs are D-dimensional, then some of the padding of later axes are calculated from padding of previous axes.
    * The *adjoint* of the padding operator performs a cumulative summation over the original positions used to pad.
      Its effect is clear from its matrix form.
      For example the matrix-form of ``Pad(arg_shape=(3,), mode="wrap", pad_width=(1, 1))`` is:

      .. math::

         \mathbf{A}
         =
         \left[
            \begin{array}{ccc}
                0 & 0 & 1 \\
                1 & 0 & 0 \\
                0 & 1 & 0 \\
                0 & 0 & 1 \\
                1 & 0 & 0
            \end{array}
         \right].

      The adjoint of :math:`\mathbf{A}` corresponds to its matrix transpose:

      .. math::

         \mathbf{A}^{\ast}
         =
         \left[
             \begin{array}{ccccc}
                 0 & 1 & 0 & 0 & 1 \\
                 0 & 0 & 1 & 0 & 0 \\
                 1 & 0 & 0 & 1 & 0
             \end{array}
         \right].

      This operation can be seen as a trimming (:math:`\mathbf{T}`) plus a cumulative summation (:math:`\mathbf{S}`):

      .. math::

         \mathbf{A}^{\ast}
         =
         \mathbf{T} + \mathbf{S}
         =
         \left[
            \begin{array}{ccccc}
                0 & 1 & 0 & 0 & 0 \\
                0 & 0 & 1 & 0 & 0 \\
                0 & 0 & 0 & 1 & 0
            \end{array}
         \right]
         +
         \left[
            \begin{array}{ccccc}
                0 & 0 & 0 & 0 & 1 \\
                0 & 0 & 0 & 0 & 0 \\
                1 & 0 & 0 & 0 & 0
            \end{array}
         \right],

      where both :math:`\mathbf{T}` and :math:`\mathbf{S}` are efficiently implemented in matrix-free form.


    * The Lipschitz constant of the multi-dimensional padding operator is upper-bounded by the product of Lipschitz
      constants of the uni-dimensional paddings applied per dimension, i.e.:

      .. math::

         L \le \prod_{i} L_{i}, \qquad i \in \{0, \dots, N-1\},

      where :math:`L_{i}` depends on the boundary condition at the :math:`i`-th axis.

      :math:`L_{i}^{2}` corresponds to the maximum singular value of the diagonal matrix

      .. math::

         \mathbf{A}_{i}^{\ast} \mathbf{A}_{i}
         =
         \mathbf{T}_{i}^{\ast} \mathbf{T}_{i} + \mathbf{S}_{i}^{\ast} \mathbf{S}_{i}
         =
         \mathbf{I}_{N} + \mathbf{S}_{i}^{\ast} \mathbf{S}_{i}.

      - In mode="constant", :math:`\text{diag}(\mathbf{S}_{i}^{\ast} \mathbf{S}_{i}) = \mathbf{0}`,
        hence :math:`L_{i} = 1`.
      - In mode="edge",

        .. math::

           \text{diag}(\mathbf{S}_{i}^{\ast} \mathbf{S}_{i})
           =
           \left[p_{lhs}, 0, \ldots, 0, p_{rhs} \right],

        hence :math:`L_{i} = \sqrt{1 + \min(p_{lhs}, p_{rhs})}`.
      - In mode="symmetric", "wrap", "reflect", :math:`\text{diag}(\mathbf{S}_{i}^{\ast}
        \mathbf{S}_{i})` equals (up to a mode-dependant permutation)

        .. math::

           \text{diag}(\mathbf{S}_{i}^{\ast} \mathbf{S}_{i})
           =
           \left[1, \ldots, 1, 0, \ldots, 0\right]
           +
           \left[0, \ldots, 0, 1, \ldots, 1\right],

        hence

        .. math::

           L^{\text{wrap, symmetric}}_{i} = \sqrt{1 + \lceil\frac{p_{lhs} + p_{rhs}}{N}\rceil}, \\
           L^{\text{reflect}}_{i} = \sqrt{1 + \lceil\frac{p_{lhs} + p_{rhs}}{N-2}\rceil}.
    """
    WidthSpec = typ.Union[
        pxt.Integer,
        cabc.Sequence[pxt.Integer],
        cabc.Sequence[tuple[pxt.Integer, pxt.Integer]],
    ]
    ModeSpec = typ.Union[str, cabc.Sequence[str]]

    def __init__(
        self,
        arg_shape: pxt.NDArrayShape,
        pad_width: WidthSpec,
        mode: ModeSpec = "constant",
    ):
        r"""
        Parameters
        ----------
        arg_shape: NDArrayShape
            Shape of the input array.
        pad_width: ~pyxu.operator.linop.pad.Pad.WidthSpec
            Number of values padded to the edges of each axis.
            Multiple forms are accepted:

            * ``int``: pad each dimension's head/tail by `pad_width`.
            * ``tuple[int, ...]``: pad dimension[k]'s head/tail by `pad_width[k]`.
            * ``tuple[tuple[int, int], ...]``: pad dimension[k]'s head/tail by `pad_width[k][0]` /
              `pad_width[k][1]` respectively.
        mode: str, :py:class:`list` ( str )
            Padding mode.
            Multiple forms are accepted:

            * str: unique mode shared amongst dimensions.
              Must be one of:

              * 'constant' (zero-padding)
              * 'wrap'
              * 'reflect'
              * 'symmetric'
              * 'edge'
            * tuple[str, ...]: pad dimension[k] using `mode[k]`.

            (See :py:func:`numpy.pad` for details.)
        """
        self._arg_shape = tuple(arg_shape)
        assert all(_ > 0 for _ in self._arg_shape)
        N_dim = len(self._arg_shape)

        # transform `pad_width` to canonical form tuple[tuple[int, int], ...]
        is_seq = lambda _: isinstance(_, cabc.Sequence)
        if not is_seq(pad_width):  # int-form
            pad_width = ((pad_width, pad_width),) * N_dim
        assert len(pad_width) == N_dim, "arg_shape/pad_width are length-mismatched."
        if not is_seq(pad_width[0]):  # tuple[int, ...] form
            pad_width = tuple((w, w) for w in pad_width)
        else:  # tuple[tulpe[int, int], ...] form
            pass
        assert all(0 <= min(lhs, rhs) for (lhs, rhs) in pad_width)
        self._pad_width = tuple(pad_width)

        # transform `mode` to canonical form tuple[str, ...]
        if isinstance(mode, str):  # shared mode
            mode = (mode,) * N_dim
        elif isinstance(mode, cabc.Sequence):  # tuple[str, ...]: different modes
            assert len(mode) == N_dim, "arg_shape/mode are length-mismatched."
            mode = tuple(mode)
        else:
            raise ValueError(f"Unkwown mode encountered: {mode}.")
        self._mode = tuple(map(lambda _: _.strip().lower(), mode))
        assert set(self._mode) <= {
            "constant",
            "wrap",
            "reflect",
            "symmetric",
            "edge",
        }, "Unknown mode(s) encountered."

        # Useful constant: shape of padded outputs
        self._pad_shape = list(self._arg_shape)
        for i, (lhs, rhs) in enumerate(self._pad_width):
            self._pad_shape[i] += lhs + rhs
        self._pad_shape = tuple(self._pad_shape)

        codim = np.prod(self._pad_shape)
        dim = np.prod(self._arg_shape)
        super().__init__(shape=(codim, dim))

        # Some modes have awkward interpretations when pad-widths cross certain thresholds.
        # Supported pad-widths are thus limited to sensible regions.
        for i in range(N_dim):
            N = self._arg_shape[i]
            w_max = dict(
                constant=np.inf,
                wrap=N,
                reflect=N - 1,
                symmetric=N,
                edge=np.inf,
            )[self._mode[i]]
            lhs, rhs = self._pad_width[i]
            assert max(lhs, rhs) <= w_max, f"pad_width along dim-{i} is limited to {w_max}."

        # We know a crude Lipschitz bound by default. Since computing it takes (code) space,
        # the estimate is computed as a special case of estimate_lipschitz()
        self.lipschitz = self.estimate_lipschitz(__rule=True)

    @pxrt.enforce_precision(i="arr")
    def apply(self, arr: pxt.NDArray) -> pxt.NDArray:
        sh = arr.shape[:-1]
        arr = arr.reshape(*sh, *self._arg_shape)
        N_dim = len(self._arg_shape)

        # Part 1: extend the core
        xp = pxu.get_array_module(arr)
        pad_width_sh = ((0, 0),) * len(sh)  # don't pad stack-dims
        out = xp.pad(
            array=arr,
            pad_width=pad_width_sh + self._pad_width,
            mode="constant",
            constant_values=0,
        )

        # Part 2: apply border effects (if any)
        for i in range(N_dim, 0, -1):
            mode = self._mode[-i]
            lhs, rhs = self._pad_width[-i]
            N = self._pad_shape[-i]

            r_s = [slice(None)] * (len(sh) + N_dim)  # read axial selector
            w_s = [slice(None)] * (len(sh) + N_dim)  # write axial selector

            if mode == "constant":
                # no border effects
                pass
            elif mode == "wrap":
                # Fix LHS
                r_s[-i] = slice(N - rhs - lhs, N - rhs)
                w_s[-i] = slice(0, lhs)
                out[tuple(w_s)] = out[tuple(r_s)]

                # Fix RHS
                r_s[-i] = slice(lhs, lhs + rhs)
                w_s[-i] = slice(N - rhs, N)
                out[tuple(w_s)] = out[tuple(r_s)]
            elif mode == "reflect":
                # Fix LHS
                r_s[-i] = slice(2 * lhs, lhs, -1)
                w_s[-i] = slice(0, lhs)
                out[tuple(w_s)] = out[tuple(r_s)]

                # Fix RHS
                r_s[-i] = slice(N - rhs - 2, N - 2 * rhs - 2, -1)
                w_s[-i] = slice(N - rhs, N)
                out[tuple(w_s)] = out[tuple(r_s)]
            elif mode == "symmetric":
                # Fix LHS
                r_s[-i] = slice(2 * lhs - 1, lhs - 1, -1)
                w_s[-i] = slice(0, lhs)
                out[tuple(w_s)] = out[tuple(r_s)]

                # Fix RHS
                r_s[-i] = slice(N - rhs - 1, N - 2 * rhs - 1, -1)
                w_s[-i] = slice(N - rhs, N)
                out[tuple(w_s)] = out[tuple(r_s)]
            elif mode == "edge":
                if lhs > 0:  # Fix LHS
                    r_s[-i] = slice(lhs, lhs + 1)
                    w_s[-i] = slice(0, lhs)
                    out[tuple(w_s)] = out[tuple(r_s)]

                if rhs > 0:  # Fix RHS
                    r_s[-i] = slice(N - rhs - 1, N - rhs)
                    w_s[-i] = slice(N - rhs, N)
                    out[tuple(w_s)] = out[tuple(r_s)]

        out = out.reshape(*sh, self.codim)
        return out

    @pxrt.enforce_precision(i="arr")
    def adjoint(self, arr: pxt.NDArray) -> pxt.NDArray:
        sh = arr.shape[:-1]
        arr = arr.reshape(*sh, *self._pad_shape)
        N_dim = len(self._arg_shape)

        # Part 1: apply correction terms (if any)
        out = arr.copy()  # in-place updates below
        for i in range(1, N_dim + 1):
            mode = self._mode[-i]
            lhs, rhs = self._pad_width[-i]
            N = self._pad_shape[-i]

            r_s = [slice(None)] * (len(sh) + N_dim)  # read axial selector
            w_s = [slice(None)] * (len(sh) + N_dim)  # write axial selector

            if mode == "constant":
                # no correction required
                pass
            elif mode == "wrap":
                # Fix LHS
                r_s[-i] = slice(0, lhs)
                w_s[-i] = slice(N - rhs - lhs, N - rhs)
                out[tuple(w_s)] += out[tuple(r_s)]

                # Fix RHS
                r_s[-i] = slice(N - rhs, N)
                w_s[-i] = slice(lhs, lhs + rhs)
                out[tuple(w_s)] += out[tuple(r_s)]
            elif mode == "reflect":
                if lhs > 0:  # Fix LHS
                    r_s[-i] = slice(lhs - 1, None, -1)
                    w_s[-i] = slice(lhs + 1, 2 * lhs + 1)
                    out[tuple(w_s)] += out[tuple(r_s)]

                # Fix RHS
                r_s[-i] = slice(N - 1, N - rhs - 1, -1)
                w_s[-i] = slice(N - 2 * rhs - 1, N - rhs - 1)
                out[tuple(w_s)] += out[tuple(r_s)]
            elif mode == "symmetric":
                if lhs > 0:  # Fix LHS
                    r_s[-i] = slice(lhs - 1, None, -1)
                    w_s[-i] = slice(lhs, 2 * lhs)
                    out[tuple(w_s)] += out[tuple(r_s)]

                # Fix RHS
                r_s[-i] = slice(N - 1, N - rhs - 1, -1)
                w_s[-i] = slice(N - 2 * rhs, N - rhs)
                out[tuple(w_s)] += out[tuple(r_s)]
            elif mode == "edge":
                if lhs > 0:  # Fix LHS
                    r_s[-i] = slice(0, lhs)
                    w_s[-i] = slice(lhs, lhs + 1)
                    out[tuple(w_s)] += out[tuple(r_s)].sum(axis=-i, keepdims=True)

                if rhs > 0:  # Fix RHS
                    r_s[-i] = slice(N - rhs, N)
                    w_s[-i] = slice(N - rhs - 1, N - rhs)
                    out[tuple(w_s)] += out[tuple(r_s)].sum(axis=-i, keepdims=True)

        # Part 2: extract the core
        selector = [slice(None)] * len(sh)
        for N, (lhs, rhs) in zip(self._pad_shape, self._pad_width):
            s = slice(lhs, N - rhs)
            selector.append(s)
        out = out[tuple(selector)]

        out = out.reshape(*sh, self.dim)
        return out

    def estimate_lipschitz(self, **kwargs) -> pxt.Real:
        no_eval = "__rule" in kwargs
        if no_eval:
            L = []  # 1D pad-op Lipschitz constants
            for N, m, (lhs, rhs) in zip(self._arg_shape, self._mode, self._pad_width):
                if m == "constant":
                    _L = 1
                elif m in {"wrap", "symmetric"}:
                    _L = np.sqrt(1 + np.ceil((lhs + rhs) / N))
                elif m == "reflect":
                    _L = np.sqrt(1 + np.ceil((lhs + rhs) / (N - 2)))
                elif m == "edge":
                    _L = np.sqrt(1 + max(lhs, rhs))
                L.append(_L)
            L = np.prod(L)
        else:
            L = super().estimate_lipschitz(**kwargs)
        return L

    def gram(self) -> pxt.OpT:
        if all(m == "constant" for m in self._mode):
            from pyxu.operator.linop.base import IdentityOp

            op = IdentityOp(dim=self.dim)
        else:
            op = super().gram()
        return op

    def cogram(self) -> pxt.OpT:
        if all(m == "constant" for m in self._mode):
            from pyxu.operator.linop.select import Trim

            # Orthogonal projection
            op = Trim(
                arg_shape=self._pad_shape,
                trim_width=self._pad_width,
            ).gram()
        else:
            op = super().cogram()
        return op
