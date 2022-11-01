import torch
from tqdm import tqdm


def solver_scaling(cost, init_duals, uot_params, tuple_pxy, train_params):
    """
    Scaling algorithm.
    """

    log_px, log_py, pxy = tuple_pxy
    vx, vy = init_duals
    rho_x, rho_y, eps = uot_params
    niters, tol, eval_freq = train_params

    tau_x = 1 if torch.isinf(rho_x) else rho_x / (rho_x + eps)
    tau_y = 1 if torch.isinf(rho_y) else rho_y / (rho_y + eps)

    for idx in range(niters):
        vx_prev, vy_prev = vx.detach().clone(), vy.detach().clone()
        if rho_y == 0:
            vy = torch.zeros_like(vy)
        else:
            vy = -tau_y * ((vx + log_px)[:, None] - cost / eps).logsumexp(dim=0)

        if rho_x == 0:
            vx = torch.zeros_like(vx)
        else:
            vx = -tau_x * ((vy + log_py)[None, :] - cost / eps).logsumexp(dim=1)

        if (
            idx % eval_freq == 0
            and max((vx - vx_prev).abs().max(), (vy - vy_prev).abs().max()) < tol
        ):
            break

    pi = pxy * (vx[:, None] + vy[None, :] - cost / eps).exp()

    return (vx, vy), pi


def solver_mm(cost, init_pi, uot_params, tuple_pxy, train_params):
    """
    Solve (entropic) UOT using the majorization-minimization algorithm.

    Allow epsilon to be 0 but rho_x and rho_y can't be infinity.

    Note that if the parameters are small so that numerically, the exponential of
    negative cost will contain zeros and this serves as sparsification of the optimal plan.

    If the parameters are large, then the resulting optimal plan is more dense than the one
    obtained from scaling algorithm.
    But all parameters should not be too small, otherwise the kernel will contain too many zeros.
    Consequently, the optimal plan will contain NaN (because the Kronecker sum of two marginals
    will eventually contain zeros, and divided by zero will result in undesirable coupling).
    """

    niters, tol, eval_freq = train_params
    px, py = tuple_pxy
    rho_x, rho_y, eps = uot_params

    sum_param = rho_x + rho_y + eps
    tau_x, tau_y, r = rho_x / sum_param, rho_y / sum_param, eps / sum_param
    K = (
        px[:, None] ** (tau_x + r)
        * py[None, :] ** (tau_y + r)
        * (-cost / sum_param).exp()
    )

    pi1, pi2, pi = init_pi.sum(1), init_pi.sum(0), init_pi

    for idx in range(niters):
        pi1_old, pi2_old = pi1.detach().clone(), pi2.detach().clone()
        pi = pi ** (tau_x + tau_y) / (pi1[:, None] ** tau_x * pi2[None, :] ** tau_y) * K
        pi1, pi2 = pi.sum(1), pi.sum(0)

        if (idx % eval_freq == 0) and max(
            (pi1 - pi1_old).abs().max(), (pi2 - pi2_old).abs().max()
        ) < tol:
            break

    return pi


def solver_dc(
    cost,
    init_pi,
    init_duals,
    uot_params,
    tuple_pxy,
    train_params,
    eps_base=1,
    verbose=True,
):

    niters, tol, eval_freq = train_params
    rho1, rho2, eps = uot_params
    px, py, pxy = tuple_pxy
    u, v = init_duals
    m1, pi = init_pi.sum(1), init_pi

    sum_eps = eps_base + eps
    tau1 = 1 if rho1 == float("inf") else rho1 / (rho1 + sum_eps)
    tau2 = 1 if rho2 == float("inf") else rho2 / (rho2 + sum_eps)

    K = torch.exp(-cost / sum_eps)
    range_niters = tqdm(range(niters)) if verbose else range(niters)

    for idx in range_niters:
        m1_prev = m1.detach().clone()

        # IPOT
        G = K * pi if (eps_base / sum_eps) == 1 else K * pi ** (eps_base / sum_eps)
        v = (G.T @ (u * px)) ** (-tau2) if rho2 != 0 else torch.ones_like(v)
        u = (G @ (v * py)) ** (-tau1) if rho1 != 0 else torch.ones_like(u)
        pi = u[:, None] * G * v[None, :]

        # Check stopping criterion
        if idx % eval_freq == 0:
            m1 = pi.sum(1)
            if m1.isnan().any() or m1.isinf().any():
                raise ValueError("There is NaN in coupling. Please increase eps_base.")

            error = (m1 - m1_prev).abs().max().item()
            if error < tol:
                break

    pi = pi * pxy  # renormalize couplings

    return (u, v), pi


def compute_approx_kl(p, q):
    # By convention: 0 log 0 = 0
    entropy = torch.nan_to_num(p * (p / q).log(), nan=0.0, posinf=0.0, neginf=0.0).sum()
    return entropy


def compute_kl(p, q):
    return compute_approx_kl(p, q) - p.sum() + q.sum()


def compute_quad_kl(mu, nu, alpha, beta):
    """
    Calculate the KL divergence between two product measures:
    KL(mu otimes nu, alpha otimes beta) =
    m_mu * KL(nu, beta)
    + m_nu * KL(mu, alpha)
    + (m_mu - m_alpha) * (m_nu - m_beta)

    Parameters
    ----------
    mu: vector or matrix
    nu: vector or matrix
    alpha: vector or matrix with the same size as mu
    beta: vector or matrix with the same size as nu

    Returns
    ----------
    KL divergence between two product measures
    """

    m_mu = mu.sum()
    m_nu = nu.sum()
    m_alpha = alpha.sum()
    m_beta = beta.sum()
    const = (m_mu - m_alpha) * (m_nu - m_beta)
    kl = m_nu * compute_kl(mu, alpha) + m_mu * compute_kl(nu, beta) + const

    return kl