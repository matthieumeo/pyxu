r"""
This module implements Bayesian methods to estimate regularization parameters in inverse problems. Setting such
parameters if often a challenge in practice; this module aims to provide principled ways of setting them automatically.

In the following example, we showcase one such algorithm :py:class:`~pycsou.opt.solver.reg_param_est.RegParamMLE` that
estimates regularization parameters via maximum likelihood estimation. We consider a deconvolution problem
:math:`\mathbf{y}=\mathbf{H}\mathbf{x}_{\mathrm{GT}}+\mathbf{n}` where :math:`\mathbf{y}` is the blurry and noisy
measured image, :math:`\mathbf{H}` (forward model) is a convolution operator with a Gaussian kernel,
:math:`\mathbf{x}_{\mathrm{GT}}` is the ground-truth image, and :math:`\mathbf{n}` is additive i.i.d Gaussian noise with
variance :math:`\sigma^2`. In Bayesian frameworks, one must typically find the expression of the *posterior
distribution*, which, using Bayes’ rule, is given by

.. math::
    p(\mathbf{x}|\mathbf{y};\boldsymbol{\theta}) \propto p(\mathbf{y}|\mathbf{x}; \boldsymbol{\theta}) p(\mathbf{x};
    \boldsymbol{\theta}),

where:

* :math:`\boldsymbol{\theta} \in \mathbb{R}^K` are the model parameters to be estimated.
* :math:`p(\mathbf{y}|\mathbf{x};\boldsymbol{\theta})` is the *likelihood* of the image :math:`\mathbf{x}`, which in an
  additive Gaussian noise model is given by :math:`p(\mathbf{y}|\mathbf{x};\boldsymbol{\theta}) \propto
  \exp(- \frac{1}{2 \sigma^2}||\mathbf{H}\mathbf{x} -\mathbf{y}||_2^2)`.
* :math:`p(\mathbf{x};\boldsymbol{\theta})` is the *prior distribution*.

In this example, we assume that the noise variance :math:`\sigma^2` is unknown; it is thus considered as a model
parameter to be estimated, i.e.

.. math::
    p(\mathbf{y}|\mathbf{x};\boldsymbol{\theta}) \propto \exp \left(-\frac{\theta_0}{2}||\mathbf{H}\mathbf{x} -
    \mathbf{y}||_2^2 \right)

with :math:`\theta_0 = \frac{1}{\sigma^2}`. Next, since the ground-truth image is a deep-field image from the Hubble
space telescope that is mostly empty with a few bright galaxies, we consider an *elastic net* prior for the
reconstruction which is known to promote group sparsity, i.e.

.. math::
    p(\mathbf{x};\boldsymbol{\theta}) \propto \exp \left(-\theta_1 ||\mathbf{x}||_1 - \frac{\theta_2}{2}
    ||\mathbf{x}||_2^2 \right)

where :math:`\theta_1, \theta_2 > 0` determine the strength of the regularization. Hence, the posterior distribution
can be expressed as

.. math::
        p(\mathbf{x} | \mathbf{y}; \boldsymbol{\theta})) \propto \exp \left( - \sum_{k=0}^{2} \theta_k
        \mathcal{G}_k(\mathbf{x}) \right),

where:

* :math:`\mathcal{G}_0(\mathbf{x}) = \frac12 ||\mathbf{H}\mathbf{x} -\mathbf{y}||_2^2`
* :math:`\mathcal{G}_1(\mathbf{x}) = ||\mathbf{x}||_1`
* :math:`\mathcal{G}_2(\mathbf{x}) = \frac12 ||\mathbf{x}||_2^2`.

We thus apply the :py:class:`~pycsou.opt.solver.reg_param_est.RegParamMLE` algorithm with the objective functional
:math:`\sum_{k=0}^{2} \theta_k\mathcal{G}_k(\mathbf{x})` to estimate the parameters :math:`\boldsymbol{\theta}`. We plot
the evolution of the :math:`\boldsymbol{\theta}` iterates throughout the algorithm to illustrate their convergence.
In this simulated example, the true noise level and thus the true value :math:`\theta_0^{\mathrm{true}}` of
:math:`\theta_0` is known; we can observe that the algorithm is able to recover it accurately. We then compute the
maximum-a-posteriori (MAP) reconstruction obtained with the estimated parameters :math:`\boldsymbol{\theta}`, i.e. the
minimum of the objective functional :math:`\sum_{k=0}^{2} \theta_k\mathcal{G}_k(\mathbf{x})`. Although the theoretical
values of :math:`\theta_1` and :math:`\theta_2` are unknown, we observe that their estimates seem reasonable since the MAP
reconstructed image is visually satisfactory.

.. code-block:: python3

    import numpy as np
    import scipy as sp
    import matplotlib.pyplot as plt
    import skimage as sk

    from pycsou.abc import Mode
    import pycsou.operator as pycop
    import pycsou.opt.stop as pycstop
    import pycsou.opt.solver as pycsol
    from pycsou.opt.solver.reg_param_est import ProxFuncMoreau, RegParamMLE

    plt.rcParams["text.usetex"] = True

    # Load ground truth image
    sh_im = (256, ) * 2
    gt = sk.transform.resize(sk.color.rgb2gray(sk.data.hubble_deep_field()), sh_im)
    N = np.prod(sh_im)  # Problem dimension

    # Forward model (blurring operator)
    sigma_blur = 2
    filt = sp.ndimage._filters._gaussian_kernel1d(sigma=sigma_blur, order=0, radius=int(3*sigma_blur + 0.5))
    H = pycop.Stencil(kernel=(filt, filt),
                      center=(filt.size//2 + 1, filt.size//2 + 1),
                      arg_shape=sh_im)

    # Noisy data
    rng = np.random.default_rng(seed=0)
    sigma_gt = 1e-2
    y = H(gt.ravel()).reshape(sh_im) + sigma_gt * rng.standard_normal(sh_im)

    # Plot ground truth and noisy data
    fig, ax = plt.subplots()
    ax.imshow(gt)
    ax.set_title("Ground truth")
    ax.axis('off')

    fig, ax = plt.subplots()
    ax.imshow(y, vmin=0, vmax=1)
    ax.set_title("Measured data")
    ax.axis('off')

    # Data fidelity
    f = 1 / 2 * pycop.SquaredL2Norm(dim=N).asloss(y.ravel()) * H

    # Regularization
    g_L1 = pycop.L1Norm(dim=N)
    g_L2 = 1/2 * pycop.SquaredL2Norm(dim=N)

    # Initialize solver
    homo_factors = 2, 1, 2  # Homogeneity factors
    mu = 0.01  # Moreau envelope parameter for g_L1
    g_L1_moreau = ProxFuncMoreau(g_L1, mu)  # Differentiable approximation of g_L1

    sapg = RegParamMLE(g=[f, g_L1_moreau, g_L2], homo_factors=homo_factors)

    # Run solver
    theta0 = 1 / sigma_gt ** 2, 0.1, 1e2
    theta_min = theta0[0]/10, 1e0, theta0[2]/10
    theta_max = theta0[0]*10, 1e2, theta0[2]*10  # Valid interval for theta
    delta0 = 1e-3, 1e-3, 1e-3

    max_iter = int(3e3)
    stop_crit = pycstop.MaxIter(n=max_iter)  # Stopping criterion

    sapg.fit(mode=Mode.MANUAL, x0=np.zeros(N), theta0=theta0, theta_min=theta_min, theta_max=theta_max, delta0=delta0,
             warm_start=30, stop_crit=stop_crit, rng=rng)

    theta_list = np.zeros((len(theta0), max_iter))
    it = 0
    for data in sapg.steps():
        theta = data["theta"]
        theta_list[:, it] = theta
        it += 1

    # Plot convergence curves
    fig, ax = plt.subplots(1, 3)
    ax[0].plot(np.log10(theta_list[0, :]))
    ax[0].axhline(y=np.log10(theta_max[0]), color='k', linestyle='--', label=r"$\log_{10}(\theta_0^{\max})$")
    ax[0].axhline(y=np.log10(1/sigma_gt**2), color='r', linestyle='--', label=r"$\log_{10}(\theta_0^{\mathrm{true}})$")
    ax[0].axhline(y=np.log10(theta_min[0]), color='b', linestyle='--', label=r"$\log_{10}(\theta_0^{\min})$")
    ax[0].set_xlabel("Iterations")
    ax[0].set_ylabel(r"$\log_{10}(\theta_0)$")
    ax[0].legend(loc="center right", bbox_to_anchor=(1, 0.7))

    ax[1].plot(np.log10(theta_list[1, :]))
    ax[1].axhline(y=np.log10(theta_max[1]), color='k', linestyle='--', label=r"$\log_{10}(\theta_1^{\max})$")
    ax[1].axhline(y=np.log10(theta_min[1]), color='b', linestyle='--', label=r"$\log_{10}(\theta_1^{\min})$")
    ax[1].set_xlabel("Iterations")
    ax[1].set_ylabel(r"$\log_{10}(\theta_1)$")
    ax[1].legend(loc="center right", bbox_to_anchor=(1, 0.7))

    ax[2].plot(np.log10(theta_list[2, :]))
    ax[2].axhline(y=np.log10(theta_max[2]), color='k', linestyle='--', label=r"$\log_{10}(\theta_2^{\max})$")
    ax[2].axhline(y=np.log10(theta_min[2]), color='b', linestyle='--', label=r"$\log_{10}(\theta_2^{\min})$")
    ax[2].set_xlabel("Iterations")
    ax[2].set_ylabel(r"$\log_{10}(\theta_2)$")
    ax[2].legend(loc="center right", bbox_to_anchor=(1, 0.7))
    fig.suptitle("Convergence plots of regularization parameters")
    fig.tight_layout()

    # Solve MAP problem with optimal theta
    pgd = pycsol.PGD(f=theta[0]*f + theta[2]*g_L2, g=theta[1] * g_L1)
    pgd.fit(x0=np.zeros(N))

    im_recon = pgd.solution().reshape(sh_im)

    # Plot MAP reconstruction
    fig, ax = plt.subplots()
    ax.imshow(im_recon, vmin=0, vmax=1)
    ax.set_title("MAP reconstruction with optimal regularization parameters")
    ax.axis('off')
    fig.tight_layout()
"""

