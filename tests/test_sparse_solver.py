import pytest
import torch
from fugw.solvers.sparse import FUGWSparseSolver
from fugw.utils import low_rank_squared_l2


@pytest.mark.parametrize("uot_solver", ["mm", "dc"])
def test_solvers(uot_solver):
    torch.manual_seed(100)

    use_cuda = torch.cuda.is_available()
    device = torch.device("cuda:0" if use_cuda else "cpu")
    torch.backends.cudnn.benchmark = True

    ns = 104
    ds = 3
    nt = 151
    dt = 7
    nf = 10

    source_features = torch.rand(ns, nf).to(device)
    target_features = torch.rand(nt, nf).to(device)
    source_embeddings = torch.rand(ns, ds).to(device)
    target_embeddings = torch.rand(nt, dt).to(device)

    # Gs = torch.cdist(source_embeddings, source_embeddings)
    # Gt = torch.cdist(target_embeddings, target_embeddings)
    # K = torch.rand(ns, nt)

    Gs = low_rank_squared_l2(source_embeddings, source_embeddings)
    Gt = low_rank_squared_l2(target_embeddings, target_embeddings)
    K = low_rank_squared_l2(source_features, target_features)

    init_plan = torch.rand(ns, nt).to_sparse()

    fugw = FUGWSparseSolver(
        nits_bcd=100,
        nits_uot=1000,
        tol_bcd=1e-7,
        tol_uot=1e-7,
        eval_bcd=2,
        eval_uot=10,
    )

    pi, gamma, duals_pi, duals_gamma, loss, loss_ent = fugw.solver(
        Gs=Gs,
        Gt=Gt,
        K=K,
        alpha=0.8,
        rho_x=2,
        rho_y=3,
        eps=0.02,
        uot_solver=uot_solver,
        reg_mode="independent",
        init_plan=init_plan,
        return_plans_only=False,
        verbose=True,
        early_stopping_threshold=1e-6,
        eps_base=1e4,
    )

    print(pi)
    print(gamma)

    assert pi.size() == (ns, nt)
    assert gamma.size() == (ns, nt)

    # if uot_solver == "mm":
    #     assert duals_pi is None
    #     assert duals_gamma is None
    # else:
    #     assert len(duals_pi) == 2
    #     assert duals_pi[0].shape == (ns,)
    #     assert duals_pi[1].shape == (nt,)
    #     assert len(duals_gamma) == 2
    #     assert duals_gamma[0].shape == (ns,)
    #     assert duals_gamma[1].shape == (nt,)

    assert len(loss) == len(loss_ent) - 1
