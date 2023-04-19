import collections.abc as cabc
import math

import pycsou.abc as pyca
import pycsou.operator.func as pycof
import pycsou.runtime as pycrt
import pycsou.util as pycu
import pycsou.util.ptype as pyct


class _Sampler:
    """Abstract base class for samplers."""

    def samples(self, rng=None, **kwargs) -> cabc.Generator:
        """Returns a generator; samples are drawn by calling next(generator)."""
        self._sample_init(rng, **kwargs)
        while True:
            yield self._sample()

    def _sample_init(self, rng, **kwargs):
        """Optional method to set initial state of the sampler (e.g., a starting point)."""
        pass

    def _sample(self) -> pyct.NDArray:
        """Method to be implemented by subclasses that returns the next sample."""
        raise NotImplementedError


class ULA(_Sampler):
    r"""
    Unajusted Langevin algorithm (ULA).

    Generates samples from the distribution

    .. math::
        p(\mathbf{x}) = \frac{\exp(-\mathcal{F}(\mathbf{x}))}{\int_{\mathbb{R}^N} \exp(-\mathcal{F}(\tilde{\mathbf{x}}))
        \mathrm{d} \tilde{\mathbf{x}} },

    where :math:`\mathcal{F}: \mathbb{R}^N \to \mathbb{R}` is *differentiable* with :math:`\beta`-*Lipschitz continuous*
    gradient.

    Notes
    -----
    ULA is a Monte-Carlo Markov chain (MCMC) method that derives from the discretization of overdamped Langevin
    diffusions. More specifically, it relies on the Langevin stochastic differential equation (SDE):

    .. math::
        \mathrm{d} \mathbf{X}_t = - \nabla \mathcal{F}(\mathbf{X}_t) \mathrm{d}t + \sqrt{2} \mathrm{d} \mathbf{B}_t,

    where :math:`(\mathbf{B}_t)_{t \geq 0}` is a :math:`N`-dimensional Brownian motion. It is well known that under mild
    technical assumptions, this SDE has a unique strong solution whose invariant distribution is :math:`p(\mathbf{x})
    \propto \exp(-\mathcal{F}(\mathbf{x}))`. The discrete-time Euler-Maruyama discretization of this SDE then yields the
    ULA Markov chain

    .. math::
        \mathbf{X}_{k+1} = \mathbf{X}_{k} - \gamma \nabla \mathcal{F}(\mathbf{X}_k) + \sqrt{2 \gamma} \mathbf{Z}_{k+1}

    for all :math:`k \in \mathbb{Z}`, where :math:`\gamma` is the discretization step size and :math:`(\mathbf{Z}_k)_{k
    \in \mathbb{Z}}` is a sequence of independant and identically distributed :math:`N`-dimensional standard Gaussian
    distributions. When :math:`\mathcal{F}` is differentiable with :math:`\beta`-Lipschitz continuous gradient and
    :math:`0 < \gamma \leq \beta`, the ULA Markov chain converges (see [ULA]_) to a unique stationary distribution
    :math:`p_\gamma` such that

    .. math::
        \lim_{\gamma \to 0} \Vert p_\gamma - p \Vert_{\mathrm{TV}} = 0.

    The dicretization step :math:`\gamma` is subject to the bias-variance tradeoff: a larger step will lead to faster
    convergence of the Markov chain at the expense of a larger bias in the approximation of the distribution :math:`p`.
    Setting :math:`\gamma` as large as possible (default behavior) is recommended for large-scale problems, since
    convergence speed (rather than approximation bias) is then typically the main bottelneck. See `Example` section
    below for a concrete illustration of this bias.

    Example
    -------
    We illustrate ULA on a 1D example (:math:`N = 1`) where :math:`\mathcal{F}(x) = \frac{x^2}{2}`; the target
    distribution :math:`p(x)` is thus the 1D standard Gaussian. In this toy example, the biased distribution
    :math:`p_\gamma(x)` can be computed in closed form. The ULA Markov chain is given by

    .. math::
        \mathbf{X}_{k+1} &= \mathbf{X}_{k} - \gamma \nabla\mathcal{F}(\mathbf{X}_k) + \sqrt{2\gamma}\mathbf{Z}_{k+1} \\
        &= \mathbf{X}_{k} (1 - \gamma) + \sqrt{2 \gamma} \mathbf{Z}_{k+1}.

    Assuming for simplicity that :math:`\mathbf{X}_0` is Gaussian with mean :math:`\mu_0` and variance
    :math:`\sigma_0^2`, :math:`\mathbf{X}_k` is Gaussian for any :math:`k \in \mathbb{Z}` as a linear combination of
    Gaussians. Taking the expected value of the recurrence relation yields

    .. math::
        \mu_k := \mathbb{E}(\mathbf{X}_{k}) = \mathbb{E}(\mathbf{X}_{k-1}) (1 - \gamma) = \mu_0 (1 - \gamma)^k

    (geometric sequence). Taking the expected value of the square of the recurrence relation yields

    .. math::
        \mu^{(2)}_k := \mathbb{E}(\mathbf{X}_{k}^2) = \mathbb{E}(\mathbf{X}_{k-1}^2) (1 - \gamma)^2 + 2 \gamma =
        (1 - \gamma)^{2k} (\sigma_0^2 - b) + b

    with :math:`b = \frac{2 \gamma}{1 - (1 - \gamma)^{2}} = \frac{1}{1-\frac{\gamma}{2}}` (arithmetico-geometric
    sequence) due to the independence of :math:`\mathbf{X}_{k-1}` and :math:`\mathbf{Z}_{k}`. Hence, :math:`p_\gamma(x)`
    is a Gaussian with mean :math:`\mu_\gamma= \lim_{k \to \infty} \mu_k = 0` and variance :math:`\sigma_\gamma^2 =
    \lim_{k \to \infty} \mu^{(2)}_k - \mu_k^2 = \frac{1}{1-\frac{\gamma}{2}}`. As expected, we have :math:`\lim_{\gamma
    \to 0} \sigma_\gamma^2 = 1`, which is the variance of the target distribution :math:`p(x)`.

    We plot the distribution of the samples of ULA for one large (:math:`\gamma_1 \approx 1`, i.e.
    :math:`\sigma_{\gamma_1}^2 \approx 2`) and one small (:math:`\gamma_2 = 0.1`, i.e. :math:`\sigma_{\gamma_2}^2
    \approx 1.05`) step size.

    .. plot::

        import matplotlib.pyplot as plt
        import numpy as np

        import pycsou.operator.func as pycof
        from pycsou.sampler.sampler import ULA
        from pycsou.sampler.statistics import OnlineMoment, OnlineVariance

        f = pycof.SquaredL2Norm(dim=1) / 2  # To sample 1D normal distribution (mean 0, variance 1)
        ula = ULA(f=f)  # Sampler with maximum step size
        ula_lb = ULA(f=f, gamma=1e-1)  # Sampler with small step size

        gen_ula = ula.samples(x0=np.zeros(1))
        gen_ula_lb = ula_lb.samples(x0=np.zeros(1))
        n_burn_in = int(1e3)  # Number of burn-in iterations
        for i in range(n_burn_in):
            next(gen_ula)
            next(gen_ula_lb)

        # Online statistics objects
        mean_ula = OnlineMoment(order=1)
        mean_ula_lb = OnlineMoment(order=1)
        var_ula = OnlineVariance()
        var_ula_lb = OnlineVariance()

        n = int(1e4)  # Number of samples
        samples_ula = np.zeros(n)
        samples_ula_lb = np.zeros(n)
        for i in range(n):
            sample = next(gen_ula)
            sample_lb = next(gen_ula_lb)
            samples_ula[i] = sample
            samples_ula_lb[i] = sample_lb
            mean = float(mean_ula.update(sample))
            var = float(var_ula.update(sample))
            mean_lb = float(mean_ula_lb.update(sample_lb))
            var_lb = float(var_ula_lb.update(sample_lb))

        # Theoretical variances of biased stationary distributions of ULA
        biased_var = 1 / (1 - ula._gamma / 2)
        biased_var_lb = 1 / (1 - ula_lb._gamma / 2)

        # Plots
        grid = np.linspace(-4, 4, 1000)

        plt.figure()
        plt.title(f"ULA samples (large step size) \n Empirical mean: {mean:.3f} (theoretical: 0) \n "
                  f"Empirical variance: {var:.3f} (theoretical: {biased_var:.3f})")
        plt.hist(samples_ula, range=(min(grid), max(grid)), bins=100, density=True)
        plt.plot(grid, np.exp(-(grid ** 2) / 2) / np.sqrt(2 * np.pi), label=r"$p(x)$")
        plt.plot(grid, np.exp(-(grid ** 2) / (2 * biased_var)) / np.sqrt(2 * np.pi * biased_var),
                 label=r"$p_{\gamma_1}(x)$")
        plt.legend()

        plt.figure()
        plt.title(f"ULA samples (small step size) \n Empirical mean: {mean_lb:.3f} (theoretical: 0) \n "
                  f"Empirical variance: {var_lb:.3f} (theoretical: {biased_var_lb:.3f})")
        plt.hist(samples_ula_lb, range=(min(grid), max(grid)), bins=100, density=True)
        plt.plot(grid, np.exp(-(grid ** 2) / 2) / np.sqrt(2 * np.pi), label=r"$p(x)$")
        plt.plot(grid, np.exp(-(grid ** 2) / (2 * biased_var_lb)) / np.sqrt(2 * np.pi * biased_var_lb),
                 label=r"$p_{\gamma_2}(x)$")
        plt.legend()
        plt.show()
    """

    def __init__(self, f: pyca.DiffFunc, gamma: pyct.Real = None):
        r"""
        Parameters
        ----------
        f: :py:class:`~pycsou.abc.operator.DiffFunc`
            Differentiable functional.
        gamma: float | None
            Euler-Maruyama discretization step of the Langevin equation (see `Notes`).
        """
        self._f = f
        self._beta = f.diff_lipschitz()
        self._gamma = self._set_gamma(gamma)
        self._rng = None
        self.x = None

    def _sample_init(self, rng, x0: pyct.NDArray):
        r"""
        Parameters
        ----------
        seed: int
            Seed for the internal random generator.
        x0: ndarray
            Starting point of the Markov chain.
        """
        self.x = x0
        if rng is None:
            xp = pycu.get_array_module(x0)
            self._rng = xp.random.default_rng(None)
        else:
            self._rng = rng

    def _sample(self) -> pyct.NDArray:
        self.x += -self._gamma * self._f.grad(self.x)
        self.x += math.sqrt(2 * self._gamma) * self._rng.standard_normal(size=self.x.shape, dtype=self.x.dtype)
        return self.x

    def objective_func(self) -> pyct.Real:
        r"""
        Negative logarithm of the target ditribution (up to the a constant) evaluated at the current state of the
        Markov chain. Useful for diagnostics purposes to monitor whether the Markov chain is sufficiently warm-started.
        If so, the samples should accumulate around the modes of the target distribution, i.e., toward the minimum of
        :math:`\mathcal{F}`.
        """
        return self._f.apply(self.x)

    def _set_gamma(self, gamma: pyct.Real = None) -> pyct.Real:
        if gamma is None:
            if math.isfinite(self._beta):
                return pycrt.coerce(0.98 / self._beta)
            else:
                msg = "If f has unbounded Lipschitz gradient, the gamma parameter must be provided."
            raise ValueError(msg)
        else:
            try:
                assert gamma > 0
            except:
                raise ValueError(f"gamma must be positive, got {gamma}.")
            return pycrt.coerce(gamma)