import functools
import itertools
import operator
import typing as typ

import numpy as np

import pycsou.abc as pyca
import pycsou.operator as pyco
import pycsou.runtime as pycrt
import pycsou.sampler.sampler as pycs
import pycsou.util.ptype as pyct


class RegParamMLE(pyca.Solver):
    r"""
    Maximum likelihood estimation (MLE) of regularization parameters.

    Estimates regularization parameters :math:`\boldsymbol{\theta} = (\theta_0, \ldots , \theta_{K-1})` for minimization
    problems of the form

    .. math::
        {\min_{\mathbf{x}\in\mathbb{R}^N} \;\mathcal{F}(\mathbf{x})\;\;+\;\; \sum_{k=0}^{K-1} \theta_k
        \mathcal{G}_k(\mathbf{x})},

    where:

    * :math:`\mathcal{F}:\mathbb{R}^N\rightarrow \mathbb{R}` is *convex* and *differentiable* with :math:`\beta`-
      *Lipschitz-continuous* gradient.
    * :math:`\mathcal{G}_k:\mathbb{R}^N\rightarrow \mathbb{R}` for all :math:`0 \leq k \leq K-1` are *convex*,
      *differentiable* with :math:`\beta_k`-*Lipschitz-continuous* gradient, and :math:`\alpha_k`-*homogeneous*, i.e.
      :math:`\mathcal{G}_k(\lambda \mathbf{x}) = \lambda^{\alpha_k}\mathcal{G}_k(\mathbf{x})` for any :math:`\mathbf{x}
      \in \mathbb{R}^N` and :math:`\lambda \neq 0`.

    Notes
    -----
    This algorithm is based on the stochastic approximation proximal gradient (SAPG) algorithm described in [SAPG1]_. It
    consists in estimating the maximum of the likelihood of the regularization parameters :math:`\boldsymbol{\theta}`

    .. math::
        \mathcal{L}(\boldsymbol{\theta}) = p(\mathbf{y};\boldsymbol{\theta})= \int_{\mathbf{x}\in\mathbb{R}^N}
        p(\mathbf{y},\mathbf{x};\boldsymbol{\theta}) \mathrm{d}\mathbf{x},

    where :math:`\mathbf{y}` is the measured data and the joint distribution
    :math:`p(\mathbf{y},\mathbf{x};\boldsymbol{\theta})`, which is proportional to the *posterior distribution*
    :math:`p(\mathbf{x} | \mathbf{y} ; \boldsymbol{\theta}))` (using Bayes' rule), is determined by the objective
    functional of the original minimization problem via the relation

    .. math::
        p(\mathbf{y},\mathbf{x}; \boldsymbol{\theta})) \propto p(\mathbf{x} | \mathbf{y} ; \boldsymbol{\theta})) \propto
        \exp \Big( -\mathcal{F}(\mathbf{x}) - \sum_{k=0}^{K-1} \theta_k \mathcal{G}_k(\mathbf{x}) \Big).

    This algorithm iteratively updates :math:`\boldsymbol{\theta}` via projected gradient ascent on the log likelihood:

    .. math::
        \boldsymbol{\theta}_{n+1} = \mathrm{Proj}_\Theta \Big( \boldsymbol{\theta}_n + \boldsymbol{\delta}_n
        \nabla_{\boldsymbol{\theta}} \log ( \mathcal{L}(\boldsymbol{\theta})) \Big),

    where :math:`\boldsymbol{\delta}_n \in \mathbb{R}^K` are step sizes and :math:`\Theta = [\theta_0^\min,
    \theta_0^\max] \times \cdots \times [\theta_{K-1}^\min, \theta_{K-1}^\max]` is the set of feasible regularization
    parameters, determined by user-provided lower bounds :math:`\theta_k^\min` and upper bounds :math:`\theta_k^\max`
    for :math:`0 \leq k \leq K-1`.

    Since the likelihood is typically intractable in large-dimensional problems, it is estimated using Fisher's identity

    .. math::
        \frac{\mathrm{d}}{\mathrm{d} \theta_k} \log (\mathcal{L}(\boldsymbol{\theta})) =
        - \int_{\mathbf{x}\in\mathbb{R}^N} \mathcal{G}_k(\mathbf{x}) p(\mathbf{x}|\mathbf{y};\boldsymbol{\theta})
        \mathrm{d}\mathbf{x} - \frac{\mathrm{d}}{\mathrm{d} \theta_k} \log(Z_k(\theta_k)),
        :label: eq:Fisher

    where :math:`Z_k(\theta_k) = \int_{\mathbf{x}\in\mathbb{R}^N}\exp(-\theta_k\mathcal{G}_k(\mathbf{x}))\mathrm{d}
    \mathbf{x}` is the normalizing constant of the distribution :math:`p_k(\mathbf{x}; \theta_k) =
    \frac{\exp(-\theta_k\mathcal{G}_k(\mathbf{x}))}{Z_k(\theta_k)}`. The integral in Eq. :math:numref:`eq:Fisher` is
    approximated using the Monte-Carlo Markov chain (MCMC) method :py:class:`~pycsou.sampler.sampler.ULA` targeting the
    posterior distribution :math:`p(\mathbf{x}|\mathbf{y};\boldsymbol{\theta})`. The second term in
    :math:numref:`eq:Fisher` is computed exactly using the :math:`\alpha_k`-homogeneity of :math:`\mathcal{G}_k`, which
    yields :math:`\frac{\mathrm{d}}{\mathrm{d} \theta_k} \log(Z_k(\theta_k)) = - \frac{N}{\alpha_k \theta_k}`. Hence,
    the iterations of the algorithm are given by

    .. math::
        (\boldsymbol{\theta}_{n+1})_k = \mathrm{Proj}_\Theta \left( (\boldsymbol{\theta}_n)_k - (\boldsymbol{\delta}_n)_k
        \left( \frac1S \sum_{s=0}^{S-1}\mathcal{G}_k(\mathbf{x}_{n, s})-\frac{N}{\alpha_k (\boldsymbol{\theta}_n)_k}
        \right)\right),

    for :math:`0 \leq k \leq K-1`, where:

    * :math:`S > 0` is the batch size for the MCMC approximation of the integral.
    * :math:`\mathbf{x}_{n, s} \in \mathbb{R}^N` for :math:`n \in \mathbb{N}` and :math:`0 \leq s \leq S-1` are samples
      of a :py:class:`~pycsou.sampler.sampler.ULA` Markov chain targeting the posterior distribution
      :math:`p(\mathbf{x}|\mathbf{y};\boldsymbol{\theta}_n)`.

    **Remark 1:**

    The algorithm is still valid if :math:`\mathcal{F}` is zero.

    **Remark 2:**

    As opposed to purely maximum-a-posteriori formulations, in this Bayesian framework, multiplicative constants of
    the objective functional are important, since they affect the sharpness of the posterior distribution
    :math:`p(\mathbf{x} | \mathbf{y} ; \boldsymbol{\theta})) \propto \exp \Big( -\mathcal{F}(\mathbf{x}) -
    \sum_{k=0}^{K-1} \theta_k \mathcal{G}_k(\mathbf{x}) \Big)` that is being sampled from. When :math:`\mathcal{F}` is
    zero, this is not an issue since multiplicative constants are absorbed in the :math:`\boldsymbol{\theta}`
    parameters. However, when :math:`\mathcal{F}` is non-zero, its multiplicative constant should be selected with care.
    For example, in the case of inverse problems :math:`\mathbf{y}=\mathbf{H}\mathbf{x}+\mathbf{n}` where
    :math:`\mathbf{H}` is the forward model and :math:`\mathbf{n}` is additive i.i.d Gaussian noise with variance
    :math:`\sigma^2`, the likelihood of an image :math:`\mathbf{x}` is given by :math:`p(\mathbf{y})\propto
    \exp(- \frac{||\mathbf{H}\mathbf{x} - \mathbf{y}||_2^2}{2 \sigma^2})`, which implies that the objective functional
    should include the term :math:`\frac{1}{2 \sigma^2}||\mathbf{H}\mathbf{x} - \mathbf{y}||_2^2`. If the noise variance
    :math:`\sigma^2` is known, this term can be included in :math:`\mathcal{F}(\mathbf{x})`; however, if it is unknown,
    it should be included as :math:`\mathcal{G}_k(\mathbf{x}) = \frac{1}{2}||\mathbf{H}\mathbf{x} - \mathbf{y}||_2^2`
    for some :math:`k \geq 0`, and the noise variance will be estimated as :math:`\hat{\sigma}^2=\frac{1}
    {\hat{\theta_k}}`, where :math:`\hat{\theta_k}` is the estimated value of :math:`\theta_k` given by the algorithm
    (see top-level example of this module).

    **Remark 3:**

    This algorithm can be applied to non-differentiable proximable functionals :math:`\mathcal{G}_k` by using the
    utility class :py:class:`~pycsou.opt.solver.ProxFuncMoreau`. The gradient then calls that of the Moreau-Yosida
    envelope of the functional. This amounts to the SAPG algorithm described in [SAPG1]_. The envelope parameter
    :math:`\mu` is subject to a tradeoff between approximation bias (smaller values lead to lower bias) and convergence
    speed (smaller values lead to slower convergence) ; see [SAPG1]_ for more details.

    **Remark 4:**

    A theoretical analysis of the convergence of SAPG is given in [SAPG2]_. Note that in general, convergence is not
    guaranteed; however, there is ample empirical evidence of convergence for standard image-reconstruction problems
    (see top-level example of this module).

    **Remark 5:**

    A new :py:class:`~pycsou.sampler.sampler.ULA` chain is initialized at every iteration :math:`n` to account for the
    fact that the posterior distribution that is being targeted depends on the current iterate
    :math:`\boldsymbol{\theta}_n`. However for stability reasons, the ULA step size :math:`\gamma` is kept constant
    across iterations. By default, it is set conservatively so that the convergence guarantees of ULA are respected for
    any value of :math:`\boldsymbol{\theta}`, i.e. based on the Lipschitz constant of the gradient of :math:`\mathcal{F}
    (\mathbf{x}) + \sum_{k=0}^{K-1} \theta_{k}^\max \mathcal{G}_k(\mathbf{x})` (see [ULA]_). The first chain can be
    warm-started with the ``warm_start`` parameters of the ``fit()`` method, and/or with a starting point ``x0`` that is
    representative of the posterior distribution. All subsequent chains are warm-started with the last sample of the
    previous chain :math:`\mathbf{x}_{n-1, S-1}`.

    **Remark 6:**

    Following the recommendations of [SAPG1]_, the step sizes :math:`\boldsymbol{\delta}_n` are set to be
    :math:`\boldsymbol{\delta}_n = \boldsymbol{\delta}_0 (n + 1)^{-0.8}` for all :math:`n \in \mathbb{N}`. Although the
    choice of :math:`\boldsymbol{\delta}_0` is irrelevant in the theoretical analysis [SAPG2]_, in practice, it can
    drastically affect the convergence speed of the algorithm.

    **Initialization parameters of the class:**

    g: DiffFunc | list[DiffFunc]
        (K,) differentiable functionals :math:`[\mathcal{G}_0, \ldots, \mathcal{G}_{K-1}]`, where each
        :math:`\mathcal{G}_k` is an instance of :py:class:`~pycsou.abc.operator.DiffFunc`.
    homo_factors: Real | iterable[Real]
        (K,) homogeneity factors :math:`[\alpha_0, \ldots, \alpha_{K-1}]` corresponding to the functionals ``g``.
    f: DiffFunc | None
        Differentiable function :math:`\mathcal{F}`, instance of :py:class:`~pycsou.abc.operator.DiffFunc`.

    **Parameterization** of the ``fit()`` method:

    x0: pyct.NDArray
        (N,) starting point of the ULA Markov chain (see `Notes`).
    theta0: Real | iterable[Real]
        (K,) starting points for the regularization parameters.
    theta_min: Real | iterable[Real]
        (K,) point-wise lower bound for the regularization parameters.
    theta_min: Real | iterable[Real]
        (K,) point-wise upper bound for the regularization parameters.
    delta0: Real | iterable[Real]
        Starting values for the gradient ascent step (see `Notes`).
    warm_start: int
        Number of warm-start iterations for the ULA Markov chain (see `Notes`). Defaults to 0.
    gamma: Real
        Discretization step of ULA (see `Notes` of :py:class:`~pycsou.sampler.sampler.ULA` documentation`).
    batch_size: int
        Batch size for Monte Carlo estimates (see `Notes`). Defaults to 1.
    log_scale: bool
        If True (default), perform the projected gradient ascent step (see `Notes`) in logarithmic scale.
    rng:
        Random number generator for reproducibility. Defaults to None.
    """

    def __init__(
        self,
        g: typ.Union[pyca.DiffFunc, list[pyca.DiffFunc]],
        homo_factors: typ.Union[pyct.Real, typ.Iterable],
        f: pyca.DiffFunc = None,
        **kwargs
    ):

        kwargs.update(
            log_var=kwargs.get("log_var", ("theta",)),
        )
        super().__init__(**kwargs)
        if isinstance(g, list):
            if len(g) > 1:
                assert len(g) == len(homo_factors)
            self._g = g
        else:
            self._g = [g]

        self._homo_factors = np.array(homo_factors)
        self._f = pyco.NullFunc(dim=g[0].dim) if (f is None) else f

    def m_init(
        self,
        x0: pyct.NDArray,
        theta0: typ.Union[pyct.Real, typ.Iterable],
        theta_min: typ.Union[pyct.Real, typ.Iterable],
        theta_max: typ.Union[pyct.Real, typ.Iterable],
        delta0: pyct.Real = typ.Union[pyct.Real, typ.Iterable],
        warm_start: pyct.Integer = 0,
        gamma: pyct.Real = None,
        batch_size: pyct.Integer = 1,
        log_scale: bool = True,
        rng=None,
    ):
        mst = self._mstate  # shorthand
        mst["theta"], mst["theta_min"], mst["theta_max"] = tuple(np.atleast_1d(theta0, theta_min, theta_max))
        mst["theta"] = self._proj_interval(mst["theta"], mst["theta_min"], mst["theta_max"])

        delta0 = self._set_delta0(delta0)
        assert (
            len(mst["theta"]) == len(mst["theta_min"]) == len(mst["theta_max"]) == len(delta0) == len(self._g)
        ), "The number of hyperparameters must correspond to the number of functionals g."
        mst["delta"] = (delta0 / (k + 1) ** 0.8 for k in itertools.count(start=0))
        mst["batch_size"] = batch_size
        mst["log_scale"] = log_scale

        # Set gamma with most conservative Lipschitz constant
        self._update_moreau()
        gamma = self._set_gamma(gamma, dl=self._diff_lipschitz(mst["theta_max"]))
        mc = pycs.ULA(f=self._MAP_objective_func(), gamma=gamma)
        mst["gamma"] = mc._gamma
        mc_gen = mc.samples(x0=x0, rng=rng)
        for _ in range(warm_start):
            x0 = next(mc_gen)  # Warm-start Markov chain
        mst["x"] = x0
        mst["mc_gen"], mst["rng"] = mc_gen, mc._rng

    def m_step(self):
        mst = self._mstate  # shorthand
        delta = next(mst["delta"])
        # Compute MC expectation of g wrt to posterior distribution
        means = np.zeros_like(mst["theta"])
        for _ in range(mst["batch_size"]):
            x = next(mst["mc_gen"])
            for i in range(len(self._g)):
                means[i] += self._g[i](x)
        means /= mst["batch_size"]
        mst["x"] = x

        # Update theta
        grad = self._f.dim / (self._homo_factors * mst["theta"]) - means
        if mst["log_scale"]:
            eta = np.log(mst["theta"])
            eta += mst["theta"] * delta * grad
            mst["theta"] = np.exp(eta)
        else:
            mst["theta"] += delta * grad

        mst["theta"] = self._proj_interval(mst["theta"], mst["theta_min"], mst["theta_max"])

        # Update MC kernel with new theta iterate
        self._update_moreau()
        mc = pycs.ULA(f=self._MAP_objective_func(), gamma=mst["gamma"])
        mst["mc_gen"] = mc.samples(x0=mst["x"], rng=mst["rng"])

    def default_stop_crit(self) -> pyca.StoppingCriterion:
        from pycsou.opt.stop import RelError

        stop_crit = RelError(
            eps=1e-4,
            var="theta",
            f=None,
            norm=2,
            satisfy_all=True,
        )
        return stop_crit

    def solution(self):
        return self._mstate["theta"]

    @staticmethod
    def _proj_interval(x, x_min, x_max):
        return np.maximum(np.minimum(x, x_max), x_min)

    def _set_delta0(self, delta: pyct.Real = None) -> pyct.Real:
        if delta is None:
            return 1 / (self._mstate["theta"] * self._f.dim)
        else:
            return np.array(delta, dtype=float)

    def _set_gamma(self, gamma: pyct.Real = None, dl: pyct.Real = 0) -> pyct.Real:
        if gamma is None:
            return pycrt.coerce(0.98 / dl)
        else:
            return pycrt.coerce(gamma)

    def _diff_lipschitz(self, theta):
        return self._f.diff_lipschitz() + sum([theta[i] * self._g[i].diff_lipschitz() for i in range(len(self._g))])

    def _MAP_objective_func(self):
        to_sum = [self._mstate["theta"][i] * self._g[i] for i in range(len(self._g))]
        return self._f + functools.reduce(operator.add, to_sum)

    def _update_moreau(self):
        for i in range(len(self._g)):
            if isinstance(self._g[i], ProxFuncMoreau):
                self._g[i].set_mu(self._g[i]._mu0 * self._mstate["theta"][i])


