import dask.array as da
import numpy as np

import pycsou.operator.linop.nufft as nufft

importlib.reload(nufft)
import pycsou.runtime as pycrt
import pycsou.util as pycu


def NUFFT1_array(x, N, isign) -> np.ndarray:
    D = x.shape[-1]
    if isinstance(N, int):
        N = (N,) * D
    A = np.meshgrid(*[np.arange(-(m // 2), (m - 1) // 2 + 1) for m in N], indexing="ij")
    B = np.stack(A, axis=0).reshape((D, -1)).T
    return np.exp(1j * np.sign(isign) * B @ x.T)


def NUFFT2_array(x, N, isign) -> np.ndarray:
    A = NUFFT1_array(x, N, isign).T
    return A


use_dask = False

rng = np.random.default_rng(0)
D, M, N = 2, 200, 5
N_full = (N,) * D if isinstance(N, int) else N
x = np.fmod(rng.normal(size=(M, D)), 2 * np.pi)
if use_dask:
    x = da.array(x)

with pycrt.Precision(pycrt.Width.DOUBLE):
    N_trans, isign = 5, -1
    A = nufft.NUFFT.type2(x, N, n_trans=N_trans, isign=isign, real=True)
    B = NUFFT2_array(x, N, isign)

    arr = rng.normal(size=(3, N_trans, *N_full)).reshape(3, N_trans, -1)
    if use_dask:
        arr = da.array(arr)

    A_out_fw = pycu.view_as_complex(A.apply(arr))
    B_out_fw = np.tensordot(arr, B, axes=[[2], [1]])

    A_out_bw = A.adjoint(pycu.view_as_real(A_out_fw))
    B_out_bw = np.tensordot(B_out_fw, B.conj().T, axes=[[2], [1]])

    res_fw = (np.linalg.norm(A_out_fw - B_out_fw, axis=-1) / np.linalg.norm(B_out_fw, axis=-1)).max()
    res_bw = (np.linalg.norm(A_out_bw - B_out_bw.real, axis=-1) / np.linalg.norm(B_out_bw.real, axis=-1)).max()
    if use_dask:
        res_fw, res_bw = pycu.compute(res_fw, res_bw)
    print(res_fw)
    print(res_bw)