class MYULA(ULA):
    r"""
    Moreau-Yosida unajusted Langevin algorithm (MYULA).

    Generates samples from the distribution

    .. math::
        p(\mathbf{x}) = \frac{\exp(-\mathcal{F}(\mathbf{x}) - \mathcal{G}(\mathbf{x}))}{\int_{\mathbb{R}^N}
        \exp(-\mathcal{F}(\tilde{\mathbf{x}}) - \mathcal{G}(\tilde{\mathbf{x}})) \mathrm{d} \tilde{\mathbf{x}} },

    where :math:`\mathcal{F}: \mathbb{R}^N \to \mathbb{R}` is *convex* and *differentiable* with :math:`\beta`-
    *Lipschitz continuous* gradient, and :math:`\mathcal{G}: \mathbb{R}^N \to \mathbb{R}` is *proper*, *lower semi-
    continuous* and *convex* with *simple proximal operator*.

    Notes
    -----
    MYULA is an extension of :py:class:`~pycsou.sampler.sampler.ULA` to sample from distributions whose logarithm is
    nonsmooth. It consists in applying ULA to the differentiable functional :math:`\mathcal{U}^\lambda = \mathcal{F} +
    \mathcal{G}^\lambda` for some :math:`\lambda > 0`, where

     .. math::
        \mathcal{G}^\lambda (\mathbf{x}) = \inf_{\tilde{\mathbf{x}} \in \mathbb{R}^N} \frac{1}{2 \lambda} \Vert
        \tilde{\mathbf{x}} - \mathbf{x} \Vert_2^2 + \mathcal{G}(\tilde{\mathbf{x}})

    is the Moreau-Yosida envelope of :math:`\mathcal{G}` with parameter :math:`\lambda`. We then have

    .. math::
        \nabla \mathcal{U}^\lambda (\mathbf{x}) = \nabla \mathcal{F}(\mathbf{x}) + \frac{1}{\lambda} (\mathbf{x} -
        \mathrm{prox}_{\lambda \mathcal{G}}(\mathbf{x})),

    hence :math:`\nabla \mathcal{U}^\lambda` is :math:`(\beta + \frac{1}{\lambda})`-Lipschitz continuous, where
    :math:`\beta` is the Lipschitz constant of :math:`\nabla \mathcal{F}`. Note that the target distribution of the
    underlying ULA Markov chain is not exactly :math:`p(\mathbf{x})`, but the distribution

    .. math::
        p^\lambda(\mathbf{x}) \propto \exp(-\mathcal{F}(\mathbf{x})-\mathcal{G}^\lambda(\mathbf{x})),

    which introduces some additional bias on top of the bias of ULA related to the step size :math:`\gamma` (see `Notes`
    of :py:class:`~pycsou.sampler.sampler.ULA` documentation). Hence, the stationary distribution of MYULA is a
    distribution :math:`p^\lambda_\gamma(\mathbf{x})`, which satisfies

    .. math::
        \lim_{\gamma, \lambda \to 0} \Vert p^\lambda_\gamma - p \Vert_{\mathrm{TV}} = 0

    (see [MYULA]_). The parameter :math:`\lambda` parameter is subject to a similar bias-variance tradeoff as
    :math:`\gamma`. It is recommended to set it in the order of :math:`\frac{1}{\beta}`, so that the contributions of
    :math:`\mathcal{F}` and :math:`\mathcal{G}^\lambda` to the Lipschitz constant of :math:`\nabla \mathcal{U}^\lambda`
    is well balanced.
    """

    def __init__(
        self, f: pyca.DiffFunc = None, g: pyca.ProxFunc = None, gamma: pyct.Real = None, lamb: pyct.Real = None
    ):
        r"""
        Parameters
        ----------
        f: :py:class:`~pycsou.abc.operator.DiffFunc` | None
            Differentiable functional.
        g: :py:class:`~pycsou.abc.operator.ProxFunc` | None
            Proximable functional.
        gamma: float | None
            Euler-Maruyama discretization step of the Langevin equation (see `Notes` of
            :py:class:`~pycsou.sampler.sampler.ULA` documentation).
        lambda: float | None
            Moreau-Yosida envelope parameter for g.
        """

        dim = None
        if f is not None:
            dim = f.dim
        if g is not None:
            if dim is None:
                dim = g.dim
            else:
                assert g.dim == dim
        if dim is None:
            raise ValueError("One of f or g must be nonzero.")

        self._f_diff = pycof.NullFunc(dim=dim) if (f is None) else f
        self._g = pycof.NullFunc(dim=dim) if (g is None) else g

        self._lambda = self._set_lambda(lamb)
        f = self._f_diff + self._g.moreau_envelope(self._lambda)
        f.diff_lipschitz()
        super().__init__(f, gamma)

    def _set_lambda(self, lamb: pyct.Real = None) -> pyct.Real:
        if lamb is None:
            if self._g._name == "NullFunc":
                return pycrt.coerce(1)  # Lambda is irrelevant if g is a NullFunc, but it must be positive
            elif math.isfinite(dl := self._f_diff.diff_lipschitz()):
                return pycrt.coerce(2) if dl == 0 else pycrt.coerce(min(2, 1 / dl))
            else:
                msg = "If f has unbounded Lipschitz gradient, the lambda parameter must be provided."
            raise ValueError(msg)
        else:
            return pycrt.coerce(lamb)