class ProxFuncMoreau(pyca.ProxDiffFunc):
    r"""
    Proximable function with Moreau-Yosida envelope approximation for the gradient.

    Utility class to make a proximable functional differentiable, by approximating its gradient with that of its
    Moreau-Yosida envelope. The ``apply()`` and ``prox()`` methods call that of the original functional. This class can
    be useful within solvers that require differentiable functionals, notably :py:class:`~pycsou.opt.solver.RegParamMLE`.
    """

    def __init__(self, f: pyca.ProxFunc, mu: pyct.Real):
        r"""
        Parameters
        ----------
        f: int
            Dimension size. (Default: domain-agnostic.)
        mu: Real
            Moreau envelope parameter.

        """
        super().__init__(shape=(1, f.dim))
        self._f = f
        self._mu0 = mu  # Specifically for RegParamMLE
        self._mu = mu
        self._moreau_envelope = None
        self.set_mu(mu)

    def apply(self, arr: pyct.NDArray) -> pyct.NDArray:
        return self._f.apply(arr)

    def grad(self, arr: pyct.NDArray) -> pyct.NDArray:
        return self._moreau_envelope.grad(arr)

    def prox(self, arr: pyct.NDArray, tau: pyct.Real) -> pyct.NDArray:
        return self._f.prox(arr, tau)

    def set_mu(self, mu: pyct.Real):
        self._mu = mu
        self._moreau_envelope = self._f.moreau_envelope(mu)
        self._diff_lipschitz = self._moreau_envelope.diff_lipschitz()
